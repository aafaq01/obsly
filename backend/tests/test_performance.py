from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from apps.projects.models import Project, ProjectKey
from apps.tracing.models import SpanStatus, Transaction
from tests.conftest import json_body

pytestmark = pytest.mark.django_db

NOW = datetime.now(tz=UTC)


@pytest.fixture
def staff_client(client: Client) -> Client:
    User.objects.create_user("viewer", password="viewer-password")
    client.login(username="viewer", password="viewer-password")
    return client


def make(
    project: Project,
    name: str,
    durations: list[float],
    status: str = SpanStatus.OK,
    minutes_ago: int = 5,
) -> None:
    when = NOW - timedelta(minutes=minutes_ago)
    Transaction.objects.bulk_create(
        Transaction(
            project=project,
            trace_id=f"{index:032x}",
            span_id=f"{index:016x}",
            name=name,
            status=status,
            start_timestamp=when,
            timestamp=when,
            duration_ms=duration,
            payload={},
        )
        for index, duration in enumerate(durations)
    )


def summary(client: Client, project: Project, period: str = "24h") -> Any:
    url = reverse("api:performance", args=[project.pk])
    return json_body(client.get(f"{url}?period={period}", secure=True))


class TestPercentiles:
    def test_percentiles_expose_a_tail_the_mean_hides(
        self, staff_client: Client, project: Project
    ) -> None:
        """5% of requests take 8 seconds. The mean (419ms) reads as "a bit slow"; the p99 says
        one request in a hundred is unusable, which is the true statement."""
        durations = [20.0] * 95 + [8000.0] * 5
        mean = sum(durations) / len(durations)

        make(project, "/checkout", durations)

        [row] = summary(staff_client, project)["endpoints"]

        assert row["p50"] == pytest.approx(20, abs=1)
        assert row["p99"] > 7000
        assert row["p50"] < mean < row["p99"], "the mean sits where nobody's request landed"

    def test_percentiles_are_interpolated_not_nearest_sample(
        self, staff_client: Client, project: Project
    ) -> None:
        """PERCENTILE_CONT interpolates between samples. With one outlier in a hundred the p99
        lands just above the fast cluster rather than jumping to the outlier — which is the
        honest answer, because 99% of those requests really were fast."""
        make(project, "/checkout", [20.0] * 99 + [8000.0])

        [row] = summary(staff_client, project)["endpoints"]

        assert 20 < row["p99"] < 200

    def test_reports_every_percentile(self, staff_client: Client, project: Project) -> None:
        make(project, "/api", [float(n) for n in range(1, 101)])

        [row] = summary(staff_client, project)["endpoints"]

        assert row["p50"] == pytest.approx(50.5, abs=1)
        assert row["p75"] == pytest.approx(75.25, abs=1)
        assert row["p95"] == pytest.approx(95.05, abs=1)
        assert row["p99"] == pytest.approx(99.01, abs=1)

    def test_ranks_by_total_time_not_by_slowest(
        self, staff_client: Client, project: Project
    ) -> None:
        """The slowest endpoint is often one nobody calls; the one burning the most time is
        the one worth fixing first."""
        make(project, "/rare-but-slow", [5000.0])
        make(project, "/hot-path", [100.0] * 200)

        names = [row["name"] for row in summary(staff_client, project)["endpoints"]]

        assert names[0] == "/hot-path"

    def test_failure_rate_counts_only_internal_errors(
        self, staff_client: Client, project: Project
    ) -> None:
        make(project, "/api", [10.0] * 8)
        make(project, "/api", [10.0, 10.0], status=SpanStatus.INTERNAL_ERROR)
        make(project, "/api", [10.0] * 5, status=SpanStatus.NOT_FOUND)

        [row] = summary(staff_client, project)["endpoints"]

        # 2 failures out of 15 — a 404 is the client's problem, not an outage.
        assert row["count"] == 15
        assert row["failure_rate"] == pytest.approx(2 / 15, abs=0.001)

    def test_throughput_is_per_minute_over_the_window(
        self, staff_client: Client, project: Project
    ) -> None:
        make(project, "/api", [10.0] * 1440)

        [row] = summary(staff_client, project)["endpoints"]

        assert row["throughput_per_minute"] == pytest.approx(1.0, abs=0.01)

    def test_the_window_excludes_older_transactions(
        self, staff_client: Client, project: Project
    ) -> None:
        make(project, "/api", [10.0] * 5, minutes_ago=5)
        make(project, "/api", [10.0] * 100, minutes_ago=60 * 48)

        [row] = summary(staff_client, project)["endpoints"]

        assert row["count"] == 5

    def test_an_unknown_period_falls_back_instead_of_erroring(
        self, staff_client: Client, project: Project
    ) -> None:
        make(project, "/api", [10.0])

        payload = summary(staff_client, project, period="wat")

        assert payload["summary"]["transactions"] == 1

    def test_buckets_are_zero_filled(self, staff_client: Client, project: Project) -> None:
        """A quiet bucket must be a gap in the chart, not a missing bar that shifts every other."""
        make(project, "/api", [10.0] * 3)

        payload = summary(staff_client, project)["summary"]

        assert len(payload["series"]) >= 24
        assert sum(payload["series"]) == 3
        assert payload["bucket_seconds"] == 3600

    def test_a_short_window_buckets_by_minute(self, staff_client: Client, project: Project) -> None:
        """An hour bucketed hourly is one bar, which says nothing."""
        make(project, "/api", [10.0] * 3, minutes_ago=2)

        payload = summary(staff_client, project, period="15m")["summary"]

        assert payload["bucket_seconds"] == 60
        assert len(payload["series"]) >= 15

    def test_the_shortest_window_buckets_by_second(
        self, staff_client: Client, project: Project
    ) -> None:
        make(project, "/api", [10.0], minutes_ago=0)

        payload = summary(staff_client, project, period="1m")["summary"]

        assert payload["bucket_seconds"] == 1
        assert len(payload["series"]) >= 60

    def test_no_data_is_an_empty_answer_not_an_error(
        self, staff_client: Client, project: Project
    ) -> None:
        payload = summary(staff_client, project)

        assert payload["endpoints"] == []
        assert payload["summary"]["failure_rate"] == 0.0

    def test_requires_authentication(self, client: Client, project: Project) -> None:
        response = client.get(reverse("api:performance", args=[project.pk]), secure=True)

        assert response.status_code == 403


