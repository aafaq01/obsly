"""Deciding whether an issue should wake somebody up.

Called from the grouping path, which already knows the two facts that matter — whether the
issue is new and whether it just came back — so neither has to be re-derived from timestamps.

Evaluation is deliberately cheap. It runs inside ingest, on every event, for every project: a
rule engine that costs a query per event per rule would make alerting the slowest thing in the
system, and the first thing switched off.
"""

from datetime import timedelta

from django.conf import settings
from django.db.models import QuerySet
from django.utils import timezone

from apps.alerts.models import AlertFire, AlertRule, Trigger
from apps.issues.models import Issue


def evaluate(issue: Issue, *, created: bool, regressed: bool) -> list[AlertFire]:
    """Fire every enabled rule this issue satisfies, and return the rows written.

    Never raises. An alerting bug must not cost an event — the whole point of storing the
    event first is that everything after it is best-effort.
    """
    if not getattr(settings, "OBSLY_ALERTS_ENABLED", True):
        return []

    try:
        rules = _candidates(issue, created=created, regressed=regressed)
        return [fire for rule in rules if (fire := _fire(rule, issue)) is not None]
    except Exception:  # see docstring: alerting must never cost an event
        return []


def _candidates(issue: Issue, *, created: bool, regressed: bool) -> QuerySet[AlertRule]:
    triggers: list[str] = []
    if created:
        triggers.append(Trigger.NEW_ISSUE)
    if regressed:
        triggers.append(Trigger.REGRESSION)
    # Frequency is checked on every event, not only the first: an issue crosses a rate long
    # after it was created, which is exactly the case the other two triggers miss.
    triggers.append(Trigger.FREQUENCY)

    rules = AlertRule.objects.filter(
        project_id=issue.project_id, enabled=True, trigger__in=triggers
    )
    # Empty level means any level. Filtering in Python would mean fetching rules that can
    # never match.
    return rules.filter(level__in=["", issue.level])


def _fire(rule: AlertRule, issue: Issue) -> AlertFire | None:
    reason = _reason(rule, issue)
    if reason is None:
        return None

    if _in_cooldown(rule, issue):
        return None

    fire = AlertFire.objects.create(rule=rule, issue=issue, reason=reason)
    # Imported here rather than at module scope: delivery imports this module's models, and
    # the cycle would break app loading.
    from apps.alerts.delivery import deliver

    deliver(fire)
    return fire


def _reason(rule: AlertRule, issue: Issue) -> str | None:
    """The sentence shown in the UI and sent in the payload, or None if the rule does not hold.

    Written here rather than in the template so the condition and its explanation cannot drift
    — a notification that says something the rule did not check is worse than none.
    """
    if rule.trigger == Trigger.NEW_ISSUE:
        return f"New {issue.level} issue in {issue.project.name}"

    if rule.trigger == Trigger.REGRESSION:
        return f"Resolved issue happened again in {issue.project.name}"

    since = timezone.now() - timedelta(minutes=rule.window_minutes)
    # Counting events rather than reading times_seen: the counter is lifetime, and an issue
    # seen a thousand times last year would fire a "50 in 5 minutes" rule on its first event
    # today.
    count = issue.events.filter(timestamp__gte=since).count()
    if count < rule.threshold:
        return None
    return f"{count} events in {rule.window_minutes} minutes (threshold {rule.threshold})"


def _in_cooldown(rule: AlertRule, issue: Issue) -> bool:
    if not rule.cooldown_minutes:
        return False
    since = timezone.now() - timedelta(minutes=rule.cooldown_minutes)
    # Per (rule, issue), not per rule: one noisy issue must not silence the alert for a
    # different issue that starts five minutes later.
    return AlertFire.objects.filter(rule=rule, issue=issue, created_at__gte=since).exists()
