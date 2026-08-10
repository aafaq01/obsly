import json

import pytest

from apps.ingest.envelope import EnvelopeError, EnvelopeTooLargeError, parse

BIG = 1_000_000


class TestFraming:
    def test_reads_header_and_items(self) -> None:
        raw = (
            b'{"event_id":"abc"}\n'
            b'{"type":"event"}\n'
            b'{"level":"error"}\n'
            b'{"type":"session"}\n'
            b'{"status":"crashed"}\n'
        )

        result = parse(raw, max_bytes=BIG)

        assert result.headers == {"event_id": "abc"}
        assert [item.type for item in result.items] == ["event", "session"]
        assert result.items[0].json() == {"level": "error"}

    def test_length_framing_allows_newlines_inside_a_payload(self) -> None:
        """The whole point of `length`: a payload containing a newline is unreadable without it."""
        payload = json.dumps({"message": "line one\nline two"}).encode()
        raw = (
            b"{}\n"
            + json.dumps({"type": "event", "length": len(payload)}).encode()
            + b"\n"
            + payload
            + b"\n"
        )

        result = parse(raw, max_bytes=BIG)

        assert result.items[0].json()["message"] == "line one\nline two"

    def test_trailing_newline_does_not_create_a_phantom_item(self) -> None:
        raw = b'{}\n{"type":"event"}\n{"a":1}\n\n\n'

        assert len(parse(raw, max_bytes=BIG).items) == 1

    def test_unknown_item_types_are_kept_not_rejected(self) -> None:
        """An old server must accept a newer SDK, or every SDK release is a coordinated deploy."""
        raw = b'{}\n{"type":"metric_from_the_future"}\n{"x":1}\n{"type":"event"}\n{"a":1}\n'

        result = parse(raw, max_bytes=BIG)

        assert len(result.items) == 2
        assert [item.type for item in result.items_of_type("event")] == ["event"]


class TestRejection:
    def test_rejects_oversize_before_parsing(self) -> None:
        with pytest.raises(EnvelopeTooLargeError):
            parse(b"x" * 101, max_bytes=100)

    def test_rejects_empty(self) -> None:
        with pytest.raises(EnvelopeError, match="empty"):
            parse(b"   \n  ", max_bytes=BIG)

    def test_rejects_non_json_header(self) -> None:
        with pytest.raises(EnvelopeError, match="not valid JSON"):
            parse(b"not json\n", max_bytes=BIG)

    def test_rejects_json_header_that_is_not_an_object(self) -> None:
        with pytest.raises(EnvelopeError, match="must be a JSON object"):
            parse(b"[1,2,3]\n", max_bytes=BIG)

    def test_rejects_item_without_a_type(self) -> None:
        with pytest.raises(EnvelopeError, match="missing a string 'type'"):
            parse(b'{}\n{"length":2}\n{}\n', max_bytes=BIG)

    def test_rejects_length_longer_than_the_remaining_body(self) -> None:
        """Otherwise a crafted length is an out-of-bounds read of whatever follows."""
        with pytest.raises(EnvelopeError, match=r"only \d+ remain"):
            parse(b'{}\n{"type":"event","length":9999}\n{"a":1}\n', max_bytes=BIG)

    def test_rejects_negative_length(self) -> None:
        with pytest.raises(EnvelopeError, match="non-negative integer"):
            parse(b'{}\n{"type":"event","length":-5}\n{"a":1}\n', max_bytes=BIG)

    def test_rejects_boolean_length(self) -> None:
        """bool is a subclass of int, so `true` would silently read one byte."""
        with pytest.raises(EnvelopeError, match="non-negative integer"):
            parse(b'{}\n{"type":"event","length":true}\n{"a":1}\n', max_bytes=BIG)

    def test_rejects_too_many_items(self) -> None:
        raw = b"{}\n" + b'{"type":"event"}\n{"a":1}\n' * 20

        with pytest.raises(EnvelopeError, match="more than 5 items"):
            parse(raw, max_bytes=BIG, max_items=5)

    def test_rejects_invalid_utf8_payload(self) -> None:
        raw = b'{}\n{"type":"event"}\n' + b"\xff\xfe\n"

        with pytest.raises(EnvelopeError):
            parse(raw, max_bytes=BIG).items[0].json()
