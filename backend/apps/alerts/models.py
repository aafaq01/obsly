"""Alert rules and what they fired.

An observability tool that only answers questions when you open it is a tool nobody opens at
3am. Everything else in Obsly is pull; this is the one push.

Two objects: the rule you configure, and the row written every time it fires. The fire is
recorded before delivery is attempted, so an alert that nobody received is still visible —
"the webhook was down" and "nothing happened" must not look the same.
"""

from django.db import models

from apps.events.models import Level
from apps.issues.models import Issue
from apps.projects.models import Project, TimestampedModel


class Trigger(models.TextChoices):
    """What a rule watches for.

    Deliberately three. Every one answers a question somebody actually asks at 3am; a rule
    language nobody can predict the behaviour of is worse than no rule at all.
    """

    NEW_ISSUE = "new_issue", "A new issue appears"
    REGRESSION = "regression", "A resolved issue happens again"
    FREQUENCY = "frequency", "An issue crosses a rate"


class AlertRule(TimestampedModel):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="alert_rules")
    name = models.CharField(max_length=200)
    trigger = models.CharField(max_length=16, choices=Trigger, default=Trigger.NEW_ISSUE)

    # FREQUENCY only: N events inside M minutes.
    threshold = models.PositiveIntegerField(default=10)
    window_minutes = models.PositiveIntegerField(default=5)

    # Blank means any level. A team that only wants paging for errors sets this to "error" and
    # stops being woken by warnings.
    level = models.CharField(max_length=16, choices=Level, blank=True, default="")

    # One delivery mechanism, because Slack, Discord, Teams, PagerDuty and Opsgenie all accept
    # an incoming webhook. Five bespoke integrations would carry five credential stores and
    # five ways to break.
    webhook_url = models.URLField(max_length=500)

    # The difference between an alert and a pager loop. Without it, an issue seen a thousand
    # times an hour sends a thousand notifications and the channel gets muted — which is the
    # same as having no alerting, only with more noise.
    cooldown_minutes = models.PositiveIntegerField(default=30)

    enabled = models.BooleanField(default=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [models.Index(fields=["project", "enabled"])]

    def __str__(self) -> str:
        return f"{self.name} ({self.get_trigger_display()})"


class Delivery(models.TextChoices):
    PENDING = "pending", "Pending"
    SENT = "sent", "Sent"
    FAILED = "failed", "Failed"


class AlertFire(TimestampedModel):
    """One firing of one rule.

    Kept even when delivery fails, because the record is the audit trail: it answers "were we
    told?" independently of whether the webhook was reachable.
    """

    rule = models.ForeignKey(AlertRule, on_delete=models.CASCADE, related_name="fires")
    issue = models.ForeignKey(Issue, on_delete=models.CASCADE, related_name="alert_fires")

    # Why it fired, in the words shown in the UI and sent in the payload. Stored rather than
    # rebuilt from the rule, because a rule edited next week must not rewrite last week's
    # history.
    reason = models.CharField(max_length=300)

    delivery = models.CharField(max_length=16, choices=Delivery, default=Delivery.PENDING)
    status_code = models.PositiveIntegerField(null=True, blank=True)
    error = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["rule", "issue", "-created_at"]),
            models.Index(fields=["-created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.rule.name} → {self.issue_id}"
