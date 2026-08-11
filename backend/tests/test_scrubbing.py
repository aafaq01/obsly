"""Secrets must not reach storage.

The SDK strips what it knows about. This is the half that matters: it covers what the SDK never
saw, protects clients that already shipped, and runs before the write — because anything
scrubbed after storage was still stored.
"""

from typing import Any

import pytest
from django.test import Client, override_settings

from apps.events.models import Event
from apps.ingest.scrubbing import scrub, scrub_value
from apps.logs.models import LogRecord
from apps.projects.models import Project, ProjectKey
from tests.conftest import build_envelope, post

pytestmark = pytest.mark.django_db

# A real-format Visa test number: passes Luhn, belongs to nobody.
CARD = "4111111111111111"
JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dBjftJeZ4CVPmB92K27uhbUJU1p1r_wW1g"


class TestSecretKeys:
    @pytest.mark.parametrize(
        "key",
        [
            "password",
            "PASSWORD",
            "user_password",
            "apiKey",
            "api_key",
            "X-Api-Key",
            "authorization",
            "session_id",
            "client_secret",
            "refresh_token",
            "aws_access_key",
            "signature",
        ],
    )
    def test_a_secret_named_key_is_redacted(self, key: str) -> None:
        assert scrub({key: "hunter2"})[key] == "[redacted]"

    @pytest.mark.parametrize("key", ["username", "email", "order_id", "path", "duration_ms"])
    def test_an_ordinary_key_is_left_alone(self, key: str) -> None:
        assert scrub({key: "value"})[key] == "value"

    def test_nested_keys_are_reached(self) -> None:
        payload = {"request": {"headers": {"Authorization": "Bearer abc"}}}

        assert scrub(payload)["request"]["headers"]["Authorization"] == "[redacted]"

    def test_keys_inside_lists_are_reached(self) -> None:
        payload = {"users": [{"name": "a", "password": "p"}, {"name": "b", "password": "q"}]}

        assert [user["password"] for user in scrub(payload)["users"]] == [
            "[redacted]",
            "[redacted]",
        ]

    def test_depth_is_bounded(self) -> None:
        """A payload is attacker-influenced. Deep nesting must cost a truncated scrub, not a
        recursion error on the ingest path."""
        deep: dict[str, Any] = {"password": "p"}
        for _ in range(200):
            deep = {"nested": deep}

        scrub(deep)  # must not raise


class TestSecretValues:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (f"token={JWT}", "[jwt]"),
            ("ghp_abcdefghijklmnopqrstuvwxyz0123", "[github-token]"),
            ("sk-abcdefghijklmnopqrstuvwx", "[api-key]"),
            ("AKIAIOSFODNN7EXAMPLE", "[aws-key-id]"),
            ("xoxb-1234567890-abcdefghij", "[slack-token]"),
        ],
    )
    def test_a_secret_shaped_value_is_redacted_whatever_it_is_called(
        self, value: str, expected: str
    ) -> None:
        """The value is the giveaway, so the key it arrived under does not matter."""
        assert expected in scrub_value(value)

    def test_a_private_key_block_is_redacted_whole(self) -> None:
        pem = "-----BEGIN RSA PRIVATE KEY-----\nMIIB\nAQAB\n-----END RSA PRIVATE KEY-----"

        assert scrub_value(pem) == "[private-key]"

    def test_a_bearer_header_keeps_its_shape(self) -> None:
        """Redacting the whole header would lose that it was a bearer token at all."""
        assert scrub_value("Bearer abcdefghijklmnopqrst") == "Bearer [redacted]"

    def test_a_card_number_is_redacted(self) -> None:
        assert scrub_value(f"charged {CARD}") == "charged [card]"

    def test_a_separated_card_number_is_redacted(self) -> None:
        assert "[card]" in scrub_value("4111 1111 1111 1111")

    def test_a_long_id_that_is_not_a_card_survives(self) -> None:
        """Luhn is the difference between a scrubber people keep on and one they turn off: an
        order id of card length is not a card, and blanking it helps nobody."""
        assert scrub_value("order 1234567890123456") == "order 1234567890123456"

    def test_a_timestamp_is_not_mistaken_for_a_card(self) -> None:
        assert scrub_value("ts 20260812000000000") == "ts 20260812000000000"


