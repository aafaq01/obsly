"""One request, however many services served it.

The id has always propagated — every SDK continues an incoming trace. What did not exist was
the reading: the trace page returned one transaction and its spans, so a request that crossed
four services was four rows in a list and the waterfall stopped at the first hop.

The tests that matter most here are the ones about what is *not* returned. Joining traces
across projects is a disclosure, and a query that quietly returns another team's rows is worse
than one that returns none.
"""

import uuid
from datetime import timedelta

import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.api.distributed import build_tree, joinable_project_ids, transactions_in_trace
from apps.events.models import Event
from apps.logs.models import LogRecord
from apps.projects.models import Organization, Project
from apps.tracing.models import Span, Transaction
from tests.conftest import json_body

pytestmark = pytest.mark.django_db

NOW = timezone.now()
TRACE = "a" * 32


def make_transaction(
    project: Project,
    name: str,
    *,
    span_id: str,
    parent_span_id: str = "",
    trace_id: str = TRACE,
    op: str = "http.server",
    offset_ms: int = 0,
    duration_ms: float = 100.0,
) -> Transaction:
    start = NOW + timedelta(milliseconds=offset_ms)
    return Transaction.objects.create(
        id=uuid.uuid4(),
        project=project,
        trace_id=trace_id,
        span_id=span_id,
        parent_span_id=parent_span_id,
        name=name,
        op=op,
        status="ok",
        start_timestamp=start,
        timestamp=start + timedelta(milliseconds=duration_ms),
        duration_ms=duration_ms,
        payload={},
    )


@pytest.fixture
def gateway(organization: Organization) -> Project:
    project = Project.objects.create(
        organization=organization, name="Gateway", slug="gateway", trace_sharing=True
    )
    return project


@pytest.fixture
def payments(organization: Organization) -> Project:
    return Project.objects.create(
        organization=organization, name="Payments", slug="payments", trace_sharing=True
    )


class TestWhatMayBeJoined:
    def test_a_project_always_sees_itself(self, project: Project) -> None:
        """Sharing widens the view; it is not a prerequisite for having one. A trace opened in
        a project shows that project's rows whether or not anybody enabled anything."""
        assert joinable_project_ids(project) == [project.pk]

    def test_two_sharing_projects_in_one_organization_join(
        self, gateway: Project, payments: Project
    ) -> None:
        assert joinable_project_ids(gateway) == sorted([gateway.pk, payments.pk])

    def test_a_project_that_has_not_opted_in_is_not_pulled_in(
        self, gateway: Project, organization: Organization
    ) -> None:
        """The setting is a disclosure control. A project with it off never appears in another
        project's waterfall, however much trace id they share."""
        Project.objects.create(
            organization=organization, name="Secrets", slug="secrets", trace_sharing=False
        )

        assert joinable_project_ids(gateway) == [gateway.pk]

    def test_another_organization_never_joins(self, gateway: Project) -> None:
        """A trace id is 128 random bits, so a collision across tenants is not a thing that
        happens — but a query that would return their row if it did is written wrong."""
        other = Organization.objects.create(name="Other", slug="other")
        Project.objects.create(organization=other, name="Theirs", slug="theirs", trace_sharing=True)

        assert joinable_project_ids(gateway) == [gateway.pk]


