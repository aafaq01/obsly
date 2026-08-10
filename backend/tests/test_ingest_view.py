import json
from typing import Any, cast

import pytest
from django.http import HttpResponse
from django.test import Client, override_settings
from django.urls import reverse

from apps.events.models import Event
from apps.projects.models import Organization, Project, ProjectKey
from tests.conftest import build_envelope

pytestmark = pytest.mark.django_db

ERROR_PAYLOAD: dict[str, Any] = {
    "event_id": "6ba7b810-9dad-11d1-80b4-00c04fd430c8",
    "level": "error",
    "platform": "python",
    "release": "checkout@1.4.2",
    "environment": "production",
    "exception": {"values": [{"type": "ValueError", "value": "cart is empty"}]},
    "tags": {"endpoint": "/checkout"},
}


def json_body(response: HttpResponse) -> dict[str, Any]:
    """django-stubs types the test client as HttpResponse, which has no .json()."""
    return json.loads(response.content)  # type: ignore[no-any-return]


def url(project: Project) -> str:
    return reverse("ingest:envelope", args=[project.pk])


def post(client: Client, project: Project, body: bytes, key: str | None = None) -> HttpResponse:
    headers = {"X-Obsly-Key": key} if key else {}
    return cast(
        HttpResponse,
        client.post(
            url(project),
            data=body,
            content_type="application/x-obsly-envelope",
            headers=headers,
            # secure=True because ingest is deliberately NOT exempt from SECURE_SSL_REDIRECT.
            # Health is exempt so cluster probes work; ingest is not, because it carries user
            # payloads and must never be accepted over plaintext. Tests use the transport
            # production uses.
            secure=True,
        ),
    )


class TestAuthentication:
    def test_accepts_a_valid_key(
        self, client: Client, project: Project, project_key: ProjectKey
    ) -> None:
        response = post(
            client, project, build_envelope(("event", ERROR_PAYLOAD)), project_key.public_key
        )

        assert response.status_code == 200

    def test_accepts_the_key_as_a_query_parameter(
        self, client: Client, project: Project, project_key: ProjectKey
    ) -> None:
        """sendBeacon cannot set headers, and it is the only transport surviving page unload."""
        response = client.post(
            f"{url(project)}?obsly_key={project_key.public_key}",
            data=build_envelope(("event", ERROR_PAYLOAD)),
            content_type="application/x-obsly-envelope",
            secure=True,
        )

        assert response.status_code == 200

    def test_rejects_a_missing_key(self, client: Client, project: Project) -> None:
        response = post(client, project, build_envelope(("event", ERROR_PAYLOAD)))

        assert response.status_code == 401
        assert Event.objects.count() == 0

    def test_rejects_an_unknown_key(self, client: Client, project: Project) -> None:
        response = post(client, project, build_envelope(("event", ERROR_PAYLOAD)), "f" * 32)

        assert response.status_code == 401

    def test_rejects_a_revoked_key(
        self, client: Client, project: Project, project_key: ProjectKey
    ) -> None:
        project_key.is_active = False
        project_key.save()

        response = post(
            client, project, build_envelope(("event", ERROR_PAYLOAD)), project_key.public_key
        )

        assert response.status_code == 401

    def test_rejects_a_key_belonging_to_another_project(
        self, client: Client, organization: Organization, project_key: ProjectKey
    ) -> None:
        """A valid key must not write into a project it was not issued for."""
        other = Project.objects.create(organization=organization, name="Other", slug="other")

        response = post(
            client, other, build_envelope(("event", ERROR_PAYLOAD)), project_key.public_key
        )

        assert response.status_code == 401
        assert Event.objects.count() == 0

    def test_a_wrong_key_and_an_unknown_project_are_indistinguishable(
        self, client: Client, project: Project, organization: Organization
    ) -> None:
        """Different errors would let an anonymous caller enumerate live projects and keys."""
        missing_project = Project(pk=999_999, organization=organization, slug="ghost")

        wrong_key = post(client, project, build_envelope(("event", ERROR_PAYLOAD)), "a" * 32)
        no_project = post(
            client, missing_project, build_envelope(("event", ERROR_PAYLOAD)), "a" * 32
        )

        assert wrong_key.status_code == no_project.status_code == 401
        assert json_body(wrong_key) == json_body(no_project)


