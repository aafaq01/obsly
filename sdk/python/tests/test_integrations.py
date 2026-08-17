"""The frameworks that are not FastAPI.

Half the Python web world is WSGI, and until these existed a Flask or Django application could
report the errors it caught itself and nothing else — no request traces, so no latency, no
throughput, and no way for a browser trace to continue into the server.

Each integration is tested through the framework's own test client rather than by calling the
middleware directly, because the interesting failures are all in the seams: a signal that never
fires, a route pattern that is not resolved yet when the hook runs, a response iterable the
server closes after the application has returned.
"""

import json
import pathlib
from collections.abc import Iterator
from typing import Any

import django
import pytest
from django.conf import settings
from django.http import HttpResponse
from django.urls import path

import obsly
from obsly import client as client_module
from obsly.integrations._http import parameterize
from obsly.integrations.wsgi import ObslyMiddleware as WsgiMiddleware

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

    def payloads(self, kind: str) -> list[dict[str, Any]]:
        out = []
        for raw in self.sent:
            lines = raw.strip().split(b"\n")
            if json.loads(lines[1]).get("type") == kind:
                out.append(json.loads(lines[2]))
        return out


@pytest.fixture
def transport() -> FakeTransport:
    return FakeTransport()


@pytest.fixture
def installed(transport: FakeTransport) -> Iterator[FakeTransport]:
    """A live global client, because these integrations reach for it through get_client()."""
    obsly.init(
        DSN,
        release="demo@1.0.0",
        environment="test",
        traces_sample_rate=1.0,
        transport=transport,
    )
    yield transport
    client_module._client = None


class TestParameterize:
    """The fallback name, used wherever a framework cannot say which route it matched."""

    @pytest.mark.parametrize(
        ("path", "expected"),
        [
            ("/orders/42", "/orders/{id}"),
            ("/orders/42/items/7", "/orders/{id}/items/{id}"),
            ("/users/9f8e7d6c5b4a3921", "/users/{id}"),
            ("/users/0b7c2f1e-1c3a-4f5b-9d2e-6a7b8c9d0e1f", "/users/{id}"),
            ("/healthz", "/healthz"),
            ("/", "/"),
            ("", "/"),
        ],
    )
    def test_ids_collapse_and_words_do_not(self, path: str, expected: str) -> None:
        assert parameterize(path) == expected

    def test_a_word_that_is_not_an_id_survives(self) -> None:
        """The guess has to be conservative: merging /orders/summary into /orders/{id} would
        hide a real endpoint inside another one's numbers."""
        assert parameterize("/orders/summary") == "/orders/summary"


