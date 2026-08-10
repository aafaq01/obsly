import json

from django.contrib import admin
from django.http import HttpRequest
from django.utils.html import format_html

from apps.events.models import Event


@admin.register(Event)
class EventAdmin(admin.ModelAdmin[Event]):
    list_display = ("title", "project", "level", "environment", "release", "timestamp")
    list_filter = ("level", "platform", "project", "environment")
    search_fields = ("exception_type", "exception_value", "message")
    date_hierarchy = "timestamp"
    readonly_fields = ("id", "received_at", "payload_pretty")
    exclude = ("payload",)

    @admin.display(description="Payload")
    def payload_pretty(self, obj: Event) -> str:
        return format_html("<pre>{}</pre>", json.dumps(obj.payload, indent=2, sort_keys=True))

    def has_add_permission(self, request: HttpRequest) -> bool:
        # Events arrive over the ingest API. Hand-authoring one would produce a row that no
        # client ever sent, which makes the stream lie.
        return False
