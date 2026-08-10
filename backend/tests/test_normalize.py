from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from apps.events.models import Event
from apps.ingest.normalize import MAX_CLOCK_SKEW, event_from_payload
from apps.projects.models import Project

NOW = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)

pytestmark = pytest.mark.django_db


def normalize(project: Project, payload: dict[str, Any]) -> Event:
    return event_from_payload(project, payload, now=NOW)


class TestTimestamp:
    def test_accepts_iso8601_with_z(self, project: Project) -> None:
        event = normalize(project, {"timestamp": "2026-08-10T11:59:00Z"})

        assert event.timestamp == datetime(2026, 8, 10, 11, 59, tzinfo=UTC)

    def test_accepts_a_unix_timestamp(self, project: Project) -> None:
        event = normalize(project, {"timestamp": NOW.timestamp() - 60})

        assert event.timestamp == NOW - timedelta(minutes=1)

    def test_assumes_utc_when_no_timezone_is_given(self, project: Project) -> None:
        event = normalize(project, {"timestamp": "2026-08-10T11:00:00"})

        assert event.timestamp.tzinfo is not None

    def test_clamps_a_future_timestamp(self, project: Project) -> None:
        """One event dated 2049 would sit at the top of every 'latest' list forever."""
        event = normalize(project, {"timestamp": "2049-01-01T00:00:00Z"})

        assert event.timestamp == NOW + MAX_CLOCK_SKEW

    def test_keeps_old_timestamps(self, project: Project) -> None:
        """Offline buffering is legitimate — a phone reports yesterday's crash today."""
        event = normalize(project, {"timestamp": "2026-08-01T00:00:00Z"})

        assert event.timestamp == datetime(2026, 8, 1, tzinfo=UTC)

    @pytest.mark.parametrize("bad", ["not-a-date", "", None, {}, [], True, 10**20])
    def test_falls_back_to_now_on_junk(self, project: Project, bad: Any) -> None:
        assert normalize(project, {"timestamp": bad}).timestamp == NOW


class TestException:
    def test_extracts_type_and_value(self, project: Project) -> None:
        event = normalize(
            project,
            {"exception": {"values": [{"type": "ValueError", "value": "bad input"}]}},
        )

        assert event.exception_type == "ValueError"
        assert event.exception_value == "bad input"

    def test_uses_the_innermost_exception_of_a_chain(self, project: Project) -> None:
        """Chains are ordered oldest cause first; the last entry is what was actually raised."""
        event = normalize(
            project,
            {
                "exception": {
                    "values": [
                        {"type": "OSError", "value": "connection reset"},
                        {"type": "RetryError", "value": "gave up after 3"},
                    ]
                }
            },
        )

        assert event.exception_type == "RetryError"

    def test_culprit_is_the_deepest_in_app_frame(self, project: Project) -> None:
        event = normalize(
            project,
            {
                "exception": {
                    "values": [
                        {
                            "type": "ValueError",
                            "value": "x",
                            "stacktrace": {
                                "frames": [
                                    {"module": "app.main", "function": "handler", "in_app": True},
                                    {"module": "app.svc", "function": "charge", "in_app": True},
                                    {"module": "site.lib", "function": "post", "in_app": False},
                                ]
                            },
                        }
                    ]
                }
            },
        )

        assert event.culprit == "app.svc in charge"

    def test_title_reads_like_a_traceback(self, project: Project) -> None:
        event = normalize(
            project, {"exception": {"values": [{"type": "KeyError", "value": "'user_id'"}]}}
        )

        assert event.title == "KeyError: 'user_id'"

    @pytest.mark.parametrize("shape", [{"exception": "boom"}, {"exception": {}}, {}])
    def test_survives_a_payload_with_no_usable_exception(
        self, project: Project, shape: dict[str, Any]
    ) -> None:
        event = normalize(project, shape)

        assert event.exception_type == ""


class TestHostileInput:
    def test_unknown_level_falls_back_to_error(self, project: Project) -> None:
        assert normalize(project, {"level": "catastrophic"}).level == "error"

    def test_oversized_fields_are_truncated_not_rejected(self, project: Project) -> None:
        """A 10KB release string is no reason to lose the stack trace attached to it."""
        event = normalize(project, {"release": "v" * 10_000})

        assert len(event.release) == 128

    def test_nested_tag_values_are_dropped(self, project: Project) -> None:
        """Tags are indexed and filterable, so they must stay flat."""
        event = normalize(project, {"tags": {"ok": "yes", "nested": {"a": 1}, "list": [1]}})

        assert event.tags == {"ok": "yes"}

    def test_tag_count_is_capped(self, project: Project) -> None:
        event = normalize(project, {"tags": {f"k{i}": "v" for i in range(200)}})

        assert len(event.tags) == 50

    def test_non_dict_tags_are_ignored(self, project: Project) -> None:
        assert normalize(project, {"tags": ["a", "b"]}).tags == {}

    def test_structured_message_prefers_the_template(self, project: Project) -> None:
        """The un-interpolated template groups; the formatted string makes N distinct issues."""
        event = normalize(project, {"message": {"message": "user %s not found", "params": ["7"]}})

        assert event.message == "user %s not found"

    def test_client_event_id_is_honoured_so_retries_are_idempotent(self, project: Project) -> None:
        event = normalize(project, {"event_id": "6ba7b810-9dad-11d1-80b4-00c04fd430c8"})

        assert str(event.id) == "6ba7b810-9dad-11d1-80b4-00c04fd430c8"

    def test_malformed_event_id_gets_a_generated_one(self, project: Project) -> None:
        event = normalize(project, {"event_id": "not-a-uuid"})

        assert event.id is not None

    def test_the_original_payload_is_kept_verbatim(self, project: Project) -> None:
        """Normalisation is lossy and our idea of the interesting fields will change."""
        payload: dict[str, Any] = {"level": "warning", "custom_field": {"deep": [1, 2, 3]}}

        assert normalize(project, payload).payload == payload
