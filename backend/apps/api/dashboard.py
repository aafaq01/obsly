"""The project overview.

One request, because a dashboard that fires eight parallel calls renders in eight stages and
every stage is a layout shift.

Every series is bucketed on the same grid and zero-filled to the same length, so the charts on
the page line up with each other. Charts that disagree about what "three hours ago" means are
worse than no charts: they invite conclusions the data does not support.
"""

from typing import Any

from django.db.models import Avg, Count, Q, QuerySet

from apps.api.performance import Percentile
from apps.api.timewindow import PERIODS, Window, resolve
from apps.events.models import Event
from apps.issues.models import Issue, IssueStatus
from apps.logs.models import LogRecord
from apps.tracing.models import SpanStatus, Transaction


def overview(project_id: int, period: str) -> dict[str, Any]:
    win = resolve(period)
    since, minutes = win.since, win.minutes

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
        "period": win.period,
        "periods": list(PERIODS),
        "buckets": len(win.buckets),
        "bucket_seconds": win.bucket_seconds,
        # When the first bucket starts. With bucket_seconds the client can name every
        # point on the axis in clock time, instead of only saying how long ago it was.
        "series_start": win.buckets[0],
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
            "throughput": _series(transactions, win),
            "failures": _series(transactions.filter(status=SpanStatus.INTERNAL_ERROR), win),
            "errors": _series(events, win),
            "logs": _series(logs, win),
            # p95 per bucket rather than a mean of means. Averaging percentiles is not a
            # percentile of anything, and the resulting line is a number nobody measured.
            "p95": _percentile_series(transactions, win),
        },
        "top_issues": _top_issues(project_id),
        "slowest_endpoints": _slowest_endpoints(transactions),
    }


def _series(queryset: QuerySet[Any], win: Window) -> list[int]:
    rows = queryset.annotate(bucket=win.truncate()).values("bucket").annotate(n=Count("id"))
    return win.zero_filled({row["bucket"]: row["n"] for row in rows})


def _percentile_series(queryset: QuerySet[Transaction], win: Window) -> list[float]:
    """p95 per bucket, computed by the database at the bucket's own granularity.

    Computing it coarsely and re-splitting in Python would average percentiles, and an average
    of percentiles is not a percentile of anything.
    """
    rows = (
        queryset.annotate(bucket=win.truncate())
        .values("bucket")
        .annotate(p95=Percentile("duration_ms", 0.95))
    )
    values = {row["bucket"]: round(row["p95"] or 0, 1) for row in rows}
    return [values.get(bucket, 0.0) for bucket in win.buckets]


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
