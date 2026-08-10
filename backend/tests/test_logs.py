from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from apps.logs.models import LogRecord
from apps.projects.models import Project, ProjectKey
from apps.tracing.models import Transaction
from tests.conftest import build_envelope, json_body, post

pytestmark = pytest.mark.django_db

NOW = datetime.now(tz=UTC)
TRACE = "a" * 32


@pytest.fixture
def staff_client(client: Client) -> Client:
    User.objects.create_user("viewer", password="viewer-password")
    client.login(username="viewer", password="viewer-password")
    return client


def batch(*items: dict[str, Any], **envelope: Any) -> dict[str, Any]:
    return {
        "items": list(items),
        "environment": "production",
        "release": "demo@1.0.0",
        "server_name": "web-1",
        **envelope,
    }


def line(body: str, level: str = "info", **extra: Any) -> dict[str, Any]:
    return {"timestamp": NOW.isoformat(), "level": level, "body": body, **extra}


def send(client: Client, project: Project, key: ProjectKey, payload: dict[str, Any]) -> Any:
    return post(client, project, build_envelope(("log", payload)), key.public_key)


class TestIngest:
    def test_stores_a_batch_of_records(
        self, client: Client, project: Project, project_key: ProjectKey
    ) -> None:
        """One item carries many lines. One request per log line would make logging the
        slowest thing an application does."""
        response = send(client, project, project_key, batch(line("started"), line("finished")))

        assert json_body(response)["accepted"] == 2
        assert LogRecord.objects.count() == 2

    def test_envelope_level_fields_apply_to_every_record(
        self, client: Client, project: Project, project_key: ProjectKey
    ) -> None:
        send(client, project, project_key, batch(line("a"), line("b")))

        assert {log.release for log in LogRecord.objects.all()} == {"demo@1.0.0"}
        assert {log.environment for log in LogRecord.objects.all()} == {"production"}

    def test_records_carry_their_trace(
        self, client: Client, project: Project, project_key: ProjectKey
    ) -> None:
        send(client, project, project_key, batch(line("hi", trace_id=TRACE, span_id="b" * 16)))

        log = LogRecord.objects.get()
        assert log.trace_id == TRACE
        assert log.span_id == "b" * 16

    def test_an_empty_body_is_dropped(
        self, client: Client, project: Project, project_key: ProjectKey
    ) -> None:
        """A line with no message is a row nobody can read or search."""
        send(client, project, project_key, batch(line(""), line("real")))

        assert LogRecord.objects.count() == 1

    def test_an_unknown_level_falls_back_to_info(
        self, client: Client, project: Project, project_key: ProjectKey
    ) -> None:
        send(client, project, project_key, batch(line("hi", level="catastrophic")))

        assert LogRecord.objects.get().level == "info"

    def test_a_malformed_trace_id_is_dropped(
        self, client: Client, project: Project, project_key: ProjectKey
    ) -> None:
        send(client, project, project_key, batch(line("hi", trace_id="nonsense")))

        assert LogRecord.objects.get().trace_id == ""

    def test_nested_attributes_are_dropped(
        self, client: Client, project: Project, project_key: ProjectKey
    ) -> None:
        send(
            client,
            project,
            project_key,
            batch(line("hi", attributes={"user": "u1", "nested": {"a": 1}})),
        )

        assert LogRecord.objects.get().attributes == {"user": "u1"}

    def test_batch_size_is_capped(
        self, client: Client, project: Project, project_key: ProjectKey
    ) -> None:
        send(client, project, project_key, batch(*[line(f"line {n}") for n in range(700)]))

        assert LogRecord.objects.count() == 500

    def test_logs_ride_alongside_events_in_one_envelope(
        self, client: Client, project: Project, project_key: ProjectKey
    ) -> None:
        body = build_envelope(
            ("event", {"exception": {"values": [{"type": "ValueError", "value": "x"}]}}),
            ("log", batch(line("context for the error above"))),
        )

        response = post(client, project, body, project_key.public_key)

        assert json_body(response)["accepted"] == 2


