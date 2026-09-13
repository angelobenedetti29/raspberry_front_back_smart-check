"""Device API URL normalization shared by the CLI and the backend config."""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from .errors import ConfigurationError

API_SUFFIX = "/api/v1"
_HTTP_RE = re.compile(r"^https?://", re.IGNORECASE)

# Plaintext http:// is only tolerated for literal loopback development hosts.
# Signatures authenticate the request but do not encrypt it, so a non-loopback
# http:// endpoint would expose proofs/bodies in clear text.
_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


def normalize_api_base_url(value: str | None) -> str:
    """Return an API base URL guaranteed to end with ``/api/v1``.

    Legacy installations may still provide the bare origin (``CENTRAL_BASE_URL``);
    those are upgraded by appending the API suffix where feasible.

    ``https://`` is required for every non-loopback host. ``http://`` is accepted
    only when the host is a literal loopback (``127.0.0.1``, ``::1`` or
    ``localhost``), which is the documented development escape hatch.
    """
    text = (value or "").strip().rstrip("/")
    if not text:
        raise ConfigurationError("device API base URL is not configured")
    if not _HTTP_RE.match(text):
        raise ConfigurationError("device API base URL must be an http(s) URL")

    split = urlsplit(text)
    scheme = split.scheme.lower()
    host = (split.hostname or "").lower()
    if scheme == "http" and host not in _LOOPBACK_HOSTS:
        raise ConfigurationError(
            "device API base URL must use https://; plaintext http:// is only "
            "allowed for literal loopback development hosts "
            "(127.0.0.1, ::1, localhost)"
        )

    if not text.endswith(API_SUFFIX):
        text = text + API_SUFFIX
    return text
