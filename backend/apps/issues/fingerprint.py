"""Fingerprinting — the function that turns four million events into thirty problems.

Two events belong to the same Issue when an engineer would fix them with one change. Everything
here is in service of that judgement, and both failure directions are expensive:

- Too coarse, and unrelated bugs merge into one issue that can never be resolved.
- Too fine, and one bug arrives as ten thousand issues and buries everything else.

Grouping deliberately ignores line numbers. Adding an import at the top of a file shifts every
line below it, and a fingerprint that moved with them would reopen every issue in the file on
the next deploy.
"""

import hashlib
import re
from typing import Any

# Values that differ per occurrence but not per bug. Without this,
# "Timeout after 3021ms" and "Timeout after 4102ms" are two issues forever.
_VARIABLE_PATTERNS = (
    (
        re.compile(
            r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
        ),
        "<uuid>",
    ),
    (re.compile(r"\b0x[0-9a-fA-F]+\b"), "<hex>"),
    (re.compile(r"\b[0-9a-fA-F]{16,}\b"), "<hash>"),
    # No trailing \b: a boundary never matches between a digit and a letter, so "3021ms" would
    # keep its number and "Timeout after 3021ms" would be its own issue forever.
    (re.compile(r"(?<!\w)\d[\d,._]*"), "<n>"),
    (re.compile(r"'[^']*'"), "'<str>'"),
    (re.compile(r'"[^"]*"'), '"<str>"'),
)

MAX_FRAMES = 10


def normalize_value(value: str) -> str:
    """Strip the parts of a message that vary between occurrences of the same bug."""
    normalized = value.strip()
    for pattern, replacement in _VARIABLE_PATTERNS:
        normalized = pattern.sub(replacement, normalized)
    return normalized[:500]


def compute(payload: dict[str, Any]) -> tuple[str, list[str]]:
    """Return (fingerprint hash, the components it was built from).

    The components are returned alongside the hash so the UI can show *why* two events grouped
    together. A grouping decision nobody can inspect is one nobody can trust.
    """
    components = _components(payload)
    digest = hashlib.sha256("\n".join(components).encode()).hexdigest()
    return digest, components


def _components(payload: dict[str, Any]) -> list[str]:
    custom = payload.get("fingerprint")
    if isinstance(custom, list) and custom:
        # An explicit fingerprint is the caller telling us they know better than the heuristic.
        return [str(part)[:200] for part in custom[:10]]

    values = _exception_values(payload)
    if values:
        latest = values[-1]
        frames = _frame_signature(latest)
        if frames:
            # Frames alone, without the exception message: the same code path failing is the
            # same bug whether the message says "id 7" or "id 9".
            return [str(latest.get("type", "")), *frames]
        return [str(latest.get("type", "")), normalize_value(str(latest.get("value", "")))]

    message = payload.get("message")
    if isinstance(message, dict):
        message = message.get("message") or message.get("formatted") or ""
    if message:
        return ["message", normalize_value(str(message))]

    return ["level", str(payload.get("level", "error"))]


def _exception_values(payload: dict[str, Any]) -> list[dict[str, Any]]:
    exception = payload.get("exception")
    raw = exception.get("values") if isinstance(exception, dict) else exception
    return [entry for entry in raw if isinstance(entry, dict)] if isinstance(raw, list) else []


def _frame_signature(entry: dict[str, Any]) -> list[str]:
    """module+function per in-app frame, deepest last, line numbers excluded.

    Falls back to every frame when nothing is marked in_app — an SDK that cannot classify
    frames should still group, just less precisely.
    """
    stacktrace = entry.get("stacktrace")
    if not isinstance(stacktrace, dict):
        return []

    frames = [f for f in stacktrace.get("frames", []) if isinstance(f, dict)]
    in_app = [f for f in frames if f.get("in_app")]
    chosen = (in_app or frames)[-MAX_FRAMES:]

    return [
        f"{frame.get('module') or frame.get('filename') or '?'}:{frame.get('function') or '?'}"
        for frame in chosen
    ]
