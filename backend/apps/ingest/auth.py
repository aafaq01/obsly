"""DSN authentication for the ingest endpoint."""

from django.http import HttpRequest

from apps.projects.models import ProjectKey

HEADER = "X-Obsly-Key"
# Browsers cannot set headers on sendBeacon, which is the only transport that survives a page
# unload — so the key is also accepted as a query parameter. It is a public credential either
# way; the URL leaking it into an access log discloses nothing a bundle does not already.
QUERY_PARAM = "obsly_key"


class AuthenticationError(Exception):
    pass


def authenticate(request: HttpRequest, project_id: int) -> ProjectKey:
    """Resolve the request to an active key for `project_id`.

    Every failure mode returns the same error. Distinguishing "no such key" from "key belongs to
    another project" would let an unauthenticated caller enumerate which project ids exist and
    which keys are live.
    """
    raw = request.headers.get(HEADER) or request.GET.get(QUERY_PARAM) or ""
    raw = raw.strip()

    if not raw:
        raise AuthenticationError("missing ingest key")

    key = (
        ProjectKey.objects.active()
        .filter(public_key=raw, project_id=project_id)
        .select_related("project")
        .first()
    )
    if key is None:
        raise AuthenticationError("invalid ingest key")

    return key
