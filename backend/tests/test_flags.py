"""Feature flags — FR-CTX-8.

Recording which flags were on is the easy half and on its own it is a list nobody reads. The
question people have after a rollout is *"is this only happening to people with X on?"*, and
answering it needs the comparison: how often the flag was on inside the issue against how often
it was on everywhere else.

A flag on for 100% of the failures and 4% of the traffic is a suspect. A flag on for 100% of
both is just a flag that is on, and a page that ranked it first would send people to revert the
wrong thing.
"""

import uuid
from datetime import timedelta
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.api.flags import suspects
from apps.events.models import Event
from apps.issues.models import Issue
from apps.projects.models import Project, ProjectKey
from tests.conftest import build_envelope, json_body, post

pytestmark = pytest.mark.django_db

NOW = timezone.now()


def make_issue(project: Project, fingerprint: str = "a" * 64, title: str = "boom") -> Issue:
    return Issue.objects.create(
        project=project,
        fingerprint=fingerprint,
        title=title,
        level="error",
        times_seen=0,
        first_seen=NOW,
        last_seen=NOW,
    )


def add_events(
    project: Project, issue: Issue | None, count: int, flags: dict[str, bool] | None = None
) -> None:
    Event.objects.bulk_create(
        Event(
            id=uuid.uuid4(),
            project=project,
            issue=issue,
            exception_type="ValueError",
            exception_value="boom",
            level="error",
            timestamp=NOW - timedelta(minutes=1),
            flags=dict(flags or {}),
            payload={},
        )
        for _ in range(count)
    )


class TestSuspectRanking:
    def test_a_flag_on_only_inside_the_issue_is_the_suspect(self, project: Project) -> None:
        issue = make_issue(project)
        add_events(project, issue, 20, {"new-checkout": True})
        # The rest of the project, mostly without it.
        other = make_issue(project, fingerprint="b" * 64, title="unrelated")
        add_events(project, other, 100, {"new-checkout": False})

        [row] = suspects(issue)

        assert row["flag"] == "new-checkout"
        assert row["issue_rate"] == 1.0
        assert row["baseline_rate"] == 0.0
        assert row["lift"] == 1.0

    def test_a_flag_on_everywhere_is_not_a_suspect(self, project: Project) -> None:
        """The whole point of the comparison. Ranking by "was it on" would put a flag that is
        on for everybody at the top of every issue in the project."""
        issue = make_issue(project)
        add_events(project, issue, 20, {"always-on": True})
        other = make_issue(project, fingerprint="b" * 64)
        add_events(project, other, 100, {"always-on": True})

        [row] = suspects(issue)

        assert row["lift"] == 0.0

    def test_the_bigger_gap_ranks_first(self, project: Project) -> None:
        issue = make_issue(project)
        add_events(project, issue, 20, {"new-checkout": True, "always-on": True})
        other = make_issue(project, fingerprint="b" * 64)
        add_events(project, other, 100, {"new-checkout": False, "always-on": True})

        ranked = [row["flag"] for row in suspects(issue)]

        assert ranked[0] == "new-checkout"

    def test_no_baseline_is_not_a_zero_baseline(self, project: Project) -> None:
        """A flag nobody else reported cannot be compared. Scoring it against an assumed zero
        would rank the least-known flag highest, which is exactly backwards."""
        issue = make_issue(project)
        add_events(project, issue, 20, {"only-here": True})

        [row] = suspects(issue)

        assert row["baseline_rate"] is None
        assert row["lift"] is None
        assert row["baseline_events"] == 0

    def test_an_uncomparable_flag_sorts_below_a_measured_one(self, project: Project) -> None:
        """ "We cannot tell yet" is a different answer from "not implicated", so it is kept and
        ranked last rather than dropped."""
        issue = make_issue(project)
        add_events(project, issue, 20, {"measured": True, "only-here": True})
        other = make_issue(project, fingerprint="b" * 64)
        add_events(project, other, 100, {"measured": False})

        ranked = [row["flag"] for row in suspects(issue)]

        assert ranked == ["measured", "only-here"]

    def test_too_few_events_is_not_evidence(self, project: Project) -> None:
        """Three failures that happen to share a flag is a coincidence, and presenting it as a
        suspect sends somebody to revert the wrong thing."""
        issue = make_issue(project)
        add_events(project, issue, 3, {"new-checkout": True})

        assert suspects(issue) == []

    def test_an_issue_with_no_flags_reports_nothing(self, project: Project) -> None:
        issue = make_issue(project)
        add_events(project, issue, 20)

        assert suspects(issue) == []