class TestStorage:
    def test_stores_the_event_with_extracted_fields(
        self, client: Client, project: Project, project_key: ProjectKey
    ) -> None:
        post(client, project, build_envelope(("event", ERROR_PAYLOAD)), project_key.public_key)

        event = Event.objects.get()
        assert event.project == project
        assert event.exception_type == "ValueError"
        assert event.exception_value == "cart is empty"
        assert event.release == "checkout@1.4.2"
        assert event.environment == "production"
        assert event.tags == {"endpoint": "/checkout"}
        assert event.payload == ERROR_PAYLOAD

    def test_stores_several_events_from_one_envelope(
        self, client: Client, project: Project, project_key: ProjectKey
    ) -> None:
        body = build_envelope(
            ("event", {**ERROR_PAYLOAD, "event_id": "a" * 32}),
            ("event", {**ERROR_PAYLOAD, "event_id": "b" * 32}),
        )

        response = post(client, project, body, project_key.public_key)

        assert json_body(response)["accepted"] == 2
        assert Event.objects.count() == 2

    def test_length_framed_payloads_are_stored_identically(
        self, client: Client, project: Project, project_key: ProjectKey
    ) -> None:
        body = build_envelope(("event", ERROR_PAYLOAD), with_length=True)

        post(client, project, body, project_key.public_key)

        assert Event.objects.get().exception_type == "ValueError"

    def test_event_id_is_taken_from_the_envelope_header(
        self, client: Client, project: Project, project_key: ProjectKey
    ) -> None:
        """Where an SDK actually puts it. Reading only the item payload broke retry-idempotency
        against the real wire format while every unit test stayed green."""
        payload = {k: v for k, v in ERROR_PAYLOAD.items() if k != "event_id"}
        body = build_envelope(
            ("event", payload), headers={"event_id": "6ba7b810-9dad-11d1-80b4-00c04fd430c8"}
        )

        post(client, project, body, project_key.public_key)

        assert str(Event.objects.get().pk) == "6ba7b810-9dad-11d1-80b4-00c04fd430c8"

    def test_a_batch_without_payload_ids_does_not_collide_on_the_header_id(
        self, client: Client, project: Project, project_key: ProjectKey
    ) -> None:
        """The header id names one event. Applying it to all of them would drop the rest."""
        payload = {k: v for k, v in ERROR_PAYLOAD.items() if k != "event_id"}
        body = build_envelope(
            ("event", payload),
            ("event", payload),
            headers={"event_id": "6ba7b810-9dad-11d1-80b4-00c04fd430c8"},
        )

        response = post(client, project, body, project_key.public_key)

        assert json_body(response)["accepted"] == 2
        assert Event.objects.count() == 2

    def test_replaying_an_envelope_does_not_duplicate_the_event(
        self, client: Client, project: Project, project_key: ProjectKey
    ) -> None:
        """A client timeout does not mean the write failed, so SDKs retry. Retries must be free."""
        body = build_envelope(("event", ERROR_PAYLOAD))

        post(client, project, body, project_key.public_key)
        post(client, project, body, project_key.public_key)

        assert Event.objects.count() == 1

    def test_non_event_items_are_accepted_and_ignored(
        self, client: Client, project: Project, project_key: ProjectKey
    ) -> None:
        body = build_envelope(("session", {"status": "crashed"}), ("event", ERROR_PAYLOAD))

        response = post(client, project, body, project_key.public_key)

        assert response.status_code == 200
        assert json_body(response)["accepted"] == 1


class TestRejection:
    def test_rejects_a_malformed_envelope(
        self, client: Client, project: Project, project_key: ProjectKey
    ) -> None:
        response = post(client, project, b"this is not an envelope", project_key.public_key)

        assert response.status_code == 400

    @override_settings(OBSLY_MAX_ENVELOPE_BYTES=200)
    def test_rejects_an_oversized_envelope(
        self, client: Client, project: Project, project_key: ProjectKey
    ) -> None:
        body = build_envelope(("event", {**ERROR_PAYLOAD, "pad": "x" * 5000}))

        response = post(client, project, body, project_key.public_key)

        assert response.status_code == 413
        assert Event.objects.count() == 0

    def test_one_bad_item_does_not_discard_the_good_ones_beside_it(
        self, client: Client, project: Project, project_key: ProjectKey
    ) -> None:
        good = json.dumps(ERROR_PAYLOAD).encode()
        body = b'{}\n{"type":"event"}\nnot-json\n{"type":"event"}\n' + good + b"\n"

        response = post(client, project, body, project_key.public_key)

        assert response.status_code == 200
        assert json_body(response)["accepted"] == 1
        assert len(json_body(response)["rejected"]) == 1
        assert Event.objects.count() == 1

    def test_rejects_get(self, client: Client, project: Project, project_key: ProjectKey) -> None:
        response = client.get(
            url(project), headers={"X-Obsly-Key": project_key.public_key}, secure=True
        )

        assert response.status_code == 405

    def test_does_not_require_a_csrf_token(self, project: Project, project_key: ProjectKey) -> None:
        """SDKs are not browsers carrying our cookies; the DSN key is the whole credential."""
        enforcing = Client(enforce_csrf_checks=True)

        response = enforcing.post(
            url(project),
            data=build_envelope(("event", ERROR_PAYLOAD)),
            content_type="application/x-obsly-envelope",
            headers={"X-Obsly-Key": project_key.public_key},
            secure=True,
        )

        assert response.status_code == 200
