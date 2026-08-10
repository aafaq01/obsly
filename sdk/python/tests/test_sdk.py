import json
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import obsly
from obsly import dsn as dsn_module
from obsly.client import Client
from obsly.integrations.fastapi import ObslyMiddleware
from obsly.stacktrace import exception_chain
from obsly.transport import build_envelope

DSN = "http://abc123@localhost:8081/7"


class FakeTransport:
    """Captures envelopes instead of sending them."""

    def __init__(self) -> None:
        self.sent: list[bytes] = []

    def send(self, envelope: bytes) -> None:
        self.sent.append(envelope)

    def flush(self, timeout: float = 2.0) -> bool:
        return True

    def close(self) -> None:
        pass

    def events(self) -> list[dict[str, Any]]:
        parsed = []
        for raw in self.sent:
            lines = raw.strip().split(b"\n")
            # header, item header, payload
            parsed.append(json.loads(lines[2]))
        return parsed


@pytest.fixture
def transport() -> FakeTransport:
    return FakeTransport()


@pytest.fixture
def client(transport: FakeTransport) -> Client:
    return Client(DSN, release="demo@1.0.0", environment="test", transport=transport)  # type: ignore[arg-type]


class TestDsn:
    def test_parses_a_valid_dsn(self) -> None:
        parsed = dsn_module.parse("https://key123@obsly.example.com/42")

        assert parsed.public_key == "key123"
        assert parsed.origin == "https://obsly.example.com"
        assert parsed.project_id == "42"
        assert parsed.envelope_url == "https://obsly.example.com/api/42/envelope/"

    def test_keeps_a_non_default_port(self) -> None:
        assert dsn_module.parse(DSN).origin == "http://localhost:8081"

    @pytest.mark.parametrize(
        "bad",
        [
            "ftp://key@host/1",
            "http://host/1",  # no key
            "http://key@host/notanumber",
            "http://key@host/",
            "http://key:secret@host/1",  # DSNs have no password
            "",
        ],
    )
    def test_rejects_malformed_dsns(self, bad: str) -> None:
        with pytest.raises(dsn_module.DsnError):
            dsn_module.parse(bad)


