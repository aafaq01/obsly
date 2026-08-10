"""Stored events.

The full client payload is kept verbatim in `payload`; the columns beside it are extracted
copies used for filtering and display. Keeping the original matters because normalisation is
lossy and our idea of which fields are interesting will change — reprocessing needs the source.
"""

import uuid

from django.db import models

from apps.projects.models import Project, TimestampedModel


class Level(models.TextChoices):
    FATAL = "fatal", "Fatal"
    ERROR = "error", "Error"
    WARNING = "warning", "Warning"
    INFO = "info", "Info"
    DEBUG = "debug", "Debug"


class Event(TimestampedModel):
    # Client-generated, so ingest is idempotent: a retried envelope after a network timeout
    # collides on the primary key instead of storing the same crash twice.
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="events")

    # Nullable: the event is stored first and grouped after, so a failure in fingerprinting
    # costs the grouping, never the event itself.
    issue = models.ForeignKey(
        "issues.Issue", on_delete=models.CASCADE, related_name="events", null=True, blank=True
    )

    # When the client says it happened, versus when we received it. They differ under clock
    # skew and offline buffering, and conflating them makes an outage timeline unreadable.
    timestamp = models.DateTimeField(db_index=True)
    received_at = models.DateTimeField(auto_now_add=True, db_index=True)

    level = models.CharField(max_length=16, choices=Level, default=Level.ERROR, db_index=True)
    platform = models.CharField(max_length=32, blank=True)

    message = models.TextField(blank=True)
    exception_type = models.CharField(max_length=256, blank=True)
    exception_value = models.TextField(blank=True)
    culprit = models.CharField(max_length=512, blank=True)

    # The join key between an error and the request it happened inside. Indexed because
    # "show me everything from this trace" is the query the whole correlation story rests on.
    trace_id = models.CharField(max_length=32, blank=True, db_index=True)
    span_id = models.CharField(max_length=16, blank=True)

    environment = models.CharField(max_length=64, blank=True, db_index=True)
    release = models.CharField(max_length=128, blank=True, db_index=True)
    server_name = models.CharField(max_length=256, blank=True)

    payload = models.JSONField()
    tags = models.JSONField(default=dict)

    class Meta:
        ordering = ["-timestamp"]
        indexes = [
            # The issue stream's default query: this project, newest first.
            models.Index(fields=["project", "-timestamp"], name="event_project_recent"),
            # The issue detail page's only query: this issue's events, newest first.
            models.Index(fields=["issue", "-timestamp"], name="event_issue_recent"),
        ]

    def __str__(self) -> str:
        return self.title

    @property
    def title(self) -> str:
        if self.exception_type:
            return f"{self.exception_type}: {self.exception_value}".strip().rstrip(":")
        return self.message or "<no title>"
