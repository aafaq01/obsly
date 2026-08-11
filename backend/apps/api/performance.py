"""Latency aggregates.

Percentiles, not averages. An average hides the tail entirely: an endpoint answering 99% of
requests in 20ms and 1% in 8 seconds averages out to something that looks healthy, while the
p99 says plainly that one request in a hundred is unusable.
"""

from datetime import datetime
from typing import Any

from django.db.models import Aggregate, Count, F, FloatField, Max, Q, QuerySet, Sum, Value
from django.db.models.functions import Floor, Least

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


def span_detail(
    project_id: int, period: str, *, op: str, description: str
) -> dict[str, Any] | None:
    """One span group: its distribution, who calls it, and traces to open.

    The aggregate says a query is expensive. This says which endpoints make it expensive and
    gives you a real trace to open — the step between "this is the problem" and "here is the
    request where it happened".
    """
    win = resolve(period)
    spans = Span.objects.filter(
        transaction__project_id=project_id,
        timestamp__gte=win.since,
        op=op,
        description=description,
    )

    totals = spans.aggregate(
        count=Count("id"),
        transactions=Count("transaction_id", distinct=True),
        total_ms=Sum("duration_ms"),
        p50=Percentile("duration_ms", 0.5),
        p95=Percentile("duration_ms", 0.95),
        p99=Percentile("duration_ms", 0.99),
        slowest=Max("duration_ms"),
    )
    if not totals["count"]:
        return None

    callers = (
        spans.values("transaction__name")
        .annotate(count=Count("id"), total_ms=Sum("duration_ms"))
        .order_by(F("total_ms").desc())[:10]
    )

    # Slowest first. A sample list sorted by time shows what happened last; sorted by duration
    # it shows the one worth opening.
    samples = (
        spans.select_related("transaction")
        .order_by("-duration_ms")[:10]
        .values(
            "duration_ms",
            "transaction__id",
            "transaction__name",
            "transaction__duration_ms",
            "transaction__timestamp",
            "transaction__trace_id",
        )
    )

    return {
        "op": op,
        "description": description,
        "period": win.period,
        "summary": {
            "count": totals["count"],
            "transactions": totals["transactions"],
            "per_transaction": round(totals["count"] / totals["transactions"], 1),
            "total_ms": round(totals["total_ms"] or 0, 1),
            "p50": round(totals["p50"] or 0, 1),
            "p95": round(totals["p95"] or 0, 1),
            "p99": round(totals["p99"] or 0, 1),
            "slowest": round(totals["slowest"] or 0, 1),
        },
        "distribution": _duration_histogram(spans, totals["slowest"] or 0),
        "callers": [
            {
                "transaction": row["transaction__name"],
                "count": row["count"],
                "total_ms": round(row["total_ms"] or 0, 1),
            }
            for row in callers
        ],
        "samples": [
            {
                "duration_ms": round(row["duration_ms"], 1),
                "trace_id": row["transaction__trace_id"],
                "transaction_id": str(row["transaction__id"]),
                "transaction": row["transaction__name"],
                "transaction_ms": round(row["transaction__duration_ms"], 1),
                "timestamp": row["transaction__timestamp"],
            }
            for row in samples
        ],
    }


MAX_SPANS_IN_DETAIL = 10


def endpoint_detail(
    project_id: int, period: str, *, name: str, op: str = ""
) -> dict[str, Any] | None:
    """One endpoint: how long it takes, where that time goes, and requests to open.

    The table ranks endpoints against each other. This answers the question that always comes
    next — "and what is it doing" — by attributing the endpoint's time to the operations
    inside it.
    """
    win = resolve(period)
    transactions = Transaction.objects.filter(
        project_id=project_id, timestamp__gte=win.since, name=name
    )
    # The summary table groups by (name, op), so two rows can share a name. Filtering on the
    # name alone would merge them into figures matching neither row you clicked.
    if op:
        transactions = transactions.filter(op=op)

    totals = transactions.aggregate(
        count=Count("id"),
        failures=Count("id", filter=~Q(status=SpanStatus.OK)),
        total_ms=Sum("duration_ms"),
        p50=Percentile("duration_ms", 0.5),
        p95=Percentile("duration_ms", 0.95),
        p99=Percentile("duration_ms", 0.99),
        slowest=Max("duration_ms"),
    )
    if not totals["count"]:
        return None

    spans = (
        Span.objects.filter(transaction__in=transactions)
        .values("op", "description")
        .annotate(
            count=Count("id"),
            total_ms=Sum("duration_ms"),
            p95=Percentile("duration_ms", 0.95),
        )
        .order_by(F("total_ms").desc())[:MAX_SPANS_IN_DETAIL]
    )

    # Slowest first: sorted by time this shows what happened last; sorted by duration it shows
    # the request worth opening.
    samples = transactions.order_by("-duration_ms")[:10].values(
        "id", "trace_id", "duration_ms", "timestamp", "status"
    )

    minutes = max(win.minutes, 1)

    return {
        "name": name,
        "op": op,
        "period": win.period,
        "summary": {
            "count": totals["count"],
            "throughput_per_minute": round(totals["count"] / minutes, 2),
            "failure_rate": round(totals["failures"] / totals["count"], 4),
            "total_ms": round(totals["total_ms"] or 0, 1),
            "p50": round(totals["p50"] or 0, 1),
            "p95": round(totals["p95"] or 0, 1),
            "p99": round(totals["p99"] or 0, 1),
            "slowest": round(totals["slowest"] or 0, 1),
        },
        "distribution": _duration_histogram(transactions, totals["slowest"] or 0),
        "spans": [
            {
                "op": row["op"],
                "description": row["description"],
                "count": row["count"],
                "total_ms": round(row["total_ms"] or 0, 1),
                "p95": round(row["p95"] or 0, 1),
                # The number that says whether fixing this span would move the endpoint at all:
                # a 2ms query at 40% of the time matters more than a 200ms one at 3%.
                "share": round((row["total_ms"] or 0) / (totals["total_ms"] or 1), 4),
            }
            for row in spans
        ],
        "samples": [
            {
                "transaction_id": str(row["id"]),
                "trace_id": row["trace_id"],
                "duration_ms": round(row["duration_ms"], 1),
                "timestamp": row["timestamp"],
                "status": row["status"],
            }
            for row in samples
        ],
    }


DISTRIBUTION_BUCKETS = 20


def _duration_histogram(rows: QuerySet[Any], slowest: float) -> list[dict[str, Any]]:
    """How the durations are actually spread.

    A p50 and a p95 describe two points. The shape between them is what says whether this is
    one slow tail or two different behaviours wearing the same statement — and those need
    different fixes.
    """
    if slowest <= 0:
        return []

    width = slowest / DISTRIBUTION_BUCKETS
    # Bucketed in SQL: pulling every duration into Python to histogram it is the scan the
    # database exists to do.
    bucketed = (
        rows.annotate(
            bucket=Least(
                Floor(F("duration_ms") / Value(width)),
                Value(float(DISTRIBUTION_BUCKETS - 1)),
            )
        )
        .values("bucket")
        .annotate(count=Count("id"))
    )
    counts = {int(row["bucket"]): row["count"] for row in bucketed if row["bucket"] is not None}

    return [
        {
            "from_ms": round(index * width, 1),
            "to_ms": round((index + 1) * width, 1),
            "count": counts.get(index, 0),
        }
        for index in range(DISTRIBUTION_BUCKETS)
    ]
