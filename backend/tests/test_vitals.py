"""Web vitals: ingest, aggregation and the browser's access to the endpoint.

The numbers here are the ones a team is judged on publicly, so getting the definition wrong is
worse than not having them. Three things this file pins down: they are p75 rather than a mean,
the thresholds are the standard's rather than ours, and browser and backend transactions are
never mixed into one population.
"""

import json
from datetime import timedelta
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.api.vitals import rating
from apps.projects.models import Project, ProjectKey
from apps.tracing.models import Transaction
from tests.conftest import build_envelope, json_body, post

pytestmark = pytest.mark.django_db

NOW = timezone.now()


def pageload(project: Project, name: str, *, minutes_ago: int = 5, **vitals: float) -> Transaction:
    when = NOW - timedelta(minutes=minutes_ago)
    return Transaction.objects.create(
        project=project,
        trace_id=f"{hash(name) & 0xFFFFFFFF:032x}",
        span_id="a" * 16,
        name=name,
        op="pageload",
        start_timestamp=when,
        timestamp=when,
        duration_ms=100.0,
        measurements={
            key: {"value": value, "unit": "" if key == "cls" else "millisecond"}
            for key, value in vitals.items()
        },
        payload={},
    )


def vitals_of(client: Client, project: Project, period: str = "24h") -> Any:
    url = reverse("api:vitals", args=[project.pk])
    return json_body(client.get(f"{url}?period={period}", secure=True))


class TestRating:
    @pytest.mark.parametrize(
        ("key", "value", "expected"),
        [
            ("lcp", 2400, "good"),
            ("lcp", 2500, "good"),
            ("lcp", 3000, "needs-improvement"),
            ("lcp", 4001, "poor"),
            ("cls", 0.05, "good"),
            ("cls", 0.2, "needs-improvement"),
            ("cls", 0.3, "poor"),
            ("inp", 199, "good"),
            ("inp", 501, "poor"),
        ],
    )
    def test_bands_come_from_the_standard(self, key: str, value: float, expected: str) -> None:
        """A tool that invents its own thresholds cannot be compared against anything else
        anyone reads."""
        assert rating(key, value) == expected

    def test_no_samples_is_not_a_good_score(self) -> None:
        """Green because nothing was measured is the most dangerous reading on the page."""
        assert rating("lcp", None) == "none"


class TestAggregate:
    def test_the_score_is_the_p75_not_the_mean(
        self, staff_client: Client, project: Project
    ) -> None:
        """Core Web Vitals are specified at p75. On a distribution with a slow tail the mean
        reads as healthy while a quarter of visits are not."""
        # 90/10, not 75/25: at exactly 75/25 the interpolated p75 equals the mean
        # algebraically, so the test would pass on a coincidence rather than on the point.
        for _ in range(90):
            pageload(project, "/", lcp=1000)
        for _ in range(10):
            pageload(project, "/", lcp=20000)

        [lcp] = [row for row in vitals_of(staff_client, project)["vitals"] if row["key"] == "lcp"]

        mean = (90 * 1000 + 10 * 20000) / 100
        assert mean == pytest.approx(2900)
        assert lcp["value"] == pytest.approx(1000, abs=1), "p75 is where 3 in 4 visits landed"

    def test_a_backend_transaction_is_not_a_page_load(
        self, staff_client: Client, project: Project
    ) -> None:
        """A server request has no layout shift. Mixing the populations produces a number
        describing neither."""
        pageload(project, "/", lcp=1000)
        Transaction.objects.create(
            project=project,
            trace_id="b" * 32,
            span_id="b" * 16,
            name="/api/orders",
            op="http.server",
            start_timestamp=NOW,
            timestamp=NOW,
            duration_ms=5000.0,
            measurements={"lcp": {"value": 9999, "unit": "millisecond"}},
            payload={},
        )

        payload = vitals_of(staff_client, project)

        assert payload["pageloads"] == 1
        [lcp] = [row for row in payload["vitals"] if row["key"] == "lcp"]
        assert lcp["value"] == pytest.approx(1000, abs=1)

    def test_cls_keeps_its_precision(self, staff_client: Client, project: Project) -> None:
        """A layout shift of 0.083 rounded to whole numbers reads as perfect."""
        pageload(project, "/", cls=0.083)

        [cls] = [row for row in vitals_of(staff_client, project)["vitals"] if row["key"] == "cls"]

        assert cls["value"] == pytest.approx(0.083, abs=0.001)
        assert cls["unit"] == "", "CLS is a ratio; labelling it ms would be a lie"

    def test_a_vital_nobody_reported_says_so(self, staff_client: Client, project: Project) -> None:
        pageload(project, "/", lcp=1000)

        [inp] = [row for row in vitals_of(staff_client, project)["vitals"] if row["key"] == "inp"]

        assert inp["value"] is None
        assert inp["samples"] == 0
        assert inp["rating"] == "none"

    def test_pages_are_ranked_by_their_worst_lcp(
        self, staff_client: Client, project: Project
    ) -> None:
        """A list ordered by traffic just repeats the traffic report."""
        pageload(project, "/fast", lcp=800)
        pageload(project, "/slow", lcp=6000)

        pages = vitals_of(staff_client, project)["pages"]

        assert [page["name"] for page in pages] == ["/slow", "/fast"]
        assert pages[0]["rating"] == "poor"

    def test_no_data_is_an_empty_answer_not_an_error(
        self, staff_client: Client, project: Project
    ) -> None:
        payload = vitals_of(staff_client, project)

        assert payload["pageloads"] == 0
        assert all(row["value"] is None for row in payload["vitals"])

    def test_requires_authentication(self, client: Client, project: Project) -> None:
        assert client.get(reverse("api:vitals", args=[project.pk]), secure=True).status_code == 403


