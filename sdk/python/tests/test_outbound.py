"""The call this service makes.

Continuing an incoming trace was never the hard half. The half that makes a *chain* possible is
this one: when the service calls another, the trace has to travel with the request, or it stops
at this edge and every hop beyond it is an unrelated row in somebody else's project.
"""

import json
from collections.abc import Iterator
from typing import Any

import pytest

import obsly
from obsly import client as client_module
from obsly.integrations import outbound
from obsly.tracing import TRACE_HEADER

DSN = "http://abc123@localhost:8081/7"


class FakeTransport:
    def __init__(self) -> None:
        self.sent: list[bytes] = []

    def send(self, envelope: bytes) -> None:
        self.sent.append(envelope)

    def flush(self, timeout: float = 2.0) -> bool:
        return True

    def close(self) -> None:
        pass

    def payloads(self, kind: str) -> list[dict[str, Any]]:
        out = []
        for raw in self.sent:
            lines = raw.strip().split(b"\n")
            if json.loads(lines[1]).get("type") == kind:
                out.append(json.loads(lines[2]))
        return out


@pytest.fixture
def installed() -> Iterator[FakeTransport]:
    transport = FakeTransport()
    obsly.init(DSN, release="demo@1", traces_sample_rate=1.0, transport=transport)
    yield transport
    outbound.uninstrument()
    client_module._client = None


class FakeResponse:
    def __init__(self, status: int = 200) -> None:
        self.status_code = status


class FakeRequest:
    def __init__(self, method: str = "GET", url: str = "https://payments.internal/charge") -> None:
        self.method = method
        self.url = url
        self.headers: dict[str, str] = {}


class TestRequests:
    """`requests` — patched at Session.send, which every call funnels through."""

    @pytest.fixture
    def requests_module(self, installed: FakeTransport, monkeypatch: Any) -> Any:
        """A stand-in module, so the test does not need the real library installed.

        The patch reads sys.modules and does not import anything, which is the behaviour under
        test as much as the header is: an SDK that imports requests to instrument it has added
        a dependency to a process that chose not to have one.
        """
        import sys
        import types

        module = types.ModuleType("requests")
        calls: list[FakeRequest] = []

        class Session:
            def send(self, request: FakeRequest, **kwargs: Any) -> FakeResponse:
                calls.append(request)
                # One URL that never answers, so the failure path is exercised through the
                # patch rather than by patching over it.
                if "/unreachable" in str(request.url):
                    raise ConnectionError("refused")
                return FakeResponse()

        module.Session = Session  # type: ignore[attr-defined]
        module.calls = calls  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "requests", module)
        outbound.uninstrument()
        outbound.instrument()
        return module

    def test_the_trace_travels_with_the_request(
        self, installed: FakeTransport, requests_module: Any
    ) -> None:
        client = client_module.get_client()
        assert client is not None

        with client.start_transaction("/checkout", op="http.server") as transaction:
            requests_module.Session().send(FakeRequest("POST"))

        header = requests_module.calls[0].headers[TRACE_HEADER]
        assert header.startswith(transaction.trace_id)
        assert header.endswith("-1")

    def test_the_downstream_service_is_told_which_span_called_it(
        self, installed: FakeTransport, requests_module: Any
    ) -> None:
        """Not the transaction's id — the client span's. That is what makes the receiving
        service's transaction indent under this exact call rather than under the service."""
        client = client_module.get_client()
        assert client is not None

        with client.start_transaction("/checkout", op="http.server"):
            requests_module.Session().send(FakeRequest("POST"))

        [transaction] = installed.payloads("transaction")
        [span] = transaction["spans"]
        assert requests_module.calls[0].headers[TRACE_HEADER].split("-")[1] == span["span_id"]

    def test_the_call_becomes_a_span(self, installed: FakeTransport, requests_module: Any) -> None:
        client = client_module.get_client()
        assert client is not None

        with client.start_transaction("/checkout", op="http.server"):
            requests_module.Session().send(FakeRequest("POST"))

        [transaction] = installed.payloads("transaction")
        [span] = transaction["spans"]
        assert span["op"] == "http.client"
        assert span["description"] == "POST https://payments.internal/charge"
        assert span["data"]["http.status_code"] == 200

    def test_a_query_string_is_never_recorded(
        self, installed: FakeTransport, requests_module: Any
    ) -> None:
        """A URL is where session tokens and email addresses end up, and a span description is
        stored and displayed."""
        client = client_module.get_client()
        assert client is not None

        with client.start_transaction("/checkout", op="http.server"):
            requests_module.Session().send(
                FakeRequest("GET", "https://payments.internal/charge?token=hunter2")
            )

        [transaction] = installed.payloads("transaction")
        assert "hunter2" not in json.dumps(transaction)

    def test_a_failed_call_still_leaves_a_span(
        self, installed: FakeTransport, requests_module: Any
    ) -> None:
        """A request that never answered is the most interesting kind, and otherwise the gap in
        the waterfall."""
        client = client_module.get_client()
        assert client is not None

        with (
            client.start_transaction("/checkout", op="http.server"),
            pytest.raises(ConnectionError),
        ):
            requests_module.Session().send(
                FakeRequest("POST", "https://payments.internal/unreachable")
            )

        [transaction] = installed.payloads("transaction")
        assert transaction["spans"][0]["status"] == "internal_error"
        assert "http.status_code" not in transaction["spans"][0]["data"]

    def test_a_call_outside_a_transaction_sends_no_header(
        self, installed: FakeTransport, requests_module: Any
    ) -> None:
        """There is nothing to continue. A header pointing at a span the collector will never
        see would give the receiving service a parent that does not exist."""
        requests_module.Session().send(FakeRequest("POST"))

        assert TRACE_HEADER not in requests_module.calls[0].headers

    def test_instrumenting_twice_does_not_double_the_span(
        self, installed: FakeTransport, requests_module: Any
    ) -> None:
        outbound.instrument()
        client = client_module.get_client()
        assert client is not None

        with client.start_transaction("/checkout", op="http.server"):
            requests_module.Session().send(FakeRequest("POST"))

        [transaction] = installed.payloads("transaction")
        assert len(transaction["spans"]) == 1

    def test_uninstrument_puts_the_original_back(
        self, installed: FakeTransport, requests_module: Any
    ) -> None:
        """A patch that outlives the SDK is a patch nobody can turn off."""
        patched = requests_module.Session.send
        outbound.uninstrument()

        assert requests_module.Session.send is not patched