class TestWsgi:
    def app(self, body: bytes = b"ok", status: str = "200 OK") -> Any:
        def application(environ: dict[str, Any], start_response: Any) -> list[bytes]:
            start_response(status, [("Content-Type", "text/plain")])
            return [body]

        return application

    def request(
        self, app: Any, path: str = "/orders/42", headers: dict[str, str] | None = None
    ) -> tuple[list[bytes], dict[str, Any]]:
        environ: dict[str, Any] = {
            "REQUEST_METHOD": "GET",
            "PATH_INFO": path,
            "QUERY_STRING": "",
            "wsgi.url_scheme": "http",
            "SERVER_NAME": "localhost",
            "SERVER_PORT": "80",
            **{f"HTTP_{k.upper().replace('-', '_')}": v for k, v in (headers or {}).items()},
        }
        captured: dict[str, Any] = {}

        def start_response(status: str, response_headers: Any, exc_info: Any = None) -> Any:
            captured["status"] = status
            return lambda data: None

        result = app(environ, start_response)
        body = list(result)
        close = getattr(result, "close", None)
        if close is not None:
            close()
        return body, captured

    def test_a_plain_wsgi_app_produces_a_transaction(self, installed: FakeTransport) -> None:
        body, _ = self.request(WsgiMiddleware(self.app()))

        assert body == [b"ok"]
        [transaction] = installed.payloads("transaction")
        assert transaction["contexts"]["trace"]["op"] == "http.server"
        assert transaction["data"]["http.method"] == "GET"

    def test_the_transaction_is_named_without_the_id(self, installed: FakeTransport) -> None:
        """No framework to ask, so the id has to be guessed out — otherwise every order id ever
        requested is its own endpoint and the aggregate is one row each."""
        self.request(WsgiMiddleware(self.app()))

        [transaction] = installed.payloads("transaction")
        assert transaction["transaction"] == "/orders/{id}"

    def test_the_status_reaches_the_transaction(self, installed: FakeTransport) -> None:
        self.request(WsgiMiddleware(self.app(status="503 Service Unavailable")))

        [transaction] = installed.payloads("transaction")
        assert transaction["contexts"]["trace"]["status"] == "internal_error"
        assert transaction["data"]["http.status_code"] == 503

    def test_it_continues_a_trace_the_browser_started(self, installed: FakeTransport) -> None:
        """The whole point of the header. Without this a page load and the request it made are
        two unrelated rows."""
        trace = "a" * 32
        self.request(WsgiMiddleware(self.app()), headers={"obsly-trace": f"{trace}-{'b' * 16}-1"})

        [transaction] = installed.payloads("transaction")
        assert transaction["contexts"]["trace"]["trace_id"] == trace
        assert transaction["contexts"]["trace"]["parent_span_id"] == "b" * 16

    def test_an_exception_is_reported_and_re_raised(self, installed: FakeTransport) -> None:
        def failing(environ: dict[str, Any], start_response: Any) -> list[bytes]:
            raise ValueError("boom")

        with pytest.raises(ValueError, match="boom"):
            self.request(WsgiMiddleware(failing))

        [event] = installed.payloads("event")
        assert event["exception"]["values"][0]["type"] == "ValueError"
        assert event["extra"]["request"]["route"] == "/orders/{id}"

    def test_authorisation_never_leaves_the_process(self, installed: FakeTransport) -> None:
        def failing(environ: dict[str, Any], start_response: Any) -> list[bytes]:
            raise ValueError("boom")

        with pytest.raises(ValueError):
            self.request(
                WsgiMiddleware(failing),
                headers={"authorization": "Bearer hunter2", "user-agent": "curl/8"},
            )

        [event] = installed.payloads("event")
        headers = event["extra"]["request"]["headers"]
        assert "authorization" not in headers
        assert headers["user-agent"] == "curl/8"

    def test_a_streaming_response_is_timed_until_it_closes(self, installed: FakeTransport) -> None:
        """A WSGI response is finished when its iterable is closed, not when the application
        returns. Timing the call alone would report a slow download as instant."""
        closed: list[str] = []

        class Stream:
            def __iter__(self) -> Iterator[bytes]:
                yield b"chunk"

            def close(self) -> None:
                closed.append("closed")

        def streaming(environ: dict[str, Any], start_response: Any) -> Any:
            start_response("200 OK", [])
            return Stream()

        self.request(WsgiMiddleware(streaming))

        assert closed == ["closed"]
        assert len(installed.payloads("transaction")) == 1

    def test_closing_twice_does_not_send_the_request_twice(self, installed: FakeTransport) -> None:
        app = WsgiMiddleware(self.app())
        environ = {"REQUEST_METHOD": "GET", "PATH_INFO": "/x", "wsgi.url_scheme": "http"}
        result = app(environ, lambda status, headers, exc_info=None: None)
        list(result)

        result.close()  # type: ignore[union-attr]
        result.close()  # type: ignore[union-attr]

        assert len(installed.payloads("transaction")) == 1

    def test_without_init_the_app_still_serves(self, transport: FakeTransport) -> None:
        """No client configured is the state every application starts in, and it must be
        indistinguishable from not having the middleware at all."""
        body, captured = self.request(WsgiMiddleware(self.app()))

        assert body == [b"ok"]
        assert captured["status"] == "200 OK"
        assert transport.sent == []


class TestFlask:
    @pytest.fixture
    def app(self) -> Any:
        from flask import Flask

        from obsly.integrations.flask import instrument

        application = Flask(__name__)
        application.config.update(TESTING=False, PROPAGATE_EXCEPTIONS=False)

        @application.route("/orders/<int:order_id>")
        def order(order_id: int) -> str:
            return f"order {order_id}"

        @application.route("/boom")
        def boom() -> str:
            raise ValueError("flask boom")

        instrument(application)
        return application

    def test_the_transaction_is_named_by_the_matched_rule(
        self, installed: FakeTransport, app: Any
    ) -> None:
        """The reason this integration exists rather than pointing Flask at the WSGI one: the
        framework's own name is right even when an id looks like a word."""
        app.test_client().get("/orders/42")

        [transaction] = installed.payloads("transaction")
        assert transaction["transaction"] == "/orders/<int:order_id>"

    def test_the_status_reaches_the_transaction(self, installed: FakeTransport, app: Any) -> None:
        app.test_client().get("/orders/42")

        [transaction] = installed.payloads("transaction")
        assert transaction["contexts"]["trace"]["status"] == "ok"
        assert transaction["data"]["http.status_code"] == 200

    def test_a_failing_view_is_reported(self, installed: FakeTransport, app: Any) -> None:
        response = app.test_client().get("/boom")

        assert response.status_code == 500
        [event] = installed.payloads("event")
        assert event["exception"]["values"][0]["value"] == "flask boom"

    def test_the_error_and_its_transaction_share_a_trace(
        self, installed: FakeTransport, app: Any
    ) -> None:
        """Correlation is the product. An error you cannot walk to the request from is half a
        report."""
        app.test_client().get("/boom")

        [event] = installed.payloads("event")
        [transaction] = installed.payloads("transaction")
        assert (
            event["contexts"]["trace"]["trace_id"] == transaction["contexts"]["trace"]["trace_id"]
        )

    def test_a_failed_request_is_marked_failed(self, installed: FakeTransport, app: Any) -> None:
        app.test_client().get("/boom")

        [transaction] = installed.payloads("transaction")
        assert transaction["contexts"]["trace"]["status"] == "internal_error"

    def test_an_unmatched_url_does_not_become_its_own_transaction(
        self, installed: FakeTransport, app: Any
    ) -> None:
        """A 404 has no rule. Naming those by raw path makes every scan for /wp-admin.php a
        row in the endpoint list."""
        app.test_client().get("/nope/12345")

        [transaction] = installed.payloads("transaction")
        assert transaction["transaction"] == "/nope/{id}"

    def test_it_continues_a_trace_the_browser_started(
        self, installed: FakeTransport, app: Any
    ) -> None:
        trace = "c" * 32
        app.test_client().get("/orders/42", headers={"obsly-trace": f"{trace}-{'d' * 16}-1"})

        [transaction] = installed.payloads("transaction")
        assert transaction["contexts"]["trace"]["trace_id"] == trace


