"""The database tier.

The two rankings are the point. A ranking by total time surfaces the query that runs
constantly; a ranking by p95 surfaces the one that is individually painful. They are usually
different statements with different fixes — a cache versus an index — and a page that shows one
and calls it "top queries" sends people to fix the wrong thing.
"""

from datetime import timedelta
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.api.database import table_of
from apps.projects.models import Project
from apps.tracing.models import Span, Transaction
from tests.conftest import json_body

pytestmark = pytest.mark.django_db

NOW = timezone.now()


def request_with(
    project: Project, spans: list[tuple[str, float]], *, op: str = "db.query", minutes_ago: int = 5
) -> Transaction:
    """One transaction carrying `spans` as (statement, duration) pairs."""
    when = NOW - timedelta(minutes=minutes_ago)
    index = Transaction.objects.count()
    txn = Transaction.objects.create(
        project=project,
        trace_id=f"{index:032x}",
        span_id=f"{index:016x}",
        name="/checkout",
        start_timestamp=when,
        timestamp=when,
        duration_ms=sum(duration for _, duration in spans) or 1.0,
        payload={},
    )
    Span.objects.bulk_create(
        Span(
            transaction=txn,
            trace_id=txn.trace_id,
            span_id=f"{Span.objects.count() + offset:016x}",
            op=op,
            description=statement,
            start_timestamp=when,
            timestamp=when,
            duration_ms=duration,
        )
        for offset, (statement, duration) in enumerate(spans)
    )
    return txn


def insights(client: Client, project: Project, period: str = "24h") -> Any:
    url = reverse("api:database", args=[project.pk])
    return json_body(client.get(f"{url}?period={period}", secure=True))


class TestTableParsing:
    @pytest.mark.parametrize(
        ("statement", "expected"),
        [
            ("SELECT * FROM carts WHERE id = %s", "carts"),
            ("select id from Orders", "orders"),
            ("INSERT INTO events (id) VALUES (%s)", "events"),
            ("UPDATE users SET name = %s", "users"),
            ("SELECT * FROM shop.items", "shop.items"),
            ('SELECT * FROM "carts"', "carts"),
            ("SELECT a FROM orders JOIN items ON x", "orders"),
        ],
    )
    def test_the_table_is_read_out_of_the_statement(self, statement: str, expected: str) -> None:
        assert table_of(statement) == expected

    @pytest.mark.parametrize("statement", ["", "BEGIN", "COMMIT", "SELECT 1"])
    def test_an_unrecognised_statement_names_no_table(self, statement: str) -> None:
        """A wrong table name sends somebody to index the wrong thing, which costs more than
        the row being absent."""
        assert table_of(statement) == ""


class TestRankings:
    def test_slowest_and_heaviest_are_different_questions(
        self, staff_client: Client, project: Project
    ) -> None:
        """The whole reason both exist. One statement is individually painful and rare; the
        other is individually trivial and constant."""
        request_with(project, [("SELECT * FROM analytics_rollup", 900.0)])
        for _ in range(60):
            request_with(project, [("SELECT * FROM carts WHERE id = %s", 20.0)])

        payload = insights(staff_client, project)

        assert payload["slowest"][0]["description"] == "SELECT * FROM analytics_rollup"
        assert payload["heaviest"][0]["description"] == "SELECT * FROM carts WHERE id = %s"

    def test_the_headline_counts_queries_and_the_requests_they_came_from(
        self, staff_client: Client, project: Project
    ) -> None:
        request_with(project, [("SELECT 1 FROM a", 5.0), ("SELECT 1 FROM b", 5.0)])
        request_with(project, [("SELECT 1 FROM a", 5.0)])

        headline = insights(staff_client, project)["headline"]

        assert headline["queries"] == 3
        assert headline["requests"] == 2
        assert headline["per_request"] == 1.5

    def test_only_database_spans_are_counted(self, staff_client: Client, project: Project) -> None:
        """A page called Database that counts http.client makes every number on it wrong."""
        request_with(project, [("SELECT 1 FROM a", 5.0)])
        request_with(project, [("POST payments.example.com", 500.0)], op="http.client")

        payload = insights(staff_client, project)

        assert payload["headline"]["queries"] == 1
        assert [row["description"] for row in payload["slowest"]] == ["SELECT 1 FROM a"]

    def test_a_statement_reports_which_table_it_touches(
        self, staff_client: Client, project: Project
    ) -> None:
        request_with(project, [("SELECT * FROM carts WHERE id = %s", 10.0)])

        assert insights(staff_client, project)["slowest"][0]["table"] == "carts"


