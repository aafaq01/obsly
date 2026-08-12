import json
import logging
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import obsly
from obsly import dsn as dsn_module
from obsly.client import Client
from obsly.integrations.fastapi import ObslyMiddleware
from obsly.logs import LogBuffer, ObslyLogHandler
from obsly.stacktrace import exception_chain
from obsly.tracing import Transaction
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


class TestErrorTraceCorrelation:
    def test_an_error_inside_a_transaction_carries_its_trace(
        self, transport: FakeTransport
    ) -> None:
        """The one line that makes an error and a trace the same story instead of two tables
        joined by a timestamp guess."""
        client = Client(DSN, transport=transport, traces_sample_rate=1.0)  # type: ignore[arg-type]

        with client.start_transaction("/checkout", "http.server") as transaction:
            client.capture_exception(ValueError("cart is empty"))

        errors = [e for e in transport.events() if e.get("type") != "transaction"]
        assert errors[0]["contexts"]["trace"]["trace_id"] == transaction.trace_id

    def test_an_error_outside_a_transaction_carries_no_trace(
        self, transport: FakeTransport
    ) -> None:
        client = Client(DSN, transport=transport)  # type: ignore[arg-type]

        client.capture_exception(ValueError("boom"))

        assert "contexts" not in transport.events()[0]

    def test_an_unsampled_transaction_does_not_attach_a_trace(
        self, transport: FakeTransport
    ) -> None:
        """Pointing an error at a trace that was never recorded is a dead link."""
        client = Client(DSN, transport=transport, traces_sample_rate=0.0)  # type: ignore[arg-type]

        with client.start_transaction("/checkout", "http.server"):
            client.capture_exception(ValueError("boom"))

        errors = [e for e in transport.events() if e.get("type") != "transaction"]
        assert "contexts" not in errors[0]

    def test_the_span_id_is_the_innermost_active_span(self, transport: FakeTransport) -> None:
        """So the error pins to the specific operation, not just the request."""
        client = Client(DSN, transport=transport, traces_sample_rate=1.0)  # type: ignore[arg-type]
        obsly.client._client = client

        with (
            client.start_transaction("/checkout", "http.server"),
            obsly.start_span("db.query", "SELECT 1") as span,
        ):
            client.capture_exception(ValueError("boom"))
            inner = span.span_id

        errors = [e for e in transport.events() if e.get("type") != "transaction"]
        assert errors[0]["contexts"]["trace"]["span_id"] == inner


