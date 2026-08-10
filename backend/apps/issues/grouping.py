"""Attach events to issues."""

from django.db import transaction
from django.db.models import F
from django.db.models.functions import Greatest, Least
from django.utils import timezone

from apps.events.models import Event
from apps.issues.fingerprint import compute
from apps.issues.models import Issue, IssueStatus


def assign_issues(events: list[Event]) -> None:
    """Group `events` in place, setting `event.issue` and updating issue counters.

    Called after the events are stored, so a failure here costs grouping, never the event.
    """
    if not events:
        return

    for event in events:
        fingerprint, components = compute(event.payload)
        event.issue = _upsert(event, fingerprint, components)

    Event.objects.bulk_update(events, ["issue"])


@transaction.atomic
def _upsert(event: Event, fingerprint: str, components: list[str]) -> Issue:
    issue, created = Issue.objects.get_or_create(
        project=event.project,
        fingerprint=fingerprint,
        defaults={
            "fingerprint_components": components,
            "title": event.title[:512],
            "culprit": event.culprit,
            "level": event.level,
            "platform": event.platform,
            "times_seen": 1,
            "first_seen": event.timestamp,
            "last_seen": event.timestamp,
        },
    )
    if created:
        return issue

    # F() rather than read-modify-write: two workers ingesting the same issue concurrently
    # would otherwise each read the same count and write the same increment, losing one.
    # Greatest/Least, not max()/min() in Python: an event that arrives late after offline
    # buffering must widen first_seen without dragging last_seen backwards.
    Issue.objects.filter(pk=issue.pk).update(
        times_seen=F("times_seen") + 1,
        last_seen=Greatest("last_seen", event.timestamp),
        first_seen=Least("first_seen", event.timestamp),
        updated_at=timezone.now(),
    )

    # A resolved issue that happens again is a regression, and staying resolved would hide it.
    if issue.status == IssueStatus.RESOLVED:
        Issue.objects.filter(pk=issue.pk, status=IssueStatus.RESOLVED).update(
            status=IssueStatus.UNRESOLVED
        )

    issue.refresh_from_db()
    return issue