class TestTraces:
    def test_lists_traces_slowest_first(self, staff_client: Client, project: Project) -> None:
        """Sorted by time shows what happened last; sorted by duration shows what to look at."""
        make(project, "/slow", [900.0])
        make(project, "/fast", [5.0])

        rows = json_body(staff_client.get(reverse("api:traces", args=[project.pk]), secure=True))

        assert [row["name"] for row in rows] == ["/slow", "/fast"]

    def test_can_sort_by_recency_instead(self, staff_client: Client, project: Project) -> None:
        make(project, "/slow", [900.0], minutes_ago=30)
        make(project, "/fast", [5.0], minutes_ago=1)

        url = reverse("api:traces", args=[project.pk])
        rows = json_body(staff_client.get(f"{url}?sort=recent", secure=True))

        assert rows[0]["name"] == "/fast"

    def test_filters_to_failures(self, staff_client: Client, project: Project) -> None:
        make(project, "/ok", [10.0])
        make(project, "/broken", [10.0], status=SpanStatus.INTERNAL_ERROR)

        url = reverse("api:traces", args=[project.pk])
        rows = json_body(staff_client.get(f"{url}?status=failed", secure=True))

        assert [row["name"] for row in rows] == ["/broken"]

    def test_filters_to_one_endpoint(self, staff_client: Client, project: Project) -> None:
        make(project, "/a", [10.0] * 3)
        make(project, "/b", [10.0])

        url = reverse("api:traces", args=[project.pk])
        rows = json_body(staff_client.get(f"{url}?name=/a", secure=True))

        assert len(rows) == 3

    def test_detail_returns_the_spans_for_the_waterfall(
        self, staff_client: Client, project: Project
    ) -> None:
        from apps.tracing.models import Span

        make(project, "/checkout", [250.0])
        transaction = Transaction.objects.get()
        Span.objects.create(
            transaction=transaction,
            trace_id=transaction.trace_id,
            span_id="c" * 16,
            parent_span_id=transaction.span_id,
            op="db.query",
            description="SELECT 1",
            start_timestamp=transaction.start_timestamp,
            timestamp=transaction.timestamp,
            duration_ms=140.0,
        )

        payload = json_body(
            staff_client.get(reverse("api:trace", args=[transaction.pk]), secure=True)
        )

        assert payload["span_count"] == 1
        assert payload["spans"][0]["op"] == "db.query"
        assert payload["spans"][0]["duration_ms"] == 140.0

    def test_requires_authentication(self, client: Client, project: Project) -> None:
        response = client.get(reverse("api:traces", args=[project.pk]), secure=True)

        assert response.status_code == 403


