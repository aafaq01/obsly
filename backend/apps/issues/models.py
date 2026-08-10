"""Issues — the unit of work.

An Issue is a group of events an engineer would fix with one change. It carries the workflow
(status, assignment) and the counters, so triage happens against thirty rows rather than four
million.
"""

from django.db import models

from apps.events.models import Level
from apps.projects.models import Project, TimestampedModel


class IssueStatus(models.TextChoices):
    UNRESOLVED = "unresolved", "Unresolved"
    RESOLVED = "resolved", "Resolved"
    IGNORED = "ignored", "Ignored"


class IssueQuerySet(models.QuerySet["Issue"]):
    def unresolved(self) -> "IssueQuerySet":
        return self.filter(status=IssueStatus.UNRESOLVED)

    def for_project(self, project_id: int) -> "IssueQuerySet":
        return self.filter(project_id=project_id)


class Issue(TimestampedModel):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="issues")

    # sha256 of the grouping components. Stored rather than recomputed so that changing the
    # algorithm later cannot silently re-group an organisation's entire history.
    fingerprint = models.CharField(max_length=64, db_index=True)
    fingerprint_components = models.JSONField(default=list)

    title = models.CharField(max_length=512)
    culprit = models.CharField(max_length=512, blank=True)
    level = models.CharField(max_length=16, choices=Level, default=Level.ERROR, db_index=True)
    platform = models.CharField(max_length=32, blank=True)

    status = models.CharField(
        max_length=16, choices=IssueStatus, default=IssueStatus.UNRESOLVED, db_index=True
    )

    times_seen = models.PositiveIntegerField(default=0)
    first_seen = models.DateTimeField(db_index=True)
    last_seen = models.DateTimeField(db_index=True)

    objects = IssueQuerySet.as_manager()

    class Meta:
        ordering = ["-last_seen"]
        constraints = [
            # One issue per fingerprint per project. Two customers hitting the same library bug
            # are two separate problems with two separate owners.
            models.UniqueConstraint(
                fields=["project", "fingerprint"], name="unique_issue_fingerprint_per_project"
            )
        ]
        indexes = [
            models.Index(fields=["project", "status", "-last_seen"], name="issue_triage_order"),
        ]

    def __str__(self) -> str:
        return self.title
