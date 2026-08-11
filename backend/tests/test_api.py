from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from apps.issues.models import Issue, IssueStatus
from apps.projects.models import Organization, Project, ProjectKey
from tests.conftest import build_envelope, json_body, post
from tests.test_grouping import FRAMES, error

pytestmark = pytest.mark.django_db


@pytest.fixture
def staff_client(client: Client) -> Client:
    User.objects.create_user("viewer", password="viewer-password")
    client.login(username="viewer", password="viewer-password")
    return client


def ingest(client: Client, project: Project, key: ProjectKey, **payload: Any) -> None:
    post(client, project, build_envelope(("event", payload)), key.public_key)


class TestAuthentication:
    def test_anonymous_users_get_403(self, client: Client) -> None:
        assert client.get(reverse("api:projects"), secure=True).status_code == 403

    def test_signed_in_users_get_data(self, staff_client: Client) -> None:
        assert staff_client.get(reverse("api:projects"), secure=True).status_code == 200


class TestProjects:
    def test_counts_only_unresolved_issues(
        self, staff_client: Client, project: Project, project_key: ProjectKey
    ) -> None:
        ingest(staff_client, project, project_key, **error(frames=FRAMES))
        ingest(
            staff_client,
            project,
            project_key,
            **error("KeyError", frames=[{"module": "a", "function": "b", "in_app": True}]),
        )
        Issue.objects.filter(title__startswith="KeyError").update(status=IssueStatus.RESOLVED)

        [payload] = json_body(staff_client.get(reverse("api:projects"), secure=True))

        assert payload["unresolved_count"] == 1


class TestIssueList:
    def url(self, project: Project) -> str:
        return reverse("api:issues", args=[project.pk])

    def test_returns_grouped_issues_with_counts(
        self, staff_client: Client, project: Project, project_key: ProjectKey
    ) -> None:
        for _ in range(3):
            ingest(staff_client, project, project_key, **error(frames=FRAMES))

        [issue] = json_body(staff_client.get(self.url(project), secure=True))

        assert issue["times_seen"] == 3
        assert issue["culprit"] == "app.crud in get_cart"

    def test_histogram_has_one_bucket_per_hour(
        self, staff_client: Client, project: Project, project_key: ProjectKey
    ) -> None:
        """The stream renders a chart per row; a ragged array would misalign every one of them."""
        ingest(staff_client, project, project_key, **error(frames=FRAMES))

        [issue] = json_body(staff_client.get(self.url(project), secure=True))

        # 25, not 24: the window runs from the floor of 24h-ago to now, so the partial
        # current hour gets its own bucket rather than being dropped.
        assert len(issue["hourly"]) == 25
        assert sum(issue["hourly"]) == 1

    def test_events_older_than_the_window_are_not_counted_in_the_histogram(
        self, staff_client: Client, project: Project, project_key: ProjectKey
    ) -> None:
        old = (datetime.now(tz=UTC) - timedelta(days=3)).isoformat()
        ingest(staff_client, project, project_key, **error(frames=FRAMES), timestamp=old)

        [issue] = json_body(staff_client.get(self.url(project), secure=True))

        assert issue["times_seen"] == 1
        assert sum(issue["hourly"]) == 0

    def test_hides_resolved_issues_by_default(
        self, staff_client: Client, project: Project, project_key: ProjectKey
    ) -> None:
        ingest(staff_client, project, project_key, **error(frames=FRAMES))
        Issue.objects.update(status=IssueStatus.RESOLVED)

        assert json_body(staff_client.get(self.url(project), secure=True)) == []

    def test_status_all_shows_everything(
        self, staff_client: Client, project: Project, project_key: ProjectKey
    ) -> None:
        ingest(staff_client, project, project_key, **error(frames=FRAMES))
        Issue.objects.update(status=IssueStatus.RESOLVED)

        response = staff_client.get(f"{self.url(project)}?status=all", secure=True)

        assert len(json_body(response)) == 1

    def test_search_matches_title_and_culprit(
        self, staff_client: Client, project: Project, project_key: ProjectKey
    ) -> None:
        ingest(
            staff_client, project, project_key, **error("ValueError", "cart empty", frames=FRAMES)
        )

        assert len(json_body(staff_client.get(f"{self.url(project)}?q=cart", secure=True))) == 1
        assert len(json_body(staff_client.get(f"{self.url(project)}?q=get_cart", secure=True))) == 1
        assert len(json_body(staff_client.get(f"{self.url(project)}?q=nomatch", secure=True))) == 0

    def test_sorting_by_event_count(
        self, staff_client: Client, project: Project, project_key: ProjectKey
    ) -> None:
        other = [{"module": "app.rare", "function": "f", "in_app": True}]
        ingest(staff_client, project, project_key, **error("KeyError", frames=other))
        for _ in range(3):
            ingest(staff_client, project, project_key, **error(frames=FRAMES))

        response = staff_client.get(f"{self.url(project)}?sort=times_seen", secure=True)

        assert [row["times_seen"] for row in json_body(response)] == [3, 1]

    def test_does_not_leak_another_projects_issues(
        self,
        staff_client: Client,
        project: Project,
        project_key: ProjectKey,
        organization: Any,
    ) -> None:
        other = Project.objects.create(organization=organization, name="Other", slug="other")
        other_key = ProjectKey.objects.create(project=other)
        ingest(staff_client, other, other_key, **error(frames=FRAMES))

        assert json_body(staff_client.get(self.url(project), secure=True)) == []


