"""Transactions and spans.

A Transaction is one service's participation in a trace — the root span of the work it did.
Spans nest inside it and say where the time went.

`duration_ms` is stored rather than computed from the two timestamps on every read. Percentiles
scan every row in the window, and making the database subtract two timestamps a few million
times per query is the difference between a chart and a timeout.
"""

import uuid

from django.db import models

from apps.projects.models import Project, TimestampedModel


class SpanStatus(models.TextChoices):
    OK = "ok", "Ok"
    NOT_FOUND = "not_found", "Not found"
    UNAUTHENTICATED = "unauthenticated", "Unauthenticated"
    INVALID_ARGUMENT = "invalid_argument", "Invalid argument"
    INTERNAL_ERROR = "internal_error", "Internal error"
    CANCELLED = "cancelled", "Cancelled"
    UNKNOWN = "unknown", "Unknown"


class Transaction(TimestampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="transactions")

    trace_id = models.CharField(max_length=32, db_index=True)
    span_id = models.CharField(max_length=16)
    parent_span_id = models.CharField(max_length=16, blank=True)

    # The route pattern, never a raw URL. "/users/{id}" aggregates; "/users/8123" produces a
    # separate row per id and a percentile computed over one sample.
    name = models.CharField(max_length=255, db_index=True)
    op = models.CharField(max_length=64, default="http.server")
    status = models.CharField(max_length=32, choices=SpanStatus, default=SpanStatus.OK)

    start_timestamp = models.DateTimeField()
    timestamp = models.DateTimeField(db_index=True)
    duration_ms = models.FloatField(db_index=True)

    environment = models.CharField(max_length=64, blank=True, db_index=True)
    release = models.CharField(max_length=128, blank=True, db_index=True)

    # Web vitals and anything else measured about the page, as {name: {value, unit}}.
    # A column rather than a dig into `payload`, because these are aggregated across millions
    # of rows — a JSON path expression per row is the scan the extracted columns exist to
    # avoid.
    measurements = models.JSONField(default=dict)

    payload = models.JSONField()

    class Meta:
        ordering = ["-timestamp"]
        indexes = [
            # The percentile query: this project's rows for one endpoint over a time window.
            models.Index(fields=["project", "name", "-timestamp"], name="txn_project_name_recent"),
            models.Index(fields=["project", "-timestamp"], name="txn_project_recent"),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.duration_ms:.0f}ms)"


class Span(models.Model):
    """A unit of work inside a transaction.

    No created_at/updated_at: spans arrive in bulk, are never edited, and two extra timestamp
    columns on the highest-volume table in the system is storage spent on nothing.
    """

    id = models.BigAutoField(primary_key=True)
    transaction = models.ForeignKey(Transaction, on_delete=models.CASCADE, related_name="spans")

    trace_id = models.CharField(max_length=32, db_index=True)
    span_id = models.CharField(max_length=16)
    parent_span_id = models.CharField(max_length=16, blank=True)

    op = models.CharField(max_length=64, db_index=True)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=32, default=SpanStatus.OK)

    start_timestamp = models.DateTimeField()
    timestamp = models.DateTimeField()
    duration_ms = models.FloatField(db_index=True)

    data = models.JSONField(default=dict)

    class Meta:
        ordering = ["start_timestamp"]
        indexes = [
            models.Index(fields=["transaction", "start_timestamp"], name="span_txn_order"),
        ]

    def __str__(self) -> str:
        return f"{self.op} {self.description[:40]}"
