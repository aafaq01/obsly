"""Latency aggregates.

Percentiles, not averages. An average hides the tail entirely: an endpoint answering 99% of
requests in 20ms and 1% in 8 seconds averages out to something that looks healthy, while the
p99 says plainly that one request in a hundred is unusable.
"""

from datetime import datetime
from typing import Any

from django.db.models import Aggregate, Count, F, FloatField, Q, Sum

from apps.api.timewindow import resolve
from apps.tracing.models import Span, SpanStatus, Transaction


class Percentile(Aggregate):
    """Postgres PERCENTILE_CONT.

    Django ships no percentile aggregate, and computing one in Python would mean pulling every
    duration in the window into memory — which is exactly the scan the database exists to do.
    """

    function = "PERCENTILE_CONT"
    name = "percentile"
    template = "%(function)s(%(percentile)s) WITHIN GROUP (ORDER BY %(expressions)s)"
    output_field = FloatField()

    def __init__(self, expression: str, percentile: float, **extra: Any) -> None:
        super().__init__(expression, percentile=percentile, **extra)


def window(period: str) -> tuple[datetime, float]:
    """Kept as a two-tuple for the callers that only need the range."""
    resolved = resolve(period)
    return resolved.since, resolved.minutes


def endpoint_summary(project_id: int, period: str, *, limit: int = 100) -> list[dict[str, Any]]:
    since, minutes = window(period)

    rows = (
        Transaction.objects.filter(project_id=project_id, timestamp__gte=since)
        .values("name", "op")
        .annotate(
            count=Count("id"),
            failures=Count("id", filter=Q(status=SpanStatus.INTERNAL_ERROR)),
            total_ms=Sum("duration_ms"),
            p50=Percentile("duration_ms", 0.5),
            p75=Percentile("duration_ms", 0.75),
            p95=Percentile("duration_ms", 0.95),
            p99=Percentile("duration_ms", 0.99),
        )
        # Ranked by total time spent, not by p95. The slowest endpoint is often one nobody
        # calls; the one burning the most time is the one worth fixing first.
        .order_by(F("total_ms").desc())[:limit]
    )

    return [
        {
            "name": row["name"],
            "op": row["op"],
            "count": row["count"],
            "throughput_per_minute": round(row["count"] / minutes, 3),
            "failure_rate": round(row["failures"] / row["count"], 4) if row["count"] else 0.0,
            "total_ms": round(row["total_ms"] or 0, 1),
            "p50": round(row["p50"] or 0, 1),
            "p75": round(row["p75"] or 0, 1),
            "p95": round(row["p95"] or 0, 1),
            "p99": round(row["p99"] or 0, 1),
        }
        for row in rows
    ]


def span_summary(
    project_id: int, period: str, *, op: str = "", limit: int = 100
) -> list[dict[str, Any]]:
    """Aggregate spans by what they do, not by which request they were in.

    This is the view that answers "which query is costing me the most", which a waterfall
    cannot: a waterfall shows one request, and the query that matters is usually the one that
    is individually fast and runs ten thousand times.
    """
    since, minutes = window(period)

    rows = (
        Span.objects.filter(transaction__project_id=project_id, timestamp__gte=since)
        .values("op", "description")
        .annotate(
            count=Count("id"),
            transactions=Count("transaction_id", distinct=True),
            total_ms=Sum("duration_ms"),
            p50=Percentile("duration_ms", 0.5),
            p95=Percentile("duration_ms", 0.95),
        )
        .order_by(F("total_ms").desc())
    )
    if op:
        rows = rows.filter(op=op)

    return [
        {
            "op": row["op"],
            "description": row["description"],
            "count": row["count"],
            "transactions": row["transactions"],
            # Calls per containing transaction. Above ~5 for a db.query this is the signature
            # of an N+1: the same statement running once per row of some earlier result.
            "per_transaction": round(row["count"] / row["transactions"], 1)
            if row["transactions"]
            else 0,
            "throughput_per_minute": round(row["count"] / minutes, 3),
            "total_ms": round(row["total_ms"] or 0, 1),
            "p50": round(row["p50"] or 0, 1),
            "p95": round(row["p95"] or 0, 1),
        }
        for row in rows[:limit]
    ]


def span_ops(project_id: int, period: str) -> list[str]:
    """The op values actually present, so the filter offers only what exists."""
    since, _ = window(period)
    return sorted(
        Span.objects.filter(transaction__project_id=project_id, timestamp__gte=since)
        # order_by() clears Meta.ordering first. Without it Django adds start_timestamp to the
        # SELECT to satisfy the sort, DISTINCT then dedupes on (op, start_timestamp), and the
        # filter offers one entry per span rather than one per operation.
        .order_by()
        .values_list("op", flat=True)
        .distinct()
    )
