"""The ownership hierarchy: Organization -> Project -> ProjectKey.

Everything Obsly stores is scoped to a Project, and a Project is the unit of ingest, quota and
access control. A ProjectKey is the credential an SDK uses to write to one.

Teams and memberships deliberately do not live here yet. Nothing enforces access control until
`feat/rbac`, and a permission model with no permission checks is a table that lies.
"""

import secrets

from django.core.validators import MinLengthValidator
from django.db import models


def generate_public_key() -> str:
    """32 hex characters, 128 bits of entropy.

    Guessing one grants write access to a single project, so it must be unguessable; it grants
    no read access, which is why it can safely ship inside a browser bundle.
    """
    return secrets.token_hex(16)


class TimestampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Organization(TimestampedModel):
    """Billing and ownership boundary. Every other row hangs off one of these."""

    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=100, unique=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class Project(TimestampedModel):
    """One deployable, one codebase. The unit of ingest and of the issue stream."""

    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="projects"
    )
    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=100)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "slug"], name="unique_project_slug_per_org"
            )
        ]

    def __str__(self) -> str:
        return f"{self.organization.slug}/{self.slug}"


class ProjectKeyQuerySet(models.QuerySet["ProjectKey"]):
    def active(self) -> "ProjectKeyQuerySet":
        return self.filter(is_active=True)


class ProjectKey(TimestampedModel):
    """A write-only ingest credential — the key half of a DSN.

    Multiple keys per project on purpose: rotating a credential means issuing a new one, moving
    clients over, then revoking the old one. With a single key, rotation is an outage.
    """

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="keys")
    label = models.CharField(max_length=100, default="default")
    public_key = models.CharField(
        max_length=32,
        unique=True,
        default=generate_public_key,
        validators=[MinLengthValidator(32)],
        editable=False,
    )
    is_active = models.BooleanField(default=True)

    objects = ProjectKeyQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.project} [{self.label}]"

    def dsn(self, origin: str) -> str:
        """Build the DSN a client is configured with.

        `origin` is passed in rather than read from settings because the value depends on how the
        client reaches us — localhost in development, a public hostname in production — and the
        server has no reliable way to know which one the caller means.
        """
        scheme, _, host = origin.rpartition("://")
        return f"{scheme or 'http'}://{self.public_key}@{host}/{self.project_id}"