class TestIngestBoundary:
    def error(self, **extra: Any) -> dict[str, Any]:
        return {
            "exception": {"values": [{"type": "ValueError", "value": "boom"}]},
            **extra,
        }

    def test_the_stored_payload_is_scrubbed_not_only_the_columns(
        self, client: Client, project: Project, project_key: ProjectKey
    ) -> None:
        """The raw payload is kept verbatim for reprocessing, so scrubbing the extracted
        columns alone would leave the secret in the row anyway."""
        payload = self.error(extra={"password": "hunter2", "note": f"key {JWT}"})

        post(client, project, build_envelope(("event", payload)), project_key.public_key)

        stored = Event.objects.get().payload
        assert stored["extra"]["password"] == "[redacted]"
        assert JWT not in str(stored)

    def test_a_log_body_is_scrubbed(
        self, client: Client, project: Project, project_key: ProjectKey
    ) -> None:
        """The case that made this necessary: arbitrary logging output nobody vetted."""
        body = build_envelope(
            (
                "log",
                {
                    "items": [
                        {"timestamp": "2026-08-12T00:00:00Z", "body": f"auth failed token={JWT}"}
                    ]
                },
            )
        )

        post(client, project, body, project_key.public_key)

        assert JWT not in LogRecord.objects.get().body
        assert "[jwt]" in LogRecord.objects.get().body

    def test_a_log_attribute_is_scrubbed(
        self, client: Client, project: Project, project_key: ProjectKey
    ) -> None:
        body = build_envelope(
            (
                "log",
                {
                    "items": [
                        {
                            "timestamp": "2026-08-12T00:00:00Z",
                            "body": "charged",
                            "attributes": {"api_key": "sk-livekey", "order": "A-1"},
                        }
                    ]
                },
            )
        )

        post(client, project, body, project_key.public_key)

        record = LogRecord.objects.get()
        assert record.attributes["api_key"] == "[redacted]"
        assert record.attributes["order"] == "A-1"

    def test_a_span_description_is_scrubbed(
        self, client: Client, project: Project, project_key: ProjectKey
    ) -> None:
        from apps.tracing.models import Span

        transaction = {
            "transaction": "/x",
            "start_timestamp": "2026-08-12T00:00:00Z",
            "timestamp": "2026-08-12T00:00:01Z",
            "contexts": {"trace": {"trace_id": "a" * 32, "span_id": "b" * 16}},
            "spans": [
                {
                    "span_id": "c" * 16,
                    "op": "db.query",
                    # A span description in a fixture, not a query this process runs. The
                    # unparameterised form is the point: it is what a badly-instrumented
                    # application would send us, and the value is what must not survive.
                    "description": f"SELECT * FROM t WHERE card = '{CARD}'",  # noqa: S608
                    "start_timestamp": "2026-08-12T00:00:00Z",
                    "timestamp": "2026-08-12T00:00:01Z",
                }
            ],
        }

        post(client, project, build_envelope(("transaction", transaction)), project_key.public_key)

        assert CARD not in Span.objects.get().description

    def test_ordinary_content_survives_intact(
        self, client: Client, project: Project, project_key: ProjectKey
    ) -> None:
        """A scrubber that mangles normal payloads is one people switch off."""
        payload = self.error(release="api@2.1.0", extra={"order_id": "ord-9182", "count": 42})

        post(client, project, build_envelope(("event", payload)), project_key.public_key)

        event = Event.objects.get()
        assert event.release == "api@2.1.0"
        assert event.payload["extra"]["order_id"] == "ord-9182"
        assert event.payload["extra"]["count"] == 42

    @override_settings(OBSLY_SCRUB_SECRETS=False)
    def test_it_can_be_turned_off_deliberately(
        self, client: Client, project: Project, project_key: ProjectKey
    ) -> None:
        """Off is a decision with a name, not the default."""
        payload = self.error(extra={"password": "hunter2"})

        post(client, project, build_envelope(("event", payload)), project_key.public_key)

        assert Event.objects.get().payload["extra"]["password"] == "hunter2"


class TestReviewFindings:
    """Every one of these was found by reading the code, not by running it."""

    def test_a_hex_span_id_that_looks_like_a_card_survives(self) -> None:
        """ "4000000000000010" is a valid 16-hex span id and a Luhn-valid card by shape.
        Redacting it made _hex() drop the id entirely and orphaned every child span."""
        payload = {"span_id": "4000000000000010", "parent_span_id": "4000000000000010"}

        assert scrub(payload) == payload

    def test_structural_ids_are_left_alone_wherever_they_appear(self) -> None:
        payload = {"contexts": {"trace": {"trace_id": "4" * 32, "span_id": "4000000000000010"}}}

        assert scrub(payload)["contexts"]["trace"]["span_id"] == "4000000000000010"

    def test_the_depth_limit_fails_closed(self) -> None:
        """A deny-by-default scrubber whose deepest branch is allow-by-default is not
        deny-by-default."""
        deep: dict[str, Any] = {"password": "hunter2"}
        for _ in range(40):
            deep = {"nested": deep}

        assert "hunter2" not in str(scrub(deep))

    @pytest.mark.parametrize(
        "key",
        ["sk-proj-abcdefghijklmnopqrst", "sk-svcacct-abcdefghijklmnopqrst"],
    )
    def test_modern_hyphenated_api_keys_are_caught(self, key: str) -> None:
        """A pattern that stops at the first hyphen matches nothing at all on these."""
        assert "[api-key]" in scrub_value(key)

    @pytest.mark.parametrize("key", ["author", "oauth_provider", "authored_by"])
    def test_ordinary_names_containing_a_secret_word_survive(self, key: str) -> None:
        """Substring matching redacted "author" for containing "auth". Over-redaction that
        destroys ordinary fields is how a scrubber ends up switched off."""
        assert scrub({key: "ada"})[key] == "ada"

    def test_a_container_under_a_secret_name_keeps_its_shape(self) -> None:
        """Replacing the whole value destroys the context around the secret."""
        scrubbed = scrub({"session": {"id": 1, "token": "x", "roles": ["admin"]}})

        assert set(scrubbed["session"]) == {"id", "token", "roles"}
        assert scrubbed["session"]["token"] == "[redacted]"
        assert scrubbed["session"]["roles"] == ["[redacted]"]

    def test_the_card_rule_is_not_selected_by_position(self) -> None:
        """It used to be identified as "the last pattern in the list", so appending any new
        rule would silently turn it into an unconditional substitution."""
        from apps.ingest import scrubbing

        assert scrubbing.CARD_PATTERN not in [pattern for pattern, _ in scrubbing.VALUE_PATTERNS]
        assert scrub_value("order 1234567890123456") == "order 1234567890123456"

    @pytest.mark.parametrize(
        "key", ["api_key", "apiKey", "api-key", "X-Api-Key", "APIKEY", "x_api_key"]
    )
    def test_one_rule_covers_every_spelling_of_a_compound_name(self, key: str) -> None:
        """Separators are collapsed before matching, so the list does not need a line per
        spelling — and a spelling nobody thought of still hits."""
        assert scrub({key: "live-key"})[key] == "[redacted]"
