"""Read API for the web UI.

Authentication is Django's session, so signing into /admin/ signs you into the UI. That is a
placeholder with a real expiry date — `feat/auth` replaces it — but it is honest: there is
exactly one auth mechanism and it is enforced, rather than an open API with a note promising
to secure it later.
"""

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from django.db.models import Count, Q, QuerySet
from django.db.models.functions import TruncHour
from django.shortcuts import get_object_or_404
from rest_framework import generics
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.api.serializers import (
    EventSerializer,
    IssueDetailSerializer,
    IssueSerializer,
    ProjectSerializer,
)
from apps.events.models import Event
from apps.issues.models import Issue, IssueStatus
from apps.projects.models import Project

HOURS = 24

SORTS = {
    "last_seen": "-last_seen",
    "first_seen": "-first_seen",
    "times_seen": "-times_seen",
}


class MeView(APIView):
    def get(self, request: Request) -> Response:
        return Response({"username": request.user.get_username()})


class ProjectListView(generics.ListAPIView[Project]):
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
        histograms = _hourly_histograms([issue.pk for issue in issues])

        for issue in issues:
            issue.hourly = histograms[issue.pk]  # type: ignore[attr-defined]

        return Response(self.get_serializer(issues, many=True).data)


class IssueDetailView(generics.RetrieveAPIView[Issue]):
    serializer_class = IssueDetailSerializer
    queryset = Issue.objects.all()

    def retrieve(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        issue = self.get_object()
        issue.hourly = _hourly_histograms([issue.pk])[issue.pk]  # type: ignore[attr-defined]

        latest = Event.objects.filter(issue=issue).order_by("-timestamp").first()

        return Response(
            {
                "issue": self.get_serializer(issue).data,
                "latest_event": EventSerializer(latest).data if latest else None,
                "tags": _tag_distribution(issue),
            }
        )


class IssueEventsView(generics.ListAPIView[Event]):
    serializer_class = EventSerializer

    def get_queryset(self) -> QuerySet[Event]:
        return Event.objects.filter(issue_id=self.kwargs["issue_id"]).order_by("-timestamp")[:50]


def _hourly_histograms(issue_ids: list[int]) -> dict[int, list[int]]:
    """Events per hour for the last 24h, one bucket per hour, zero-filled.

    One grouped query for every issue on the page rather than one per row — the stream shows a
    hundred issues, and a hundred round trips is how a list view becomes a page-load problem.
    """
    if not issue_ids:
        return {}

    since = datetime.now(tz=UTC).replace(minute=0, second=0, microsecond=0) - timedelta(
        hours=HOURS - 1
    )

    rows = (
        Event.objects.filter(issue_id__in=issue_ids, timestamp__gte=since)
        .annotate(hour=TruncHour("timestamp"))
        .values("issue_id", "hour")
        .annotate(count=Count("id"))
    )

    counts: dict[int, dict[datetime, int]] = defaultdict(dict)
    for row in rows:
        counts[row["issue_id"]][row["hour"]] = row["count"]

    buckets = [since + timedelta(hours=offset) for offset in range(HOURS)]
    return {
        issue_id: [counts[issue_id].get(bucket, 0) for bucket in buckets] for issue_id in issue_ids
    }


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
