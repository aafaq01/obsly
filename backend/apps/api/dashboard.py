"""The project overview.

One request, because a dashboard that fires eight parallel calls renders in eight stages and
every stage is a layout shift.

Every series is bucketed on the same grid and zero-filled to the same length, so the charts on
the page line up with each other. Charts that disagree about what "three hours ago" means are
worse than no charts: they invite conclusions the data does not support.
"""

from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any

from django.db.models import Avg, Count, Q, QuerySet
from django.db.models.functions import TruncHour

from apps.api.performance import Percentile, window
from apps.events.models import Event
from apps.issues.models import Issue, IssueStatus
from apps.logs.models import LogRecord
from apps.tracing.models import SpanStatus, Transaction


def overview(project_id: int, period: str) -> dict[str, Any]:
    since, minutes = window(period)
    buckets = _buckets(since)

    transactions = Transaction.objects.filter(project_id=project_id, timestamp__gte=since)
    events = Event.objects.filter(project_id=project_id, timestamp__gte=since)
    logs = LogRecord.objects.filter(project_id=project_id, timestamp__gte=since)

    totals = transactions.aggregate(
        count=Count("id"),
        failures=Count("id", filter=Q(status=SpanStatus.INTERNAL_ERROR)),
        p95=Percentile("duration_ms", 0.95),
        avg=Avg("duration_ms"),
    )
    total = totals["count"] or 0

    return {
        "period": period,
        "buckets": len(buckets),
        "headline": {
            "transactions": total,
            "throughput_per_minute": round(total / minutes, 3) if minutes else 0,
            "failure_rate": round((totals["failures"] or 0) / total, 4) if total else 0.0,
            "p95_ms": round(totals["p95"] or 0, 1),
            "errors": events.count(),
            "unresolved_issues": Issue.objects.filter(
                project_id=project_id, status=IssueStatus.UNRESOLVED
            ).count(),
            "logs": logs.count(),
        },
        "series": {
            "throughput": _series(transactions, buckets),
            "failures": _series(transactions.filter(status=SpanStatus.INTERNAL_ERROR), buckets),
            "errors": _series(events, buckets),
            "logs": _series(logs, buckets),
            # p95 per bucket rather than a mean of means. Averaging percentiles is not a
            # percentile of anything, and the resulting line is a number nobody measured.
            "p95": _percentile_series(transactions, buckets),
        },
        "top_issues": _top_issues(project_id),
        "slowest_endpoints": _slowest_endpoints(transactions),
    }


def _buckets(since: datetime) -> list[datetime]:
    start = since.replace(minute=0, second=0, microsecond=0)
    count = int((datetime.now(tz=UTC) - start).total_seconds() // 3600) + 1
    return [start + timedelta(hours=offset) for offset in range(max(count, 1))]


def _series(queryset: QuerySet[Any], buckets: Iterable[datetime]) -> list[int]:
    rows = queryset.annotate(hour=TruncHour("timestamp")).values("hour").annotate(n=Count("id"))
    counts = {row["hour"]: row["n"] for row in rows}
    return [counts.get(bucket, 0) for bucket in buckets]


def _percentile_series(queryset: QuerySet[Transaction], buckets: Iterable[datetime]) -> list[float]:
    rows = (
        queryset.annotate(hour=TruncHour("timestamp"))
        .values("hour")
        .annotate(p95=Percentile("duration_ms", 0.95))
    )
    values = {row["hour"]: round(row["p95"] or 0, 1) for row in rows}
    return [values.get(bucket, 0.0) for bucket in buckets]


def _top_issues(project_id: int, limit: int = 5) -> list[dict[str, Any]]:
    """Unresolved only. A resolved issue on the overview is a distraction from the open ones."""
    return [
        {
            "id": issue.pk,
            "title": issue.title,
            "culprit": issue.culprit,
            "level": issue.level,
            "times_seen": issue.times_seen,
            "last_seen": issue.last_seen,
        }
        for issue in Issue.objects.filter(
            project_id=project_id, status=IssueStatus.UNRESOLVED
        ).order_by("-times_seen")[:limit]
    ]


def _slowest_endpoints(transactions: QuerySet[Transaction], limit: int = 5) -> list[dict[str, Any]]:
    rows = (
        transactions.values("name")
        .annotate(count=Count("id"), p95=Percentile("duration_ms", 0.95))
        .order_by("-p95")[:limit]
    )
    return [
        {"name": row["name"], "count": row["count"], "p95": round(row["p95"] or 0, 1)}
        for row in rows
    ]