class TestInit:
    def test_a_missing_dsn_disables_reporting_instead_of_raising(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Nobody wants their service to refuse to boot because telemetry was misconfigured."""
        monkeypatch.delenv("OBSLY_DSN", raising=False)

        assert obsly.init(dsn=None) is None
        assert obsly.capture_message("ignored") is None

    def test_a_malformed_dsn_disables_reporting_instead_of_raising(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("OBSLY_DSN", raising=False)

        assert obsly.init(dsn="totally-broken") is None

    def test_reads_the_dsn_from_the_environment(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """So a deploy can turn reporting on or off without a code change."""
        monkeypatch.setenv("OBSLY_DSN", DSN)

        assert obsly.init() is not None

        obsly.init(dsn=None)  # reset for other tests


class TestCapture:
    def test_captures_an_exception_with_type_and_value(
        self, client: Client, transport: FakeTransport
    ) -> None:
        try:
            raise ValueError("cart is empty")
        except ValueError as exc:
            client.capture_exception(exc)

        [event] = transport.events()
        assert event["exception"]["values"][-1]["type"] == "ValueError"
        assert event["exception"]["values"][-1]["value"] == "cart is empty"

    def test_includes_release_and_environment(
        self, client: Client, transport: FakeTransport
    ) -> None:
        client.capture_message("hello")

        [event] = transport.events()
        assert event["release"] == "demo@1.0.0"
        assert event["environment"] == "test"

    def test_event_id_appears_in_the_envelope_header(
        self, client: Client, transport: FakeTransport
    ) -> None:
        """The server reads it from there; omitting it breaks retry-idempotency."""
        event_id = client.capture_message("hello")

        header = json.loads(transport.sent[0].split(b"\n")[0])
        assert header["event_id"] == event_id

    def test_marks_application_frames_as_in_app(
        self, client: Client, transport: FakeTransport
    ) -> None:
        """Without this a Starlette traceback is forty frames of somebody else's middleware."""
        try:
            raise RuntimeError("boom")
        except RuntimeError as exc:
            client.capture_exception(exc)

        frames = transport.events()[0]["exception"]["values"][-1]["stacktrace"]["frames"]
        assert any(frame["in_app"] for frame in frames)

    def test_json_module_frames_are_not_in_app(self) -> None:
        try:
            json.loads("{definitely not json")
        except ValueError as exc:
            chain = exception_chain(exc)

        stdlib_frames = [
            frame
            for frame in chain[-1]["stacktrace"]["frames"]
            if "json" in frame["module"].split(".")
        ]
        assert stdlib_frames
        assert not any(frame["in_app"] for frame in stdlib_frames)


class TestExceptionChain:
    def test_records_the_whole_chain_oldest_first(self) -> None:
        try:
            try:
                raise OSError("connection reset")
            except OSError as cause:
                raise RuntimeError("retry gave up") from cause
        except RuntimeError as exc:
            chain = exception_chain(exc)

        assert [item["type"] for item in chain] == ["OSError", "RuntimeError"]

    def test_survives_a_self_referential_chain(self) -> None:
        """A cycle here would hang the process being observed."""
        first = ValueError("a")
        second = ValueError("b")
        first.__cause__ = second
        second.__cause__ = first

        assert len(exception_chain(first)) == 2

    def test_survives_an_exception_whose_str_raises(self) -> None:
        class HostileError(Exception):
            def __str__(self) -> str:
                raise RuntimeError("nope")

        assert exception_chain(HostileError())[0]["value"] == "<unprintable exception>"


class TestEnvelopeFormat:
    def test_uses_length_framing(self) -> None:
        raw = build_envelope({"event_id": "x"}, [("event", {"message": "with\nnewline"})])
        lines = raw.split(b"\n")

        assert json.loads(lines[1])["type"] == "event"
        assert json.loads(lines[1])["length"] > 0

    def test_unserialisable_values_cost_the_field_not_the_event(self) -> None:
        raw = build_envelope({"event_id": "x"}, [("event", {"obj": object()})])

        assert b"event" in raw


class TestFastapiIntegration:
    def test_reports_an_unhandled_exception_and_still_raises(
        self, transport: FakeTransport, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(obsly.client, "_client", Client(DSN, transport=transport))

        app = FastAPI()
        app.add_middleware(ObslyMiddleware)

        @app.get("/boom")
        def boom() -> None:
            raise ValueError("kaboom")

        with pytest.raises(ValueError, match="kaboom"), TestClient(app) as test_client:
            test_client.get("/boom")

        [event] = transport.events()
        assert event["exception"]["values"][-1]["type"] == "ValueError"
        assert event["extra"]["request"]["method"] == "GET"
        assert event["extra"]["request"]["route"] == "/boom"

    def test_does_not_report_successful_requests(
        self, transport: FakeTransport, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(obsly.client, "_client", Client(DSN, transport=transport))

        app = FastAPI()
        app.add_middleware(ObslyMiddleware)

        @app.get("/ok")
        def ok() -> dict[str, bool]:
            return {"ok": True}

        with TestClient(app) as test_client:
            assert test_client.get("/ok").status_code == 200

        assert transport.events() == []

    def test_strips_authorization_headers_even_with_pii_enabled(
        self, transport: FakeTransport, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An SDK that can leak a bearer token into a third-party store is a vulnerability."""
        monkeypatch.setattr(
            obsly.client, "_client", Client(DSN, transport=transport, send_default_pii=True)
        )

        app = FastAPI()
        app.add_middleware(ObslyMiddleware)

        @app.get("/boom")
        def boom() -> None:
            raise ValueError("kaboom")

        with pytest.raises(ValueError, match="kaboom"), TestClient(app) as test_client:
            test_client.get("/boom", headers={"Authorization": "Bearer supersecret"})

        headers = transport.events()[0]["extra"]["request"]["headers"]
        assert "authorization" not in headers
        assert "supersecret" not in json.dumps(headers)

    def test_omits_the_query_string_unless_pii_is_enabled(
        self, transport: FakeTransport, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(obsly.client, "_client", Client(DSN, transport=transport))

        app = FastAPI()
        app.add_middleware(ObslyMiddleware)

        @app.get("/boom")
        def boom() -> None:
            raise ValueError("kaboom")

        with pytest.raises(ValueError, match="kaboom"), TestClient(app) as test_client:
            test_client.get("/boom?email=someone@example.com")

        assert "query_string" not in transport.events()[0]["extra"]["request"]
