"""Device API URL normalization shared by the CLI and the backend config."""

from __future__ import annotations

import re
from urllib.parse import SplitResult, urlsplit

from .errors import ConfigurationError

API_SUFFIX = "/api/v1"
_HTTP_RE = re.compile(r"^https?://", re.IGNORECASE)

# Plaintext http:// is only tolerated for literal loopback development hosts.
# Signatures authenticate the request but do not encrypt it, so a non-loopback
# http:// endpoint would expose proofs/bodies in clear text.
_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


def _parse_http_url(value: str | None) -> SplitResult:
    """Parse a non-empty ``http(s)`` URL, rejecting any other scheme.

    Shared by both public helpers so URL parsing and component validation live
    in exactly one place.
    """
    text = (value or "").strip()
    if not text or not _HTTP_RE.match(text):
        raise ConfigurationError("device API base URL must be an http(s) URL")
    return urlsplit(text)


def _reject_embedded_components(split: SplitResult) -> None:
    """Reject userinfo credentials, query strings and fragments."""
    if split.username is not None or split.password is not None:
        raise ConfigurationError("device API base URL must not embed userinfo credentials")
    if split.query:
        raise ConfigurationError("device API base URL must not contain a query string")
    if split.fragment:
        raise ConfigurationError("device API base URL must not contain a fragment")


def validate_secure_base_url(value: str | None) -> str:
    """Enforce the scheme/TLS policy for an (already normalized) API base URL.

    Returns the value unchanged when it is a syntactically valid ``http(s)`` URL
    and, for plaintext ``http://``, targets a literal loopback host. Raises
    ``ConfigurationError`` otherwise. Callers that receive an already-normalized
    URL (e.g. ``EnrollmentClient``) use this to reject insecure endpoints that
    bypassed ``normalize_api_base_url``.

    It also rejects userinfo credentials, query strings and fragments, mirroring
    ``normalize_api_base_url`` so a directly-constructed caller cannot smuggle
    them past normalization.
    """
    text = (value or "").strip()
    split = _parse_http_url(text)
    _reject_embedded_components(split)

    scheme = split.scheme.lower()
    host = (split.hostname or "").lower()
    if scheme == "http" and host not in _LOOPBACK_HOSTS:
        raise ConfigurationError(
            "device API base URL must use https://; plaintext http:// is only "
            "allowed for literal loopback development hosts "
            "(127.0.0.1, ::1, localhost)"
        )
    return text


def normalize_api_base_url(value: str | None) -> str:
    """Return an API base URL guaranteed to end with ``/api/v1``.

    Legacy installations may still provide the bare origin (``CENTRAL_BASE_URL``);
    those are upgraded by appending the API suffix where feasible.

    ``https://`` is required for every non-loopback host. ``http://`` is accepted
    only when the host is a literal loopback (``127.0.0.1``, ``::1`` or
    ``localhost``), which is the documented development escape hatch.

    Userinfo, query strings and fragments are rejected instead of being naively
    carried into the appended suffix (e.g. ``...?x=1/api/v1``).
    """
    text = (value or "").strip().rstrip("/")
    if not text:
        raise ConfigurationError("device API base URL is not configured")

    # Single source of truth for scheme/TLS policy plus userinfo/query/fragment.
    text = validate_secure_base_url(text)

    # Case-insensitive so an explicit ``/API/V1`` is recognized, not duplicated.
    if not text.lower().endswith(API_SUFFIX):
        text = text + API_SUFFIX
    return text
