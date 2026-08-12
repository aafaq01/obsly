"""Release health.

`release` was already on every signal; this is the question those tags exist to answer — did
the thing we shipped make it worse?

The tests that matter here are about honesty of naming. A number called "crash-free rate" that
is not measured over sessions invites a comparison against other tools that is not valid, and a
metric people compare wrongly is worse than one they do not have.
"""

from datetime import timedelta
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.events.models import Event
from apps.issues.models import Issue, IssueStatus
from apps.projects.models import Project, ProjectKey
from apps.tracing.models import SpanStatus, Transaction
from tests.conftest import build_envelope, json_body, post

pytestmark = pytest.mark.django_db

NOW = timezone.now()


def request_at(
    project: Project,
    release: str,
    *,
    count: int = 1,
    status: str = SpanStatus.OK,
    duration: float = 100.0,
    minutes_ago: int = 5,
) -> None:
    when = NOW - timedelta(minutes=minutes_ago)
    Transaction.objects.bulk_create(
        Transaction(
            project=project,
            trace_id=f"{index:032x}",
            span_id=f"{index:016x}",
            name="/checkout",
            status=status,
            release=release,
            start_timestamp=when,
            timestamp=when,
            duration_ms=duration,
            payload={},
        )
        for index in range(Transaction.objects.count(), Transaction.objects.count() + count)
    )


def releases_of(client: Client, project: Project, period: str = "24h") -> Any:
    url = reverse("api:releases", args=[project.pk])
    return json_body(client.get(f"{url}?period={period}", secure=True))["releases"]


class TestReleaseHealth:
    def test_a_release_reports_what_it_served(self, staff_client: Client, project: Project) -> None:
        request_at(project, "api@2.0.0", count=8)
        request_at(project, "api@2.0.0", count=2, status=SpanStatus.INTERNAL_ERROR)

        [row] = releases_of(staff_client, project)

        assert row["version"] == "api@2.0.0"
        assert row["requests"] == 10
        assert row["failures"] == 2
        assert row["failure_free_rate"] == pytest.approx(0.8)

    def test_the_rate_is_named_for_what_it_measures(
        self, staff_client: Client, project: Project
    ) -> None:
        """Not "crash_free_rate". Sentry's is computed over sessions; reporting a
        request-based number under that name would be compared against other tools and be
        wrong every time."""
        request_at(project, "api@2.0.0")

        [row] = releases_of(staff_client, project)

        assert "failure_free_rate" in row
        assert not any(key.startswith("crash") for key in row)

    def test_releases_are_separated_not_averaged(
        self, staff_client: Client, project: Project
    ) -> None:
        """The entire point: a bad deploy must be visible next to the good one it replaced,
        not blended into it."""
        request_at(project, "api@1.0.0", count=10)
        request_at(project, "api@2.0.0", count=8)
        request_at(project, "api@2.0.0", count=2, status=SpanStatus.INTERNAL_ERROR)

        rates = {
            row["version"]: row["failure_free_rate"] for row in releases_of(staff_client, project)
        }

        assert rates["api@1.0.0"] == pytest.approx(1.0)
        assert rates["api@2.0.0"] == pytest.approx(0.8)

    def test_untagged_traffic_is_left_out_rather_than_bucketed_as_unknown(
        self, staff_client: Client, project: Project
    ) -> None:
        """A row called "" would sit at the top of a list sorted by traffic and mean nothing."""
        request_at(project, "", count=50)
        request_at(project, "api@2.0.0", count=5)

        versions = [row["version"] for row in releases_of(staff_client, project)]

        assert versions == ["api@2.0.0"]

    def test_adoption_is_share_of_traffic_in_the_window(
        self, staff_client: Client, project: Project
    ) -> None:
        request_at(project, "api@1.0.0", count=25)
        request_at(project, "api@2.0.0", count=75)

        adoption = {row["version"]: row["adoption"] for row in releases_of(staff_client, project)}

        assert adoption["api@2.0.0"] == pytest.approx(0.75)
        assert adoption["api@1.0.0"] == pytest.approx(0.25)

    def test_newest_release_comes_first(self, staff_client: Client, project: Project) -> None:
        """After a deploy, the release you are watching is the one you just shipped."""
        request_at(project, "api@1.0.0", minutes_ago=120)
        request_at(project, "api@2.0.0", minutes_ago=2)

        newest = releases_of(staff_client, project)[0]

        assert newest["version"] == "api@2.0.0"

    def test_the_window_is_respected(self, staff_client: Client, project: Project) -> None:
        request_at(project, "api@old", minutes_ago=60 * 48)
        request_at(project, "api@new", minutes_ago=5)

        assert [row["version"] for row in releases_of(staff_client, project)] == ["api@new"]

    def test_no_releases_is_an_empty_answer_not_an_error(
        self, staff_client: Client, project: Project
    ) -> None:
        assert releases_of(staff_client, project) == []

    def test_requires_authentication(self, client: Client, project: Project) -> None:
        url = reverse("api:releases", args=[project.pk])
        assert client.get(url, secure=True).status_code == 403


