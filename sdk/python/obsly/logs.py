"""Structured logs.

Errors tell you what broke. Traces tell you where the time went. Logs tell you what the
application was *saying* — including on the requests that succeeded, which is most of them and
where the explanation for the failures usually lives.

Every record carries the active trace and span, so a log line and the request that produced it
are joined by an index lookup rather than by scrolling to a timestamp.

Two entry points, deliberately:

- `obsly.logger.info("...")` for new code.
- `ObslyLogHandler`, attached to the stdlib root logger, so an application that already logs
  gets its logs ingested without editing a single call site. An observability SDK that only
  sees logs written specially for it sees the least interesting ones.
"""

import logging
import threading
import time
from datetime import UTC, datetime
from typing import Any

# The levels Obsly stores, mapped from stdlib. `trace` has no stdlib equivalent and exists for
# SDKs whose language has one.
LEVELS = ("trace", "debug", "info", "warning", "error", "fatal")

_STDLIB_LEVELS = {
    logging.DEBUG: "debug",
    logging.INFO: "info",
    logging.WARNING: "warning",
    logging.ERROR: "error",
    logging.CRITICAL: "fatal",
}

MAX_BODY = 8192
MAX_ATTRIBUTES = 40


class LogBuffer:
    """Batches records and flushes on size or age.

    One HTTP request per log line would make logging the slowest thing an application does.

    No drop guard here on purpose: the buffer empties every time it reaches capacity, so it
    cannot grow past it, and the bound that actually matters under a burst is the transport's
    queue — which drops and counts. A second limiter here would be dead code pretending to
    protect something.
    """

    def __init__(self, capacity: int = 100, max_age: float = 5.0) -> None:
        self._capacity = capacity
        self._max_age = max_age
        self._records: list[dict[str, Any]] = []
        self._oldest: float | None = None
        self._lock = threading.Lock()

    def add(self, record: dict[str, Any]) -> list[dict[str, Any]] | None:
        """Append, returning a batch when one is ready to send."""
        with self._lock:
            self._records.append(record)
            if self._oldest is None:
                self._oldest = time.time()

            full = len(self._records) >= self._capacity
            stale = time.time() - self._oldest >= self._max_age
            if full or stale:
                return self._drain_locked()
        return None

    def drain(self) -> list[dict[str, Any]]:
        with self._lock:
            return self._drain_locked()

    def _drain_locked(self) -> list[dict[str, Any]]:
        batch, self._records, self._oldest = self._records, [], None
        return batch


def build_record(
    level: str,
    body: str,
    *,
    logger_name: str = "",
    attributes: dict[str, Any] | None = None,
    trace_id: str = "",
    span_id: str = "",
    timestamp: datetime | None = None,
) -> dict[str, Any]:
    return {
        "timestamp": (timestamp or datetime.now(tz=UTC)).isoformat(),
        "level": level if level in LEVELS else "info",
        "body": str(body)[:MAX_BODY],
        "logger": str(logger_name)[:200],
        "trace_id": trace_id,
        "span_id": span_id,
        "attributes": _attributes(attributes),
    }


def _attributes(raw: dict[str, Any] | None) -> dict[str, Any]:
    """Flat and bounded — attributes are filterable, and a nested blob is not."""
    if not raw:
        return {}
    return {
        str(key)[:64]: value
        for key, value in list(raw.items())[:MAX_ATTRIBUTES]
        if not isinstance(value, dict | list)
    }


class ObslyLogHandler(logging.Handler):
    """Bridge from the standard library.

        logging.getLogger().addHandler(ObslyLogHandler())

    Attaching this is how an application that already logs starts sending logs without editing
    a call site.
    """

    def __init__(self, level: int = logging.INFO) -> None:
        super().__init__(level)

    def emit(self, record: logging.LogRecord) -> None:
        from obsly.client import get_client

        client = get_client()
        if client is None:
            return

        try:
            client.capture_log(
                _STDLIB_LEVELS.get(record.levelno, "info"),
                record.getMessage(),
                logger_name=record.name,
                # The un-interpolated template groups. "user %s not found" is one line in the
                # UI; the formatted string is ten thousand distinct ones.
                attributes={"template": str(record.msg)[:500]} if record.args else None,
                timestamp=datetime.fromtimestamp(record.created, tz=UTC),
            )
        except Exception:  # noqa: BLE001
            # A logging handler that raises turns every log call in the application into a
            # failure. logging.Handler.handleError is the documented place for this.
            self.handleError(record)