class TestIssueDetail:
    def test_returns_the_latest_event_with_frames(
        self, staff_client: Client, project: Project, project_key: ProjectKey
    ) -> None:
        ingest(staff_client, project, project_key, **error(frames=FRAMES), release="v1")

        issue = Issue.objects.get()
        payload = json_body(staff_client.get(reverse("api:issue", args=[issue.pk]), secure=True))

        assert payload["issue"]["title"] == "ValueError: boom"
        assert payload["latest_event"]["release"] == "v1"
        assert payload["latest_event"]["exception"][0]["frames"][0]["in_app"] is True

    def test_exposes_the_fingerprint_components(
        self, staff_client: Client, project: Project, project_key: ProjectKey
    ) -> None:
        """So the UI can answer "why did these group?" without anyone reading the source."""
        ingest(staff_client, project, project_key, **error(frames=FRAMES))

        issue = Issue.objects.get()
        payload = json_body(staff_client.get(reverse("api:issue", args=[issue.pk]), secure=True))

        assert "app.crud:get_cart" in payload["issue"]["fingerprint_components"]

    def test_tag_distribution_is_a_percentage_of_events(
        self, staff_client: Client, project: Project, project_key: ProjectKey
    ) -> None:
        for endpoint in ("/a", "/a", "/a", "/b"):
            ingest(
                staff_client,
                project,
                project_key,
                **error(frames=FRAMES),
                tags={"endpoint": endpoint},
            )

        issue = Issue.objects.get()
        payload = json_body(staff_client.get(reverse("api:issue", args=[issue.pk]), secure=True))

        endpoints = payload["tags"]["endpoint"]
        assert endpoints[0] == {"value": "/a", "count": 3, "percentage": 75}

    def test_an_issue_with_no_events_does_not_500(
        self, staff_client: Client, project: Project
    ) -> None:
        now = datetime.now(tz=UTC)
        issue = Issue.objects.create(
            project=project, fingerprint="x" * 64, title="orphan", first_seen=now, last_seen=now
        )

        response = staff_client.get(reverse("api:issue", args=[issue.pk]), secure=True)

        assert response.status_code == 200
        assert json_body(response)["latest_event"] is None


