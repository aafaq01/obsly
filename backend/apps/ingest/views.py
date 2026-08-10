"""The ingest endpoint.

POST /api/{project_id}/envelope/

Written to be boring and fast. It authenticates, parses, stores, and returns — no grouping,
no alerting, no fan-out. Everything expensive belongs behind this call, because a client
blocked on our latency is a client whose own request is slower because it tried to report
an error.
"""

import logging
from datetime import UTC, datetime
from typing import Any

from django.conf import settings
from django.db import transaction
from django.http import HttpRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from apps.events.models import Event
from apps.ingest import envelope as envelope_parser
from apps.ingest.auth import AuthenticationError, authenticate
from apps.ingest.normalize import event_from_payload
from apps.issues.grouping import assign_issues

logger = logging.getLogger(__name__)

EVENT_ITEM = "event"


@csrf_exempt  # SDKs are not browsers with our cookies; the DSN key is the credential.
@require_POST
def envelope(request: HttpRequest, project_id: int) -> JsonResponse:
    try:
        key = authenticate(request, project_id)
    except AuthenticationError as exc:
        return JsonResponse({"detail": str(exc)}, status=401)

    try:
        parsed = envelope_parser.parse(request.body, max_bytes=settings.OBSLY_MAX_ENVELOPE_BYTES)
    except envelope_parser.EnvelopeTooLargeError as exc:
        return JsonResponse({"detail": str(exc)}, status=413)
    except envelope_parser.EnvelopeError as exc:
        return JsonResponse({"detail": str(exc)}, status=400)

    now = datetime.now(tz=UTC)
    events: list[Event] = []
    rejected: list[dict[str, Any]] = []

    for index, item in enumerate(parsed.items_of_type(EVENT_ITEM)):
        try:
            payload = item.json()
        except envelope_parser.EnvelopeError as exc:
            # One bad item must not discard the good items beside it in the same envelope.
            rejected.append({"type": item.type, "reason": str(exc)})
            continue

        events.append(
            event_from_payload(
                key.project,
                payload,
                now=now,
                # The header id names the envelope's primary event, so it applies to the first
                # event item only. Handing it to every item would make a batch of unidentified
                # events collide on the primary key and silently keep just one.
                envelope_event_id=parsed.headers.get("event_id") if index == 0 else None,
            )
        )

    stored = _store(events)

    try:
        assign_issues(stored)
    except Exception:
        # Grouping runs after storage precisely so this is survivable. An ungrouped event is
        # still queryable and can be regrouped later; a lost event is gone.
        logger.exception("ingest: grouping failed for project %s", project_id)

    if rejected:
        logger.warning(
            "ingest: dropped %d malformed item(s) for project %s", len(rejected), project_id
        )

    return JsonResponse(
        {
            "accepted": len(stored),
            "rejected": rejected,
            "event_ids": [str(event.pk) for event in stored],
        },
        status=200,
    )


@transaction.atomic
def _store(events: list[Event]) -> list[Event]:
    """Insert, ignoring ids we already hold.

    Clients retry on timeout, and a timeout does not mean the write failed. ignore_conflicts
    makes a replayed envelope a no-op instead of a duplicate crash in the stream.
    """
    if not events:
        return []
    Event.objects.bulk_create(events, ignore_conflicts=True)
    return events
