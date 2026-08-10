from typing import Any

from django.conf import settings
from rest_framework import serializers

from apps.events.models import Event
from apps.issues.models import Issue
from apps.projects.models import Organization, Project, ProjectKey
from apps.tracing.models import Span
from apps.tracing.models import Transaction as TransactionModel


class OrganizationSerializer(serializers.ModelSerializer[Organization]):
    class Meta:
        model = Organization
        fields = ("id", "name", "slug")


class ProjectKeySerializer(serializers.ModelSerializer[ProjectKey]):
    dsn = serializers.SerializerMethodField()

    class Meta:
        model = ProjectKey
        fields = ("id", "label", "public_key", "dsn", "is_active", "created_at")

    def get_dsn(self, obj: ProjectKey) -> str:
        # From a setting, not the request: the host an operator browses on is not necessarily
        # the host their services can reach.
        return obj.dsn(settings.OBSLY_INGEST_ORIGIN)


class ProjectSerializer(serializers.ModelSerializer[Project]):
    organization = serializers.CharField(source="organization.name", read_only=True)
    organization_id = serializers.PrimaryKeyRelatedField(
        source="organization", queryset=Organization.objects.all(), write_only=True
    )
    unresolved_count = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = Project
        # Widened so ProjectDetailSerializer can add `keys`; without the annotation mypy infers
        # a fixed-length tuple and any subclass overriding it is an error.
        fields: tuple[str, ...] = (
            "id",
            "name",
            "slug",
            "platform",
            "organization",
            "organization_id",
            "unresolved_count",
        )


class ProjectDetailSerializer(ProjectSerializer):
    keys = ProjectKeySerializer(many=True, read_only=True)

    class Meta(ProjectSerializer.Meta):
        fields = (
            "id",
            "name",
            "slug",
            "platform",
            "organization",
            "organization_id",
            "unresolved_count",
            "keys",
        )


class IssueSerializer(serializers.ModelSerializer[Issue]):
    """The issue stream row. Deliberately narrow — the stream renders hundreds of these and
    must not carry a full event payload per row."""

    project = serializers.IntegerField(source="project_id", read_only=True)
    hourly = serializers.SerializerMethodField()

    def get_hourly(self, obj: Issue) -> list[int]:
        """Empty unless a view attached a histogram.

        A plain ListField would raise AttributeError on any endpoint that serialises an Issue
        without one — which is every mutation response.
        """
        return list(getattr(obj, "hourly", []))

    class Meta:
        model = Issue
        # Widened so IssueDetailSerializer can add fields; without the annotation mypy infers a
        # fixed-length tuple and any subclass overriding it is an error.
        fields: tuple[str, ...] = (
            "id",
            "project",
            "title",
            "culprit",
            "level",
            "status",
            "times_seen",
            "first_seen",
            "last_seen",
            "hourly",
        )


class EventSerializer(serializers.ModelSerializer[Event]):
    exception = serializers.SerializerMethodField()

    class Meta:
        model = Event
        fields = (
            "id",
            "timestamp",
            "received_at",
            "level",
            "platform",
            "message",
            "exception_type",
            "exception_value",
            "culprit",
            "environment",
            "release",
            "server_name",
            "tags",
            "trace_id",
            "span_id",
            "exception",
            "payload",
        )

    def get_exception(self, obj: Event) -> list[dict[str, Any]]:
        """The exception chain with frames, pulled out of the raw payload.

        Returned separately from `payload` so the UI renders a stack trace without having to
        know the wire format, while `payload` stays available for the raw view.
        """
        exception = obj.payload.get("exception")
        values = exception.get("values") if isinstance(exception, dict) else exception
        if not isinstance(values, list):
            return []

        return [
            {
                "type": entry.get("type", ""),
                "value": entry.get("value", ""),
                "frames": _frames(entry),
            }
            for entry in values
            if isinstance(entry, dict)
        ]


def _frames(entry: dict[str, Any]) -> list[dict[str, Any]]:
    stacktrace = entry.get("stacktrace")
    if not isinstance(stacktrace, dict):
        return []
    return [
        {
            "filename": frame.get("filename", ""),
            "module": frame.get("module", ""),
            "function": frame.get("function", ""),
            "lineno": frame.get("lineno"),
            "in_app": bool(frame.get("in_app")),
        }
        for frame in stacktrace.get("frames", [])
        if isinstance(frame, dict)
    ]


class IssueDetailSerializer(IssueSerializer):
    fingerprint_components = serializers.ListField(child=serializers.CharField(), read_only=True)

    class Meta(IssueSerializer.Meta):
        # Spelled out rather than spread from the parent: a subclass widening an inherited
        # tuple type is a mypy error, and the explicit list is the clearer contract anyway.
        fields = (
            "id",
            "project",
            "title",
            "culprit",
            "level",
            "status",
            "times_seen",
            "first_seen",
            "last_seen",
            "hourly",
            "fingerprint",
            "fingerprint_components",
        )


class SpanSerializer(serializers.ModelSerializer[Span]):
    class Meta:
        model = Span
        fields = (
            "span_id",
            "parent_span_id",
            "op",
            "description",
            "status",
            "start_timestamp",
            "timestamp",
            "duration_ms",
            "data",
        )


class TransactionSerializer(serializers.ModelSerializer[TransactionModel]):
    span_count = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = TransactionModel
        fields: tuple[str, ...] = (
            "id",
            "trace_id",
            "span_id",
            "name",
            "op",
            "status",
            "start_timestamp",
            "timestamp",
            "duration_ms",
            "environment",
            "release",
            "span_count",
        )


class TraceDetailSerializer(TransactionSerializer):
    spans = SpanSerializer(many=True, read_only=True)

    class Meta(TransactionSerializer.Meta):
        fields = (
            "id",
            "trace_id",
            "span_id",
            "name",
            "op",
            "status",
            "start_timestamp",
            "timestamp",
            "duration_ms",
            "environment",
            "release",
            "span_count",
            "spans",
        )


class CorrelatedErrorSerializer(serializers.ModelSerializer[Event]):
    """An error, as seen from the trace it happened inside."""

    issue_id = serializers.IntegerField(source="issue.id", read_only=True, default=None)
    title = serializers.CharField(read_only=True)

    class Meta:
        model = Event
        fields = ("id", "issue_id", "title", "level", "timestamp", "span_id")