class TestIssuesIntroduced:
    def issue_in(self, project: Project, release: str, **overrides: Any) -> Issue:
        return Issue.objects.create(
            project=project,
            fingerprint=overrides.pop("fingerprint", "a" * 64),
            title="ValueError: boom",
            level="error",
            first_release=release,
            times_seen=1,
            first_seen=NOW,
            last_seen=NOW,
            **overrides,
        )

    def test_an_issue_is_counted_against_the_release_that_introduced_it(
        self, staff_client: Client, project: Project
    ) -> None:
        """ "What did this deploy break?" is a different question from "what is broken?", and
        only the first one tells you whether to roll back."""
        request_at(project, "api@2.0.0")
        self.issue_in(project, "api@2.0.0")
        self.issue_in(project, "api@1.0.0", fingerprint="b" * 64)

        [row] = releases_of(staff_client, project)

        assert row["issues_introduced"] == 1

    def test_a_resolved_issue_still_counts_as_introduced(
        self, staff_client: Client, project: Project
    ) -> None:
        """Fixing it afterwards does not change what the release did."""
        request_at(project, "api@2.0.0")
        self.issue_in(project, "api@2.0.0", status=IssueStatus.RESOLVED)

        [row] = releases_of(staff_client, project)

        assert row["issues_introduced"] == 1
        assert row["issues_unresolved"] == 0

    def test_the_release_is_recorded_when_the_issue_is_created(
        self, client: Client, project: Project, project_key: ProjectKey
    ) -> None:
        """Derivable from the earliest event, but only by sorting every event in the group —
        and this question is asked of the whole issue table at once."""
        payload = {
            "exception": {"values": [{"type": "ValueError", "value": "boom"}]},
            "release": "api@2.1.0",
        }

        post(client, project, build_envelope(("event", payload)), project_key.public_key)

        assert Issue.objects.get().first_release == "api@2.1.0"

    def test_a_later_release_does_not_rewrite_where_the_bug_came_from(
        self, client: Client, project: Project, project_key: ProjectKey
    ) -> None:
        """The bug was introduced once. Seeing it again in a newer version does not move it."""
        for release in ("api@2.1.0", "api@2.2.0"):
            payload = {
                "exception": {"values": [{"type": "ValueError", "value": "boom"}]},
                "release": release,
            }
            post(client, project, build_envelope(("event", payload)), project_key.public_key)

        assert Event.objects.count() == 2
        assert Issue.objects.get().first_release == "api@2.1.0"

    def test_an_untagged_event_leaves_the_field_blank_rather_than_guessing(
        self, client: Client, project: Project, project_key: ProjectKey
    ) -> None:
        payload = {"exception": {"values": [{"type": "ValueError", "value": "boom"}]}}

        post(client, project, build_envelope(("event", payload)), project_key.public_key)

        assert Issue.objects.get().first_release == ""

    def test_the_errors_count_is_events_not_issues(
        self, staff_client: Client, project: Project
    ) -> None:
        """Twelve occurrences of one bug is a different fact from twelve bugs."""
        request_at(project, "api@2.0.0")
        issue = self.issue_in(project, "api@2.0.0")
        Event.objects.bulk_create(
            Event(
                project=project,
                issue=issue,
                exception_type="ValueError",
                exception_value="boom",
                level="error",
                release="api@2.0.0",
                timestamp=NOW - timedelta(minutes=1),
                payload={},
            )
            for _ in range(12)
        )

        [row] = releases_of(staff_client, project)

        assert row["errors"] == 12
        assert row["issues_introduced"] == 1
