from django.urls import path

from apps.api import auth_views, views

app_name = "api"

urlpatterns = [
    path("me/", auth_views.SessionView.as_view(), name="me"),
    path("auth/login/", auth_views.LoginView.as_view(), name="login"),
    path("auth/logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("organizations/", views.OrganizationListView.as_view(), name="organizations"),
    path("projects/", views.ProjectListView.as_view(), name="projects"),
    path("projects/<int:pk>/", views.ProjectDetailView.as_view(), name="project"),
    path(
        "projects/<int:project_id>/keys/",
        views.ProjectKeyCreateView.as_view(),
        name="project-keys",
    ),
    path("keys/<int:pk>/", views.ProjectKeyUpdateView.as_view(), name="key"),
    path("projects/<int:project_id>/issues/", views.IssueListView.as_view(), name="issues"),
    path("issues/<int:pk>/", views.IssueDetailView.as_view(), name="issue"),
    path("issues/<int:pk>/status/", views.IssueStatusView.as_view(), name="issue-status"),
    path("issues/<int:issue_id>/events/", views.IssueEventsView.as_view(), name="issue-events"),
]
