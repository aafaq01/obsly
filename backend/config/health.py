"""Health endpoint.

Serves three consumers with one route: CI waits on it before running integration tests, the
docker-compose stack uses it as a healthcheck, and it is the first thing an uptime probe will
hit once Obsly can monitor itself.
"""

import logging

from django.db import DatabaseError, connection, transaction
from django.http import HttpRequest, JsonResponse

logger = logging.getLogger(__name__)


@transaction.non_atomic_requests
def health(request: HttpRequest) -> JsonResponse:
    """Report process liveness and database reachability.

    Returns 503 rather than 200-with-a-body on failure, so that a load balancer pulls the
    instance without needing to parse JSON.

    Must stay non-atomic. ATOMIC_REQUESTS opens a transaction before the view is entered, so
    with it enabled a genuinely unreachable database raises OperationalError out of the
    middleware and this function never runs — the endpoint would fail to report the one
    condition it exists to report.
    """
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except DatabaseError:
        logger.exception("health check failed: database unreachable")
        return JsonResponse({"status": "unhealthy", "database": "unreachable"}, status=503)

    return JsonResponse({"status": "ok", "database": "ok"})