class TestSpanInsights:
    def make_span(
        self, project: Project, name: str, op: str, description: str, durations: list[float]
    ) -> None:
        from apps.tracing.models import Span

        when = NOW - timedelta(minutes=5)
        txn = Transaction.objects.create(
            project=project,
            trace_id="f" * 32,
            span_id="e" * 16,
            name=name,
            start_timestamp=when,
            timestamp=when,
            duration_ms=sum(durations),
            payload={},
        )
        Span.objects.bulk_create(
            Span(
                transaction=txn,
                trace_id=txn.trace_id,
                span_id=f"{index:016x}",
                op=op,
                description=description,
                start_timestamp=when,
                timestamp=when,
                duration_ms=duration,
            )
            for index, duration in enumerate(durations)
        )

    def url(self, project: Project) -> str:
        return reverse("api:spans", args=[project.pk])

    def test_groups_spans_by_what_they_do(self, staff_client: Client, project: Project) -> None:
        """A waterfall shows one request; the span that matters is the one that runs constantly."""
        self.make_span(project, "/a", "db.query", "SELECT * FROM users", [5.0, 6.0, 7.0])
        self.make_span(project, "/b", "db.query", "SELECT * FROM users", [5.0])

        rows = json_body(staff_client.get(self.url(project), secure=True))["spans"]

        [row] = [r for r in rows if r["description"] == "SELECT * FROM users"]
        assert row["count"] == 4
        assert row["transactions"] == 2

    def test_calls_per_request_exposes_an_n_plus_one(
        self, staff_client: Client, project: Project
    ) -> None:
        """25 identical queries in one request is the signature, and the aggregate is where it
        is visible without reading a waterfall."""
        self.make_span(project, "/report", "db.query", "SELECT total FROM orders", [2.0] * 25)

        rows = json_body(staff_client.get(self.url(project), secure=True))["spans"]

        [row] = [r for r in rows if "orders" in r["description"]]
        assert row["per_transaction"] == 25.0

    def test_ranks_by_time_spent(self, staff_client: Client, project: Project) -> None:
        self.make_span(project, "/a", "db.query", "fast but constant", [1.0] * 300)
        self.make_span(project, "/b", "db.query", "slow but rare", [200.0])

        rows = json_body(staff_client.get(self.url(project), secure=True))["spans"]

        assert rows[0]["description"] == "fast but constant"

    def test_filters_by_operation(self, staff_client: Client, project: Project) -> None:
        self.make_span(project, "/a", "db.query", "a query", [5.0])
        self.make_span(project, "/a", "cache.get", "a cache read", [1.0])

        rows = json_body(staff_client.get(f"{self.url(project)}?op=cache.get", secure=True))

        assert [r["description"] for r in rows["spans"]] == ["a cache read"]
        assert set(rows["ops"]) == {"db.query", "cache.get"}

    def test_requires_authentication(self, client: Client, project: Project) -> None:
        assert client.get(self.url(project), secure=True).status_code == 403


class TestDashboard:
    def url(self, project: Project) -> str:
        return reverse("api:dashboard", args=[project.pk])

    def test_every_series_shares_one_bucket_grid(
        self, staff_client: Client, project: Project
    ) -> None:
        """Charts that disagree about what "three hours ago" means invite conclusions the data
        does not support."""
        make(project, "/a", [10.0] * 3)

        payload = json_body(staff_client.get(self.url(project), secure=True))

        lengths = {len(series) for series in payload["series"].values()}
        assert len(lengths) == 1
        assert lengths.pop() == payload["buckets"]

    def test_headline_counts_every_signal(self, staff_client: Client, project: Project) -> None:
        make(project, "/a", [10.0] * 4)
        make(project, "/a", [10.0], status=SpanStatus.INTERNAL_ERROR)

        headline = json_body(staff_client.get(self.url(project), secure=True))["headline"]

        assert headline["transactions"] == 5
        assert headline["failure_rate"] == pytest.approx(0.2)
        assert headline["p95_ms"] > 0

    def test_top_issues_are_unresolved_only(
        self, staff_client: Client, project: Project, project_key: ProjectKey
    ) -> None:
        """A resolved issue on the overview is a distraction from the open ones."""
        from apps.issues.models import Issue, IssueStatus
        from tests.conftest import build_envelope, post
        from tests.test_grouping import FRAMES, error

        post(
            staff_client,
            project,
            build_envelope(("event", error(frames=FRAMES))),
            project_key.public_key,
        )
        assert Issue.objects.count() == 1

        payload = json_body(staff_client.get(self.url(project), secure=True))
        assert len(payload["top_issues"]) == 1

        Issue.objects.update(status=IssueStatus.RESOLVED)
        payload = json_body(staff_client.get(self.url(project), secure=True))
        assert payload["top_issues"] == []

    def test_an_empty_project_returns_zeroes_not_an_error(
        self, staff_client: Client, project: Project
    ) -> None:
        payload = json_body(staff_client.get(self.url(project), secure=True))

        assert payload["headline"]["transactions"] == 0
        assert payload["headline"]["failure_rate"] == 0.0
        assert payload["top_issues"] == []

    def test_requires_authentication(self, client: Client, project: Project) -> None:
        assert client.get(self.url(project), secure=True).status_code == 403