class TestIngest:
    def error(self, **extra: Any) -> dict[str, Any]:
        return {"exception": {"values": [{"type": "ValueError", "value": "boom"}]}, **extra}

    def test_flags_reach_their_own_column(
        self, client: Client, project: Project, project_key: ProjectKey
    ) -> None:
        payload = self.error(flags={"new-checkout": True, "legacy-pricing": False})

        post(client, project, build_envelope(("event", payload)), project_key.public_key)

        assert Event.objects.get().flags == {"new-checkout": True, "legacy-pricing": False}

    def test_an_evaluation_log_is_accepted_in_list_form(
        self, client: Client, project: Project, project_key: ProjectKey
    ) -> None:
        """The wire shape an ordered evaluation log naturally takes, and the one the
        OpenFeature-style integrations emit."""
        payload = self.error(
            flags=[{"flag": "new-checkout", "result": True}, {"flag": "beta", "result": False}]
        )

        post(client, project, build_envelope(("event", payload)), project_key.public_key)

        assert Event.objects.get().flags == {"new-checkout": True, "beta": False}

    @pytest.mark.parametrize("value", ["yes", 1, None, {"on": True}])
    def test_a_non_boolean_is_dropped_rather_than_coerced(
        self, client: Client, project: Project, project_key: ProjectKey, value: Any
    ) -> None:
        """A flag is a decision the application already made. Coercing "yes" to True records an
        outcome nobody chose, and it would then be counted as evidence."""
        payload = self.error(flags={"good": True, "weird": value})

        post(client, project, build_envelope(("event", payload)), project_key.public_key)

        assert Event.objects.get().flags == {"good": True}

    def test_the_log_is_bounded(
        self, client: Client, project: Project, project_key: ProjectKey
    ) -> None:
        payload = self.error(flags={f"flag-{index}": True for index in range(200)})

        post(client, project, build_envelope(("event", payload)), project_key.public_key)

        assert len(Event.objects.get().flags) == 100

    def test_an_event_without_flags_is_unaffected(
        self, client: Client, project: Project, project_key: ProjectKey
    ) -> None:
        post(client, project, build_envelope(("event", self.error())), project_key.public_key)

        assert Event.objects.get().flags == {}


class TestApi:
    def url(self, project: Project) -> str:
        return reverse("api:flags", args=[project.pk])

    def test_the_project_reports_which_flags_it_sees(
        self, staff_client: Client, project: Project
    ) -> None:
        """The first thing anyone asks after wiring the SDK up is whether it is arriving."""
        issue = make_issue(project)
        add_events(project, issue, 10, {"new-checkout": True})
        add_events(project, issue, 10, {"new-checkout": False})

        [row] = json_body(staff_client.get(self.url(project), secure=True))["flags"]

        assert row["flag"] == "new-checkout"
        assert row["seen"] == 20
        assert row["on"] == 10
        assert row["rate"] == 0.5

    def test_a_flag_can_be_asked_what_it_broke(
        self, staff_client: Client, project: Project
    ) -> None:
        """The other direction of the same question, asked while rolling one out — and not
        answerable from a per-issue view without opening every issue in turn."""
        issue = make_issue(project, title="ValueError in checkout")
        add_events(project, issue, 10, {"new-checkout": True})

        payload = json_body(staff_client.get(f"{self.url(project)}?flag=new-checkout", secure=True))

        assert [row["title"] for row in payload["issues"]] == ["ValueError in checkout"]
        assert payload["issues"][0]["events_with_flag"] == 10

    def test_an_issue_where_the_flag_was_off_is_not_listed(
        self, staff_client: Client, project: Project
    ) -> None:
        issue = make_issue(project)
        add_events(project, issue, 10, {"new-checkout": False})

        payload = json_body(staff_client.get(f"{self.url(project)}?flag=new-checkout", secure=True))

        assert payload["issues"] == []

    def test_no_flags_is_an_empty_answer_not_an_error(
        self, staff_client: Client, project: Project
    ) -> None:
        assert json_body(staff_client.get(self.url(project), secure=True))["flags"] == []

    def test_requires_authentication(self, client: Client, project: Project) -> None:
        assert client.get(self.url(project), secure=True).status_code == 403
