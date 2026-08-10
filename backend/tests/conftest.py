"""Shared fixtures.

Env vars are set by pytest-env in pyproject.toml, not here — pytest-django configures Django
before any conftest is imported, so a conftest is too late to influence settings.
"""

import json
import uuid
from typing import Any, cast

import pytest
from django.http import HttpResponse
from django.test import Client
from django.urls import reverse

from apps.projects.models import Organization, Project, ProjectKey


@pytest.fixture
def organization(db: None) -> Organization:
    return Organization.objects.create(name="Acme", slug="acme")


@pytest.fixture
def project(organization: Organization) -> Project:
    return Project.objects.create(
        organization=organization, name="Checkout", slug="checkout", platform="python"
    )


@pytest.fixture
def project_key(project: Project) -> ProjectKey:
    return ProjectKey.objects.create(project=project)


def build_envelope(
    *items: tuple[str, dict[str, Any]],
    headers: dict[str, Any] | None = None,
    with_length: bool = False,
) -> bytes:
    """Assemble a wire-format envelope.

    `with_length` toggles the two payload framings the parser supports, so tests can exercise
    both without hand-writing bytes.
    """
    # The event id belongs in the envelope header — that is where an SDK puts it, and where the
    # server must read it from for retries to be idempotent.
    lines = [json.dumps(headers or {"event_id": str(uuid.uuid4())}).encode()]

    for item_type, payload in items:
        body = json.dumps(payload).encode()
        item_header: dict[str, Any] = {"type": item_type}
        if with_length:
            item_header["length"] = len(body)
        lines.append(json.dumps(item_header).encode())
        lines.append(body)

    return b"\n".join(lines) + b"\n"


def json_body(response: Any) -> Any:
    """Read a JSON response body.

    Deliberately Any: django-stubs types the test client's return as a private
    _MonkeyPatchedWSGIResponse that is not assignable to HttpResponse, and pinning a test
    helper to a stub-internal name breaks on every stubs upgrade.
    """
    return json.loads(response.content)


def post(client: Client, project: Project, body: bytes, key: str | None = None) -> HttpResponse:
    """POST an envelope over HTTPS.

    secure=True because ingest is deliberately NOT exempt from SECURE_SSL_REDIRECT: health is
    exempt so cluster probes work, ingest carries user payloads and must never be accepted over
    plaintext. Tests use the transport production uses.
    """
    headers = {"X-Obsly-Key": key} if key else {}
    return cast(
        HttpResponse,
        client.post(
            reverse("ingest:envelope", args=[project.pk]),
            data=body,
            content_type="application/x-obsly-envelope",
            headers=headers,
            secure=True,
        ),
    )
