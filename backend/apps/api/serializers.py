from typing import Any

from rest_framework import serializers

from apps.events.models import Event
from apps.issues.models import Issue
from apps.projects.models import Project


class ProjectSerializer(serializers.ModelSerializer[Project]):
    organization = serializers.CharField(source="organization.name", read_only=True)
    unresolved_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Project
        fields = ("id", "name", "slug", "platform", "organization", "unresolved_count")


class IssueSerializer(serializers.ModelSerializer[Issue]):
    """The issue stream row. Deliberately narrow — the stream renders hundreds of these and
    must not carry a full event payload per row."""

    project = serializers.IntegerField(source="project_id", read_only=True)
    hourly = serializers.ListField(child=serializers.IntegerField(), read_only=True)

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