class TestMeasurementIngest:
    def envelope(self, **measurements: Any) -> bytes:
        return build_envelope(
            (
                "transaction",
                {
                    "transaction": "/checkout",
                    "op": "pageload",
                    "start_timestamp": "2026-08-12T00:00:00Z",
                    "timestamp": "2026-08-12T00:00:01Z",
                    "contexts": {"trace": {"trace_id": "c" * 32, "span_id": "d" * 16}},
                    "measurements": measurements,
                },
            )
        )

    def test_measurements_reach_their_own_column(
        self, client: Client, project: Project, project_key: ProjectKey
    ) -> None:
        """A column rather than a dig into the payload: these are aggregated across millions
        of rows, and a JSON path per row is the scan the extracted columns exist to avoid."""
        body = self.envelope(lcp={"value": 2400, "unit": "millisecond"})

        post(client, project, body, project_key.public_key)

        assert Transaction.objects.get().measurements["lcp"]["value"] == 2400

    @pytest.mark.parametrize(
        "bad",
        [
            {"value": "fast"},
            {"value": None},
            {"value": True},
            {"value": -1},
            {"nope": 1},
            "not-a-dict",
        ],
    )
    def test_an_unusable_measurement_costs_that_measurement_only(
        self, client: Client, project: Project, project_key: ProjectKey, bad: Any
    ) -> None:
        """Same posture as the rest of ingest: a browser sends what it likes, and one bad
        entry must not cost the transaction carrying it."""
        body = self.envelope(lcp={"value": 2400, "unit": "millisecond"}, junk=bad)

        post(client, project, body, project_key.public_key)

        stored = Transaction.objects.get().measurements
        assert stored["lcp"]["value"] == 2400
        assert "junk" not in stored

    def test_infinity_is_rejected_rather_than_500ing_the_request(
        self, client: Client, project: Project, project_key: ProjectKey
    ) -> None:
        """Python's json parser accepts `Infinity` as a bare token. Postgres does not, and the
        payload is stored verbatim — so six characters from a hostile client used to take the
        whole ingest request down with a 500.

        It is not JSON, so it is rejected like any other malformed payload."""
        body = self.envelope(lcp={"value": float("inf"), "unit": "millisecond"})

        response = post(client, project, body, project_key.public_key)

        assert response.status_code == 200
        assert json_body(response)["rejected"], "the item was dropped, and said so"
        assert Transaction.objects.count() == 0

    def test_one_poisoned_item_does_not_cost_the_good_one_beside_it(
        self, client: Client, project: Project, project_key: ProjectKey
    ) -> None:
        good = {
            "transaction": "/ok",
            "op": "pageload",
            "start_timestamp": "2026-08-12T00:00:00Z",
            "timestamp": "2026-08-12T00:00:01Z",
            "contexts": {"trace": {"trace_id": "e" * 32, "span_id": "f" * 16}},
            "measurements": {"lcp": {"value": 1200, "unit": "millisecond"}},
        }
        # Hand-built: json.dumps will not emit a bare NaN, which is the whole point — only a
        # client that is not using a normal encoder can produce this.
        bad = b'{"transaction": "/bad", "op": "pageload", "measurements": {"lcp": {"value": NaN}}}'
        body = (
            build_envelope(("transaction", good))
            + json.dumps({"type": "transaction"}).encode()
            + b"\n"
            + bad
            + b"\n"
        )

        response = post(client, project, body, project_key.public_key)

        assert Transaction.objects.get().name == "/ok"
        assert len(json_body(response)["rejected"]) == 1

    def test_a_transaction_with_no_measurements_is_still_stored(
        self, client: Client, project: Project, project_key: ProjectKey
    ) -> None:
        body = build_envelope(
            (
                "transaction",
                {
                    "transaction": "/checkout",
                    "start_timestamp": "2026-08-12T00:00:00Z",
                    "timestamp": "2026-08-12T00:00:01Z",
                    "contexts": {"trace": {"trace_id": "c" * 32, "span_id": "d" * 16}},
                },
            )
        )

        post(client, project, body, project_key.public_key)

        assert Transaction.objects.get().measurements == {}


