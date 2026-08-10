from django.urls import path

from apps.ingest.views import envelope

app_name = "ingest"

urlpatterns = [
    path("<int:project_id>/envelope/", envelope, name="envelope"),
]
