from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from apps.projects.models import Project
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

    def test_hourly_buckets_are_zero_filled(self, staff_client: Client, project: Project) -> None:
        """A quiet hour must be a gap in the chart, not a missing bar that shifts every other."""
        make(project, "/api", [10.0] * 3)

        hourly = summary(staff_client, project)["summary"]["hourly"]

        assert len(hourly) >= 24
        assert sum(hourly) == 3

    def test_no_data_is_an_empty_answer_not_an_error(
        self, staff_client: Client, project: Project
    ) -> None:
        payload = summary(staff_client, project)

        assert payload["endpoints"] == []
        assert payload["summary"]["failure_rate"] == 0.0

    def test_requires_authentication(self, client: Client, project: Project) -> None:
        response = client.get(reverse("api:performance", args=[project.pk]), secure=True)

        assert response.status_code == 403
