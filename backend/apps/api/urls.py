from django.urls import path

from apps.api import views

app_name = "api"

urlpatterns = [
    path("me/", views.MeView.as_view(), name="me"),
    path("projects/", views.ProjectListView.as_view(), name="projects"),
    path("projects/<int:project_id>/issues/", views.IssueListView.as_view(), name="issues"),
    path("issues/<int:pk>/", views.IssueDetailView.as_view(), name="issue"),
    path("issues/<int:issue_id>/events/", views.IssueEventsView.as_view(), name="issue-events"),
]
