"""Envelope parsing.

An envelope is newline-delimited JSON: one header line, then alternating item-header and
payload lines.

    {"event_id":"9f8e...","sent_at":"2026-08-10T10:00:00Z"}
    {"type":"event","length":812}
    {"level":"error","exception":{...}}
    {"type":"session"}
    {"sid":"...","status":"crashed"}

`length` is optional. When present it is a byte count and the payload may itself contain
newlines; when absent the payload runs to the next newline. SDKs that can cheaply measure their
payload should send it, because it is the only way to transmit embedded newlines.

Unknown item types are kept rather than rejected — an old server must accept envelopes from a
newer SDK, or every SDK release becomes a coordinated deploy.
"""

import json
from dataclasses import dataclass
from typing import Any


class EnvelopeError(Exception):
    """The envelope is malformed. Always the client's fault, always a 400."""


class EnvelopeTooLargeError(EnvelopeError):
    """Rejected on size before any parsing work is done."""


@dataclass(frozen=True)
class EnvelopeItem:
    type: str
    headers: dict[str, Any]
    payload: bytes

    def json(self) -> dict[str, Any]:
        return _load_object(self.payload, f"{self.type} payload")


@dataclass(frozen=True)
class Envelope:
    headers: dict[str, Any]
    items: list[EnvelopeItem]

    def items_of_type(self, item_type: str) -> list[EnvelopeItem]:
        return [item for item in self.items if item.type == item_type]


def _reject_constant(name: str) -> Any:
    """NaN, Infinity and -Infinity are not JSON, but Python's parser accepts them anyway.

    Postgres does not, and the payload is stored verbatim — so one of these anywhere in an
    item turned the whole request into a 500. Raising here makes it a rejected item instead,
    which is what every other malformed payload already gets.
    """
    raise ValueError(f"{name} is not a JSON value")


def _load_object(raw: bytes, what: str) -> dict[str, Any]:
    try:
        value = json.loads(raw, parse_constant=_reject_constant)
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
        raise EnvelopeError(f"{what} is not valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise EnvelopeError(f"{what} must be a JSON object, got {type(value).__name__}")
    return value


def parse(raw: bytes, *, max_bytes: int, max_items: int = 100) -> Envelope:
    """Parse an envelope, or raise EnvelopeError.

    Size is checked before anything else: a client can send arbitrary bytes, and parsing them to
    discover they are too many is work an attacker gets to choose the cost of.
    """
    if len(raw) > max_bytes:
        raise EnvelopeTooLargeError(f"envelope is {len(raw)} bytes, limit is {max_bytes}")
    if not raw.strip():
        raise EnvelopeError("envelope is empty")

    header_line, _, rest = raw.partition(b"\n")
    headers = _load_object(header_line, "envelope header")

    items: list[EnvelopeItem] = []
    while rest.strip():
        if len(items) >= max_items:
            raise EnvelopeError(f"envelope holds more than {max_items} items")

        item_line, _, rest = rest.partition(b"\n")
        item_headers = _load_object(item_line, "item header")

        item_type = item_headers.get("type")
        if not isinstance(item_type, str) or not item_type:
            raise EnvelopeError("item header is missing a string 'type'")

        payload, rest = _read_payload(item_headers.get("length"), rest)
        items.append(EnvelopeItem(type=item_type, headers=item_headers, payload=payload))

    return Envelope(headers=headers, items=items)


def _read_payload(length: Any, rest: bytes) -> tuple[bytes, bytes]:
    if length is None:
        payload, _, remainder = rest.partition(b"\n")
        return payload, remainder

    # bool is an int subclass, and {"length": true} must not be read as one byte.
    if not isinstance(length, int) or isinstance(length, bool) or length < 0:
        raise EnvelopeError(f"item 'length' must be a non-negative integer, got {length!r}")
    if length > len(rest):
        raise EnvelopeError(f"item claims {length} bytes but only {len(rest)} remain")

    payload, remainder = rest[:length], rest[length:]
    # The newline terminating the payload is a separator, not part of the next header.
    if remainder.startswith(b"\n"):
        remainder = remainder[1:]
    return payload, remainder
