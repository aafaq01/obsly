"""Errors and traces are one story, joined by trace_id rather than by timestamp guessing."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from apps.events.models import Event
from apps.issues.models import Issue
from apps.projects.models import Project, ProjectKey
from apps.tracing.models import Transaction
from tests.conftest import build_envelope, json_body, post

pytestmark = pytest.mark.django_db

TRACE = "a" * 32
ROOT_SPAN = "b" * 16
NOW = datetime.now(tz=UTC)


@pytest.fixture
def staff_client(client: Client) -> Client:
    User.objects.create_user("viewer", password="viewer-password")
    client.login(username="viewer", password="viewer-password")
    return client


def error_payload(trace_id: str | None = TRACE, span_id: str = ROOT_SPAN) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "exception": {
            "values": [
                {
                    "type": "ValueError",
                    "value": "cart is empty",
                    "stacktrace": {
                        "frames": [{"module": "app.api", "function": "checkout", "in_app": True}]
                    },
                }
            ]
        }
    }
    if trace_id:
        payload["contexts"] = {"trace": {"trace_id": trace_id, "span_id": span_id}}
    return payload


def transaction_payload() -> dict[str, Any]:
    return {
        "transaction": "/checkout",
        "start_timestamp": (NOW - timedelta(milliseconds=300)).isoformat(),
        "timestamp": NOW.isoformat(),
        "contexts": {
            "trace": {
                "trace_id": TRACE,
                "span_id": ROOT_SPAN,
                "op": "http.server",
                "status": "internal_error",
            }
        },
        "spans": [],
    }


class TestExtraction:
    def test_stores_the_trace_id_from_the_error(
        self, client: Client, project: Project, project_key: ProjectKey
    ) -> None:
        post(client, project, build_envelope(("event", error_payload())), project_key.public_key)

        event = Event.objects.get()
        assert event.trace_id == TRACE
        assert event.span_id == ROOT_SPAN

    def test_an_error_outside_a_trace_has_no_trace_id(
        self, client: Client, project: Project, project_key: ProjectKey
    ) -> None:
        """Empty is honest. A fabricated id would point at a trace that does not exist."""
        post(
            client,
            project,
            build_envelope(("event", error_payload(trace_id=None))),
            project_key.public_key,
        )

        assert Event.objects.get().trace_id == ""

    @pytest.mark.parametrize("bad", ["not-hex", "abc", "z" * 32, "", 12345, None])
    def test_a_malformed_trace_id_is_dropped_not_stored(
        self, client: Client, project: Project, project_key: ProjectKey, bad: Any
    ) -> None:
        payload = error_payload()
        payload["contexts"]["trace"]["trace_id"] = bad

        post(client, project, build_envelope(("event", payload)), project_key.public_key)

        assert Event.objects.get().trace_id == ""


class TestTraceToErrors:
    def test_a_trace_lists_the_errors_that_happened_inside_it(
        self, staff_client: Client, project: Project, project_key: ProjectKey
    ) -> None:
        body = build_envelope(("transaction", transaction_payload()), ("event", error_payload()))
        post(staff_client, project, body, project_key.public_key)

        trace = Transaction.objects.get()
        payload = json_body(staff_client.get(reverse("api:trace", args=[trace.pk]), secure=True))

        [error] = payload["errors"]
        assert error["title"] == "ValueError: cart is empty"
        assert error["issue_id"] == Issue.objects.get().pk

    def test_errors_from_another_trace_are_not_included(
        self, staff_client: Client, project: Project, project_key: ProjectKey
    ) -> None:
        body = build_envelope(
            ("transaction", transaction_payload()),
            ("event", error_payload()),
            ("event", error_payload(trace_id="c" * 32)),
        )
        post(staff_client, project, body, project_key.public_key)

        trace = Transaction.objects.get()
        payload = json_body(staff_client.get(reverse("api:trace", args=[trace.pk]), secure=True))

        assert len(payload["errors"]) == 1

    def test_errors_from_another_project_are_not_included(
        self,
        staff_client: Client,
        project: Project,
        project_key: ProjectKey,
        organization: Any,
    ) -> None:
        """Two services can legitimately share a trace id; their errors are still not each
        other's to display."""
        other = Project.objects.create(organization=organization, name="Other", slug="other")
        other_key = ProjectKey.objects.create(project=other)

        post(
            staff_client,
            project,
            build_envelope(("transaction", transaction_payload())),
            project_key.public_key,
        )
        post(
            staff_client,
            other,
            build_envelope(("event", error_payload())),
            other_key.public_key,
        )

        trace = Transaction.objects.get()
        payload = json_body(staff_client.get(reverse("api:trace", args=[trace.pk]), secure=True))

        assert payload["errors"] == []


class TestErrorToTrace:
    def test_an_issue_links_to_the_trace_its_error_happened_in(
        self, staff_client: Client, project: Project, project_key: ProjectKey
    ) -> None:
        body = build_envelope(("transaction", transaction_payload()), ("event", error_payload()))
        post(staff_client, project, body, project_key.public_key)

        issue = Issue.objects.get()
        payload = json_body(staff_client.get(reverse("api:issue", args=[issue.pk]), secure=True))

        assert payload["trace"]["name"] == "/checkout"
        assert payload["trace"]["status"] == "internal_error"
        assert payload["latest_event"]["trace_id"] == TRACE

    def test_an_issue_with_no_trace_says_so_rather_than_guessing(
        self, staff_client: Client, project: Project, project_key: ProjectKey
    ) -> None:
        post(
            staff_client,
            project,
            build_envelope(("event", error_payload(trace_id=None))),
            project_key.public_key,
        )

        issue = Issue.objects.get()
        payload = json_body(staff_client.get(reverse("api:issue", args=[issue.pk]), secure=True))

        assert payload["trace"] is None

    def test_a_trace_id_with_no_matching_transaction_yields_no_link(
        self, staff_client: Client, project: Project, project_key: ProjectKey
    ) -> None:
        """The error was sampled but the transaction was not — a real and common case."""
        post(
            staff_client,
            project,
            build_envelope(("event", error_payload())),
            project_key.public_key,
        )

        issue = Issue.objects.get()
        payload = json_body(staff_client.get(reverse("api:issue", args=[issue.pk]), secure=True))

        assert payload["trace"] is None