class TestHttpx:
    @pytest.fixture
    def httpx_module(self, installed: FakeTransport, monkeypatch: Any) -> Any:
        import sys
        import types

        module = types.ModuleType("httpx")
        calls: list[FakeRequest] = []

        class Client:
            def send(self, request: FakeRequest, **kwargs: Any) -> FakeResponse:
                calls.append(request)
                return FakeResponse(503)

        class AsyncClient:
            async def send(self, request: FakeRequest, **kwargs: Any) -> FakeResponse:
                calls.append(request)
                return FakeResponse()

        module.Client = Client  # type: ignore[attr-defined]
        module.AsyncClient = AsyncClient  # type: ignore[attr-defined]
        module.calls = calls  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "httpx", module)
        outbound.uninstrument()
        outbound.instrument()
        return module

    def test_the_trace_travels_with_the_request(
        self, installed: FakeTransport, httpx_module: Any
    ) -> None:
        client = client_module.get_client()
        assert client is not None

        with client.start_transaction("/checkout", op="http.server") as transaction:
            httpx_module.Client().send(FakeRequest("POST"))

        assert httpx_module.calls[0].headers[TRACE_HEADER].startswith(transaction.trace_id)

    def test_a_failing_status_marks_the_span_failed(
        self, installed: FakeTransport, httpx_module: Any
    ) -> None:
        client = client_module.get_client()
        assert client is not None

        with client.start_transaction("/checkout", op="http.server"):
            httpx_module.Client().send(FakeRequest("POST"))

        [transaction] = installed.payloads("transaction")
        assert transaction["spans"][0]["status"] == "internal_error"
        assert transaction["spans"][0]["data"]["http.status_code"] == 503

    def test_the_async_client_is_patched_too(
        self, installed: FakeTransport, httpx_module: Any
    ) -> None:
        """Most ASGI services make their outbound calls this way, so instrumenting only the
        sync client would cover the half that is rarer in exactly the applications that need
        distributed tracing most."""
        import asyncio

        client = client_module.get_client()
        assert client is not None

        async def call() -> None:
            with client.start_transaction("/checkout", op="http.server"):
                await httpx_module.AsyncClient().send(FakeRequest("GET"))

        asyncio.run(call())

        assert TRACE_HEADER in httpx_module.calls[0].headers


class TestDelivery:
    def test_the_sdk_does_not_trace_its_own_reports(self) -> None:
        """urllib is what the transport itself uses. Tracing it would report a span describing
        the report, and then a span describing that."""
        from urllib.request import Request

        request = Request(
            "http://localhost:8081/api/7/envelope/",
            data=b"{}",
            headers={"Content-Type": "application/x-obsly-envelope"},
        )

        assert outbound._is_collector(request) is True

    def test_an_ordinary_request_is_traced(self) -> None:
        from urllib.request import Request

        assert outbound._is_collector(Request("https://payments.internal/charge")) is False


class TestOptOut:
    def test_propagation_can_be_turned_off(self) -> None:
        """A process that cannot afford a patched HTTP client — or has its own — must be able
        to say so, and the SDK must still report everything else."""
        transport = FakeTransport()
        obsly.init(DSN, traces_sample_rate=1.0, transport=transport, propagate_trace=False)
        try:
            assert outbound._undo == []
        finally:
            client_module._client = None