class TestLogs:
    def logs(self, transport: FakeTransport) -> list[dict[str, Any]]:
        records = []
        for raw in transport.sent:
            lines = raw.strip().split(b"\n")
            payload = json.loads(lines[2])
            records.extend(payload.get("items", []))
        return records

    def test_logs_are_off_unless_enabled(self, transport: FakeTransport) -> None:
        """Logs are the highest-volume signal by far. Turning that on must be a decision."""
        client = Client(DSN, transport=transport)  # type: ignore[arg-type]

        client.capture_log("info", "hello")
        client.flush()

        assert self.logs(transport) == []

    def test_records_are_batched_not_sent_one_by_one(self, transport: FakeTransport) -> None:
        """One HTTP request per line would make logging the slowest thing an app does."""
        client = Client(DSN, transport=transport, enable_logs=True)  # type: ignore[arg-type]

        for index in range(5):
            client.capture_log("info", f"line {index}")

        assert transport.sent == []

        client.flush()
        assert len(transport.sent) == 1
        assert len(self.logs(transport)) == 5

    def test_flush_drains_buffered_logs(self, transport: FakeTransport) -> None:
        """A process exiting cleanly must not lose what it said in the last few seconds."""
        client = Client(DSN, transport=transport, enable_logs=True)  # type: ignore[arg-type]
        client.capture_log("warning", "about to exit")

        client.flush()

        assert self.logs(transport)[0]["body"] == "about to exit"

    def test_a_log_inside_a_transaction_carries_its_trace(self, transport: FakeTransport) -> None:
        client = Client(  # type: ignore[arg-type]
            DSN, transport=transport, enable_logs=True, traces_sample_rate=1.0
        )

        with client.start_transaction("/checkout", "http.server") as transaction:
            client.capture_log("info", "cart loaded")
        client.flush()

        assert self.logs(transport)[0]["trace_id"] == transaction.trace_id

    def test_a_successful_request_still_logs(self, transport: FakeTransport) -> None:
        """The point of the success case: most requests succeed, and the explanation for the
        ones that do not usually lives in them."""
        client = Client(  # type: ignore[arg-type]
            DSN, transport=transport, enable_logs=True, traces_sample_rate=1.0
        )

        with client.start_transaction("/healthz", "http.server") as transaction:
            client.capture_log("info", "all good")
        client.flush()

        assert transaction.status == "ok"
        assert self.logs(transport)[0]["body"] == "all good"

    def test_the_buffer_empties_at_capacity_so_it_cannot_grow(self) -> None:
        """A burst of logs must cost truncated telemetry, not the memory of the process. The
        buffer bounds itself by emptying; the transport queue is what drops under real
        pressure."""
        buffer = LogBuffer(capacity=10)
        batches = [buffer.add({"body": str(index)}) for index in range(500)]

        assert len([b for b in batches if b]) == 50
        assert len(buffer.drain()) == 0

    def test_stdlib_handler_forwards_existing_log_calls(self, transport: FakeTransport) -> None:
        """An SDK that only sees logs written specially for it sees the least interesting ones."""
        obsly.client._client = Client(DSN, transport=transport, enable_logs=True)  # type: ignore[arg-type]

        stdlib = logging.getLogger("app.billing")
        stdlib.setLevel(logging.INFO)
        handler = ObslyLogHandler()
        stdlib.addHandler(handler)
        try:
            stdlib.warning("card declined for %s", "order-7")
        finally:
            stdlib.removeHandler(handler)

        obsly.client._client.flush()

        [record] = self.logs(transport)
        assert record["level"] == "warning"
        assert record["body"] == "card declined for order-7"
        assert record["logger"] == "app.billing"
        # The un-interpolated template is kept: it groups, the formatted string does not.
        assert record["attributes"]["template"] == "card declined for %s"

    def test_the_handler_never_raises_into_the_application(
        self, transport: FakeTransport, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A logging handler that raises turns every log call into a failure."""
        obsly.client._client = Client(DSN, transport=transport, enable_logs=True)  # type: ignore[arg-type]
        monkeypatch.setattr(obsly.client._client, "capture_log", _raise, raising=True)

        stdlib = logging.getLogger("app.hostile")
        handler = ObslyLogHandler()
        handler.handleError = lambda record: None  # type: ignore[method-assign]
        stdlib.addHandler(handler)
        try:
            stdlib.error("this must not blow up")
        finally:
            stdlib.removeHandler(handler)


def _raise(*args: Any, **kwargs: Any) -> None:
    raise RuntimeError("transport exploded")


class TestLogFlusher:
    def test_a_stale_batch_is_flushed_without_another_log_call(
        self, transport: FakeTransport
    ) -> None:
        """The buffer's age check only runs on add(). Without a background flusher the last
        lines of a burst sit stranded until the application happens to log again — and the end
        of a burst is usually the part worth reading."""
        client = Client(DSN, transport=transport, enable_logs=True)  # type: ignore[arg-type]
        client.capture_log("info", "last thing before it went quiet")
        assert transport.sent == []

        # Drive the loop body directly rather than sleeping: a test that waits on a real
        # 2-second interval is a test that makes the suite slower for everyone.
        client._send_logs(client._logs.drain())

        assert transport.sent != []

    def test_the_flusher_thread_only_runs_when_logs_are_enabled(
        self, transport: FakeTransport
    ) -> None:
        assert Client(DSN, transport=transport)._flusher is None  # type: ignore[arg-type]
        assert Client(DSN, transport=transport, enable_logs=True)._flusher is not None  # type: ignore[arg-type]


class TestSqlalchemyIntegration:
    """Automatic database spans — the point being that the application adds no span calls."""

    @pytest.fixture
    def engine(self) -> Iterator[Any]:
        from sqlalchemy import create_engine, text

        import obsly.integrations.sqlalchemy as integration

        integration._instrumented = False
        assert integration.instrument() is True

        created = create_engine("sqlite://")
        with created.begin() as conn:
            conn.execute(text("CREATE TABLE users (id INTEGER, email TEXT)"))
            conn.execute(text("INSERT INTO users VALUES (1, 'a@example.com')"))

        yield created, text

        # Disposed explicitly: left to the garbage collector, sqlite finalises its connections
        # at an arbitrary later moment and pytest reports it as an unraisable exception.
        created.dispose()

    def spans(self, transport: FakeTransport) -> list[dict[str, Any]]:
        for event in transport.events():
            if event.get("type") == "transaction":
                return event["spans"]
        return []

    def test_a_query_becomes_a_span_without_any_application_change(
        self, transport: FakeTransport, engine: Any
    ) -> None:
        engine, text = engine
        client = Client(DSN, transport=transport, traces_sample_rate=1.0)  # type: ignore[arg-type]

        with client.start_transaction("/users", "http.server"), engine.connect() as conn:
            conn.execute(text("SELECT email FROM users WHERE id = :id"), {"id": 1})

        [span] = [s for s in self.spans(transport) if s["op"] == "db.query"]
        assert "SELECT email FROM users" in span["description"]
        assert span["data"]["db.system"] == "sqlite"

    def test_parameter_values_are_never_captured(
        self, transport: FakeTransport, engine: Any
    ) -> None:
        """Bind values are the row itself, which is where the personal data lives."""
        engine, text = engine
        client = Client(DSN, transport=transport, traces_sample_rate=1.0)  # type: ignore[arg-type]

        with client.start_transaction("/users", "http.server"), engine.connect() as conn:
            conn.execute(
                text("SELECT id FROM users WHERE email = :email"),
                {"email": "secret@example.com"},
            )

        assert "secret@example.com" not in json.dumps(self.spans(transport))

    def test_statements_are_collapsed_to_one_line(
        self, transport: FakeTransport, engine: Any
    ) -> None:
        """A multi-line ORM SELECT is unreadable in a waterfall row, and the indentation is
        not what identifies the query."""
        engine, text = engine
        client = Client(DSN, transport=transport, traces_sample_rate=1.0)  # type: ignore[arg-type]

        with client.start_transaction("/users", "http.server"), engine.connect() as conn:
            conn.execute(text("SELECT\n   email\nFROM users\nWHERE id = 1"))

        [span] = [s for s in self.spans(transport) if "email" in s["description"]]
        assert "\n" not in span["description"]

    def test_a_failing_query_still_produces_a_span(
        self, transport: FakeTransport, engine: Any
    ) -> None:
        """A failed query still took time, and its span is the only record of how long."""
        engine, text = engine
        client = Client(DSN, transport=transport, traces_sample_rate=1.0)  # type: ignore[arg-type]

        with (
            client.start_transaction("/users", "http.server"),
            engine.connect() as conn,
            pytest.raises(Exception, match="no such table"),
        ):
            conn.execute(text("SELECT * FROM table_that_does_not_exist"))

        assert [s for s in self.spans(transport) if "does_not_exist" in s["description"]]

    def test_queries_outside_a_transaction_are_ignored(
        self, transport: FakeTransport, engine: Any
    ) -> None:
        """Instrumentation in a library must not depend on the app enabling tracing."""
        engine, text = engine
        obsly.client._client = Client(DSN, transport=transport)  # type: ignore[arg-type]

        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))

        assert transport.sent == []

    def test_instrumenting_twice_does_not_double_count(
        self, transport: FakeTransport, engine: Any
    ) -> None:
        """A framework and an application both calling instrument() is normal."""
        engine, text = engine
        import obsly.integrations.sqlalchemy as integration

        integration.instrument()
        client = Client(DSN, transport=transport, traces_sample_rate=1.0)  # type: ignore[arg-type]

        with client.start_transaction("/users", "http.server"), engine.connect() as conn:
            conn.execute(text("SELECT 1"))

        assert len([s for s in self.spans(transport) if s["op"] == "db.query"]) == 1


def test_a_continued_trace_keeps_the_span_it_continues_from() -> None:
    """The incoming header is parsed and the parent stored, and it was then dropped on the way
    back out.

    Both sides shared a trace id with no link between them — which looks correct in a trace
    holding one request, and says nothing in a page that made four.
    """
    transaction = Transaction(
        op="http.server",
        description="GET /checkout",
        trace_id="a" * 32,
        span_id="c" * 16,
        parent_span_id="b" * 16,
        name="GET /checkout",
    )

    trace = transaction.to_payload()["contexts"]["trace"]

    assert trace["trace_id"] == "a" * 32
    assert trace["parent_span_id"] == "b" * 16, "the caller's span must survive serialisation"
