"""DSN parsing.

    http://<public_key>@<host>[:<port>]/<project_id>

The key is public and write-only, which is what makes a DSN safe to paste into a config file
or ship in a bundle.
"""

from dataclasses import dataclass
from urllib.parse import urlsplit


class DsnError(ValueError):
    """The DSN is unusable. Raised at init, never at capture time."""


@dataclass(frozen=True)
class Dsn:
    public_key: str
    origin: str
    project_id: str

    @property
    def envelope_url(self) -> str:
        return f"{self.origin}/api/{self.project_id}/envelope/"


def parse(raw: str) -> Dsn:
    parts = urlsplit(raw.strip())

    if parts.scheme not in ("http", "https"):
        raise DsnError(f"DSN scheme must be http or https, got {parts.scheme!r}")
    if not parts.username:
        raise DsnError("DSN is missing its public key (expected http://<key>@host/<project_id>)")
    if parts.password:
        # A DSN is not a secret and there is nothing for a password to protect. One appearing
        # here means somebody pasted a database URL, and silently ignoring it hides that.
        raise DsnError("DSN must not contain a password")
    if not parts.hostname:
        raise DsnError("DSN is missing a host")

    project_id = parts.path.strip("/")
    if not project_id.isdigit():
        raise DsnError(f"DSN path must be a numeric project id, got {parts.path!r}")

    netloc = parts.hostname if parts.port is None else f"{parts.hostname}:{parts.port}"
    return Dsn(
        public_key=parts.username, origin=f"{parts.scheme}://{netloc}", project_id=project_id
    )
