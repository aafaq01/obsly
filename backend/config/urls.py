from django.contrib import admin
from django.urls import include, path

from config.health import health

urlpatterns = [
    path("health/", health, name="health"),
    path("admin/", admin.site.urls),
    path("api/", include("apps.ingest.urls")),
    # Versioned separately from ingest: the wire protocol and the UI API change for
    # different reasons and must be free to move at different speeds.
    path("api/0/", include("apps.api.urls")),
]
