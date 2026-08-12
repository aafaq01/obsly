"""Which flag is implicated in an issue.

Recording the flags on an event is the easy half and on its own it is a list nobody reads. The
question people actually have after a deploy is *"is this only happening to people with X
on?"*, and answering it needs a comparison: how often the flag was on inside this issue,
against how often it was on across everything else the project served.

A flag that is on for 100% of the failures and 4% of the traffic is the suspect. A flag that is
on for 100% of both is just a flag that is on.
"""

from typing import Any

from django.db.models import Count, Q

from apps.events.models import Event
from apps.issues.models import Issue

# Below this many events the comparison is noise: three failures that all happen to have a flag
# on is not evidence, and presenting it as a suspect sends people to revert the wrong thing.
MIN_EVENTS = 5


def suspects(issue: Issue, *, window_events: int = 5000) -> list[dict[str, Any]]:
    """Flags ranked by how much more often they are on inside this issue than outside it."""
    inside = list(
        Event.objects.filter(issue=issue).exclude(flags={}).values_list("flags", flat=True)[:1000]
    )
    if len(inside) < MIN_EVENTS:
        return []

    # The comparison population: everything else this project served recently. Without it the
    # only available statement is "the flag was on", which is true of most flags most of the
    # time.
    outside = list(
        Event.objects.filter(project_id=issue.project_id)
        .exclude(issue=issue)
        .exclude(flags={})
        .order_by("-timestamp")
        .values_list("flags", flat=True)[:window_events]
    )

    names = {name for entry in inside for name in entry}
    ranked = []

    for name in names:
        on_inside = sum(1 for entry in inside if entry.get(name))
        seen_outside = [entry for entry in outside if name in entry]
        on_outside = sum(1 for entry in seen_outside if entry.get(name))

        inside_rate = on_inside / len(inside)
        # No baseline is not a zero baseline. A flag nobody else reported cannot be compared,
        # and scoring it against an assumed zero would rank the least-known flag highest.
        outside_rate = (on_outside / len(seen_outside)) if seen_outside else None

        ranked.append(
            {
                "flag": name,
                "on_in_issue": on_inside,
                "issue_events": len(inside),
                "issue_rate": round(inside_rate, 4),
                "baseline_rate": round(outside_rate, 4) if outside_rate is not None else None,
                "baseline_events": len(seen_outside),
                # How much more often it is on here than elsewhere. None where there is nothing
                # to compare against, which the UI states rather than hides.
                "lift": round(inside_rate - outside_rate, 4) if outside_rate is not None else None,
            }
        )

    # Biggest gap first; flags with no baseline sort last rather than being dropped, because
    # "we cannot tell yet" is a different answer from "not implicated".
    return sorted(ranked, key=lambda row: (row["lift"] is None, -(row["lift"] or 0)))


def project_summary(project_id: int, since: Any) -> list[dict[str, Any]]:
    """Every flag seen recently, and how often it was on.

    The reference the suspect ranking is read against, and the answer to "what flags is this
    project even reporting" — which is the first thing anyone asks after wiring the SDK up.
    """
    rows = (
        Event.objects.filter(project_id=project_id, timestamp__gte=since)
        .exclude(flags={})
        .values_list("flags", flat=True)[:20000]
    )

    counts: dict[str, dict[str, int]] = {}
    for entry in rows:
        for name, value in entry.items():
            seen = counts.setdefault(name, {"seen": 0, "on": 0})
            seen["seen"] += 1
            seen["on"] += 1 if value else 0

    return sorted(
        (
            {
                "flag": name,
                "seen": data["seen"],
                "on": data["on"],
                "rate": round(data["on"] / data["seen"], 4) if data["seen"] else 0.0,
            }
            for name, data in counts.items()
        ),
        key=lambda row: -row["seen"],
    )


def issues_touching(project_id: int, flag: str, since: Any) -> list[dict[str, Any]]:
    """Issues whose events had this flag on — the other direction of the same question.

    "What did this flag break?" is asked while rolling one out, and it is not answerable from a
    per-issue view without opening every issue in turn.
    """
    rows = (
        Issue.objects.filter(
            project_id=project_id, events__timestamp__gte=since, events__flags__has_key=flag
        )
        .annotate(
            with_flag=Count("events", filter=Q(**{f"events__flags__{flag}": True}), distinct=True)
        )
        .filter(with_flag__gt=0)
        .order_by("-with_flag")[:20]
    )

    return [
        {
            "id": issue.pk,
            "title": issue.title,
            "level": issue.level,
            "status": issue.status,
            "events_with_flag": issue.with_flag,  # type: ignore[attr-defined]
        }
        for issue in rows
    ]
