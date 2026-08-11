"""Alerting.

Everything else in Obsly is pull — you open a page and ask. This is the one push, and the only
part of the system that has to work while nobody is looking at it.

The tests that matter here are the ones about what happens when things go wrong: a webhook that
is down, a rule that would fire a thousand times, an issue that has been seen for a year. An
alerting feature that is only correct on the happy path is one that gets muted.
"""

import io
import urllib.error
import uuid
from datetime import timedelta
from typing import Any
from unittest.mock import patch

import pytest
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.alerts.delivery import payload_for, send_now
from apps.alerts.models import AlertFire, AlertRule, Delivery, Trigger
from apps.alerts.rules import evaluate
from apps.events.models import Event
from apps.issues.models import Issue, IssueStatus
from apps.projects.models import Project, ProjectKey
from tests.conftest import build_envelope, json_body, post

pytestmark = pytest.mark.django_db

HOOK = "https://hooks.example.com/services/T000/B000/xxxx"


def make_rule(project: Project, **overrides: Any) -> AlertRule:
    return AlertRule.objects.create(
        project=project,
        name=overrides.pop("name", "Page the on-call"),
        webhook_url=overrides.pop("webhook_url", HOOK),
        **overrides,
    )


def make_issue(project: Project, **overrides: Any) -> Issue:
    now = timezone.now()
    return Issue.objects.create(
        project=project,
        fingerprint=overrides.pop("fingerprint", "f" * 64),
        title=overrides.pop("title", "ValueError: cart is empty"),
        culprit="app.crud in get_cart",
        level=overrides.pop("level", "error"),
        times_seen=overrides.pop("times_seen", 1),
        first_seen=now,
        last_seen=now,
        **overrides,
    )


class TestTriggers:
    def test_a_new_issue_fires_a_new_issue_rule(self, project: Project) -> None:
        rule = make_rule(project, trigger=Trigger.NEW_ISSUE)
        issue = make_issue(project)

        fires = evaluate(issue, created=True, regressed=False)

        assert [fire.rule_id for fire in fires] == [rule.pk]
        assert "New error issue" in fires[0].reason

    def test_an_existing_issue_does_not_fire_a_new_issue_rule(self, project: Project) -> None:
        make_rule(project, trigger=Trigger.NEW_ISSUE)
        issue = make_issue(project)

        assert evaluate(issue, created=False, regressed=False) == []

    def test_a_regression_fires_a_regression_rule(self, project: Project) -> None:
        """A resolved bug coming back is the case people most want to hear about, and the one
        a "new issue" rule silently misses — the issue is not new."""
        make_rule(project, trigger=Trigger.REGRESSION)
        issue = make_issue(project, status=IssueStatus.UNRESOLVED)

        fires = evaluate(issue, created=False, regressed=True)

        assert len(fires) == 1
        assert "happened again" in fires[0].reason

    def test_a_frequency_rule_stays_quiet_below_its_threshold(self, project: Project) -> None:
        make_rule(project, trigger=Trigger.FREQUENCY, threshold=5, window_minutes=5)
        issue = make_issue(project)
        _add_events(issue, count=4)

        assert evaluate(issue, created=False, regressed=False) == []

    def test_a_frequency_rule_fires_at_its_threshold(self, project: Project) -> None:
        make_rule(project, trigger=Trigger.FREQUENCY, threshold=5, window_minutes=5)
        issue = make_issue(project)
        _add_events(issue, count=5)

        fires = evaluate(issue, created=False, regressed=False)

        assert len(fires) == 1
        assert "5 events in 5 minutes" in fires[0].reason

    def test_a_frequency_rule_counts_the_window_not_the_lifetime(self, project: Project) -> None:
        """times_seen is a lifetime counter. Reading it would fire a "50 in 5 minutes" rule on
        the first event of an issue that was busy last year and quiet since."""
        make_rule(project, trigger=Trigger.FREQUENCY, threshold=5, window_minutes=5)
        issue = make_issue(project, times_seen=10_000)
        _add_events(issue, count=20, minutes_ago=180)

        assert evaluate(issue, created=False, regressed=False) == []

    def test_a_level_filter_keeps_warnings_out_of_the_pager(self, project: Project) -> None:
        make_rule(project, trigger=Trigger.NEW_ISSUE, level="error")
        warning = make_issue(project, level="warning", fingerprint="a" * 64)

        assert evaluate(warning, created=True, regressed=False) == []

    def test_a_rule_with_no_level_takes_everything(self, project: Project) -> None:
        make_rule(project, trigger=Trigger.NEW_ISSUE, level="")
        warning = make_issue(project, level="warning")

        assert len(evaluate(warning, created=True, regressed=False)) == 1

    def test_a_disabled_rule_does_nothing(self, project: Project) -> None:
        make_rule(project, trigger=Trigger.NEW_ISSUE, enabled=False)

        assert evaluate(make_issue(project), created=True, regressed=False) == []

    def test_rules_belong_to_their_project(self, project: Project) -> None:
        other = Project.objects.create(
            organization=project.organization, name="Other", slug="other"
        )
        make_rule(other, trigger=Trigger.NEW_ISSUE)

        assert evaluate(make_issue(project), created=True, regressed=False) == []

    @override_settings(OBSLY_ALERTS_ENABLED=False)
    def test_alerting_can_be_turned_off(self, project: Project) -> None:
        make_rule(project, trigger=Trigger.NEW_ISSUE)

        assert evaluate(make_issue(project), created=True, regressed=False) == []


