from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from django.test import Client

from apps.events.models import Event
from apps.issues.fingerprint import compute, normalize_value
from apps.issues.models import Issue, IssueStatus
from apps.projects.models import Project, ProjectKey
from tests.conftest import build_envelope
from tests.test_ingest_view import post

pytestmark = pytest.mark.django_db


def error(
    exc_type: str = "ValueError",
    value: str = "boom",
    frames: list[dict[str, Any]] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    entry: dict[str, Any] = {"type": exc_type, "value": value}
    if frames is not None:
        entry["stacktrace"] = {"frames": frames}
    return {"exception": {"values": [entry]}, **extra}


FRAMES: list[dict[str, Any]] = [
    {"module": "app.api", "function": "checkout", "lineno": 10, "in_app": True},
    {"module": "app.crud", "function": "get_cart", "lineno": 42, "in_app": True},
]


class TestNormalisation:
    @pytest.mark.parametrize(
        ("first", "second"),
        [
            ("Timeout after 3021ms", "Timeout after 4102ms"),
            (
                "user 6ba7b810-9dad-11d1-80b4-00c04fd430c8 missing",
                "user 123e4567-e89b-12d3-a456-426614174000 missing",
            ),
            ("bad pointer 0xdeadbeef", "bad pointer 0xcafef00d"),
            ("key 'session_a1' not found", "key 'session_b2' not found"),
        ],
    )
    def test_values_that_differ_only_by_a_variable_normalise_together(
        self, first: str, second: str
    ) -> None:
        assert normalize_value(first) == normalize_value(second)

    def test_genuinely_different_messages_stay_different(self) -> None:
        assert normalize_value("disk full") != normalize_value("permission denied")


class TestFingerprint:
    def test_same_exception_and_frames_group_together(self) -> None:
        first, _ = compute(error(frames=FRAMES))
        second, _ = compute(error(value="different message entirely", frames=FRAMES))

        assert first == second, "the same code path failing is the same bug"

    def test_line_numbers_do_not_affect_grouping(self) -> None:
        """Adding an import shifts every line below it. Grouping on line numbers would reopen
        every issue in the file on the next deploy."""
        shifted = [{**frame, "lineno": frame["lineno"] + 7} for frame in FRAMES]

        assert compute(error(frames=FRAMES))[0] == compute(error(frames=shifted))[0]

    def test_a_different_code_path_is_a_different_issue(self) -> None:
        other = [{"module": "app.billing", "function": "charge", "in_app": True}]

        assert compute(error(frames=FRAMES))[0] != compute(error(frames=other))[0]

    def test_a_different_exception_type_is_a_different_issue(self) -> None:
        assert compute(error(frames=FRAMES))[0] != compute(error("KeyError", frames=FRAMES))[0]

    def test_system_frames_are_ignored_when_in_app_frames_exist(self) -> None:
        """Two deploys of a dependency must not split one issue in two."""
        with_lib = [*FRAMES, {"module": "psycopg.v1", "function": "execute", "in_app": False}]
        other_lib = [*FRAMES, {"module": "psycopg.v2", "function": "execute", "in_app": False}]

        assert compute(error(frames=with_lib))[0] == compute(error(frames=other_lib))[0]

    def test_falls_back_to_all_frames_when_none_are_in_app(self) -> None:
        """An SDK that cannot classify frames should still group, just less precisely."""
        frames = [{"module": "somelib", "function": "run", "in_app": False}]

        assert compute(error(frames=frames))[0] != compute(error(frames=[]))[0]

    def test_falls_back_to_the_message_without_a_stacktrace(self) -> None:
        assert compute(error(value="a"))[0] != compute(error(value="b"))[0]

    def test_an_explicit_fingerprint_wins(self) -> None:
        """The caller telling us they know better than the heuristic."""
        first, _ = compute(error(frames=FRAMES, fingerprint=["payments-down"]))
        second, _ = compute(error("KeyError", "x", frames=[], fingerprint=["payments-down"]))

        assert first == second

    def test_components_are_returned_so_the_grouping_is_inspectable(self) -> None:
        """A grouping decision nobody can inspect is one nobody can trust."""
        _, components = compute(error(frames=FRAMES))

        assert "app.crud:get_cart" in components

    def test_messages_group_when_only_a_number_differs(self) -> None:
        assert (
            compute({"message": "retry 1 failed"})[0] == compute({"message": "retry 9 failed"})[0]
        )


class TestIssueCreation:
    def send(self, client: Client, project: Project, key: ProjectKey, **payload: Any) -> None:
        post(client, project, build_envelope(("event", payload)), key.public_key)

    def test_repeated_errors_become_one_issue_with_a_count(
        self, client: Client, project: Project, project_key: ProjectKey
    ) -> None:
        for _ in range(5):
            self.send(client, project, project_key, **error(frames=FRAMES))

        issue = Issue.objects.get()
        assert issue.times_seen == 5
        assert Event.objects.count() == 5

    def test_different_bugs_become_different_issues(
        self, client: Client, project: Project, project_key: ProjectKey
    ) -> None:
        self.send(client, project, project_key, **error(frames=FRAMES))
        self.send(
            client,
            project,
            project_key,
            **error("KeyError", frames=[{"module": "app.b", "function": "f", "in_app": True}]),
        )

        assert Issue.objects.count() == 2

    def test_events_are_linked_to_their_issue(
        self, client: Client, project: Project, project_key: ProjectKey
    ) -> None:
        self.send(client, project, project_key, **error(frames=FRAMES))

        assert Event.objects.get().issue == Issue.objects.get()

    def test_issue_carries_the_title_and_culprit(
        self, client: Client, project: Project, project_key: ProjectKey
    ) -> None:
        self.send(client, project, project_key, **error("ValueError", "cart empty", frames=FRAMES))

        issue = Issue.objects.get()
        assert issue.title == "ValueError: cart empty"
        assert issue.culprit == "app.crud in get_cart"

    def test_two_projects_do_not_share_an_issue(
        self, client: Client, project: Project, project_key: ProjectKey, organization: Any
    ) -> None:
        """The same library bug in two projects is two problems with two owners."""
        other = Project.objects.create(organization=organization, name="Other", slug="other")
        other_key = ProjectKey.objects.create(project=other)

        self.send(client, project, project_key, **error(frames=FRAMES))
        self.send(client, other, other_key, **error(frames=FRAMES))

        assert Issue.objects.count() == 2

    def test_first_and_last_seen_track_the_window(
        self, client: Client, project: Project, project_key: ProjectKey
    ) -> None:
        now = datetime.now(tz=UTC)
        for offset in (timedelta(hours=-3), timedelta(hours=-1), timedelta(hours=-2)):
            self.send(
                client,
                project,
                project_key,
                **error(frames=FRAMES),
                timestamp=(now + offset).isoformat(),
            )

        issue = Issue.objects.get()
        assert issue.first_seen < issue.last_seen
        assert (issue.last_seen - issue.first_seen) > timedelta(hours=1)

    def test_a_late_event_widens_first_seen_without_moving_last_seen_back(
        self, client: Client, project: Project, project_key: ProjectKey
    ) -> None:
        """Offline buffering delivers yesterday's crash today. It must not rewrite 'last seen'."""
        now = datetime.now(tz=UTC)
        self.send(client, project, project_key, **error(frames=FRAMES), timestamp=now.isoformat())
        recent = Issue.objects.get().last_seen

        self.send(
            client,
            project,
            project_key,
            **error(frames=FRAMES),
            timestamp=(now - timedelta(days=2)).isoformat(),
        )

        issue = Issue.objects.get()
        assert issue.last_seen == recent
        assert issue.first_seen < recent

    def test_an_event_on_a_resolved_issue_reopens_it(
        self, client: Client, project: Project, project_key: ProjectKey
    ) -> None:
        """Staying resolved would hide the regression, which is the whole point of resolving."""
        self.send(client, project, project_key, **error(frames=FRAMES))
        Issue.objects.update(status=IssueStatus.RESOLVED)

        self.send(client, project, project_key, **error(frames=FRAMES))

        assert Issue.objects.get().status == IssueStatus.UNRESOLVED

    def test_grouping_failure_does_not_lose_the_event(
        self,
        client: Client,
        project: Project,
        project_key: ProjectKey,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """An ungrouped event can be regrouped later. A lost event is gone."""

        def explode(events: list[Event]) -> None:
            raise RuntimeError("fingerprinting is broken")

        monkeypatch.setattr("apps.ingest.views.assign_issues", explode)

        response = post(
            client, project, build_envelope(("event", error(frames=FRAMES))), project_key.public_key
        )

        assert response.status_code == 200
        assert Event.objects.count() == 1
        assert Issue.objects.count() == 0
