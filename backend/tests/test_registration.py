"""First-run registration.

A fresh install has no users and no way in short of running `createsuperuser` inside the
container, which is a bad first experience and worse advice to put in a README.

The window is the fix, and the window closing is the security property. An observability
platform holds production stack traces, SQL statements and whatever PII survived scrubbing; an
endpoint that hands out accounts for that permanently is not a sign-up form, it is a way to
read somebody's production.
"""

from typing import Any

import pytest
from django.contrib.auth.models import User
from django.test import Client, override_settings
from django.urls import reverse

from tests.conftest import json_body

pytestmark = pytest.mark.django_db

STRONG = "correct-horse-battery-7"


def register(client: Client, username: str = "first", password: str = STRONG) -> Any:
    return client.post(
        reverse("api:register"),
        {"username": username, "password": password},
        content_type="application/json",
        secure=True,
    )


class TestFirstRun:
    def test_the_first_account_can_be_created_with_nobody_signed_in(self, client: Client) -> None:
        response = register(client)

        assert response.status_code == 201
        assert User.objects.get().username == "first"

    def test_the_first_account_owns_the_install(self, client: Client) -> None:
        """There is nobody to grant it anything, and an instance whose only user cannot reach
        settings is an instance nobody can administer."""
        register(client)

        user = User.objects.get()
        assert user.is_staff
        assert user.is_superuser

    def test_registering_signs_you_in(self, client: Client) -> None:
        """Making somebody type the password they just chose, into a second form, to reach the
        thing they were already trying to reach."""
        register(client)

        assert json_body(client.get(reverse("api:me"), secure=True))["authenticated"] is True

    def test_the_door_closes_behind_the_first_account(self, client: Client) -> None:
        register(client)

        response = register(client, username="second")

        assert response.status_code == 403
        assert User.objects.count() == 1

    def test_a_closed_door_says_what_to_do_instead(self, client: Client) -> None:
        register(client)

        assert "administrator" in json_body(register(client, username="second"))["detail"]

    @override_settings(OBSLY_ALLOW_SIGNUP=True)
    def test_it_can_be_left_open_deliberately(self, client: Client) -> None:
        """Possible, and it has to be typed out. A setting somebody chose, not a default they
        inherited."""
        register(client)

        assert register(client, username="second").status_code == 201
        assert User.objects.count() == 2

    @override_settings(OBSLY_ALLOW_SIGNUP=True)
    def test_only_the_first_account_owns_the_install(self, client: Client) -> None:
        """Everyone after the first is an ordinary user. A signup form that mints superusers is
        a signup form that hands over the instance."""
        register(client)
        register(client, username="second")

        assert not User.objects.get(username="second").is_staff
        assert not User.objects.get(username="second").is_superuser


class TestValidation:
    @pytest.mark.parametrize("password", ["short", "password", "12345678", ""])
    def test_a_weak_password_is_refused(self, client: Client, password: str) -> None:
        """Django's own validators, so the rules match the ones the admin and every management
        command already enforce. A second, weaker set here would be the one people meet."""
        response = register(client, password=password)

        assert response.status_code == 400
        assert User.objects.count() == 0

    def test_the_refusal_says_why(self, client: Client) -> None:
        """ "Invalid password" sends somebody to guess. The validator already wrote the
        sentence."""
        detail = json_body(register(client, password="short"))["detail"]

        assert "8 characters" in detail

    def test_a_password_like_the_username_is_refused(self, client: Client) -> None:
        response = register(client, username="alexandra", password="alexandra1")

        assert response.status_code == 400

    def test_a_missing_username_is_refused(self, client: Client) -> None:
        assert register(client, username="  ").status_code == 400

    @override_settings(OBSLY_ALLOW_SIGNUP=True)
    def test_a_taken_username_is_refused(self, client: Client) -> None:
        register(client)

        response = register(client, username="first")

        assert response.status_code == 400
        assert "taken" in json_body(response)["detail"]


class TestSessionReporting:
    def test_a_fresh_install_says_registration_is_open(self, client: Client) -> None:
        """The sign-in screen has to know whether to offer it, and a fresh install has to show
        a register form rather than a login form nobody can satisfy."""
        assert json_body(client.get(reverse("api:me"), secure=True))["signup_open"] is True

    def test_it_says_closed_once_somebody_owns_the_install(self, client: Client) -> None:
        register(client)
        client.post(reverse("api:logout"), secure=True)

        assert json_body(client.get(reverse("api:me"), secure=True))["signup_open"] is False

    @override_settings(OBSLY_ALLOW_SIGNUP=True)
    def test_it_says_open_when_left_open(self, client: Client) -> None:
        register(client)
        client.post(reverse("api:logout"), secure=True)

        assert json_body(client.get(reverse("api:me"), secure=True))["signup_open"] is True
