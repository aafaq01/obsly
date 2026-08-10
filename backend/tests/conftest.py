"""Shared fixtures.

Env vars are set by pytest-env in pyproject.toml, not here — pytest-django configures Django
before any conftest is imported, so a conftest is too late to influence settings.
"""

import json
import uuid
from typing import Any

import pytest

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
