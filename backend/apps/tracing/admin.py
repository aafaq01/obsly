from django.contrib import admin
from django.http import HttpRequest

from apps.tracing.models import Transaction


@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin[Transaction]):
    list_display = ("name", "project", "status", "duration_ms", "environment", "timestamp")
    list_filter = ("status", "op", "project", "environment")
    search_fields = ("name", "trace_id")
    date_hierarchy = "timestamp"

    def has_add_permission(self, request: HttpRequest) -> bool:
        # Transactions arrive over ingest. A hand-made one measures nothing.
        return False
