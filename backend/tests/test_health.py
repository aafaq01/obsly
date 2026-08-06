import pytest
from django.db import DatabaseError
from django.test import Client
from django.urls import reverse


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
