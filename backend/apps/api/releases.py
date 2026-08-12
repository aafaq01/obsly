"""Release health.

`release` is already on every event, transaction and log. What was missing is the question they
exist to answer: *did the thing we shipped make it worse?*

Two deliberate limits:

**No sessions, so no "crash-free users".** Sentry's crash-free rate is computed over sessions,
which need their own protocol and their own storage. Reporting a request-based number under
that name would be worse than not having it — people compare crash-free rates between tools and
would be comparing two different measurements. What is here is failure-free *requests*, named
as such.

**Adoption is share of traffic in the window, not installed base.** Without sessions there is no
honest way to say how many users are on a version; how much of the traffic it served is a fact
we do have.
"""

from typing import Any

from django.db.models import Count, F, Max, Min, Q

from apps.api.performance import Percentile
from apps.api.timewindow import resolve
from apps.events.models import Event
from apps.issues.models import Issue, IssueStatus
from apps.tracing.models import SpanStatus, Transaction

MAX_RELEASES = 25


def summary(project_id: int, period: str) -> dict[str, Any]:
    win = resolve(period)

    transactions = Transaction.objects.filter(
        project_id=project_id, timestamp__gte=win.since
    ).exclude(release="")
    events = Event.objects.filter(project_id=project_id, timestamp__gte=win.since).exclude(
        release=""
    )

    # Materialised once: the versions drive three more grouped queries below, and leaving it
    # lazy would re-run the aggregate for each of them.
    rows = list(
        transactions.values("release")
        .annotate(
            requests=Count("id"),
            failures=Count("id", filter=~Q(status=SpanStatus.OK)),
            p95=Percentile("duration_ms", 0.95),
            first_seen=Min("timestamp"),
            last_seen=Max("timestamp"),
        )
        .order_by(F("last_seen").desc())[:MAX_RELEASES]
    )

    versions = [row["release"] for row in rows]
    errors = _count_by_release(events, versions)
    # Issues whose *first* sighting was this release. The number that answers "what did we
    # break" rather than "what is still broken", which is a different question with a
    # different answer.
    introduced = _count_by_release(
        Issue.objects.filter(project_id=project_id), versions, field="first_release"
    )
    unresolved = _count_by_release(
        Issue.objects.filter(project_id=project_id, status=IssueStatus.UNRESOLVED),
        versions,
        field="first_release",
    )

    total_requests = sum(row["requests"] for row in rows) or 1

    return {
        "period": win.period,
        "releases": [
            {
                "version": row["release"],
                "requests": row["requests"],
                # Named for what it measures. It is not a crash-free session rate, and calling
                # it one would invite a comparison against other tools that is not valid.
                "failure_free_rate": round(1 - (row["failures"] / row["requests"]), 4)
                if row["requests"]
                else 0.0,
                "failures": row["failures"],
                "p95": round(row["p95"] or 0, 1),
                "errors": errors.get(row["release"], 0),
                "issues_introduced": introduced.get(row["release"], 0),
                "issues_unresolved": unresolved.get(row["release"], 0),
                # Share of traffic in this window, not installed base — see the module note.
                "adoption": round(row["requests"] / total_requests, 4),
                "first_seen": row["first_seen"],
                "last_seen": row["last_seen"],
            }
            for row in rows
        ],
    }


def _count_by_release(queryset: Any, versions: list[str], field: str = "release") -> dict[str, int]:
    """One grouped query rather than one query per release.

    The naive shape here is a count inside the row loop, which is the N+1 this project files
    issues about.
    """
    if not versions:
        return {}
    rows = queryset.filter(**{f"{field}__in": versions}).values(field).annotate(total=Count("id"))
    return {row[field]: row["total"] for row in rows}
