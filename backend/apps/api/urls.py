from django.urls import path

from apps.api import auth_views, views

app_name = "api"

urlpatterns = [
    path("me/", auth_views.SessionView.as_view(), name="me"),
    path("auth/login/", auth_views.LoginView.as_view(), name="login"),
    path("auth/logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("organizations/", views.OrganizationListView.as_view(), name="organizations"),
    path(
        "organizations/<int:pk>/",
        views.OrganizationDetailView.as_view(),
        name="organization",
    ),
    path("projects/", views.ProjectListView.as_view(), name="projects"),
    path("projects/<int:pk>/", views.ProjectDetailView.as_view(), name="project"),
    path(
        "projects/<int:project_id>/keys/",
        views.ProjectKeyCreateView.as_view(),
        name="project-keys",
    ),
    path("keys/<int:pk>/", views.ProjectKeyUpdateView.as_view(), name="key"),
    path("projects/<int:project_id>/issues/", views.IssueListView.as_view(), name="issues"),
    path(
        "projects/<int:project_id>/alert-rules/",
        views.AlertRuleListView.as_view(),
        name="alert-rules",
    ),
    path("alert-rules/<int:pk>/", views.AlertRuleDetailView.as_view(), name="alert-rule"),
    path(
        "alert-rules/<int:pk>/test/",
        views.AlertRuleTestView.as_view(),
        name="alert-rule-test",
    ),
    path(
        "projects/<int:project_id>/alerts/",
        views.AlertFireListView.as_view(),
        name="alerts",
    ),
    path(
        "projects/<int:project_id>/performance/",
        views.PerformanceView.as_view(),
        name="performance",
    ),
    path("projects/<int:project_id>/traces/", views.TraceListView.as_view(), name="traces"),
    path("projects/<int:project_id>/vitals/", views.WebVitalsView.as_view(), name="vitals"),
    path(
        "projects/<int:project_id>/database/",
        views.DatabaseInsightsView.as_view(),
        name="database",
    ),
    path("projects/<int:project_id>/releases/", views.ReleasesView.as_view(), name="releases"),
    path("projects/<int:project_id>/logs/", views.LogListView.as_view(), name="logs"),
    path("projects/<int:project_id>/spans/", views.SpanInsightsView.as_view(), name="spans"),
    path(
        "projects/<int:project_id>/endpoint/",
        views.EndpointDetailView.as_view(),
        name="endpoint-detail",
    ),
    path(
        "projects/<int:project_id>/span/",
        views.SpanDetailView.as_view(),
        name="span-detail",
    ),
    path(
        "projects/<int:project_id>/dashboard/",
        views.DashboardView.as_view(),
        name="dashboard",
    ),
    path("traces/<uuid:pk>/", views.TraceDetailView.as_view(), name="trace"),
    path("issues/<int:pk>/", views.IssueDetailView.as_view(), name="issue"),
    path("issues/<int:pk>/status/", views.IssueStatusView.as_view(), name="issue-status"),
    path("issues/<int:issue_id>/events/", views.IssueEventsView.as_view(), name="issue-events"),
]