class TestCooldown:
    def test_the_same_issue_does_not_fire_twice_inside_the_cooldown(self, project: Project) -> None:
        """The difference between an alert and a pager loop. An issue seen a thousand times an
        hour would otherwise send a thousand notifications, and the channel gets muted — which
        is the same as no alerting, with more noise."""
        make_rule(project, trigger=Trigger.FREQUENCY, threshold=1, cooldown_minutes=30)
        issue = make_issue(project)
        _add_events(issue, count=3)

        assert len(evaluate(issue, created=False, regressed=False)) == 1
        assert evaluate(issue, created=False, regressed=False) == []

    def test_the_cooldown_is_per_issue_not_per_rule(self, project: Project) -> None:
        """One noisy issue must not silence the alert for a different issue that starts five
        minutes later."""
        make_rule(project, trigger=Trigger.NEW_ISSUE, cooldown_minutes=30)
        first = make_issue(project, fingerprint="a" * 64)
        second = make_issue(project, fingerprint="b" * 64, title="KeyError: sku")

        assert len(evaluate(first, created=True, regressed=False)) == 1
        assert len(evaluate(second, created=True, regressed=False)) == 1

    def test_a_rule_fires_again_once_the_cooldown_has_passed(self, project: Project) -> None:
        rule = make_rule(project, trigger=Trigger.FREQUENCY, threshold=1, cooldown_minutes=30)
        issue = make_issue(project)
        _add_events(issue, count=2)

        evaluate(issue, created=False, regressed=False)
        AlertFire.objects.filter(rule=rule).update(
            created_at=timezone.now() - timedelta(minutes=31)
        )

        assert len(evaluate(issue, created=False, regressed=False)) == 1


