"""Performance detectors.

The difference between a tracing tool and an observability one: a waterfall shows a human 25
identical queries and waits for them to notice.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from django.test import Client

from apps.issues.models import Issue, IssueCategory, IssueStatus
from apps.projects.models import Project, ProjectKey
from tests.conftest import build_envelope, post

pytestmark = pytest.mark.django_db

NOW = datetime.now(tz=UTC)
TRACE = "a" * 32


def span(description: str, ms: float, op: str = "db.query", index: int = 0) -> dict[str, Any]:
    start = NOW - timedelta(milliseconds=ms)
    return {
        "span_id": f"{index:016x}",
        "parent_span_id": "b" * 16,
        "op": op,
        "description": description,
        "status": "ok",
        "start_timestamp": start.isoformat(),
        "timestamp": NOW.isoformat(),
    }


def transaction(name: str, spans: list[dict[str, Any]], trace: str = TRACE) -> dict[str, Any]:
    return {
        "transaction": name,
        "start_timestamp": (NOW - timedelta(milliseconds=500)).isoformat(),
        "timestamp": NOW.isoformat(),
        "contexts": {"trace": {"trace_id": trace, "span_id": "b" * 16, "status": "ok"}},
        "spans": spans,
    }


def send(client: Client, project: Project, key: ProjectKey, payload: dict[str, Any]) -> None:
    post(client, project, build_envelope(("transaction", payload)), key.public_key)


def repeated(statement: str, count: int, ms: float) -> list[dict[str, Any]]:
    return [span(statement, ms, index=n) for n in range(count)]


class TestNPlusOne:
    def test_files_an_issue_for_a_repeated_query(
        self, client: Client, project: Project, project_key: ProjectKey
    ) -> None:
        send(
            client,
            project,
            project_key,
            transaction("/report", repeated("SELECT total FROM orders WHERE id = %s", 25, 10.0)),
        )

        issue = Issue.objects.get()
        assert issue.category == IssueCategory.PERFORMANCE
        assert issue.issue_type == "n_plus_one_queries"
        assert "SELECT total FROM orders" in issue.title
        assert issue.culprit == "/report"

    def test_evidence_answers_what_how_bad_and_where(
        self, client: Client, project: Project, project_key: ProjectKey
    ) -> None:
        """A detector that cannot answer all three is not worth firing."""
        send(
            client,
            project,
            project_key,
            transaction("/report", repeated("SELECT 1", 20, 10.0)),
        )

        evidence = Issue.objects.get().evidence
        assert evidence["description"] == "SELECT 1"
        assert evidence["repeat_count"] == 20
        assert evidence["transaction"] == "/report"
        assert evidence["trace_id"] == TRACE

    def test_wasted_time_excludes_the_one_query_you_would_keep(
        self, client: Client, project: Project, project_key: ProjectKey
    ) -> None:
        """Reporting the whole total overstates the saving, and loses trust the first time
        somebody checks."""
        send(client, project, project_key, transaction("/r", repeated("SELECT 1", 10, 10.0)))

        evidence = Issue.objects.get().evidence
        assert evidence["total_ms"] == pytest.approx(100, abs=2)
        assert evidence["wasted_ms"] == pytest.approx(90, abs=2)

    def test_a_few_repeats_are_not_an_n_plus_one(
        self, client: Client, project: Project, project_key: ProjectKey
    ) -> None:
        """A handful is a loop somebody wrote on purpose."""
        send(client, project, project_key, transaction("/r", repeated("SELECT 1", 4, 30.0)))

        assert Issue.objects.count() == 0

    def test_many_but_trivial_repeats_are_not_worth_reporting(
        self, client: Client, project: Project, project_key: ProjectKey
    ) -> None:
        """Ten repeats of a 0.1ms cached read is not a problem worth a human's morning, and a
        detector that fires on healthy applications teaches people to ignore it."""
        send(client, project, project_key, transaction("/r", repeated("SELECT 1", 15, 0.1)))

        assert Issue.objects.count() == 0

    def test_different_statements_are_not_grouped_together(
        self, client: Client, project: Project, project_key: ProjectKey
    ) -> None:
        spans = repeated("SELECT a", 6, 10.0) + repeated("SELECT b", 6, 10.0)

        send(client, project, project_key, transaction("/r", spans))

        assert Issue.objects.count() == 0

    def test_the_same_query_on_two_endpoints_is_two_issues(
        self, client: Client, project: Project, project_key: ProjectKey
    ) -> None:
        """Different callers, different fix."""
        send(client, project, project_key, transaction("/a", repeated("SELECT 1", 12, 10.0)))
        send(
            client,
            project,
            project_key,
            transaction("/b", repeated("SELECT 1", 12, 10.0), trace="c" * 32),
        )

        assert Issue.objects.count() == 2

    def test_recurrence_increments_rather_than_duplicating(
        self, client: Client, project: Project, project_key: ProjectKey
    ) -> None:
        for trace in ("a" * 32, "c" * 32, "d" * 32):
            send(
                client,
                project,
                project_key,
                transaction("/r", repeated("SELECT 1", 12, 10.0), trace=trace),
            )

        issue = Issue.objects.get()
        assert issue.times_seen == 3

    def test_it_reopens_when_it_comes_back(
        self, client: Client, project: Project, project_key: ProjectKey
    ) -> None:
        """The same regression rule as an error, because it is the same Issue table."""
        send(client, project, project_key, transaction("/r", repeated("SELECT 1", 12, 10.0)))
        Issue.objects.update(status=IssueStatus.RESOLVED)

        send(
            client,
            project,
            project_key,
            transaction("/r", repeated("SELECT 1", 12, 10.0), trace="c" * 32),
        )

        assert Issue.objects.get().status == IssueStatus.UNRESOLVED

    def test_non_database_spans_are_ignored(
        self, client: Client, project: Project, project_key: ProjectKey
    ) -> None:
        spans = [span("render row", 10.0, op="ui.render", index=n) for n in range(20)]

        send(client, project, project_key, transaction("/r", spans))

        assert Issue.objects.count() == 0


class TestSlowQuery:
    def test_files_an_issue_for_a_single_slow_query(
        self, client: Client, project: Project, project_key: ProjectKey
    ) -> None:
        send(
            client,
            project,
            project_key,
            transaction("/dashboard", [span("SELECT * FROM analytics_rollup", 2500.0)]),
        )

        issue = Issue.objects.get()
        assert issue.issue_type == "slow_db_query"
        assert issue.evidence["total_ms"] == pytest.approx(2500, abs=5)

    def test_a_fast_query_is_not_reported(
        self, client: Client, project: Project, project_key: ProjectKey
    ) -> None:
        send(client, project, project_key, transaction("/x", [span("SELECT 1", 40.0)]))

        assert Issue.objects.count() == 0


class TestSeparation:
    def test_performance_issues_are_not_filed_at_error_level(
        self, client: Client, project: Project, project_key: ProjectKey
    ) -> None:
        """Filing a slow query beside a crash is how an issue stream stops being a priority
        list."""
        send(client, project, project_key, transaction("/r", repeated("SELECT 1", 12, 10.0)))

        assert Issue.objects.get().level == "warning"

    def test_a_detector_failure_does_not_lose_the_transaction(
        self,
        client: Client,
        project: Project,
        project_key: ProjectKey,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The transaction is already stored; a detector that cannot run costs an insight
        rather than a measurement."""
        from apps.tracing.models import Transaction

        def explode(*args: Any, **kwargs: Any) -> None:
            raise RuntimeError("detector is broken")

        monkeypatch.setattr("apps.ingest.views.detect_performance_issues", explode)

        send(client, project, project_key, transaction("/r", repeated("SELECT 1", 12, 10.0)))

        assert Transaction.objects.count() == 1
        assert Issue.objects.count() == 0