class TestAssembly:
    def test_the_whole_request_comes_back_not_just_the_hop_that_was_opened(
        self, gateway: Project, payments: Project
    ) -> None:
        root = make_transaction(gateway, "/checkout", span_id="1" * 16)
        make_transaction(payments, "/charge", span_id="2" * 16, parent_span_id="1" * 16)

        assert [row.name for row in transactions_in_trace(root)] == ["/checkout", "/charge"]

    def test_a_service_that_did_not_opt_in_is_left_out(
        self, gateway: Project, organization: Organization
    ) -> None:
        quiet = Project.objects.create(
            organization=organization, name="Quiet", slug="quiet", trace_sharing=False
        )
        root = make_transaction(gateway, "/checkout", span_id="1" * 16)
        make_transaction(quiet, "/secret", span_id="2" * 16, parent_span_id="1" * 16)

        assert [row.name for row in transactions_in_trace(root)] == ["/checkout"]

    def test_a_single_service_trace_is_unchanged(self, project: Project) -> None:
        """The common case, and it has to read exactly as it did before any of this."""
        root = make_transaction(project, "/checkout", span_id="1" * 16)

        assert [row.name for row in transactions_in_trace(root)] == ["/checkout"]

    def test_the_trace_you_opened_is_always_in_it(self, project: Project) -> None:
        """A detail page that does not contain the row you clicked is a bug that reads as
        missing data."""
        root = make_transaction(project, "/checkout", span_id="1" * 16)
        Transaction.objects.filter(pk=root.pk).delete()

        assert [row.name for row in transactions_in_trace(root)] == ["/checkout"]


def make_span(
    transaction: Transaction,
    *,
    span_id: str,
    op: str = "http.client",
    description: str = "POST http://payments/charge",
    duration_ms: float = 90.0,
) -> Span:
    return Span.objects.create(
        transaction=transaction,
        span_id=span_id,
        parent_span_id=transaction.span_id,
        trace_id=transaction.trace_id,
        op=op,
        description=description,
        status="ok",
        start_timestamp=transaction.start_timestamp,
        timestamp=transaction.start_timestamp + timedelta(milliseconds=duration_ms),
        duration_ms=duration_ms,
        data={},
    )


