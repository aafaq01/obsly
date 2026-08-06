import pytest
from django.conf import settings
from django.db import DatabaseError
from django.test import Client
from django.urls import reverse


def test_tests_run_with_production_security_settings() -> None:
    """Guards the CI-vs-local gap: a developer's .env must not relax the test posture."""
    assert settings.DEBUG is False
    assert settings.SECURE_SSL_REDIRECT is True


@pytest.mark.django_db
def test_health_is_reachable_over_plain_http(client: Client) -> None:
    """Cluster probes reach the pod over HTTP. Redirecting them to HTTPS fails every probe."""
    response = client.get(reverse("health"))

    assert response.status_code != 301, "health must be exempt from SECURE_SSL_REDIRECT"


def test_other_paths_are_still_redirected_to_https(client: Client) -> None:
    """The health exemption must be an exemption, not a hole in the redirect."""
    response = client.get("/admin/")

    assert response.status_code == 301
    assert response.headers["Location"].startswith("https://")


@pytest.mark.django_db
def test_health_reports_ok_when_database_is_reachable(client: Client) -> None:
    response = client.get(reverse("health"))

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


class UnreachableConnection:
    """Stands in for `django.db.connection` when the database is down."""

    def cursor(self) -> None:
        raise DatabaseError("connection refused")


@pytest.mark.django_db
def test_health_returns_503_when_database_is_unreachable(
    client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A load balancer must be able to pull this instance on a status code alone."""
    # Patch the name health.py imported, not django.db.connection itself — ATOMIC_REQUESTS
    # opens a savepoint through the real connection before the view is ever called.
    monkeypatch.setattr("config.health.connection", UnreachableConnection())

    response = client.get(reverse("health"))

    assert response.status_code == 503
    assert response.json() == {"status": "unhealthy", "database": "unreachable"}