class TestDelivery:
    def test_the_fire_is_recorded_even_when_the_webhook_is_unreachable(
        self, project: Project
    ) -> None:
        """ "The webhook was down" and "nothing happened" must not look the same. The record is
        the audit trail, independent of whether anyone received it."""
        rule = make_rule(project, trigger=Trigger.NEW_ISSUE)
        issue = make_issue(project)

        with patch("urllib.request.urlopen", side_effect=OSError("connection refused")):
            fires = evaluate(issue, created=True, regressed=False)

        fire = AlertFire.objects.get(pk=fires[0].pk)
        assert fire.rule_id == rule.pk
        assert fire.delivery == Delivery.FAILED
        assert "connection refused" in fire.error

    def test_an_http_error_keeps_the_status_code(self, project: Project) -> None:
        """ "410 Gone" says the Slack webhook was revoked. "failed" does not."""
        fire = AlertFire.objects.create(
            rule=make_rule(project), issue=make_issue(project), reason="x"
        )
        # Closed explicitly. HTTPError is a file wrapper, and an unclosed one raises from
        # its deallocator whenever the collector eventually reaches it — which lands the
        # failure on some unrelated test, several files later.
        error = urllib.error.HTTPError(HOOK, 410, "Gone", {}, io.BytesIO(b""))  # type: ignore[arg-type]
        with error, patch("urllib.request.urlopen", side_effect=error):
            send_now(fire)

        fire.refresh_from_db()
        assert fire.status_code == 410
        assert fire.delivery == Delivery.FAILED

    def test_a_successful_post_is_recorded_with_its_status(self, project: Project) -> None:
        fire = AlertFire.objects.create(
            rule=make_rule(project), issue=make_issue(project), reason="x"
        )

        with patch("urllib.request.urlopen") as urlopen:
            urlopen.return_value.__enter__.return_value.status = 200
            send_now(fire)

        fire.refresh_from_db()
        assert fire.delivery == Delivery.SENT
        assert fire.status_code == 200

    def test_the_payload_carries_everything_needed_to_triage(self, project: Project) -> None:
        """The receiver is a Slack workflow or a two-line script, not a client that will look
        anything up."""
        fire = AlertFire.objects.create(
            rule=make_rule(project, trigger=Trigger.NEW_ISSUE),
            issue=make_issue(project),
            reason="New error issue",
        )

        with override_settings(OBSLY_PUBLIC_URL="https://obsly.example.com"):
            body = payload_for(fire)

        assert body["reason"] == "New error issue"
        assert body["issue"]["title"] == "ValueError: cart is empty"
        assert body["issue"]["level"] == "error"
        assert body["project"] == project.name
        assert body["url"] == f"https://obsly.example.com/issues/{fire.issue_id}"

    @override_settings(OBSLY_PUBLIC_URL="")
    def test_no_public_url_means_no_link_rather_than_a_broken_one(self, project: Project) -> None:
        """A notification linking to localhost is worse than one that admits it does not know
        where it lives."""
        fire = AlertFire.objects.create(
            rule=make_rule(project), issue=make_issue(project), reason="x"
        )

        assert payload_for(fire)["url"] == ""

    def test_delivery_runs_off_the_calling_thread_by_default(self, project: Project) -> None:
        """A webhook host that takes thirty seconds must not hold an ingest worker for thirty
        seconds. The rest of this class forces synchronous delivery; this is the check that the
        default is not synchronous."""
        fire = AlertFire.objects.create(
            rule=make_rule(project), issue=make_issue(project), reason="x"
        )

        from apps.alerts.delivery import deliver

        with (
            override_settings(OBSLY_ALERTS_SYNC_DELIVERY=False),
            patch("apps.alerts.delivery.threading.Thread") as thread,
        ):
            deliver(fire)

        assert thread.called
        assert thread.call_args.kwargs["daemon"] is True


class TestIngestIsNeverCostAnEvent:
    def test_an_event_is_stored_even_when_alerting_raises(
        self, client: Client, project: Project, project_key: ProjectKey
    ) -> None:
        """Storing the event is the one thing that must not fail. Everything after it —
        grouping, detectors, alerting — is best-effort by design."""
        make_rule(project, trigger=Trigger.NEW_ISSUE)
        payload = {"exception": {"values": [{"type": "ValueError", "value": "boom"}]}}

        with patch("apps.alerts.rules._candidates", side_effect=RuntimeError("rules exploded")):
            response = post(
                client, project, build_envelope(("event", payload)), project_key.public_key
            )

        assert response.status_code == 200
        assert Event.objects.count() == 1

    def test_a_real_ingest_fires_the_rule(
        self, client: Client, project: Project, project_key: ProjectKey
    ) -> None:
        """End to end, through the actual ingest path rather than by calling evaluate()."""
        make_rule(project, trigger=Trigger.NEW_ISSUE)
        payload = {"exception": {"values": [{"type": "KeyError", "value": "sku"}]}}

        with patch("urllib.request.urlopen") as urlopen:
            urlopen.return_value.__enter__.return_value.status = 200
            post(client, project, build_envelope(("event", payload)), project_key.public_key)

        fire = AlertFire.objects.get()
        assert fire.issue.title.startswith("KeyError")
        assert fire.delivery == Delivery.SENT