class TestTree:
    def test_a_service_hangs_off_the_client_span_that_called_it(
        self, gateway: Project, payments: Project
    ) -> None:
        """The shape the SDKs actually send, and the one every earlier test got wrong.

        `parent_span_id` on a downstream transaction names the **client span** in the caller,
        not the caller's transaction. Indexing only transaction ids left every hop of a real
        two-service request at the top level — which the tests all passed, because they built
        the link the way the tree wanted rather than the way the wire carries it. Found by
        running two services against the stack.
        """
        root = make_transaction(gateway, "/checkout", span_id="1" * 16)
        call = make_span(root, span_id="c" * 16)
        make_transaction(payments, "/charge", span_id="2" * 16, parent_span_id=call.span_id)

        nodes = build_tree(transactions_in_trace(root))

        assert [(node["name"], node["depth"]) for node in nodes] == [
            ("/checkout", 0),
            ("/charge", 1),
        ]

    def test_the_work_inside_each_hop_comes_with_it(
        self, gateway: Project, payments: Project
    ) -> None:
        """ "Which service was slow" is answered by the row; "why" is answered by what is under
        it, and a page that cannot answer the second sends somebody to another tool."""
        root = make_transaction(gateway, "/checkout", span_id="1" * 16)
        make_span(root, span_id="c" * 16)
        charge = make_transaction(payments, "/charge", span_id="2" * 16, parent_span_id="c" * 16)
        make_span(charge, span_id="d" * 16, op="db.query", description="UPDATE ledger")

        nodes = build_tree(transactions_in_trace(root))

        assert [span.op for span in nodes[1]["spans"]] == ["db.query"]

    def test_a_transaction_parented_by_its_own_span_does_not_count_itself(
        self, gateway: Project
    ) -> None:
        """Malformed, but it comes off the wire, so it must indent oddly rather than loop."""
        root = make_transaction(gateway, "/checkout", span_id="1" * 16)
        span = make_span(root, span_id="c" * 16)
        Transaction.objects.filter(pk=root.pk).update(parent_span_id=span.span_id)
        root.refresh_from_db()

        nodes = build_tree(transactions_in_trace(root))

        assert nodes[0]["depth"] == 0

    def test_a_called_service_is_indented_under_its_caller(
        self, gateway: Project, payments: Project
    ) -> None:
        """The edge already exists in the data: an outbound request leaves an http.client span
        and sends its id downstream, which the receiver stores as parent_span_id."""
        root = make_transaction(gateway, "/checkout", span_id="1" * 16)
        make_transaction(payments, "/charge", span_id="2" * 16, parent_span_id="1" * 16)

        nodes = build_tree(transactions_in_trace(root))

        assert [(node["name"], node["depth"]) for node in nodes] == [
            ("/checkout", 0),
            ("/charge", 1),
        ]

    def test_three_hops_indent_three_deep(self, gateway: Project, payments: Project) -> None:
        root = make_transaction(gateway, "/checkout", span_id="1" * 16)
        make_transaction(payments, "/charge", span_id="2" * 16, parent_span_id="1" * 16)
        make_transaction(
            payments, "/ledger", span_id="3" * 16, parent_span_id="2" * 16, offset_ms=10
        )

        depths = [node["depth"] for node in build_tree(transactions_in_trace(root))]

        assert depths == [0, 1, 2]

    def test_a_hop_whose_caller_was_never_instrumented_still_shows(
        self, gateway: Project, payments: Project
    ) -> None:
        """A partially instrumented estate is the normal state of one. Hiding the hops that do
        work would make the tool useless exactly when it is most needed."""
        root = make_transaction(gateway, "/checkout", span_id="1" * 16)
        make_transaction(payments, "/charge", span_id="2" * 16, parent_span_id="9" * 16)

        nodes = build_tree(transactions_in_trace(root))

        assert [(node["name"], node["depth"]) for node in nodes] == [
            ("/checkout", 0),
            ("/charge", 0),
        ]

    def test_two_transactions_pointing_at_each_other_do_not_hang(
        self, gateway: Project, payments: Project
    ) -> None:
        """The parent ids come off the wire. Malformed input must indent oddly, not spin."""
        root = make_transaction(gateway, "/a", span_id="1" * 16, parent_span_id="2" * 16)
        make_transaction(payments, "/b", span_id="2" * 16, parent_span_id="1" * 16)

        nodes = build_tree(transactions_in_trace(root))

        assert len(nodes) == 2

    def test_the_browser_page_load_is_the_root_of_the_whole_thing(
        self, gateway: Project, payments: Project
    ) -> None:
        """The point of the product in one assertion: the paint the reader waited for, the
        request it caused, and the service behind that, in one tree."""
        page = make_transaction(gateway, "/checkout", span_id="1" * 16, op="pageload")
        make_transaction(gateway, "/api/checkout", span_id="2" * 16, parent_span_id="1" * 16)
        make_transaction(
            payments, "/charge", span_id="3" * 16, parent_span_id="2" * 16, offset_ms=5
        )

        nodes = build_tree(transactions_in_trace(page))

        assert [(node["op"], node["depth"]) for node in nodes] == [
            ("pageload", 0),
            ("http.server", 1),
            ("http.server", 2),
        ]


