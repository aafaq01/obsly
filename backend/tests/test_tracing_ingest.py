from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from django.test import Client

from apps.projects.models import Project, ProjectKey
from apps.tracing.models import Span, Transaction
from tests.conftest import build_envelope, json_body, post

pytestmark = pytest.mark.django_db

NOW = datetime.now(tz=UTC)
TRACE = "a" * 32
ROOT = "b" * 16


def txn_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "type": "transaction",
        "transaction": "/items/{item_id}",
        "start_timestamp": (NOW - timedelta(milliseconds=250)).isoformat(),
        "timestamp": NOW.isoformat(),
        "environment": "production",
        "release": "api@2.0.0",
        "contexts": {
            "trace": {"trace_id": TRACE, "span_id": ROOT, "op": "http.server", "status": "ok"}
        },
        "spans": [
            {
                "span_id": "c" * 16,
                "parent_span_id": ROOT,
                "op": "db.query",
                "description": "SELECT * FROM items WHERE id = %s",
                "status": "ok",
                "start_timestamp": (NOW - timedelta(milliseconds=200)).isoformat(),
                "timestamp": (NOW - timedelta(milliseconds=60)).isoformat(),
            }
        ],
        "data": {"http.status_code": 200},
    }
    payload.update(overrides)
    return payload


def send(client: Client, project: Project, key: ProjectKey, payload: dict[str, Any]) -> Any:
    return post(client, project, build_envelope(("transaction", payload)), key.public_key)


class TestTransactionIngest:
    def test_stores_a_transaction_with_its_duration(
        self, client: Client, project: Project, project_key: ProjectKey
    ) -> None:
        response = send(client, project, project_key, txn_payload())

        assert json_body(response)["accepted"] == 1
        txn = Transaction.objects.get()
        assert txn.name == "/items/{item_id}"
        assert txn.trace_id == TRACE
        assert 240 < txn.duration_ms < 260
        assert txn.release == "api@2.0.0"

    def test_stores_child_spans_linked_to_the_transaction(
        self, client: Client, project: Project, project_key: ProjectKey
    ) -> None:
        send(client, project, project_key, txn_payload())

        span = Span.objects.get()
        assert span.op == "db.query"
        assert span.parent_span_id == ROOT
        assert span.trace_id == TRACE
        assert 130 < span.duration_ms < 150

    def test_replaying_an_envelope_does_not_duplicate_spans(
        self, client: Client, project: Project, project_key: ProjectKey
    ) -> None:
        """Retries are expected. Without the guard the span table grows on every one."""
        payload = txn_payload(event_id="6ba7b810-9dad-11d1-80b4-00c04fd430c8")

        send(client, project, project_key, payload)
        send(client, project, project_key, payload)

        assert Transaction.objects.count() == 1
        assert Span.objects.count() == 1

    def test_an_unnamed_transaction_is_rejected(
        self, client: Client, project: Project, project_key: ProjectKey
    ) -> None:
        """It cannot be grouped, filtered or charted, so it is a row nobody can use."""
        response = send(client, project, project_key, txn_payload(transaction=""))

        assert json_body(response)["accepted"] == 0
        assert Transaction.objects.count() == 0

    def test_a_negative_duration_is_rejected(
        self, client: Client, project: Project, project_key: ProjectKey
    ) -> None:
        """A clock that moved backwards would give a p95 below the p50."""
        response = send(
            client,
            project,
            project_key,
            txn_payload(
                start_timestamp=NOW.isoformat(),
                timestamp=(NOW - timedelta(seconds=5)).isoformat(),
            ),
        )

        assert json_body(response)["accepted"] == 0

    def test_an_absurdly_long_transaction_is_rejected(
        self, client: Client, project: Project, project_key: ProjectKey
    ) -> None:
        """Two hours is a stuck process, not a slow request, and it drags every percentile."""
        response = send(
            client,
            project,
            project_key,
            txn_payload(start_timestamp=(NOW - timedelta(hours=2)).isoformat()),
        )

        assert json_body(response)["accepted"] == 0

    def test_a_broken_span_costs_that_span_not_the_transaction(
        self, client: Client, project: Project, project_key: ProjectKey
    ) -> None:
        payload = txn_payload()
        payload["spans"] = [
            {"op": "db.query", "start_timestamp": "nonsense", "timestamp": "nonsense"},
            payload["spans"][0],
        ]

        send(client, project, project_key, payload)

        assert Transaction.objects.count() == 1
        assert Span.objects.count() == 2  # the broken one falls back to `now`, zero duration

    def test_span_count_is_capped(
        self, client: Client, project: Project, project_key: ProjectKey
    ) -> None:
        payload = txn_payload()
        payload["spans"] = payload["spans"] * 1500

        send(client, project, project_key, payload)

        assert Span.objects.count() == 1000

    def test_an_unknown_status_is_stored_as_unknown_not_rejected(
        self, client: Client, project: Project, project_key: ProjectKey
    ) -> None:
        payload = txn_payload()
        payload["contexts"]["trace"]["status"] = "wat"

        send(client, project, project_key, payload)

        assert Transaction.objects.get().status == "unknown"

    def test_a_garbage_trace_id_gets_a_generated_one(
        self, client: Client, project: Project, project_key: ProjectKey
    ) -> None:
        payload = txn_payload()
        payload["contexts"]["trace"]["trace_id"] = "not-hex"

        send(client, project, project_key, payload)

        assert len(Transaction.objects.get().trace_id) == 32

    def test_events_and_transactions_can_share_one_envelope(
        self, client: Client, project: Project, project_key: ProjectKey
    ) -> None:
        """The whole reason the envelope carries typed items."""
        body = build_envelope(
            ("event", {"exception": {"values": [{"type": "ValueError", "value": "x"}]}}),
            ("transaction", txn_payload()),
        )

        response = post(client, project, body, project_key.public_key)

        assert json_body(response)["accepted"] == 2
        assert Transaction.objects.count() == 1
