"""Read API for the web UI.

Authentication is Django's session, so signing into /admin/ signs you into the UI. That is a
placeholder with a real expiry date — `feat/auth` replaces it — but it is honest: there is
exactly one auth mechanism and it is enforced, rather than an open API with a note promising
to secure it later.
"""

from collections import defaultdict
from datetime import datetime
from typing import Any

from django.db.models import Count, Q, QuerySet
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import generics, status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.api.dashboard import overview
from apps.api.performance import (
    endpoint_summary,
    span_detail,
    span_ops,
    span_summary,
    window,
)
from apps.api.serializers import (
    CorrelatedErrorSerializer,
    EventSerializer,
    IssueDetailSerializer,
    IssueSerializer,
    LogRecordSerializer,
    OrganizationSerializer,
    ProjectDetailSerializer,
    ProjectKeySerializer,
    ProjectSerializer,
    TraceDetailSerializer,
    TransactionSerializer,
)
from apps.api.timewindow import PERIODS, Window, resolve
from apps.events.models import Event
from apps.issues.models import Issue, IssueStatus
from apps.logs.models import LogLevel, LogRecord
from apps.projects.models import Organization, Project, ProjectKey
from apps.tracing.models import SpanStatus, Transaction

# Worst last. The order is the filter semantics for min_level, so it lives beside them.
LOG_LEVEL_ORDER = ["trace", "debug", "info", "warning", "error", "fatal"]

SORTS = {
    "last_seen": "-last_seen",
    "first_seen": "-first_seen",
    "times_seen": "-times_seen",
}


class OrganizationListView(generics.ListCreateAPIView[Organization]):
    serializer_class = OrganizationSerializer
    queryset = Organization.objects.all()


class ProjectListView(generics.ListCreateAPIView[Project]):
    serializer_class = ProjectSerializer

    def get_queryset(self) -> QuerySet[Project]:
        return (
            Project.objects.select_related("organization")
            .annotate(
                unresolved_count=Count(
                    "issues", filter=Q(issues__status=IssueStatus.UNRESOLVED), distinct=True
                )
            )
            .order_by("name")
        )


class ProjectDetailView(generics.RetrieveAPIView[Project]):
    """A project and its ingest keys — everything needed to wire an SDK up, in one response."""

    serializer_class = ProjectDetailSerializer

    def get_queryset(self) -> QuerySet[Project]:
        return (
            Project.objects.select_related("organization")
            .prefetch_related("keys")
            .annotate(
                unresolved_count=Count(
                    "issues", filter=Q(issues__status=IssueStatus.UNRESOLVED), distinct=True
                )
            )
        )


class ProjectKeyCreateView(generics.CreateAPIView[ProjectKey]):
    """Issue an additional key.

    Rotation is issue-new, migrate clients, revoke old. Without a second key that sequence is
    an outage, which is why revoking is a flag rather than a delete.
    """

    serializer_class = ProjectKeySerializer

    def perform_create(self, serializer: Any) -> None:
        project = get_object_or_404(Project, pk=self.kwargs["project_id"])
        serializer.save(project=project)


class ProjectKeyUpdateView(generics.UpdateAPIView[ProjectKey]):
    serializer_class = ProjectKeySerializer
    queryset = ProjectKey.objects.all()
    http_method_names = ["patch"]


class PerformanceView(APIView):
    """Latency and throughput per endpoint.

    Percentiles rather than averages: an endpoint answering 99% of requests in 20ms and 1% in
    8 seconds averages out to something that looks healthy, while the p99 says plainly that one
    request in a hundred is unusable.
    """

    def get(self, request: Request, project_id: int) -> Response:
        get_object_or_404(Project, pk=project_id)
        win = resolve(request.query_params.get("period"))
        period, minutes = win.period, win.minutes

        endpoints = endpoint_summary(project_id, period)
        totals = Transaction.objects.filter(project_id=project_id, timestamp__gte=win.since)

        return Response(
            {
                "period": period,
                "periods": list(PERIODS),
                "endpoints": endpoints,
                "summary": {
                    "transactions": sum(row["count"] for row in endpoints),
                    "throughput_per_minute": round(
                        sum(row["count"] for row in endpoints) / minutes, 3
                    ),
                    "failure_rate": _overall_failure_rate(endpoints),
                    "series": _throughput(totals, win),
                    "bucket_seconds": win.bucket_seconds,
                },
            }
        )