class TestApi:
    def url(self, trace: Transaction) -> str:
        return reverse("api:trace", args=[trace.pk])

    def test_the_response_carries_every_service(
        self, staff_client: Client, gateway: Project, payments: Project
    ) -> None:
        root = make_transaction(gateway, "/checkout", span_id="1" * 16)
        make_transaction(payments, "/charge", span_id="2" * 16, parent_span_id="1" * 16)

        body = json_body(staff_client.get(self.url(root), secure=True))

        assert [row["project_name"] for row in body["transactions"]] == ["Gateway", "Payments"]

    def test_it_says_which_services_took_part_and_for_how_long(
        self, staff_client: Client, gateway: Project, payments: Project
    ) -> None:
        """The first question about a slow distributed request is *which service*, and the page
        should answer it before anybody expands anything."""
        root = make_transaction(gateway, "/checkout", span_id="1" * 16, duration_ms=500)
        make_transaction(
            payments, "/charge", span_id="2" * 16, parent_span_id="1" * 16, duration_ms=800
        )

        services = json_body(staff_client.get(self.url(root), secure=True))["services"]

        assert [row["project_name"] for row in services] == ["Payments", "Gateway"]
        assert services[0]["duration_ms"] == 800

    def test_an_error_three_hops_down_belongs_to_the_request_that_caused_it(
        self, staff_client: Client, gateway: Project, payments: Project
    ) -> None:
        root = make_transaction(gateway, "/checkout", span_id="1" * 16)
        Event.objects.create(
            id=uuid.uuid4(),
            project=payments,
            trace_id=TRACE,
            exception_type="CardDeclined",
            exception_value="no funds",
            level="error",
            timestamp=NOW,
            payload={},
        )

        errors = json_body(staff_client.get(self.url(root), secure=True))["errors"]

        assert errors[0]["project_name"] == "Payments"

    def test_a_log_from_another_service_says_whose_it_is(
        self, staff_client: Client, gateway: Project, payments: Project
    ) -> None:
        """A cross-service trace interleaves several applications' logs, and without the name
        they read as one confused program."""
        root = make_transaction(gateway, "/checkout", span_id="1" * 16)
        LogRecord.objects.create(
            project=payments, trace_id=TRACE, level="info", body="charging", timestamp=NOW
        )

        logs = json_body(staff_client.get(self.url(root), secure=True))["logs"]

        assert logs[0]["project_name"] == "Payments"

    def test_a_project_with_sharing_off_sees_only_itself(
        self, staff_client: Client, project: Project, organization: Organization
    ) -> None:
        other = Project.objects.create(
            organization=organization, name="Other", slug="other", trace_sharing=True
        )
        root = make_transaction(project, "/checkout", span_id="1" * 16)
        make_transaction(other, "/charge", span_id="2" * 16, parent_span_id="1" * 16)

        body = json_body(staff_client.get(self.url(root), secure=True))

        assert [row["name"] for row in body["transactions"]] == ["/checkout"]

    def test_sharing_is_off_until_somebody_turns_it_on(
        self, staff_client: Client, project: Project
    ) -> None:
        body = json_body(staff_client.get(reverse("api:project", args=[project.pk]), secure=True))

        assert body["trace_sharing"] is False

    def test_it_can_be_turned_on_from_the_api(self, staff_client: Client, project: Project) -> None:
        response = staff_client.patch(
            reverse("api:project", args=[project.pk]),
            {"trace_sharing": True},
            content_type="application/json",
            secure=True,
        )

        assert response.status_code == 200
        project.refresh_from_db()
        assert project.trace_sharing is True

    def test_turning_it_on_requires_authentication(self, client: Client, project: Project) -> None:
        response = client.patch(
            reverse("api:project", args=[project.pk]),
            {"trace_sharing": True},
            content_type="application/json",
            secure=True,
        )

        assert response.status_code == 403
        project.refresh_from_db()
        assert project.trace_sharing is False

    def test_a_bigger_trace_does_not_cost_more_queries(
        self, staff_client: Client, gateway: Project, payments: Project
    ) -> None:
        """One query per transaction to count its spans is the N+1 this product exists to point
        at — and it would arrive precisely when a trace has the most services in it.

        Counted rather than pinned to a number: the assertion is that the cost does not grow
        with the trace, not that some particular refactor never changes it.
        """
        root = make_transaction(gateway, "/checkout", span_id="1" * 16)
        make_transaction(payments, "/charge", span_id="2" * 16, parent_span_id="1" * 16)

        with CaptureQueriesContext(connection) as small:
            staff_client.get(self.url(root), secure=True)

        for index in range(3, 9):
            make_transaction(
                payments,
                f"/charge/{index}",
                span_id=str(index) * 16,
                parent_span_id="1" * 16,
                offset_ms=index,
            )

        with CaptureQueriesContext(connection) as large:
            staff_client.get(self.url(root), secure=True)

        assert len(large) == len(small)
