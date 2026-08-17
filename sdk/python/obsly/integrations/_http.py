"""What every HTTP integration needs and none of them should own a private copy of.

There are three server integrations now — ASGI, WSGI and Django — and the rules that matter
here are the ones you cannot afford to get wrong in only two of them: which headers never
leave the process, and what counts as a failure.
"""

import re
from typing import Any

# Headers that identify a person or authorise a request. Never sent, regardless of
# send_default_pii — an SDK that can leak an Authorization header into a third-party store is a
# vulnerability wearing a feature's clothes.
ALWAYS_STRIP = frozenset(
    {"authorization", "proxy-authorization", "cookie", "set-cookie", "x-api-key", "x-auth-token"}
)

# Kept when send_default_pii is off: they describe the request, not the requester.
SAFE_HEADERS = frozenset({"content-type", "user-agent", "host", "referer"})

_NUMERIC = re.compile(r"^\d+$")
_HEXISH = re.compile(r"^[0-9a-f]{8,}$", re.IGNORECASE)
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)


def status_label(status: int) -> str:
    """gRPC-style labels, so "did it work" is one field rather than a numeric range check."""
    if status < 400:
        return "ok"
    if status == 404:
        return "not_found"
    if status in (401, 403):
        return "unauthenticated"
    if status < 500:
        return "invalid_argument"
    return "internal_error"


def safe_headers(pairs: Any, *, include_all: bool) -> dict[str, str]:
    """Header pairs, minus anything that authorises or identifies."""
    safe: dict[str, str] = {}
    for name, value in pairs:
        lowered = name.lower()
        if lowered in ALWAYS_STRIP:
            continue
        if not include_all and lowered not in SAFE_HEADERS:
            continue
        safe[lowered] = value[:500]
    return safe


def parameterize(path: str) -> str:
    """`/orders/42` and `/orders/43` are the same endpoint.

    Only a fallback. Where the framework can name the route it matched — Starlette, Flask,
    Django all can — that name is better, because it is the one the code is written in and it
    is right even when an id happens to be a word.

    But the fallback has to exist and has to guess, because the alternative is a raw path: one
    transaction per id ever requested, an aggregate of millions of rows seen once each, and a
    p95 computed over a population of one. A wrong guess merges two endpoints; no guess at all
    destroys the aggregate entirely.
    """
    if not path or path == "/":
        return "/"

    parts = []
    for part in path.split("/"):
        if _UUID.match(part) or _NUMERIC.match(part) or _HEXISH.match(part):
            parts.append("{id}")
        else:
            parts.append(part)
    return "/".join(parts) or "/"
