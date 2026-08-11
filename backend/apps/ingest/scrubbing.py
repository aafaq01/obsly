"""Server-side scrubbing, applied before anything is written.

The SDK strips what it knows about — Authorization headers, cookies, query strings. That is the
weaker half of the defence, because it only covers what the SDK can see coming. The half that
matters runs here, for three reasons:

- Logs now carry arbitrary `logging.getLogger()` output. An application that writes
  `log.info("auth failed for token=%s", token)` puts that token in our database, and no
  client-side setting prevents it.
- An old SDK cannot be upgraded retroactively. A rule added here protects every client that
  already shipped.
- Anything scrubbed after storage was still stored. The only scrubbing that means anything runs
  before the write.

Deny by default: the key list is what an application is likely to name a secret, and the value
patterns catch the shapes that are secrets whatever they are called.
"""

import re
from typing import Any

REDACTED = "[redacted]"

# Matched against the key's word parts, not as a substring. Substring matching redacted
# "author" for containing "auth" and "oauth_provider" for the same reason — over-redaction
# that destroys ordinary fields is how a scrubber ends up switched off.
SECRET_KEY_WORDS: frozenset[str] = frozenset(
    {
        "password",
        "passwd",
        "pwd",
        "secret",
        "token",
        "auth",
        "authorization",
        "credential",
        "credentials",
        "cookie",
        "signature",
        "session",
        "sessionid",
    }
)

# Compound names, matched after separators are collapsed — so one entry covers "api_key",
# "apiKey", "api-key" and "X-Api-Key" rather than needing a line for each spelling.
SECRET_KEY_SUBSTRINGS: tuple[str, ...] = (
    "apikey",
    "accesskey",
    "privatekey",
    "clientsecret",
    "refreshtoken",
    "bearertoken",
)

# Structural identifiers. Never value-scrubbed, because they are ids rather than content and
# some of them are all digits: a 16-character hex span id like "4000000000000010" is a valid
# card number by shape, and redacting it silently orphaned every child span beneath it.
STRUCTURAL_KEYS: frozenset[str] = frozenset(
    {"span_id", "parent_span_id", "trace_id", "event_id", "id", "sid"}
)

# Shapes that are secrets regardless of the key they arrive under.
VALUE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # PEM blocks first: they contain base64 that later patterns would only partly match.
    (
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
        "[private-key]",
    ),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"), "[jwt]"),
    (re.compile(r"\b(?:gh[pousr]|github_pat)_[A-Za-z0-9_]{20,}\b"), "[github-token]"),
    # Hyphens included: modern keys are sk-proj-… and sk-svcacct-…, and a pattern that stops at
    # the first hyphen matches nothing at all on them.
    (re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"), "[api-key]"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[aws-key-id]"),
    (re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}\b"), "[slack-token]"),
    (re.compile(r"\bBearer\s+[A-Za-z0-9._~+/-]{16,}=*", re.I), "Bearer [redacted]"),
)

# Held separately rather than as the last entry of VALUE_PATTERNS. It needs a Luhn check the
# others do not, and selecting it by "is the last pattern" meant appending any new rule would
# silently turn it into an unconditional substitution that blanked every long number.
CARD_PATTERN = re.compile(r"\b(?:\d[ -]?){13,19}\b")

MAX_DEPTH = 12

_WORD_SPLIT = re.compile(r"[^a-z0-9]+")
_SEPARATORS = re.compile(r"[^a-z0-9]")


def scrub_value(value: str) -> str:
    """Apply the value patterns to one string."""
    for pattern, replacement in VALUE_PATTERNS:
        value = pattern.sub(replacement, value)
    return CARD_PATTERN.sub(_redact_if_card, value)


def _redact_if_card(match: re.Match[str]) -> str:
    digits = re.sub(r"[ -]", "", match.group(0))
    return "[card]" if _luhn(digits) else match.group(0)


def _luhn(digits: str) -> bool:
    """Card numbers pass Luhn; timestamps, order ids and phone numbers generally do not.

    Without this the pattern eats any long number, and a scrubber that blanks ordinary ids
    trains people to turn it off.
    """
    if not digits.isdigit() or not 13 <= len(digits) <= 19:
        return False

    total = 0
    for index, char in enumerate(reversed(digits)):
        digit = int(char)
        if index % 2 == 1:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


def is_secret_key(key: str) -> bool:
    lowered = str(key).lower()

    collapsed = _SEPARATORS.sub("", lowered)
    if any(part in collapsed for part in SECRET_KEY_SUBSTRINGS):
        return True

    return any(word in SECRET_KEY_WORDS for word in _WORD_SPLIT.split(lowered) if word)


def scrub(payload: Any, *, depth: int = 0) -> Any:
    """Walk a payload, redacting secret-named keys and secret-shaped values.

    Depth-bounded, and the bound fails closed. Returning the subtree untouched at the limit
    would mean a password nested thirteen levels deep is stored verbatim — a deny-by-default
    scrubber whose deepest branch is allow-by-default is not deny-by-default.
    """
    if depth >= MAX_DEPTH:
        return REDACTED

    if isinstance(payload, dict):
        return {key: _scrub_entry(key, value, depth) for key, value in payload.items()}

    if isinstance(payload, list):
        return [scrub(item, depth=depth + 1) for item in payload]

    if isinstance(payload, str):
        return scrub_value(payload)

    return payload


def _scrub_entry(key: Any, value: Any, depth: int) -> Any:
    name = str(key)

    if name.lower() in STRUCTURAL_KEYS and not isinstance(value, dict | list):
        return value

    if is_secret_key(name):
        # A container under a secret-sounding name is redacted leaf by leaf rather than
        # replaced wholesale: `{"session": {"id": 1, "token": "x"}}` should lose the token and
        # keep the shape, because destroying the structure destroys the context around it.
        if isinstance(value, dict | list):
            return _redact_leaves(value, depth + 1)
        return REDACTED

    return scrub(value, depth=depth + 1)


def _redact_leaves(value: Any, depth: int) -> Any:
    if depth >= MAX_DEPTH:
        return REDACTED
    if isinstance(value, dict):
        return {key: _redact_leaves(item, depth + 1) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_leaves(item, depth + 1) for item in value]
    return REDACTED
