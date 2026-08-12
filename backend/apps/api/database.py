"""The database tier.

The Spans page already ranked queries by total time. This answers the questions that ranking
provokes and cannot itself answer:

- **Which query is slow, as opposed to expensive?** Ranking by total time surfaces the query
  that runs constantly. Ranking by p95 surfaces the one that is individually painful. They are
  usually different queries with different fixes — an index versus a cache — so the page shows
  both rather than picking one and calling it "top queries".

- **Is it getting worse?** A single number cannot say. A p95 line over the window can.

- **Which table is the cost in?** A statement is the unit you fix; a table is the unit you
  reason about when deciding where an index goes.

- **What is being called once per row?** The N+1 signature is calls-per-request, and it is
  invisible in any ranking sorted by duration.
"""

import re
from typing import Any

from django.db.models import Avg, Count, F, Max, QuerySet, Sum

from apps.api.performance import Percentile
from apps.api.timewindow import Window, resolve
from apps.tracing.models import Span

TOP_N = 10

# Calls of the same statement inside one request, above which the shape is an N+1 rather than
# a loop somebody wrote deliberately. Five is low enough to catch a real one and high enough
# that a handful of lookups does not cry wolf.
N_PLUS_ONE_CALLS = 5

# FROM / JOIN / INTO / UPDATE, optionally schema-qualified, optionally quoted. Anything this
# does not recognise is left out rather than guessed at: a wrong table name sends somebody to
# index the wrong thing.
TABLE_PATTERN = re.compile(
    r'\b(?:from|join|into|update)\s+["`\[]?([a-z_][a-z0-9_]*(?:\.[a-z_][a-z0-9_]*)?)["`\]]?',
    re.IGNORECASE,
)


def summary(project_id: int, period: str) -> dict[str, Any]:
    win = resolve(period)
    queries = Span.objects.filter(
        transaction__project_id=project_id, timestamp__gte=win.since, op__startswith="db."
    )

    totals = queries.aggregate(
        count=Count("id"),
        requests=Count("transaction_id", distinct=True),
        total_ms=Sum("duration_ms"),
        p50=Percentile("duration_ms", 0.5),
        p95=Percentile("duration_ms", 0.95),
        p99=Percentile("duration_ms", 0.99),
        slowest=Max("duration_ms"),
    )

    return {
        "period": win.period,
        "bucket_seconds": win.bucket_seconds,
        "series_start": win.buckets[0],
        "headline": {
            "queries": totals["count"] or 0,
            "requests": totals["requests"] or 0,
            "per_request": round((totals["count"] or 0) / (totals["requests"] or 1), 1),
            "total_ms": round(totals["total_ms"] or 0, 1),
            "p50": round(totals["p50"] or 0, 1),
            "p95": round(totals["p95"] or 0, 1),
            "p99": round(totals["p99"] or 0, 1),
            "slowest": round(totals["slowest"] or 0, 1),
        },
        "series": {
            "throughput": _throughput(queries, win),
            "p95": _p95_series(queries, win),
        },
        # Two rankings, deliberately. The slowest query and the most expensive query are
        # usually different statements needing different fixes.
        "slowest": _statements(queries, order="p95"),
        "heaviest": _statements(queries, order="total_ms"),
        "tables": _tables(queries),
        "repeated": _repeated(queries),
    }


def _statements(queries: QuerySet[Span], *, order: str) -> list[dict[str, Any]]:
    rows = (
        queries.values("description", "op")
        .annotate(
            count=Count("id"),
            requests=Count("transaction_id", distinct=True),
            total_ms=Sum("duration_ms"),
            p50=Percentile("duration_ms", 0.5),
            p95=Percentile("duration_ms", 0.95),
            slowest=Max("duration_ms"),
        )
        .order_by(F(order).desc())[:TOP_N]
    )

    return [
        {
            "description": row["description"],
            "op": row["op"],
            "count": row["count"],
            "requests": row["requests"],
            "per_request": round(row["count"] / row["requests"], 1) if row["requests"] else 0,
            "total_ms": round(row["total_ms"] or 0, 1),
            "p50": round(row["p50"] or 0, 1),
            "p95": round(row["p95"] or 0, 1),
            "slowest": round(row["slowest"] or 0, 1),
            "table": table_of(row["description"]),
        }
        for row in rows
    ]


def _tables(queries: QuerySet[Span]) -> list[dict[str, Any]]:
    """Grouped in Python, because the table name is inside the statement text.

    Doing it in SQL would mean a regexp per row across the whole window; the statement set is
    small — it is distinct statements, not distinct queries — so grouping the aggregate is
    cheaper than grouping the raw rows.
    """
    rows = queries.values("description").annotate(
        count=Count("id"), total_ms=Sum("duration_ms"), slowest=Max("duration_ms")
    )

    tables: dict[str, dict[str, Any]] = {}
    for row in rows:
        name = table_of(row["description"])
        if not name:
            continue
        entry = tables.setdefault(
            name, {"table": name, "count": 0, "total_ms": 0.0, "statements": 0, "slowest": 0.0}
        )
        entry["count"] += row["count"]
        entry["total_ms"] += row["total_ms"] or 0
        entry["statements"] += 1
        entry["slowest"] = max(entry["slowest"], row["slowest"] or 0)

    ranked = sorted(tables.values(), key=lambda entry: -entry["total_ms"])[:TOP_N]
    for entry in ranked:
        entry["total_ms"] = round(entry["total_ms"], 1)
        entry["slowest"] = round(entry["slowest"], 1)
    return ranked


def _repeated(queries: QuerySet[Span]) -> list[dict[str, Any]]:
    """Statements running many times inside one request.

    Invisible in any ranking sorted by duration: each call is fast, which is exactly why the
    pattern survives review. The number that gives it away is calls per request.
    """
    rows = (
        queries.values("description", "op")
        .annotate(
            count=Count("id"),
            requests=Count("transaction_id", distinct=True),
            total_ms=Sum("duration_ms"),
            avg_ms=Avg("duration_ms"),
        )
        .filter(requests__gt=0)
    )

    found = []
    for row in rows:
        per_request = row["count"] / row["requests"]
        if per_request < N_PLUS_ONE_CALLS:
            continue
        found.append(
            {
                "description": row["description"],
                "op": row["op"],
                "per_request": round(per_request, 1),
                "count": row["count"],
                "requests": row["requests"],
                "total_ms": round(row["total_ms"] or 0, 1),
                # What collapsing the loop into one query would give back: every call after
                # the first, at the average cost of a call.
                "wasted_ms": round((row["avg_ms"] or 0) * (row["count"] - row["requests"]), 1),
                "table": table_of(row["description"]),
            }
        )

    return sorted(found, key=lambda entry: -entry["wasted_ms"])[:TOP_N]


def table_of(statement: str) -> str:
    match = TABLE_PATTERN.search(statement or "")
    return match.group(1).lower() if match else ""


def _throughput(queries: QuerySet[Span], win: Window) -> list[int]:
    rows = queries.annotate(bucket=win.truncate()).values("bucket").annotate(n=Count("id"))
    return win.zero_filled({row["bucket"]: row["n"] for row in rows})


def _p95_series(queries: QuerySet[Span], win: Window) -> list[float]:
    """p95 per bucket, at the bucket's own granularity — an average of percentiles is not a
    percentile of anything."""
    rows = (
        queries.annotate(bucket=win.truncate())
        .values("bucket")
        .annotate(p95=Percentile("duration_ms", 0.95))
    )
    values = {row["bucket"]: round(row["p95"] or 0, 1) for row in rows}
    return [values.get(bucket, 0.0) for bucket in win.buckets]