def ok_view(request: Any, **kwargs: Any) -> HttpResponse:
    return HttpResponse("ok")


def boom_view(request: Any, **kwargs: Any) -> HttpResponse:
    raise ValueError("django boom")


urlpatterns = [
    path("orders/<int:pk>/", ok_view),
    path("boom/", boom_view),
]


if not settings.configured:
    settings.configure(
        DEBUG=False,
        SECRET_KEY="only-for-tests",  # noqa: S106 - Django refuses to boot without one
        ALLOWED_HOSTS=["*"],
        ROOT_URLCONF=__name__,
        DATABASES={},
        MIDDLEWARE=["obsly.integrations.django.ObslyMiddleware"],
        LOGGING_CONFIG=None,
    )
    django.setup()


class TestDjango:
    @pytest.fixture
    def client(self) -> Any:
        from django.test import Client as DjangoClient

        return DjangoClient(raise_request_exception=False)

    def test_a_request_produces_a_transaction(self, installed: FakeTransport, client: Any) -> None:
        """Django is the one framework that is neither WSGI nor ASGI from the SDK's point of
        view — it is both, and a Django middleware covers either deployment."""
        client.get("/orders/42/")

        [transaction] = installed.payloads("transaction")
        assert transaction["contexts"]["trace"]["op"] == "http.server"
        assert transaction["data"]["http.status_code"] == 200

    def test_the_transaction_is_named_by_the_resolved_route(
        self, installed: FakeTransport, client: Any
    ) -> None:
        client.get("/orders/42/")

        [transaction] = installed.payloads("transaction")
        assert transaction["transaction"] == "/orders/<int:pk>/"

    def test_a_failing_view_is_reported(self, installed: FakeTransport, client: Any) -> None:
        response = client.get("/boom/")

        assert response.status_code == 500
        [event] = installed.payloads("event")
        assert event["exception"]["values"][0]["value"] == "django boom"
        assert event["extra"]["request"]["route"] == "/boom/"

    def test_the_error_and_its_transaction_share_a_trace(
        self, installed: FakeTransport, client: Any
    ) -> None:
        client.get("/boom/")

        [event] = installed.payloads("event")
        [transaction] = installed.payloads("transaction")
        assert (
            event["contexts"]["trace"]["trace_id"] == transaction["contexts"]["trace"]["trace_id"]
        )

    def test_a_missing_page_is_not_an_error(self, installed: FakeTransport, client: Any) -> None:
        """404s are traffic, not failures. Reporting them as errors buries the real ones."""
        client.get("/nothing/here/")

        assert installed.payloads("event") == []
        [transaction] = installed.payloads("transaction")
        assert transaction["contexts"]["trace"]["status"] == "not_found"

    def test_it_continues_a_trace_the_browser_started(
        self, installed: FakeTransport, client: Any
    ) -> None:
        trace = "e" * 32
        client.get("/orders/42/", headers={"obsly-trace": f"{trace}-{'f' * 16}-1"})

        [transaction] = installed.payloads("transaction")
        assert transaction["contexts"]["trace"]["trace_id"] == trace

    def test_without_init_the_view_still_serves(
        self, transport: FakeTransport, client: Any
    ) -> None:
        response = client.get("/orders/42/")

        assert response.status_code == 200
        assert transport.sent == []


def test_the_client_is_not_left_installed_by_these_tests() -> None:
    """A global client leaking between test modules would make every later assertion suspect."""
    assert client_module.get_client() is None


class TestPackaging:
    """The version is not decoration: the server records it against every event.

    It had drifted — 0.1.0 in the code while 0.2.0 was on PyPI — so every event from the
    published package claimed to come from a version that never shipped these integrations.
    """

    def version(self) -> str:
        import tomllib

        source = pathlib.Path(__file__).resolve().parent.parent / "pyproject.toml"
        return str(tomllib.loads(source.read_text(encoding="utf-8"))["project"]["version"])

    def test_the_declared_version_is_the_one_the_sdk_reports(self) -> None:
        assert obsly.__version__ == self.version()

    def test_the_version_on_the_wire_matches_too(self) -> None:
        from obsly.client import SDK

        assert SDK["version"] == self.version()
