"""The client and the module-level API."""

import logging
import os
import random
import socket
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from types import TracebackType
from typing import Any

from obsly import dsn as dsn_module
from obsly.logs import LogBuffer, build_record
from obsly.stacktrace import exception_chain
from obsly.tracing import Transaction, _current_span, get_current_span
from obsly.transport import Transport, build_envelope

_sdk_log = logging.getLogger("obsly")

SDK = {"name": "obsly.python", "version": "0.1.0"}

# An application evaluating thousands of distinct flags is misusing them, and an unbounded map
# here would let a flag name built from a user id grow without limit inside a long-lived
# process.
MAX_FLAGS = 100


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
        enable_logs: bool = False,
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
        # Evaluated feature flags, in the order they were decided. A bounded, ordered map: the
        # order is the evaluation order, which is what makes it a log of what had already been
        # decided when something broke rather than a snapshot of settings.
        self._flags: dict[str, bool] = {}
        self.enable_logs = enable_logs
        self._logs = LogBuffer()
        self._stop_flusher = threading.Event()
        self._flusher: threading.Thread | None = None
        self._transport = transport or Transport(parsed.envelope_url, parsed.public_key)

        # Started only after _transport exists: the thread touches it on its first tick, and
        # starting it earlier is a race that shows up as an AttributeError under load.
        # It exists because the buffer's age check only runs when something is added — without
        # it the last lines of a burst sit stranded until the application happens to log
        # again, and the end of a burst is usually the part worth reading.
        if enable_logs:
            self._flusher = threading.Thread(
                target=self._flush_periodically, name="obsly-logs", daemon=True
            )
            self._flusher.start()

    def set_flag(self, name: str, result: bool) -> None:
        """Record that a feature flag was evaluated, and to what.

        Called from wherever the decision is made — usually a thin wrapper around whatever
        flag provider the application already uses — so the log reflects what the code actually
        asked for rather than what a snapshot of the flag service says now. Those differ
        exactly when it matters: during a rollout.

        Re-evaluating a flag moves it to the end, because the last decision is the one the
        code acted on.
        """
        if not name or not isinstance(result, bool):
            return
        self._flags.pop(name, None)
        if len(self._flags) >= MAX_FLAGS:
            # Oldest out. A bounded log that drops the newest would hide the evaluation
            # closest to the failure, which is the one worth keeping.
            self._flags.pop(next(iter(self._flags)))
        self._flags[str(name)[:200]] = result

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
            "flags": dict(self._flags),
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

    def capture_log(
        self,
        level: str,
        body: str,
        *,
        logger_name: str = "",
        attributes: dict[str, Any] | None = None,
        timestamp: datetime | None = None,
    ) -> None:
        """Buffer a log record, sending a batch when one is ready.

        Off unless enable_logs is set. Logs are by far the highest-volume signal — an
        application emits them on every request, not only the failing ones — and turning that
        on must be a decision somebody made.
        """
        if not self.enable_logs:
            return

        span = get_current_span()
        record = build_record(
            level,
            body,
            logger_name=logger_name,
            attributes=attributes,
            trace_id=span.trace_id if span is not None and span.sampled else "",
            span_id=span.span_id if span is not None and span.sampled else "",
            timestamp=timestamp,
        )

        batch = self._logs.add(record)
        if batch:
            self._send_logs(batch)

    def _send_logs(self, records: list[dict[str, Any]]) -> None:
        if not records:
            return
        envelope_id = str(uuid.uuid4())
        self._transport.send(
            build_envelope(
                {"event_id": envelope_id, "sent_at": datetime.now(tz=UTC).isoformat()},
                [
                    (
                        "log",
                        {
                            "items": records,
                            "environment": self.environment,
                            "release": self.release,
                            "server_name": self.server_name,
                        },
                    )
                ],
            )
        )

    def _flush_periodically(self, interval: float = 2.0) -> None:
        while not self._stop_flusher.wait(interval):
            try:
                self._send_logs(self._logs.drain())
            except Exception:  # noqa: BLE001
                # This thread outliving one bad flush matters more than the batch it lost.
                _sdk_log.debug("obsly: periodic log flush failed", exc_info=True)

    def flush(self, timeout: float = 2.0) -> bool:
        # Drain buffered logs first, or a process exiting cleanly loses everything it said in
        # the last few seconds — which is the window that usually matters.
        self._send_logs(self._logs.drain())
        return self._transport.flush(timeout)

    def close(self) -> None:
        self._stop_flusher.set()
        self.flush()
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
        _sdk_log.info("obsly: no DSN configured, error reporting is disabled")
        _client = None
        return None

    try:
        _client = Client(dsn, **options)
    except dsn_module.DsnError as exc:
        _sdk_log.warning("obsly: disabled, %s", exc)
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


class _Logger:
    """`obsly.logger.info("...")` — the terse entry point for new code."""

    def _log(self, level: str, body: str, **attributes: Any) -> None:
        if _client is not None:
            _client.capture_log(level, body, attributes=attributes or None)

    def trace(self, body: str, **attributes: Any) -> None:
        self._log("trace", body, **attributes)

    def debug(self, body: str, **attributes: Any) -> None:
        self._log("debug", body, **attributes)

    def info(self, body: str, **attributes: Any) -> None:
        self._log("info", body, **attributes)

    def warning(self, body: str, **attributes: Any) -> None:
        self._log("warning", body, **attributes)

    def error(self, body: str, **attributes: Any) -> None:
        self._log("error", body, **attributes)

    def fatal(self, body: str, **attributes: Any) -> None:
        self._log("fatal", body, **attributes)


logger = _Logger()


def capture_message(message: str, **kwargs: Any) -> str | None:
    return None if _client is None else _client.capture_message(message, **kwargs)


def set_flag(name: str, result: bool) -> None:
    """Record a feature-flag evaluation on the current client.

    A no-op before init, like every other module-level call here: an application should not
    have to guard its own instrumentation with a check that the SDK is running.
    """
    if _client is not None:
        _client.set_flag(name, result)


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
    "logger",
    "start_transaction",
]
