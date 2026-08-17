from typing import Any

from django.conf import settings
from rest_framework import serializers

from apps.alerts.models import AlertFire, AlertRule, Trigger
from apps.events.models import Event
from apps.issues.models import Issue
from apps.logs.models import LogRecord
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


def detected_platforms(project: Project) -> list[str]:
    """What is actually reporting, rather than what somebody picked from a list.

    A project used to declare one platform at creation, which was wrong in the case this
    product exists for: a browser page load and the backend request it triggers have to sit in
    one project or the trace cannot join them. So the same project holds both, and asking for a
    single answer up front forced a lie.

    Derived, so it is never stale and never needs maintaining.
    """
    seen = set(
        Event.objects.filter(project=project).values_list("platform", flat=True).distinct()[:10]
    )
    # A page load is only ever reported by a browser SDK, and it arrives as a transaction
    # rather than an event — so a frontend with no errors yet would otherwise show nothing.
    if TransactionModel.objects.filter(project=project, op="pageload").exists():
        seen.add("javascript")

    return sorted(name for name in seen if name)


class ProjectSerializer(serializers.ModelSerializer[Project]):
    organization = serializers.CharField(source="organization.name", read_only=True)
    platforms = serializers.SerializerMethodField()

    def get_platforms(self, obj: Project) -> list[str]:
        return detected_platforms(obj)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        """Say which name is taken, in the words somebody typed it in.

        DRF's own answer is "The fields organization_id, slug must make a unique set", which is
        accurate and describes a database constraint rather than the thing on screen. A person
        naming a project should not have to work out that a slug is derived from the name.
        """
        organization = attrs.get("organization") or getattr(self.instance, "organization", None)
        slug = attrs.get("slug") or getattr(self.instance, "slug", None)

        if organization and slug:
            clash = Project.objects.filter(organization=organization, slug=slug)
            if self.instance is not None:
                clash = clash.exclude(pk=self.instance.pk)
            if clash.exists():
                raise serializers.ValidationError(
                    {"name": f"A project called “{clash.get().name}” already exists here."}
                )

        return attrs

    organization_id = serializers.PrimaryKeyRelatedField(
        source="organization", queryset=Organization.objects.all(), write_only=True
    )
    unresolved_count = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = Project
        # Widened so ProjectDetailSerializer can add `keys`; without the annotation mypy infers
        # a fixed-length tuple and any subclass overriding it is an error.
        # DRF generates a UniqueTogetherValidator from the model's constraint and runs it
        # before validate(), so its wording — "the fields organization_id, slug must make a
        # unique set" — was always the one people met. The check itself moves into validate();
        # the database constraint remains the actual guarantee either way.
        validators: list[Any] = []

        fields: tuple[str, ...] = (
            "id",
            "name",
            "slug",
            "platforms",
            "organization",
            "organization_id",
            "unresolved_count",
            "trace_sharing",
        )


class ProjectDetailSerializer(ProjectSerializer):
    keys = ProjectKeySerializer(many=True, read_only=True)

    class Meta(ProjectSerializer.Meta):
        fields = (
            "id",
            "name",
            "slug",
            "platforms",
            "organization",
            "organization_id",
            "unresolved_count",
            "trace_sharing",
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
            "first_release",
            "category",
            "issue_type",
            "evidence",
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
            "flags",
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
    # Only on the detail page. The stream draws the same counts without an axis, so a timestamp
    # per row would be a hundred copies of a string nothing reads.
    hourly_start = serializers.DateTimeField(read_only=True)
    bucket_seconds = serializers.IntegerField(read_only=True)

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
            "first_release",
            "hourly_start",
            "bucket_seconds",
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


class TraceNodeSerializer(serializers.Serializer[dict[str, Any]]):
    """One service's part of a request.

    A plain Serializer over dicts rather than a ModelSerializer, because the rows come from
    `distributed.build_tree` already carrying their depth in the call chain — a property of the
    trace, not of any row in the database.
    """

    id = serializers.CharField()
    project_id = serializers.IntegerField()
    project_name = serializers.CharField()
    name = serializers.CharField()
    op = serializers.CharField()
    status = serializers.CharField()
    start_timestamp = serializers.DateTimeField()
    timestamp = serializers.DateTimeField()
    duration_ms = serializers.FloatField()
    span_id = serializers.CharField()
    parent_span_id = serializers.CharField()
    environment = serializers.CharField()
    release = serializers.CharField()
    span_count = serializers.IntegerField()
    spans = SpanSerializer(many=True, read_only=True)
    depth = serializers.IntegerField()


class CorrelatedErrorSerializer(serializers.ModelSerializer[Event]):
    """An error, as seen from the trace it happened inside."""

    issue_id = serializers.IntegerField(source="issue.id", read_only=True, default=None)
    title = serializers.CharField(read_only=True)
    # Which service threw it. One word, and without it a cross-service trace lists errors with
    # no way to tell whose they are.
    project_name = serializers.CharField(source="project.name", read_only=True)

    class Meta:
        model = Event
        fields = ("id", "issue_id", "title", "level", "timestamp", "span_id", "project_name")


class LogRecordSerializer(serializers.ModelSerializer[LogRecord]):
    # Which service said it. A cross-service trace interleaves four applications' logs, and
    # without the name they read as one confused program.
    project_name = serializers.CharField(source="project.name", read_only=True)

    class Meta:
        model = LogRecord
        fields = (
            "id",
            "timestamp",
            "level",
            "body",
            "logger",
            "project_name",
            "trace_id",
            "span_id",
            "environment",
            "release",
            "attributes",
        )


class AlertRuleSerializer(serializers.ModelSerializer[AlertRule]):
    """A rule, plus what it has actually done — a rule page that cannot tell you whether a rule
    has ever fired is a page that hides its own misconfiguration."""

    trigger_label = serializers.CharField(source="get_trigger_display", read_only=True)
    fire_count = serializers.IntegerField(read_only=True)
    last_fired_at = serializers.DateTimeField(read_only=True)

    class Meta:
        model = AlertRule
        fields = (
            "id",
            "name",
            "trigger",
            "trigger_label",
            "threshold",
            "window_minutes",
            "level",
            "webhook_url",
            "cooldown_minutes",
            "enabled",
            "created_at",
            "fire_count",
            "last_fired_at",
        )

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        trigger = attrs.get("trigger", getattr(self.instance, "trigger", Trigger.NEW_ISSUE))
        if trigger == Trigger.FREQUENCY:
            # A threshold of zero fires on every event forever. Rejecting it here is cheaper
            # than explaining the resulting pager storm.
            if attrs.get("threshold", getattr(self.instance, "threshold", 0)) < 1:
                raise serializers.ValidationError(
                    {"threshold": "A frequency rule needs a threshold of at least 1."}
                )
            if attrs.get("window_minutes", getattr(self.instance, "window_minutes", 0)) < 1:
                raise serializers.ValidationError(
                    {"window_minutes": "A frequency rule needs a window of at least 1 minute."}
                )
        return attrs


class AlertFireSerializer(serializers.ModelSerializer[AlertFire]):
    rule_name = serializers.CharField(source="rule.name", read_only=True)
    issue_title = serializers.CharField(source="issue.title", read_only=True)
    issue_level = serializers.CharField(source="issue.level", read_only=True)
    issue = serializers.IntegerField(source="issue_id", read_only=True)

    class Meta:
        model = AlertFire
        fields = (
            "id",
            "rule_name",
            "issue",
            "issue_title",
            "issue_level",
            "reason",
            "delivery",
            "status_code",
            "error",
            "created_at",
        )
