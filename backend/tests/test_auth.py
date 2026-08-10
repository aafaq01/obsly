from typing import cast

import pytest
from django.contrib.auth.models import User
from django.http import HttpResponse
from django.test import Client
from django.urls import reverse

from tests.conftest import json_body

pytestmark = pytest.mark.django_db

PASSWORD = "correct-horse-battery"


@pytest.fixture
def user() -> User:
    return User.objects.create_user("viewer", password=PASSWORD)


def login(client: Client, username: str, password: str) -> HttpResponse:
    return cast(
        HttpResponse,
        client.post(
            reverse("api:login"),
            data={"username": username, "password": password},
            content_type="application/json",
            secure=True,
        ),
    )


class TestSession:
    def test_anonymous_callers_get_a_session_answer_not_a_403(self, client: Client) -> None:
        """A 403 here would run before the view and never set the CSRF cookie, leaving a
        first-time visitor unable to sign in at all."""
        response = client.get(reverse("api:me"), secure=True)

        assert response.status_code == 200
        assert json_body(response) == {"authenticated": False, "username": None}

    def test_sets_a_csrf_cookie_for_anonymous_callers(self, client: Client) -> None:
        response = client.get(reverse("api:me"), secure=True)

        assert "csrftoken" in response.cookies

    def test_reports_the_signed_in_user(self, client: Client, user: User) -> None:
        login(client, "viewer", PASSWORD)

        payload = json_body(client.get(reverse("api:me"), secure=True))

        assert payload["authenticated"] is True
        assert payload["username"] == "viewer"


class TestLogin:
    def test_valid_credentials_start_a_session(self, client: Client, user: User) -> None:
        response = login(client, "viewer", PASSWORD)

        assert response.status_code == 200
        assert client.get(reverse("api:projects"), secure=True).status_code == 200

    def test_wrong_password_is_rejected(self, client: Client, user: User) -> None:
        assert login(client, "viewer", "wrong").status_code == 401

    def test_unknown_user_and_wrong_password_are_indistinguishable(
        self, client: Client, user: User
    ) -> None:
        """Different messages turn the login form into a way to enumerate accounts."""
        unknown = login(client, "nobody", PASSWORD)
        wrong = login(client, "viewer", "wrong")

        assert unknown.status_code == wrong.status_code == 401
        assert json_body(unknown) == json_body(wrong)

    @pytest.mark.parametrize(
        "payload", [{}, {"username": "viewer"}, {"password": PASSWORD}, {"username": "  "}]
    )
    def test_missing_credentials_are_a_400(self, client: Client, payload: dict[str, str]) -> None:
        response = client.post(
            reverse("api:login"), data=payload, content_type="application/json", secure=True
        )

        assert response.status_code == 400

    def test_a_non_object_body_is_a_400_not_a_500(self, client: Client) -> None:
        response = client.post(
            reverse("api:login"), data=[1, 2], content_type="application/json", secure=True
        )

        assert response.status_code == 400

    def test_the_session_key_changes_on_login(self, client: Client, user: User) -> None:
        """Otherwise a session fixated before login still works after it."""
        session = client.session
        session["visited"] = True
        session.save()
        before = session.session_key
        assert before is not None

        login(client, "viewer", PASSWORD)

        assert client.session.session_key != before

    def test_repeated_failures_are_throttled(self, client: Client, user: User) -> None:
        """Login is the one anonymous write endpoint, so it is the one worth guarding."""
        codes = [login(client, "viewer", "wrong").status_code for _ in range(12)]

        assert 429 in codes, f"expected a 429 among {codes}"


class TestLogout:
    def test_ends_the_session(self, client: Client, user: User) -> None:
        login(client, "viewer", PASSWORD)

        client.post(reverse("api:logout"), content_type="application/json", secure=True)

        assert client.get(reverse("api:projects"), secure=True).status_code == 403

    def test_is_harmless_when_not_signed_in(self, client: Client) -> None:
        response = client.post(reverse("api:logout"), content_type="application/json", secure=True)

        assert response.status_code == 200