class TestSpanOps:
    def test_operations_are_deduplicated(self, staff_client: Client, project: Project) -> None:
        """Django's Meta.ordering adds start_timestamp to the SELECT, so a naive .distinct()
        dedupes on (op, start_timestamp) and returns one entry per span."""
        insights = TestSpanInsights()
        insights.make_span(project, "/a", "db.query", "q", [1.0] * 30)
        insights.make_span(project, "/a", "cache.get", "c", [1.0] * 30)

        ops = json_body(staff_client.get(reverse("api:spans", args=[project.pk]), secure=True))

        assert ops["ops"] == ["cache.get", "db.query"]


class TestSpanDetail:
    def url(self, project: Project) -> str:
        return reverse("api:span-detail", args=[project.pk])

    def setup_spans(self, project: Project) -> None:
        insights = TestSpanInsights()
        insights.make_span(project, "/a", "db.query", "SELECT 1", [5.0, 6.0, 40.0])
        insights.make_span(project, "/b", "db.query", "SELECT 1", [7.0])
        insights.make_span(project, "/a", "db.query", "SELECT 2", [9.0])

    def test_summarises_one_span_group(self, staff_client: Client, project: Project) -> None:
        self.setup_spans(project)

        payload = json_body(
            staff_client.get(f"{self.url(project)}?op=db.query&description=SELECT 1", secure=True)
        )

        assert payload["summary"]["count"] == 4
        assert payload["summary"]["transactions"] == 2
        assert payload["summary"]["slowest"] == pytest.approx(40, abs=1)

    def test_names_the_endpoints_that_call_it(self, staff_client: Client, project: Project) -> None:
        """The aggregate says a query is expensive; this says who makes it expensive."""
        self.setup_spans(project)

        payload = json_body(
            staff_client.get(f"{self.url(project)}?op=db.query&description=SELECT 1", secure=True)
        )

        callers = {row["transaction"]: row["count"] for row in payload["callers"]}
        assert callers == {"/a": 3, "/b": 1}

    def test_offers_traces_to_open_slowest_first(
        self, staff_client: Client, project: Project
    ) -> None:
        """A sample list sorted by time shows what happened last; sorted by duration it shows
        the one worth opening."""
        self.setup_spans(project)

        payload = json_body(
            staff_client.get(f"{self.url(project)}?op=db.query&description=SELECT 1", secure=True)
        )

        assert payload["samples"][0]["duration_ms"] == pytest.approx(40, abs=1)
        assert payload["samples"][0]["transaction_id"]

    def test_the_distribution_shows_the_shape_not_two_points(
        self, staff_client: Client, project: Project
    ) -> None:
        """A p50 and a p95 describe two points. The shape between them says whether this is one
        slow tail or two behaviours wearing the same statement."""
        self.setup_spans(project)

        payload = json_body(
            staff_client.get(f"{self.url(project)}?op=db.query&description=SELECT 1", secure=True)
        )

        distribution = payload["distribution"]
        assert len(distribution) == 20
        assert sum(bucket["count"] for bucket in distribution) == 4

        # Buckets are slowest/20 wide, so with a 40ms outlier each is 2ms: the three fast calls
        # (5, 6, 7ms) land low but not in bucket zero, and the outlier lands in the last one.
        # That separation is the point — it is what says "one slow tail", not "uniformly slow".
        assert sum(bucket["count"] for bucket in distribution[:5]) == 3
        assert distribution[-1]["count"] == 1

    def test_a_span_with_no_data_in_the_window_is_not_a_500(
        self, staff_client: Client, project: Project
    ) -> None:
        response = staff_client.get(
            f"{self.url(project)}?op=db.query&description=never%20ran", secure=True
        )

        assert response.status_code == 404
        assert "period" in json_body(response)["detail"].lower()

    def test_op_is_required(self, staff_client: Client, project: Project) -> None:
        assert staff_client.get(self.url(project), secure=True).status_code == 400

    def test_requires_authentication(self, client: Client, project: Project) -> None:
        assert client.get(f"{self.url(project)}?op=db.query", secure=True).status_code == 403