class TestTables:
    def test_cost_is_rolled_up_per_table(self, staff_client: Client, project: Project) -> None:
        """A statement is the unit you fix; a table is the unit you reason about when deciding
        where an index goes."""
        request_with(
            project,
            [
                ("SELECT * FROM carts WHERE id = %s", 30.0),
                ("SELECT total FROM carts WHERE user_id = %s", 20.0),
                ("SELECT * FROM items WHERE cart_id = %s", 5.0),
            ],
        )

        tables = {row["table"]: row for row in insights(staff_client, project)["tables"]}

        assert tables["carts"]["total_ms"] == pytest.approx(50.0)
        assert tables["carts"]["statements"] == 2
        assert tables["items"]["total_ms"] == pytest.approx(5.0)

    def test_a_statement_with_no_readable_table_is_left_out(
        self, staff_client: Client, project: Project
    ) -> None:
        request_with(project, [("COMMIT", 1.0)])

        assert insights(staff_client, project)["tables"] == []


class TestRepeatedQueries:
    def test_a_query_run_once_per_row_is_surfaced(
        self, staff_client: Client, project: Project
    ) -> None:
        """Invisible in any ranking sorted by duration: each call is fast, which is exactly why
        the pattern survives review."""
        request_with(project, [("SELECT total FROM orders WHERE id = %s", 2.0)] * 25)

        [row] = insights(staff_client, project)["repeated"]

        assert row["per_request"] == 25.0
        assert row["count"] == 25

    def test_a_handful_of_calls_is_not_flagged(
        self, staff_client: Client, project: Project
    ) -> None:
        """A few lookups is a loop somebody wrote on purpose. A detector that cries wolf at
        three calls is one people stop reading."""
        request_with(project, [("SELECT * FROM carts WHERE id = %s", 2.0)] * 3)

        assert insights(staff_client, project)["repeated"] == []

    def test_the_wasted_time_is_what_collapsing_the_loop_would_give_back(
        self, staff_client: Client, project: Project
    ) -> None:
        """Every call after the first, at the cost of a call — not the whole total, because one
        query still has to run."""
        request_with(project, [("SELECT total FROM orders WHERE id = %s", 2.0)] * 25)

        [row] = insights(staff_client, project)["repeated"]

        assert row["total_ms"] == pytest.approx(50.0)
        assert row["wasted_ms"] == pytest.approx(48.0)

    def test_the_rate_is_per_request_not_across_the_window(
        self, staff_client: Client, project: Project
    ) -> None:
        """Ten requests each issuing one query is not an N+1, and a count that ignored the
        request it belonged to would call it one."""
        for _ in range(10):
            request_with(project, [("SELECT * FROM carts WHERE id = %s", 2.0)])

        assert insights(staff_client, project)["repeated"] == []


class TestSeries:
    def test_the_series_says_when_it_starts(self, staff_client: Client, project: Project) -> None:
        request_with(project, [("SELECT 1 FROM a", 5.0)])

        payload = insights(staff_client, project)

        assert payload["series_start"]
        assert sum(payload["series"]["throughput"]) == 1
        assert len(payload["series"]["p95"]) == len(payload["series"]["throughput"])

    def test_no_queries_is_an_empty_answer_not_an_error(
        self, staff_client: Client, project: Project
    ) -> None:
        payload = insights(staff_client, project)

        assert payload["headline"]["queries"] == 0
        assert payload["slowest"] == []
        assert payload["tables"] == []

    def test_requires_authentication(self, client: Client, project: Project) -> None:
        url = reverse("api:database", args=[project.pk])
        assert client.get(url, secure=True).status_code == 403