def _overall_failure_rate(endpoints: list[dict[str, Any]]) -> float:
    total = sum(row["count"] for row in endpoints)
    if not total:
        return 0.0
    failures = sum(row["count"] * row["failure_rate"] for row in endpoints)
    return float(round(failures / total, 4))


def _throughput(queryset: QuerySet[Transaction], win: Window) -> list[int]:
    """Zero-filled buckets, so a quiet period is a gap in the chart rather than a missing bar."""
    rows = queryset.annotate(bucket=win.truncate()).values("bucket").annotate(count=Count("id"))
    return win.zero_filled({row["bucket"]: row["count"] for row in rows})


class DashboardView(APIView):
    """The project overview, in one request.

    A dashboard that fires eight parallel calls renders in eight stages, and every stage is a
    layout shift.
    """

    def get(self, request: Request, project_id: int) -> Response:
        get_object_or_404(Project, pk=project_id)
        return Response(overview(project_id, request.query_params.get("period", "24h")))


class SpanInsightsView(APIView):
    """Spans aggregated by what they do, not by which request they were in.

    A waterfall shows one request. The span that matters is usually the one that is
    individually fast and runs ten thousand times, and only an aggregate can show that.
    """

    def get(self, request: Request, project_id: int) -> Response:
        get_object_or_404(Project, pk=project_id)
        period = request.query_params.get("period", "24h")
        op = request.query_params.get("op", "").strip()

        return Response(
            {
                "period": period,
                "periods": list(PERIODS),
                "ops": span_ops(project_id, period),
                "spans": span_summary(project_id, period, op=op),
            }
        )


class SpanDetailView(APIView):
    """One span group: distribution, callers, and traces to open.

    The aggregate says a query is expensive. This says which endpoints make it expensive and
    hands over a real trace — the step between "this is the problem" and "here is the request
    where it happened".
    """

    def get(self, request: Request, project_id: int) -> Response:
        get_object_or_404(Project, pk=project_id)

        op = request.query_params.get("op", "").strip()
        description = request.query_params.get("description", "")
        if not op:
            return Response({"detail": "op is required"}, status=status.HTTP_400_BAD_REQUEST)

        detail = span_detail(
            project_id,
            request.query_params.get("period", "24h"),
            op=op,
            description=description,
        )
        if detail is None:
            # Nothing in the window rather than nothing ever: a 404 would read as "this span
            # does not exist", which is a different and more alarming statement.
            return Response(
                {"detail": "No spans matched in this period.", "op": op},
                status=status.HTTP_404_NOT_FOUND,
            )

        return Response(detail)


class LogListView(generics.ListAPIView[LogRecord]):
    """The log viewer.

    Newest first, because a log viewer opened during an incident is asking "what is happening",
    not "what happened first".
    """

    serializer_class = LogRecordSerializer

    def get_queryset(self) -> QuerySet[LogRecord]:
        get_object_or_404(Project, pk=self.kwargs["project_id"])
        win = resolve(self.request.query_params.get("period"))

        logs = LogRecord.objects.filter(
            project_id=self.kwargs["project_id"], timestamp__gte=win.since
        )
        logs = _filter_levels(logs, self.request)
        logs = _search_logs(logs, self.request.query_params.get("q", ""))

        trace_id = self.request.query_params.get("trace_id", "").strip()
        if trace_id:
            logs = logs.filter(trace_id=trace_id)

        logger_name = self.request.query_params.get("logger", "").strip()
        if logger_name:
            logs = logs.filter(logger=logger_name)

        return logs.order_by("-timestamp")[:200]


