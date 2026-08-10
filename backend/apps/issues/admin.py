from django.contrib import admin
from django.http import HttpRequest

from apps.issues.models import Issue


@admin.register(Issue)
class IssueAdmin(admin.ModelAdmin[Issue]):
    list_display = ("title", "project", "level", "status", "times_seen", "last_seen")
    list_filter = ("status", "level", "project")
    search_fields = ("title", "culprit", "fingerprint")
    readonly_fields = ("fingerprint", "fingerprint_components", "times_seen", "first_seen")

    def has_add_permission(self, request: HttpRequest) -> bool:
        # Issues are derived from events. A hand-made one groups nothing.
        return False
