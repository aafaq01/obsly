"""Latency aggregates.

Percentiles, not averages. An average hides the tail entirely: an endpoint answering 99% of
requests in 20ms and 1% in 8 seconds averages out to something that looks healthy, while the
p99 says plainly that one request in a hundred is unusable.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

from django.db.models import Aggregate, Count, F, FloatField, Q, Sum

from apps.tracing.models import SpanStatus, Transaction

PERIODS = {"1h": 1, "24h": 24, "7d": 24 * 7, "30d": 24 * 30}


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
    """Return (since, minutes). Unknown periods fall back to 24h rather than erroring."""
    hours = PERIODS.get(period, 24)
    return datetime.now(tz=UTC) - timedelta(hours=hours), hours * 60


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
