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

    def test_the_sdks_own_frames_are_never_in_app(
        self, transport: FakeTransport, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Caught running against a real app: the ASGI middleware showed up as the user's code,
        pointing an engineer at our file instead of their handler. site-packages excludes it by
        accident; an editable install or a vendored copy does not."""
        monkeypatch.setattr(obsly.client, "_client", Client(DSN, transport=transport))

        app = FastAPI()
        app.add_middleware(ObslyMiddleware)

        @app.get("/boom")
        def boom() -> None:
            raise ValueError("kaboom")

        with pytest.raises(ValueError, match="kaboom"), TestClient(app) as test_client:
            test_client.get("/boom")

        frames = transport.events()[0]["exception"]["values"][-1]["stacktrace"]["frames"]
        obsly_frames = [f for f in frames if f["module"].startswith("obsly")]
        assert obsly_frames, "the middleware frame should still be recorded"
        assert not any(f["in_app"] for f in obsly_frames)

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


class TestTracing:
    def app_with_tracing(self, transport: FakeTransport, rate: float = 1.0) -> FastAPI:
        client = Client(DSN, transport=transport, traces_sample_rate=rate)  # type: ignore[arg-type]
        obsly.client._client = client

        app = FastAPI()
        app.add_middleware(ObslyMiddleware)

        @app.get("/items/{item_id}")
        def read_item(item_id: int) -> dict[str, int]:
            with obsly.start_span("db.query", "SELECT * FROM items WHERE id = %s"):
                pass
            return {"id": item_id}

        @app.get("/boom")
        def boom() -> None:
            raise ValueError("kaboom")

        return app

    def transactions(self, transport: FakeTransport) -> list[dict[str, Any]]:
        return [event for event in transport.events() if event.get("type") == "transaction"]

    def test_names_a_transaction_by_route_pattern_not_url(self, transport: FakeTransport) -> None:
        """/items/8123 as a name makes a separate row per id and a percentile of one."""
        app = self.app_with_tracing(transport)

        with TestClient(app) as test_client:
            test_client.get("/items/8123")

        [txn] = self.transactions(transport)
        assert txn["transaction"] == "/items/{item_id}"

    def test_records_nested_spans_under_the_transaction(self, transport: FakeTransport) -> None:
        app = self.app_with_tracing(transport)

        with TestClient(app) as test_client:
            test_client.get("/items/1")

        [txn] = self.transactions(transport)
        [span] = [s for s in txn["spans"] if s["op"] == "db.query"]
        assert span["description"].startswith("SELECT")
        assert span["parent_span_id"] == txn["contexts"]["trace"]["span_id"]
        assert span["trace_id"] == txn["contexts"]["trace"]["trace_id"]

    def test_records_the_status_code(self, transport: FakeTransport) -> None:
        app = self.app_with_tracing(transport)

        with TestClient(app) as test_client:
            test_client.get("/items/1")

        [txn] = self.transactions(transport)
        assert txn["data"]["http.status_code"] == 200
        assert txn["contexts"]["trace"]["status"] == "ok"

    def test_a_failed_request_is_marked_internal_error(self, transport: FakeTransport) -> None:
        app = self.app_with_tracing(transport)

        with pytest.raises(ValueError, match="kaboom"), TestClient(app) as test_client:
            test_client.get("/boom")

        [txn] = self.transactions(transport)
        assert txn["contexts"]["trace"]["status"] == "internal_error"

    def test_sample_rate_zero_sends_nothing(self, transport: FakeTransport) -> None:
        """Tracing multiplies volume by request count; nobody should learn that from a bill."""
        app = self.app_with_tracing(transport, rate=0.0)

        with TestClient(app) as test_client:
            test_client.get("/items/1")

        assert self.transactions(transport) == []

    def test_continues_an_upstream_trace(self, transport: FakeTransport) -> None:
        app = self.app_with_tracing(transport, rate=0.0)
        upstream_trace = "a" * 32

        with TestClient(app) as test_client:
            test_client.get("/items/1", headers={"obsly-trace": f"{upstream_trace}-{'b' * 16}-1"})

        # Sampled because the upstream said so, despite this service's own rate being 0.
        [txn] = self.transactions(transport)
        assert txn["contexts"]["trace"]["trace_id"] == upstream_trace

    def test_a_malformed_trace_header_does_not_break_the_request(
        self, transport: FakeTransport
    ) -> None:
        """A broken header from an upstream we do not control must start a new trace."""
        app = self.app_with_tracing(transport)

        with TestClient(app) as test_client:
            response = test_client.get("/items/1", headers={"obsly-trace": "garbage"})

        assert response.status_code == 200
        assert len(self.transactions(transport)[0]["contexts"]["trace"]["trace_id"]) == 32

    def test_start_span_is_a_no_op_without_a_transaction(self) -> None:
        """Instrumentation in a library must not depend on the app enabling tracing."""
        obsly.client._client = None

        with obsly.start_span("db.query", "SELECT 1") as span:
            assert span.sampled is False

    def test_span_count_is_bounded(self, transport: FakeTransport) -> None:
        """A runaway loop must cost a truncated trace, not the memory of the host process."""
        client = Client(DSN, transport=transport, traces_sample_rate=1.0)  # type: ignore[arg-type]

        with client.start_transaction("bulk", "custom"):
            for _ in range(1200):
                with obsly.start_span("db.query"):
                    pass

        [txn] = self.transactions(transport)
        assert len(txn["spans"]) == 1000
        assert txn["dropped_spans"] == 200
