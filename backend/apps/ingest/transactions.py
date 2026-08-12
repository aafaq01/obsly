"""Turn a transaction payload into unsaved Transaction and Span rows.

Same posture as event normalisation: the payload is hostile, nothing raises, everything
degrades. A span with a broken timestamp costs that span, not the transaction it sits in.
"""

import math
import uuid
from datetime import datetime, timedelta
from typing import Any

from apps.ingest.normalize import MAX_CLOCK_SKEW, _timestamp
from apps.projects.models import Project
from apps.tracing.models import Span, SpanStatus, Transaction

MAX_SPANS = 1000

# A transaction longer than this is a clock problem or a stuck process, not a slow request.
# Left in, it would drag every percentile it lands in and hide the real distribution.
MAX_DURATION = timedelta(hours=1)


def transaction_from_payload(
    project: Project,
    payload: dict[str, Any],
    *,
    now: datetime,
    envelope_event_id: Any = None,
) -> tuple[Transaction, list[Span]] | None:
    """Return the transaction and its spans, or None if the payload cannot be a measurement."""
    trace = payload.get("contexts", {})
    trace = trace.get("trace", {}) if isinstance(trace, dict) else {}
    if not isinstance(trace, dict):
        trace = {}

    name = _text(payload.get("transaction"), 255)
    if not name:
        # An unnamed transaction cannot be aggregated with anything, so it would only ever be
        # a row nobody can group, filter or chart.
        return None

    start = _timestamp(payload.get("start_timestamp"), now=now)
    end = _timestamp(payload.get("timestamp"), now=now)
    duration = _duration_ms(start, end)
    if duration is None:
        return None

    transaction = Transaction(
        id=_uuid(payload.get("event_id") or envelope_event_id),
        project=project,
        trace_id=_hex(trace.get("trace_id"), 32) or uuid.uuid4().hex,
        span_id=_hex(trace.get("span_id"), 16),
        parent_span_id=_hex(trace.get("parent_span_id"), 16),
        name=name,
        op=_text(trace.get("op") or payload.get("op"), 64) or "http.server",
        status=_status(trace.get("status")),
        start_timestamp=start,
        timestamp=end,
        duration_ms=duration,
        environment=_text(payload.get("environment"), 64),
        release=_text(payload.get("release"), 128),
        measurements=_measurements(payload.get("measurements")),
        payload=payload,
    )

    return transaction, _spans(transaction, payload.get("spans"), now=now)


MAX_MEASUREMENTS = 30

# Vitals are milliseconds except CLS, which is a unitless ratio. Storing the unit alongside the
# value is what stops a chart from labelling a 0.08 layout shift as "0.08ms".
DEFAULT_UNIT = "millisecond"


def _measurements(raw: Any) -> dict[str, dict[str, Any]]:
    """Web vitals and friends, keyed by name.

    Same posture as everything else on this path: a browser sends what it likes, and one
    unusable entry costs that entry rather than the transaction carrying it.
    """
    if not isinstance(raw, dict):
        return {}

    kept: dict[str, dict[str, Any]] = {}
    for name, entry in list(raw.items())[:MAX_MEASUREMENTS]:
        if not isinstance(entry, dict):
            continue
        value = entry.get("value")
        if not isinstance(value, int | float) or isinstance(value, bool):
            continue
        # NaN and infinity survive JSON parsing and poison every aggregate they reach.
        if not math.isfinite(value) or value < 0:
            continue
        kept[_text(name, 32)] = {
            "value": float(value),
            "unit": _text(entry.get("unit"), 24) or DEFAULT_UNIT,
        }
    return kept


def _spans(transaction: Transaction, raw: Any, *, now: datetime) -> list[Span]:
    if not isinstance(raw, list):
        return []

    spans: list[Span] = []
    for entry in raw[:MAX_SPANS]:
        if not isinstance(entry, dict):
            continue

        start = _timestamp(entry.get("start_timestamp"), now=now)
        end = _timestamp(entry.get("timestamp"), now=now)
        duration = _duration_ms(start, end)
        if duration is None:
            continue

        spans.append(
            Span(
                transaction=transaction,
                trace_id=transaction.trace_id,
                span_id=_hex(entry.get("span_id"), 16),
                parent_span_id=_hex(entry.get("parent_span_id"), 16),
                op=_text(entry.get("op"), 64) or "unknown",
                description=str(entry.get("description") or "")[:2000],
                status=_status(entry.get("status")),
                start_timestamp=start,
                timestamp=end,
                duration_ms=duration,
                data=entry.get("data") if isinstance(entry.get("data"), dict) else {},
            )
        )
    return spans


def _duration_ms(start: datetime, end: datetime) -> float | None:
    """None when the span cannot be a measurement.

    A negative duration means the clock moved backwards mid-request; storing it would produce
    a p95 lower than a p50 and make the whole chart untrustworthy.
    """
    delta = end - start
    if delta < timedelta(0) or delta > MAX_DURATION:
        return None
    return delta.total_seconds() * 1000


def _uuid(raw: Any) -> uuid.UUID:
    if isinstance(raw, str):
        try:
            return uuid.UUID(raw)
        except ValueError:
            pass
    return uuid.uuid4()


def _hex(raw: Any, length: int) -> str:
    if not isinstance(raw, str):
        return ""
    value = raw.strip().lower()
    if len(value) != length or not all(c in "0123456789abcdef" for c in value):
        return ""
    return value


def _text(raw: Any, limit: int) -> str:
    if raw is None or isinstance(raw, dict | list):
        return ""
    return str(raw)[:limit]


def _status(raw: Any) -> str:
    return raw if isinstance(raw, str) and raw in SpanStatus.values else SpanStatus.UNKNOWN


__all__ = ["MAX_CLOCK_SKEW", "MAX_SPANS", "transaction_from_payload"]
