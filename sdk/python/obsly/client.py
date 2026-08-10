"""The client and the module-level API."""

import logging
import os
import random
import socket
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from types import TracebackType
from typing import Any

from obsly import dsn as dsn_module
from obsly.stacktrace import exception_chain
from obsly.tracing import Transaction, _current_span, get_current_span
from obsly.transport import Transport, build_envelope

logger = logging.getLogger("obsly")

SDK = {"name": "obsly.python", "version": "0.1.0"}


class Client:
    def __init__(
        self,
        dsn: str,
        *,
        environment: str = "production",
        release: str = "",
        server_name: str | None = None,
        send_default_pii: bool = False,
        traces_sample_rate: float = 0.0,
        tags: dict[str, str] | None = None,
        transport: Transport | None = None,
    ) -> None:
        parsed = dsn_module.parse(dsn)
        self.environment = environment
        self.release = release
        self.server_name = server_name if server_name is not None else socket.gethostname()
        # Off by default. Turning on the capture of user identifiers, addresses and request
        # bodies must be a decision somebody made, not a default they inherited.
        self.send_default_pii = send_default_pii
        # Off by default. Tracing multiplies event volume by however many requests a
        # service handles, and nobody should discover that from a bill.
        self.traces_sample_rate = max(0.0, min(1.0, traces_sample_rate))
        self.tags = dict(tags or {})
        self._transport = transport or Transport(parsed.envelope_url, parsed.public_key)

    def capture_exception(self, exc: BaseException, *, extra: dict[str, Any] | None = None) -> str:
        return self._capture({"exception": {"values": exception_chain(exc)}}, extra)

    def capture_message(
        self, message: str, *, level: str = "info", extra: dict[str, Any] | None = None
    ) -> str:
        return self._capture({"message": message, "level": level}, extra)

    def _capture(self, payload: dict[str, Any], extra: dict[str, Any] | None) -> str:
        event_id = str(uuid.uuid4())

        event: dict[str, Any] = {
            "event_id": event_id,
            "timestamp": datetime.now(tz=UTC).isoformat(),
            "platform": "python",
            "level": payload.pop("level", "error"),
            "environment": self.environment,
            "release": self.release,
            "server_name": self.server_name,
            "sdk": SDK,
            "tags": {**self.tags, **(extra or {}).pop("tags", {})} if extra else dict(self.tags),
            **payload,
        }
        if extra:
            event["extra"] = extra

        # The single line that makes an error and a trace the same story. Without it the two
        # are separate tables joined by a timestamp guess, which is exactly the correlation
        # every other tool leaves to the operator.
        span = get_current_span()
        if span is not None and span.sampled:
            event["contexts"] = {
                "trace": {"trace_id": span.trace_id, "span_id": span.span_id, "op": span.op}
            }

        envelope = build_envelope(
            {"event_id": event_id, "sent_at": event["timestamp"]},
            [("event", event)],
        )
        self._transport.send(envelope)
        return event_id

    @contextmanager
    def start_transaction(
        self,
        name: str,
        op: str = "custom",
        *,
        trace_id: str | None = None,
        parent_span_id: str | None = None,
        sampled: bool | None = None,
    ) -> Iterator[Transaction]:
        """Begin a trace, or continue one an upstream service started.

        `sampled` is inherited when the caller passes it, and only decided here when it is None.
        A per-service decision would produce traces with holes in the middle, which read as
        "that service was never called" rather than "we chose not to record it".
        """
        if sampled is None:
            sampled = random.random() < self.traces_sample_rate  # noqa: S311 - not a secret

        transaction = Transaction(op=op, name=name, sampled=sampled, parent_span_id=parent_span_id)
        if trace_id:
            # Continuing an upstream trace rather than starting one, so the id must be theirs.
            transaction.trace_id = trace_id
        # A transaction is its own root: nested spans find their container through this.
        transaction.transaction = transaction

        token = _current_span.set(transaction)
        try:
            yield transaction
        except Exception:
            transaction.finish("internal_error")
            raise
        else:
            transaction.finish()
        finally:
            _current_span.reset(token)
            if transaction.sampled:
                self._send_transaction(transaction)

    def _send_transaction(self, transaction: Transaction) -> None:
        event_id = str(uuid.uuid4())
        payload = {
            "event_id": event_id,
            "platform": "python",
            "environment": self.environment,
            "release": self.release,
            "server_name": self.server_name,
            "sdk": SDK,
            "tags": dict(self.tags),
            **transaction.to_payload(),
        }
        self._transport.send(
            build_envelope(
                {"event_id": event_id, "sent_at": payload["timestamp"]},
                [("transaction", payload)],
            )
        )

    def flush(self, timeout: float = 2.0) -> bool:
        return self._transport.flush(timeout)

    def close(self) -> None:
        self._transport.close()


_client: Client | None = None


def init(dsn: str | None = None, **options: Any) -> Client | None:
    """Configure the SDK.

    A missing or unparseable DSN disables reporting instead of raising. Nobody wants their
    service to refuse to boot because the telemetry endpoint was misconfigured, and DSN is read
    from the environment so that a deploy can turn reporting off without a code change.
    """
    global _client

    dsn = dsn or os.environ.get("OBSLY_DSN", "")
    if not dsn:
        logger.info("obsly: no DSN configured, error reporting is disabled")
        _client = None
        return None

    try:
        _client = Client(dsn, **options)
    except dsn_module.DsnError as exc:
        logger.warning("obsly: disabled, %s", exc)
        _client = None

    return _client


def get_client() -> Client | None:
    return _client


def capture_exception(exc: BaseException | None = None, **kwargs: Any) -> str | None:
    if _client is None:
        return None
    if exc is None:
        import sys

        exc = sys.exc_info()[1]
    if exc is None:
        return None
    return _client.capture_exception(exc, **kwargs)


@contextmanager
def start_transaction(name: str, op: str = "custom", **kwargs: Any) -> Iterator[Transaction]:
    """No-op when the SDK is not configured, so instrumentation never depends on it."""
    if _client is None:
        yield Transaction(op=op, name=name, sampled=False)
        return
    with _client.start_transaction(name, op, **kwargs) as transaction:
        yield transaction


def capture_message(message: str, **kwargs: Any) -> str | None:
    return None if _client is None else _client.capture_message(message, **kwargs)


def flush(timeout: float = 2.0) -> bool:
    return True if _client is None else _client.flush(timeout)


__all__ = [
    "Client",
    "TracebackType",
    "capture_exception",
    "capture_message",
    "flush",
    "get_client",
    "init",
    "start_transaction",
]