def _filter_levels(logs: QuerySet[LogRecord], request: Request) -> QuerySet[LogRecord]:
    """Two filters, because people mean two different things.

    `levels=warning,error` is an exact set — "show me only these". `min_level=warning` is
    level-and-worse, which is what somebody triaging means when they say "warnings and above".
    Offering only the second makes it impossible to look at warnings without the errors
    drowning them; offering only the first makes "everything bad" a five-click operation.
    """
    exact = [
        level
        for level in request.query_params.get("levels", "").split(",")
        if level.strip() in LogLevel.values
    ]
    if exact:
        return logs.filter(level__in=exact)

    minimum = request.query_params.get("min_level", "").strip()
    if minimum in LOG_LEVEL_ORDER:
        return logs.filter(level__in=LOG_LEVEL_ORDER[LOG_LEVEL_ORDER.index(minimum) :])

    return logs


def _search_logs(logs: QuerySet[LogRecord], query: str) -> QuerySet[LogRecord]:
    """Substring search over message, logger and attribute values.

    icontains rather than full-text: a log line is not prose, and the thing people paste into
    this box is an order id or a hostname, which stemming would mangle. The trigram index makes
    the substring match an index lookup rather than the sequential scan it would otherwise be.
    """
    query = query.strip()
    if not query:
        return logs

    return logs.filter(
        Q(body__icontains=query)
        | Q(logger__icontains=query)
        # Attributes are JSONB; casting to text lets one search box cover the structured
        # fields too, which is where request ids and user ids actually live.
        | Q(attributes__icontains=query)
    )


class TraceListView(generics.ListAPIView[Transaction]):
    """Recent traces, slowest first by default.

    A trace list sorted by time shows you what happened last; sorted by duration it shows you
    what to look at. The second is almost always why somebody opened the page.
    """

    serializer_class = TransactionSerializer

    def get_queryset(self) -> QuerySet[Transaction]:
        get_object_or_404(Project, pk=self.kwargs["project_id"])
        since, _ = window(self.request.query_params.get("period", "24h"))

        traces = Transaction.objects.filter(
            project_id=self.kwargs["project_id"], timestamp__gte=since
        ).annotate(span_count=Count("spans"))

        name = self.request.query_params.get("name", "").strip()
        if name:
            traces = traces.filter(name=name)

        if self.request.query_params.get("status") == "failed":
            traces = traces.filter(status=SpanStatus.INTERNAL_ERROR)

        order = (
            "-duration_ms" if self.request.query_params.get("sort") != "recent" else "-timestamp"
        )
        return traces.order_by(order)[:100]


