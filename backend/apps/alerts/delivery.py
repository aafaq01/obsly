"""Getting the alert out of the building.

The hard constraint: ingest must never block on somebody else's HTTP endpoint. A webhook host
that takes thirty seconds to answer would hold an ingest worker for thirty seconds, and a
handful of those stalls the whole pipeline — an alerting feature that takes the system down is
worse than no alerting.

So the fire row is written synchronously (it is the record that something happened) and the
POST runs on a background thread that updates the row with the outcome. The alert is visible in
the UI the moment it fires, whether or not the webhook is reachable.
"""

import json
import logging
import threading
import urllib.error
import urllib.request
from typing import Any

from django.conf import settings
from django.db import connection

from apps.alerts.models import AlertFire, Delivery

log = logging.getLogger(__name__)

TIMEOUT_SECONDS = 10


def payload_for(fire: AlertFire) -> dict[str, Any]:
    """What the webhook receives.

    Flat and self-describing: the receiver is usually a Slack workflow or a two-line script,
    not a client that will look anything up. Everything needed to decide whether to care is in
    the body, and `url` is there because the first thing anyone does is open it.
    """
    issue = fire.issue
    base = getattr(settings, "OBSLY_PUBLIC_URL", "").rstrip("/")
    return {
        "rule": fire.rule.name,
        "trigger": fire.rule.trigger,
        "reason": fire.reason,
        "project": issue.project.name,
        "issue": {
            "id": issue.pk,
            "title": issue.title,
            "culprit": issue.culprit,
            "level": issue.level,
            "times_seen": issue.times_seen,
            "first_seen": issue.first_seen.isoformat(),
            "last_seen": issue.last_seen.isoformat(),
        },
        "url": f"{base}/issues/{issue.pk}" if base else "",
        "fired_at": fire.created_at.isoformat(),
    }


def deliver(fire: AlertFire) -> None:
    """Send `fire` to its rule's webhook, off the request thread."""
    if getattr(settings, "OBSLY_ALERTS_SYNC_DELIVERY", False):
        # Tests take this path. A thread that outlives the test transaction sees a database
        # that no longer contains the row it was handed.
        send_now(fire)
        return

    thread = threading.Thread(target=_send_and_close, args=(fire,), daemon=True)
    thread.start()


def _send_and_close(fire: AlertFire) -> None:
    try:
        send_now(fire)
    finally:
        # A thread that opens a connection owns it. Without this the pool leaks one connection
        # per alert until Postgres refuses new ones.
        connection.close()


def send_now(fire: AlertFire) -> None:
    """Deliver on this thread and record the outcome. Callers that must report what happened
    — the "send a test" button — use this; ingest never does."""
    body = json.dumps(payload_for(fire)).encode()
    request = urllib.request.Request(  # noqa: S310 - scheme is validated by URLField
        fire.rule.webhook_url,
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": "obsly-alerts/1.0"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # noqa: S310
            _record(fire, Delivery.SENT, status=response.status)
    except urllib.error.HTTPError as exc:
        # A 4xx or 5xx is a real answer, so the code is worth keeping: "410 Gone" tells you the
        # Slack webhook was revoked, which "failed" alone does not.
        _record(fire, Delivery.FAILED, status=exc.code, error=f"HTTP {exc.code}")
    except Exception as exc:  # a webhook host can fail in any way it likes
        _record(fire, Delivery.FAILED, error=str(exc)[:300])


def _record(fire: AlertFire, delivery: str, *, status: int | None = None, error: str = "") -> None:
    # ponytail: one attempt, no retry queue. The fire row records the failure and the UI shows
    # it; add retries when somebody has a webhook that is flaky rather than misconfigured.
    updated = AlertFire.objects.filter(pk=fire.pk).update(
        delivery=delivery, status_code=status, error=error
    )
    if not updated:
        log.warning("alert fire %s vanished before delivery was recorded", fire.pk)
