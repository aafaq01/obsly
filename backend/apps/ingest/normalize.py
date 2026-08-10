"""Turn a client event payload into an unsaved Event.

Everything here treats the payload as hostile: it arrives from a public endpoint authenticated
only by a write-only key, so every field is optional, wrongly typed, or oversized until proven
otherwise. Normalisation never raises on bad input — it degrades. A malformed `release` is not
a reason to lose the stack trace attached to it.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from apps.events.models import Event, Level
from apps.projects.models import Project

# Client clocks are wrong more often than anyone expects: skew, offline buffering, a device
# whose battery died. Accept the past, but never let a client claim the future — a single
# event dated 2049 would sit at the top of every "latest" list forever.
MAX_CLOCK_SKEW = timedelta(minutes=5)

_MAX_LENGTHS = {
    "platform": 32,
    "exception_type": 256,
    "culprit": 512,
    "environment": 64,
    "release": 128,
    "server_name": 256,
}


def event_from_payload(
    project: Project,
    payload: dict[str, Any],
    *,
    now: datetime,
    envelope_event_id: Any = None,
) -> Event:
    """Build an unsaved Event.

    `envelope_event_id` is the id from the envelope header. That header is where the id
    canonically lives on the wire — the item payload usually repeats it, but is not required to.
    Ignoring the header means generating a fresh UUID for every delivery, which silently breaks
    the retry-idempotency the primary key exists to provide.
    """
    exception_type, exception_value, culprit = _extract_exception(payload)

    return Event(
        id=_event_id(payload.get("event_id") or envelope_event_id),
        project=project,
        timestamp=_timestamp(payload.get("timestamp"), now=now),
        level=_level(payload.get("level")),
        platform=_text(payload.get("platform"), "platform"),
        message=_message(payload.get("message")),
        exception_type=_text(exception_type, "exception_type"),
        exception_value=str(exception_value or "")[:4000],
        culprit=_text(culprit, "culprit"),
        trace_id=_trace_field(payload, "trace_id", 32),
        span_id=_trace_field(payload, "span_id", 16),
        environment=_text(payload.get("environment"), "environment"),
        release=_text(payload.get("release"), "release"),
        server_name=_text(payload.get("server_name"), "server_name"),
        tags=_tags(payload.get("tags")),
        payload=payload,
    )


def _trace_field(payload: dict[str, Any], key: str, length: int) -> str:
    """Read contexts.trace.<key>, or empty when the error happened outside a trace.

    Validated as hex of the exact length: a junk value here would silently point an error at a
    trace that does not exist, which is worse than admitting there is no link.
    """
    contexts = payload.get("contexts")
    trace = contexts.get("trace") if isinstance(contexts, dict) else None
    value = trace.get(key) if isinstance(trace, dict) else None

    if not isinstance(value, str):
        return ""
    value = value.strip().lower()
    if len(value) != length or not all(c in "0123456789abcdef" for c in value):
        return ""
    return value


def _event_id(raw: Any) -> uuid.UUID:
    """Honour the client's id so retries are idempotent, but never trust its shape."""
    if isinstance(raw, str):
        try:
            return uuid.UUID(raw)
        except ValueError:
            pass
    return uuid.uuid4()


def _timestamp(raw: Any, *, now: datetime) -> datetime:
    parsed: datetime | None = None

    if isinstance(raw, int | float) and not isinstance(raw, bool):
        try:
            parsed = datetime.fromtimestamp(raw, tz=UTC)
        except (OverflowError, OSError, ValueError):
            parsed = None
    elif isinstance(raw, str):
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            parsed = None

    if parsed is None:
        return now
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return min(parsed, now + MAX_CLOCK_SKEW)


def _level(raw: Any) -> str:
    if isinstance(raw, str) and raw in Level.values:
        return raw
    return Level.ERROR


def _message(raw: Any) -> str:
    if isinstance(raw, dict):
        # Structured messages carry the un-interpolated template, which is the better title
        # because it groups: "user %s not found" instead of ten thousand distinct strings.
        raw = raw.get("formatted") or raw.get("message") or ""
    return str(raw or "")[:8000]


def _text(raw: Any, field: str) -> str:
    if raw is None or isinstance(raw, dict | list):
        return ""
    return str(raw)[: _MAX_LENGTHS[field]]


def _tags(raw: Any) -> dict[str, str]:
    """Tags are indexed and filterable, so they must stay flat, short and stringy."""
    if not isinstance(raw, dict):
        return {}
    return {
        str(key)[:64]: str(value)[:200]
        for key, value in list(raw.items())[:50]
        if not isinstance(value, dict | list)
    }


def _extract_exception(payload: dict[str, Any]) -> tuple[Any, Any, Any]:
    """Pull type/value/culprit from the innermost exception in a chain.

    The chain is ordered oldest cause first, so the last entry is the one actually raised —
    that is the one an engineer sees at the top of a traceback and searches for.
    """
    exception = payload.get("exception")
    values: list[Any] = []

    if isinstance(exception, dict):
        raw_values = exception.get("values")
        if isinstance(raw_values, list):
            values = raw_values
    elif isinstance(exception, list):
        values = exception

    for entry in reversed(values):
        if isinstance(entry, dict) and entry.get("type"):
            return entry.get("type"), entry.get("value"), _culprit(entry)

    return None, None, payload.get("culprit")


def _culprit(entry: dict[str, Any]) -> Any:
    """`module.function` of the deepest in-app frame — where an engineer should start reading."""
    stacktrace = entry.get("stacktrace")
    if not isinstance(stacktrace, dict):
        return None

    frames = stacktrace.get("frames")
    if not isinstance(frames, list):
        return None

    in_app = [f for f in frames if isinstance(f, dict) and f.get("in_app")]
    candidates = in_app or [f for f in frames if isinstance(f, dict)]
    if not candidates:
        return None

    deepest = candidates[-1]
    module = deepest.get("module") or deepest.get("filename") or ""
    function = deepest.get("function") or ""
    return f"{module} in {function}".strip() if module or function else None
