from django.conf import settings
from django.contrib import admin
from django.utils.html import format_html

from apps.projects.models import Organization, Project, ProjectKey


class ProjectKeyInline(admin.TabularInline[ProjectKey, Project]):
    model = ProjectKey
    extra = 0
    readonly_fields = ("public_key", "dsn_display", "created_at")
    fields = ("label", "public_key", "dsn_display", "is_active", "created_at")

    @admin.display(description="DSN")
    def dsn_display(self, obj: ProjectKey) -> str:
        if not obj.pk:
            return "—"
        # Rendered from a setting rather than from the request. ModelAdmin instances are shared
        # across concurrent requests, so stashing per-request state on self would race.
        return format_html("<code>{}</code>", obj.dsn(settings.OBSLY_INGEST_ORIGIN))


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin[Organization]):
    list_display = ("name", "slug", "project_count", "created_at")
    search_fields = ("name", "slug")
    prepopulated_fields = {"slug": ("name",)}

    @admin.display(description="Projects")
    def project_count(self, obj: Organization) -> int:
        return obj.projects.count()


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin[Project]):
    list_display = ("name", "organization", "slug", "created_at")
    list_filter = ("organization",)
    search_fields = ("name", "slug")
    prepopulated_fields = {"slug": ("name",)}
    inlines = [ProjectKeyInline]


@admin.register(ProjectKey)
class ProjectKeyAdmin(admin.ModelAdmin[ProjectKey]):
    list_display = ("project", "label", "public_key", "dsn_display", "is_active", "created_at")
    list_filter = ("is_active",)
    search_fields = ("public_key", "project__name")
    readonly_fields = ("public_key", "dsn_display")

    @admin.display(description="DSN")
    def dsn_display(self, obj: ProjectKey) -> str:
        return obj.dsn(settings.OBSLY_INGEST_ORIGIN)
