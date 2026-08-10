from django.contrib import admin
from django.http import HttpRequest

from apps.logs.models import LogRecord


@admin.register(LogRecord)
class LogRecordAdmin(admin.ModelAdmin[LogRecord]):
    list_display = ("timestamp", "level", "logger", "body", "environment")
    list_filter = ("level", "project", "environment")
    search_fields = ("body", "logger", "trace_id")
    date_hierarchy = "timestamp"

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False
