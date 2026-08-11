"""Stored log records.

The highest-volume signal by a wide margin: an application emits logs on every request, not
only the failing ones. Everything here is shaped by that — narrow columns, aggressive indexes
on the two things anyone filters by, and no per-row overhead that is not earned.
"""

import uuid

from django.contrib.postgres.indexes import GinIndex
from django.db import models

from apps.projects.models import Project


class LogLevel(models.TextChoices):
    TRACE = "trace", "Trace"
    DEBUG = "debug", "Debug"
    INFO = "info", "Info"
    WARNING = "warning", "Warning"
    ERROR = "error", "Error"
    FATAL = "fatal", "Fatal"


class LogRecord(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="logs")

    timestamp = models.DateTimeField(db_index=True)
    received_at = models.DateTimeField(auto_now_add=True)

    level = models.CharField(max_length=16, choices=LogLevel, default=LogLevel.INFO, db_index=True)
    body = models.TextField()
    logger = models.CharField(max_length=200, blank=True, db_index=True)

    # The join back to the request that produced the line. Without it a log viewer is grep with
    # extra steps.
    trace_id = models.CharField(max_length=32, blank=True, db_index=True)
    span_id = models.CharField(max_length=16, blank=True)

    environment = models.CharField(max_length=64, blank=True, db_index=True)
    release = models.CharField(max_length=128, blank=True)
    server_name = models.CharField(max_length=256, blank=True)

    attributes = models.JSONField(default=dict)

    class Meta:
        ordering = ["-timestamp"]
        indexes = [
            # The log viewer's default query: this project, newest first.
            models.Index(fields=["project", "-timestamp"], name="log_project_recent"),
            # And the correlated one: every line from a single request.
            models.Index(fields=["trace_id", "timestamp"], name="log_trace_order"),
            # Substring search. Without these, `body__icontains` is a sequential scan over
            # every row in the window — the query that stops working first on a table that
            # grows by a row per log line, and it stops working exactly when somebody is
            # searching it during an incident.
            GinIndex(fields=["body"], name="log_body_trgm", opclasses=["gin_trgm_ops"]),
            GinIndex(fields=["logger"], name="log_logger_trgm", opclasses=["gin_trgm_ops"]),
        ]

    def __str__(self) -> str:
        return f"[{self.level}] {self.body[:60]}"
