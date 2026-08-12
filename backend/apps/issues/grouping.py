"""Attach events to issues, and file what the performance detectors find."""

from typing import Any

from django.db import transaction
from django.db.models import F
from django.db.models.functions import Greatest, Least
from django.utils import timezone

from apps.alerts.rules import evaluate
from apps.events.models import Event
from apps.issues import detectors
from apps.issues.fingerprint import compute
from apps.issues.models import Issue, IssueCategory, IssueStatus
from apps.tracing.models import Span, Transaction


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
            "first_release": event.release,
            "times_seen": 1,
            "first_seen": event.timestamp,
            "last_seen": event.timestamp,
        },
    )
    if created:
        evaluate(issue, created=True, regressed=False)
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
    regressed = issue.status == IssueStatus.RESOLVED
    if regressed:
        Issue.objects.filter(pk=issue.pk, status=IssueStatus.RESOLVED).update(
            status=IssueStatus.UNRESOLVED
        )

    issue.refresh_from_db()
    # After refresh, so a frequency rule counts the event that triggered this call.
    evaluate(issue, created=False, regressed=regressed)
    return issue


def detect_performance_issues(transactions: list[Transaction], spans: list[Span]) -> int:
    """Run the detectors over freshly stored transactions and file what they find.

    Returns the number of findings, so ingest can report it without re-querying.
    """
    if not transactions or not detectors.enabled():
        return 0

    by_transaction: dict[Any, list[Span]] = {}
    for span in spans:
        by_transaction.setdefault(span.transaction_id, []).append(span)

    filed = 0
    for txn in transactions:
        for finding in detectors.detect(txn, by_transaction.get(txn.pk, [])):
            _upsert_performance_issue(txn, finding)
            filed += 1
    return filed


@transaction.atomic
def _upsert_performance_issue(txn: Transaction, finding: "detectors.Finding") -> Issue:
    issue, created = Issue.objects.get_or_create(
        project_id=txn.project_id,
        fingerprint=finding.fingerprint,
        defaults={
            "category": IssueCategory.PERFORMANCE,
            "issue_type": finding.issue_type,
            "fingerprint_components": [finding.issue_type, txn.name],
            "title": finding.title[:512],
            "culprit": finding.culprit[:512],
            # Not "error": nothing failed. Filing a slow query at the same level as a crash is
            # how an issue stream stops being a priority list.
            "level": "warning",
            "platform": "",
            "first_release": txn.release,
            "evidence": finding.evidence,
            "times_seen": 1,
            "first_seen": txn.timestamp,
            "last_seen": txn.timestamp,
        },
    )
    if created:
        evaluate(issue, created=True, regressed=False)
        return issue

    Issue.objects.filter(pk=issue.pk).update(
        times_seen=F("times_seen") + 1,
        last_seen=Greatest("last_seen", txn.timestamp),
        first_seen=Least("first_seen", txn.timestamp),
        # The newest occurrence replaces the evidence, so the linked trace is one that still
        # exists rather than the first one ever seen.
        evidence=finding.evidence,
        updated_at=timezone.now(),
    )

    regressed = issue.status == IssueStatus.RESOLVED
    if regressed:
        Issue.objects.filter(pk=issue.pk, status=IssueStatus.RESOLVED).update(
            status=IssueStatus.UNRESOLVED
        )

    issue.refresh_from_db()
    evaluate(issue, created=False, regressed=regressed)
    return issue