class TestAlertsAPI:
    def rules_url(self, project: Project) -> str:
        return reverse("api:alert-rules", args=[project.pk])

    def test_creating_a_rule(self, staff_client: Client, project: Project) -> None:
        response = staff_client.post(
            self.rules_url(project),
            {
                "name": "Page the on-call",
                "trigger": Trigger.NEW_ISSUE,
                "webhook_url": HOOK,
                "level": "error",
            },
            content_type="application/json",
            secure=True,
        )

        assert response.status_code == 201
        assert AlertRule.objects.get().project_id == project.pk

    def test_a_frequency_rule_needs_a_usable_threshold(
        self, staff_client: Client, project: Project
    ) -> None:
        """A threshold of zero fires on every event forever. Rejecting it is cheaper than
        explaining the pager storm."""
        response = staff_client.post(
            self.rules_url(project),
            {
                "name": "Spike",
                "trigger": Trigger.FREQUENCY,
                "threshold": 0,
                "webhook_url": HOOK,
            },
            content_type="application/json",
            secure=True,
        )

        assert response.status_code == 400
        assert "threshold" in json_body(response)

    def test_the_list_says_whether_a_rule_has_ever_fired(
        self, staff_client: Client, project: Project
    ) -> None:
        """A rules page that cannot tell you this is a page that hides its own
        misconfiguration."""
        rule = make_rule(project, trigger=Trigger.NEW_ISSUE)
        AlertFire.objects.create(rule=rule, issue=make_issue(project), reason="x")

        [row] = json_body(staff_client.get(self.rules_url(project), secure=True))

        assert row["fire_count"] == 1
        assert row["last_fired_at"]

    def test_a_rule_can_be_disabled_without_being_deleted(
        self, staff_client: Client, project: Project
    ) -> None:
        """Deleting loses the history of what was live; a flag keeps it."""
        rule = make_rule(project)

        response = staff_client.patch(
            reverse("api:alert-rule", args=[rule.pk]),
            {"enabled": False},
            content_type="application/json",
            secure=True,
        )

        assert response.status_code == 200
        rule.refresh_from_db()
        assert rule.enabled is False

    def test_the_feed_shows_what_fired(self, staff_client: Client, project: Project) -> None:
        rule = make_rule(project)
        AlertFire.objects.create(rule=rule, issue=make_issue(project), reason="New error issue")

        [row] = json_body(staff_client.get(reverse("api:alerts", args=[project.pk]), secure=True))

        assert row["reason"] == "New error issue"
        assert row["rule_name"] == rule.name
        assert row["issue_title"] == "ValueError: cart is empty"

    def test_a_rule_can_be_tested_before_an_incident(
        self, staff_client: Client, project: Project
    ) -> None:
        """A webhook you cannot test is one you find out is broken during the incident it was
        supposed to warn you about."""
        rule = make_rule(project)
        make_issue(project)

        with patch("urllib.request.urlopen") as urlopen:
            urlopen.return_value.__enter__.return_value.status = 200
            response = staff_client.post(
                reverse("api:alert-rule-test", args=[rule.pk]), secure=True
            )

        assert response.status_code == 201
        assert json_body(response)["delivery"] == Delivery.SENT

    def test_testing_a_rule_with_no_issues_says_so(
        self, staff_client: Client, project: Project
    ) -> None:
        rule = make_rule(project)

        response = staff_client.post(reverse("api:alert-rule-test", args=[rule.pk]), secure=True)

        assert response.status_code == 400
        assert "no issues" in json_body(response)["detail"]

    def test_requires_authentication(self, client: Client, project: Project) -> None:
        assert client.get(self.rules_url(project), secure=True).status_code == 403


def _add_events(issue: Issue, *, count: int, minutes_ago: int = 1) -> None:
    when = timezone.now() - timedelta(minutes=minutes_ago)
    # title is derived from the exception, not a column — the event has to carry the parts
    # it is built from.
    Event.objects.bulk_create(
        Event(
            project=issue.project,
            issue=issue,
            id=uuid.UUID(f"{index:032x}"),
            exception_type="ValueError",
            exception_value="cart is empty",
            level=issue.level,
            timestamp=when,
            payload={},
        )
        for index in range(count)
    )
