import pytest
from django.db import IntegrityError
from django.test import Client
from django.urls import reverse

from apps.projects.models import Organization, Project, ProjectKey, generate_public_key
from tests.conftest import json_body

pytestmark = pytest.mark.django_db


class TestPublicKey:
    def test_is_32_hex_characters(self) -> None:
        key = generate_public_key()

        assert len(key) == 32
        assert all(c in "0123456789abcdef" for c in key)

    def test_keys_do_not_repeat(self) -> None:
        """A repeated key would hand one project's ingest to another."""
        keys = {generate_public_key() for _ in range(500)}

        assert len(keys) == 500

    def test_is_generated_without_being_asked_for(self, project: Project) -> None:
        key = ProjectKey.objects.create(project=project)

        assert len(key.public_key) == 32


class TestProject:
    def test_slug_is_unique_within_an_organization(self, organization: Organization) -> None:
        Project.objects.create(organization=organization, name="A", slug="dup")

        with pytest.raises(IntegrityError):
            Project.objects.create(organization=organization, name="B", slug="dup")

    def test_same_slug_is_allowed_in_a_different_organization(
        self, organization: Organization
    ) -> None:
        """Slugs are namespaced per org — two customers may both have a project called 'api'."""
        other = Organization.objects.create(name="Globex", slug="globex")
        Project.objects.create(organization=organization, name="API", slug="api")

        Project.objects.create(organization=other, name="API", slug="api")

        assert Project.objects.filter(slug="api").count() == 2

    def test_str_identifies_org_and_project(self, project: Project) -> None:
        assert str(project) == "acme/checkout"


class TestDsn:
    def test_contains_the_key_and_the_project_id(self, project: Project) -> None:
        key = ProjectKey.objects.create(project=project)

        dsn = key.dsn("http://localhost:8081")

        assert dsn == f"http://{key.public_key}@localhost:8081/{project.pk}"

    def test_preserves_https(self, project: Project) -> None:
        key = ProjectKey.objects.create(project=project)

        assert key.dsn("https://obsly.example.com").startswith("https://")

    def test_defaults_to_http_when_the_origin_has_no_scheme(self, project: Project) -> None:
        key = ProjectKey.objects.create(project=project)

        assert key.dsn("localhost:8081").startswith("http://")

    def test_carries_no_secret_beyond_the_public_key(self, project: Project) -> None:
        """The DSN ships in browser bundles. Anything private in it would leak on first deploy."""
        key = ProjectKey.objects.create(project=project)

        dsn = key.dsn("http://localhost:8081")

        assert ":" not in dsn.split("//", 1)[1].split("@", 1)[0], "no password component"


class TestKeyRotation:
    def test_a_project_can_hold_several_keys(self, project: Project) -> None:
        """Rotation means issue new, migrate clients, revoke old. One key makes that an outage."""
        ProjectKey.objects.create(project=project, label="old")
        ProjectKey.objects.create(project=project, label="new")

        assert project.keys.count() == 2

    def test_active_excludes_revoked_keys(self, project: Project) -> None:
        ProjectKey.objects.create(project=project, label="old", is_active=False)
        live = ProjectKey.objects.create(project=project, label="new")

        assert list(ProjectKey.objects.active()) == [live]

    def test_deleting_a_project_removes_its_keys(self, project: Project) -> None:
        """A revoked project must not leave a credential that still authenticates."""
        ProjectKey.objects.create(project=project)
        project.delete()

        assert ProjectKey.objects.count() == 0


def test_a_new_project_is_born_with_an_ingest_key(
    staff_client: Client, organization: Organization
) -> None:
    """A project without a key cannot receive anything, so creating one and stopping leaves a
    row that looks finished and does nothing — and the next step, wiring up an SDK, has no DSN
    to paste."""
    response = staff_client.post(
        reverse("api:projects"),
        {
            "name": "Checkout",
            "slug": "checkout",
            "platform": "python",
            "organization_id": organization.pk,
        },
        content_type="application/json",
        secure=True,
    )

    assert response.status_code == 201
    project = Project.objects.get(slug="checkout")
    [key] = project.keys.all()
    assert key.is_active
    assert len(key.public_key) == 32


def test_a_duplicate_name_says_which_name_is_taken(
    staff_client: Client, organization: Organization
) -> None:
    """DRF's own answer describes a database constraint — "the fields organization_id, slug
    must make a unique set" — and a person naming a project should not have to work out that a
    slug is derived from the name."""
    Project.objects.create(organization=organization, name="Demo App", slug="demo-app")

    response = staff_client.post(
        reverse("api:projects"),
        {"name": "Demo App", "slug": "demo-app", "organization_id": organization.pk},
        content_type="application/json",
        secure=True,
    )

    assert response.status_code == 400
    assert "Demo App" in json_body(response)["name"][0]