class TestLogViewer:
    def url(self, project: Project) -> str:
        return reverse("api:logs", args=[project.pk])

    def test_newest_first(
        self, staff_client: Client, project: Project, project_key: ProjectKey
    ) -> None:
        """A log viewer opened during an incident asks what is happening, not what happened
        first."""
        send(
            staff_client,
            project,
            project_key,
            batch(
                {"timestamp": (NOW - timedelta(minutes=5)).isoformat(), "body": "older"},
                {"timestamp": NOW.isoformat(), "body": "newer"},
            ),
        )

        rows = json_body(staff_client.get(self.url(project), secure=True))

        assert [row["body"] for row in rows] == ["newer", "older"]

    def test_level_filter_includes_worse_levels(
        self, staff_client: Client, project: Project, project_key: ProjectKey
    ) -> None:
        """Levels are ordered. Filtering to exactly one hides the errors, which nobody means."""
        send(
            staff_client,
            project,
            project_key,
            batch(
                line("chatter", level="debug"),
                line("careful", level="warning"),
                line("broken", level="error"),
            ),
        )

        rows = json_body(staff_client.get(f"{self.url(project)}?level=warning", secure=True))

        assert sorted(row["body"] for row in rows) == ["broken", "careful"]

    def test_search_matches_body_and_logger(
        self, staff_client: Client, project: Project, project_key: ProjectKey
    ) -> None:
        send(
            staff_client,
            project,
            project_key,
            batch(line("payment declined"), line("all good", logger="app.cache")),
        )

        assert len(json_body(staff_client.get(f"{self.url(project)}?q=declined", secure=True))) == 1
        assert len(json_body(staff_client.get(f"{self.url(project)}?q=cache", secure=True))) == 1

    def test_filters_by_trace(
        self, staff_client: Client, project: Project, project_key: ProjectKey
    ) -> None:
        send(
            staff_client,
            project,
            project_key,
            batch(line("mine", trace_id=TRACE), line("theirs", trace_id="c" * 32)),
        )

        rows = json_body(staff_client.get(f"{self.url(project)}?trace_id={TRACE}", secure=True))

        assert [row["body"] for row in rows] == ["mine"]

    def test_requires_authentication(self, client: Client, project: Project) -> None:
        assert client.get(self.url(project), secure=True).status_code == 403


class TestTraceCorrelation:
    def transaction_payload(self, name: str, status: str = "ok") -> dict[str, Any]:
        return {
            "transaction": name,
            "start_timestamp": (NOW - timedelta(milliseconds=100)).isoformat(),
            "timestamp": NOW.isoformat(),
            "contexts": {"trace": {"trace_id": TRACE, "span_id": "b" * 16, "status": status}},
            "spans": [],
        }

    def test_a_trace_carries_everything_the_app_said_during_it(
        self, staff_client: Client, project: Project, project_key: ProjectKey
    ) -> None:
        body = build_envelope(
            ("transaction", self.transaction_payload("/checkout")),
            ("log", batch(line("cart loaded", trace_id=TRACE), line("charged", trace_id=TRACE))),
        )
        post(staff_client, project, body, project_key.public_key)

        trace = Transaction.objects.get()
        payload = json_body(staff_client.get(reverse("api:trace", args=[trace.pk]), secure=True))

        assert [log["body"] for log in payload["logs"]] == ["cart loaded", "charged"]

    def test_a_successful_request_still_carries_its_logs(
        self, staff_client: Client, project: Project, project_key: ProjectKey
    ) -> None:
        """The point of logging the success case: most requests succeed, and the explanation
        for the ones that do not usually lives in them."""
        body = build_envelope(
            ("transaction", self.transaction_payload("/healthz")),
            ("log", batch(line("ok", trace_id=TRACE))),
        )
        post(staff_client, project, body, project_key.public_key)

        trace = Transaction.objects.get()
        payload = json_body(staff_client.get(reverse("api:trace", args=[trace.pk]), secure=True))

        assert payload["status"] == "ok"
        assert len(payload["logs"]) == 1