class TestIssueWorkflow:
    def url(self, issue: Issue) -> str:
        return reverse("api:issue-status", args=[issue.pk])

    def issue(self, client: Client, project: Project, key: ProjectKey) -> Issue:
        ingest(client, project, key, **error(frames=FRAMES))
        return Issue.objects.get()

    def test_resolving_an_issue(
        self, staff_client: Client, project: Project, project_key: ProjectKey
    ) -> None:
        issue = self.issue(staff_client, project, project_key)

        response = staff_client.patch(
            self.url(issue),
            data={"status": "resolved"},
            content_type="application/json",
            secure=True,
        )

        assert response.status_code == 200
        assert json_body(response)["status"] == "resolved"
        issue.refresh_from_db()
        assert issue.status == IssueStatus.RESOLVED

    def test_ignoring_and_reopening(
        self, staff_client: Client, project: Project, project_key: ProjectKey
    ) -> None:
        issue = self.issue(staff_client, project, project_key)

        for wanted in ("ignored", "unresolved"):
            staff_client.patch(
                self.url(issue),
                data={"status": wanted},
                content_type="application/json",
                secure=True,
            )
            issue.refresh_from_db()
            assert issue.status == wanted

    def test_rejects_an_unknown_status(
        self, staff_client: Client, project: Project, project_key: ProjectKey
    ) -> None:
        issue = self.issue(staff_client, project, project_key)

        response = staff_client.patch(
            self.url(issue),
            data={"status": "deleted"},
            content_type="application/json",
            secure=True,
        )

        assert response.status_code == 400
        issue.refresh_from_db()
        assert issue.status == IssueStatus.UNRESOLVED

    def test_a_non_object_body_is_a_400_not_a_500(
        self, staff_client: Client, project: Project, project_key: ProjectKey
    ) -> None:
        """A JSON array parses to a list, and .get() on a list is an AttributeError."""
        issue = self.issue(staff_client, project, project_key)

        response = staff_client.patch(
            self.url(issue), data=[1, 2], content_type="application/json", secure=True
        )

        assert response.status_code == 400

    def test_anonymous_users_cannot_change_status(
        self, client: Client, project: Project, project_key: ProjectKey
    ) -> None:
        issue = self.issue(client, project, project_key)

        response = client.patch(
            self.url(issue),
            data={"status": "resolved"},
            content_type="application/json",
            secure=True,
        )

        assert response.status_code == 403
        issue.refresh_from_db()
        assert issue.status == IssueStatus.UNRESOLVED


class TestProjectManagement:
    """Everything that used to require the Django admin."""

    def test_creating_an_organization(self, staff_client: Client) -> None:
        response = staff_client.post(
            reverse("api:organizations"),
            data={"name": "Globex", "slug": "globex"},
            content_type="application/json",
            secure=True,
        )

        assert response.status_code == 201
        assert Organization.objects.filter(slug="globex").exists()

    def test_creating_a_project(self, staff_client: Client, organization: Organization) -> None:
        response = staff_client.post(
            reverse("api:projects"),
            data={
                "name": "Payments",
                "slug": "payments",
                "platform": "python",
                "organization_id": organization.pk,
            },
            content_type="application/json",
            secure=True,
        )

        assert response.status_code == 201
        assert Project.objects.filter(slug="payments").exists()

    def test_a_duplicate_slug_in_one_org_is_a_400_not_a_500(
        self, staff_client: Client, organization: Organization, project: Project
    ) -> None:
        response = staff_client.post(
            reverse("api:projects"),
            data={
                "name": "Clash",
                "slug": project.slug,
                "platform": "python",
                "organization_id": organization.pk,
            },
            content_type="application/json",
            secure=True,
        )

        assert response.status_code == 400

    def test_project_detail_exposes_the_dsn(
        self, staff_client: Client, project: Project, project_key: ProjectKey
    ) -> None:
        """The whole reason this page exists — wiring an SDK up without opening the admin."""
        response = staff_client.get(reverse("api:project", args=[project.pk]), secure=True)

        [key] = json_body(response)["keys"]
        assert key["public_key"] in key["dsn"]
        assert key["dsn"].endswith(f"/{project.pk}")

    def test_issuing_an_additional_key(
        self, staff_client: Client, project: Project, project_key: ProjectKey
    ) -> None:
        response = staff_client.post(
            reverse("api:project-keys", args=[project.pk]),
            data={"label": "rotation"},
            content_type="application/json",
            secure=True,
        )

        assert response.status_code == 201
        assert project.keys.count() == 2
        assert json_body(response)["public_key"] != project_key.public_key

    def test_revoking_a_key_stops_ingest_with_it(
        self, staff_client: Client, project: Project, project_key: ProjectKey
    ) -> None:
        """Revoking is a flag, not a delete: the audit trail of what was live matters."""
        staff_client.patch(
            reverse("api:key", args=[project_key.pk]),
            data={"is_active": False},
            content_type="application/json",
            secure=True,
        )

        response = post(
            staff_client,
            project,
            build_envelope(("event", error(frames=FRAMES))),
            project_key.public_key,
        )

        assert response.status_code == 401

    def test_anonymous_users_cannot_create_projects(
        self, client: Client, organization: Organization
    ) -> None:
        response = client.post(
            reverse("api:projects"),
            data={"name": "X", "slug": "x", "organization_id": organization.pk},
            content_type="application/json",
            secure=True,
        )

        assert response.status_code == 403
        assert not Project.objects.filter(slug="x").exists()