class TraceDetailView(generics.RetrieveAPIView[Transaction]):
    serializer_class = TraceDetailSerializer
    lookup_field = "pk"

    def get_queryset(self) -> QuerySet[Transaction]:
        return Transaction.objects.prefetch_related("spans").annotate(span_count=Count("spans"))

    def retrieve(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        trace = self.get_object()

        # Errors recorded inside this request. This is the correlation: same trace_id, no
        # timestamp guessing, no "these probably happened together".
        errors = (
            Event.objects.filter(project_id=trace.project_id, trace_id=trace.trace_id)
            .select_related("issue")
            .order_by("timestamp")[:50]
        )

        # Everything the application said during this request, in order.
        logs = LogRecord.objects.filter(
            project_id=trace.project_id, trace_id=trace.trace_id
        ).order_by("timestamp")[:200]

        return Response(
            {
                **self.get_serializer(trace).data,
                "errors": CorrelatedErrorSerializer(errors, many=True).data,
                "logs": LogRecordSerializer(logs, many=True).data,
            }
        )


class IssueListView(generics.ListAPIView[Issue]):
    serializer_class = IssueSerializer

    def get_queryset(self) -> QuerySet[Issue]:
        project = get_object_or_404(Project, pk=self.kwargs["project_id"])
        issues = Issue.objects.for_project(project.pk)

        status = self.request.query_params.get("status", IssueStatus.UNRESOLVED)
        if status != "all":
            issues = issues.filter(status=status)

        query = self.request.query_params.get("q", "").strip()
        if query:
            issues = issues.filter(Q(title__icontains=query) | Q(culprit__icontains=query))

        level = self.request.query_params.get("level", "").strip()
        if level:
            issues = issues.filter(level=level)

        sort = SORTS.get(self.request.query_params.get("sort", ""), "-last_seen")
        return issues.order_by(sort)[:100]

    def list(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        issues = list(self.get_queryset())
        win = resolve(self.request.query_params.get("period"))
        histograms = _histograms([issue.pk for issue in issues], win)

        for issue in issues:
            issue.hourly = histograms[issue.pk]  # type: ignore[attr-defined]

        return Response(self.get_serializer(issues, many=True).data)


class IssueDetailView(generics.RetrieveAPIView[Issue]):
    serializer_class = IssueDetailSerializer
    queryset = Issue.objects.all()

    def retrieve(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        issue = self.get_object()
        win = resolve(request.query_params.get("period"))
        issue.hourly = _histograms([issue.pk], win)[issue.pk]  # type: ignore[attr-defined]

        latest = Event.objects.filter(issue=issue).order_by("-timestamp").first()

        # The transaction this error happened inside, if any — the other half of the link.
        trace = None
        if latest is not None and latest.trace_id:
            trace = (
                Transaction.objects.filter(project_id=issue.project_id, trace_id=latest.trace_id)
                .values("id", "name", "duration_ms", "status")
                .first()
            )

        return Response(
            {
                "issue": self.get_serializer(issue).data,
                "latest_event": EventSerializer(latest).data if latest else None,
                "tags": _tag_distribution(issue),
                "trace": trace,
            }
        )


class IssueStatusView(APIView):
    """Move an issue through its workflow.

    Only `status` is writable. Everything else on an Issue is derived from events, and a
    hand-edited title or count would make the row disagree with the data behind it.
    """

    def patch(self, request: Request, pk: int) -> Response:
        issue = get_object_or_404(Issue, pk=pk)

        # A JSON array body parses to a list, and .get() on it is an AttributeError -> 500.
        # A malformed body is the client's mistake and must read as 400.
        payload = request.data if isinstance(request.data, dict) else {}
        new_status = payload.get("status")

        if new_status not in IssueStatus.values:
            return Response(
                {"detail": f"status must be one of {', '.join(IssueStatus.values)}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Recorded so a later regression can be judged against when it was resolved rather
        # than against "sometime before now".
        Issue.objects.filter(pk=issue.pk).update(status=new_status, updated_at=timezone.now())
        issue.refresh_from_db()

        return Response(IssueSerializer(issue).data)


class IssueEventsView(generics.ListAPIView[Event]):
    serializer_class = EventSerializer

    def get_queryset(self) -> QuerySet[Event]:
        return Event.objects.filter(issue_id=self.kwargs["issue_id"]).order_by("-timestamp")[:50]


def _histograms(issue_ids: list[int], win: Window) -> dict[int, list[int]]:
    """Events per bucket over the window, zero-filled.

    One grouped query for every issue on the page rather than one per row — the stream shows a
    hundred issues, and a hundred round trips is how a list view becomes a page-load problem.
    """
    if not issue_ids:
        return {}

    rows = (
        Event.objects.filter(issue_id__in=issue_ids, timestamp__gte=win.since)
        .annotate(bucket=win.truncate())
        .values("issue_id", "bucket")
        .annotate(count=Count("id"))
    )

    counts: dict[int, dict[datetime, int]] = defaultdict(dict)
    for row in rows:
        counts[row["issue_id"]][row["bucket"]] = row["count"]

    return {issue_id: win.zero_filled(counts[issue_id]) for issue_id in issue_ids}


def _tag_distribution(issue: Issue, limit: int = 5) -> dict[str, list[dict[str, Any]]]:
    """Top values per tag key, so the detail page can answer "is this only on Chrome?"."""
    events = Event.objects.filter(issue=issue).values_list("tags", flat=True)[:1000]

    tallies: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    total = 0
    for tags in events:
        total += 1
        if isinstance(tags, dict):
            for key, value in tags.items():
                tallies[key][str(value)] += 1

    if not total:
        return {}

    return {
        key: [
            {"value": value, "count": count, "percentage": round(100 * count / total)}
            for value, count in sorted(values.items(), key=lambda kv: -kv[1])[:limit]
        ]
        for key, values in tallies.items()
    }