class TestBrowserAccess:
    """Without these, a browser SDK cannot reach ingest at all — and unlike a server SDK there
    is no way for the page to work around it."""

    def url(self, project: Project) -> str:
        return reverse("ingest:envelope", args=[project.pk])

    def test_a_preflight_is_answered(self, client: Client, project: Project) -> None:
        response = client.options(self.url(project), secure=True)

        assert response.status_code == 204
        assert response["Access-Control-Allow-Origin"] == "*"
        assert "X-Obsly-Key" in response["Access-Control-Allow-Headers"]

    def test_the_preflight_needs_no_key(self, client: Client, project: Project) -> None:
        """A preflight cannot carry the key header — that is the header it is asking about."""
        assert client.options(self.url(project), secure=True).status_code == 204

    def test_a_successful_post_is_readable_by_the_page(
        self, client: Client, project: Project, project_key: ProjectKey
    ) -> None:
        payload = {"exception": {"values": [{"type": "TypeError", "value": "x"}]}}

        response = post(client, project, build_envelope(("event", payload)), project_key.public_key)

        assert response["Access-Control-Allow-Origin"] == "*"

    def test_a_rejection_is_readable_too(self, client: Client, project: Project) -> None:
        """Without the header the browser discards the response it did get, and the page cannot
        tell a bad key from an outage."""
        response = client.post(
            self.url(project),
            data=b"garbage",
            content_type="application/x-obsly-envelope",
            secure=True,
        )

        assert response.status_code == 401
        assert response["Access-Control-Allow-Origin"] == "*"


class TestDepth:
    """A p75 is one point on a distribution. Two sites can share it with a completely different
    share of visitors having a bad time, and the share is what says how many people it is."""

    def test_the_bands_split_the_page_loads(self, staff_client: Client, project: Project) -> None:
        for _ in range(6):
            pageload(project, "/", lcp=1000)  # good
        for _ in range(3):
            pageload(project, "/", lcp=3000)  # needs improvement
        pageload(project, "/", lcp=9000)  # poor

        [lcp] = [row for row in vitals_of(staff_client, project)["vitals"] if row["key"] == "lcp"]

        assert lcp["distribution"] == {
            "good": 6,
            "needs_improvement": 3,
            "poor": 1,
            "total": 10,
        }

    def test_the_middle_band_is_the_remainder_not_a_third_threshold(
        self, staff_client: Client, project: Project
    ) -> None:
        """Defined by the two edges. A third number could disagree with them and put a visit in
        no band at all."""
        pageload(project, "/", lcp=2500)  # exactly the good edge
        pageload(project, "/", lcp=4000)  # exactly the poor edge, so not yet poor

        [lcp] = [row for row in vitals_of(staff_client, project)["vitals"] if row["key"] == "lcp"]

        assert lcp["distribution"]["good"] == 1
        assert lcp["distribution"]["needs_improvement"] == 1
        assert lcp["distribution"]["poor"] == 0

    def test_a_quiet_bucket_is_a_gap_not_a_perfect_score(
        self, staff_client: Client, project: Project
    ) -> None:
        """Drawing an hour with no page loads as zero renders an outage as a perfect LCP."""
        pageload(project, "/", lcp=1000, minutes_ago=5)

        [lcp] = [row for row in vitals_of(staff_client, project)["vitals"] if row["key"] == "lcp"]

        assert None in lcp["trend"], "empty buckets must be gaps"
        assert any(value is not None for value in lcp["trend"])

    def test_the_worst_page_loads_are_openable(
        self, staff_client: Client, project: Project
    ) -> None:
        """Every other number on the page is an aggregate, and an aggregate cannot be
        debugged — at some point you need one slow load and its trace."""
        pageload(project, "/fast", lcp=800)
        pageload(project, "/checkout", lcp=7000, cls=0.3)

        worst = vitals_of(staff_client, project)["worst"]

        assert worst[0]["name"] == "/checkout"
        assert worst[0]["transaction_id"]
        assert worst[0]["rating"] == "poor"
        assert worst[0]["cls"] == pytest.approx(0.3)

    def test_a_vital_nobody_reported_has_an_empty_distribution(
        self, staff_client: Client, project: Project
    ) -> None:
        pageload(project, "/", lcp=1000)

        [inp] = [row for row in vitals_of(staff_client, project)["vitals"] if row["key"] == "inp"]

        assert inp["distribution"]["total"] == 0
        assert inp["rating"] == "none"
