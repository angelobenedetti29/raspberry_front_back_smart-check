"""Signed HTTP transport for operational device traffic.

Logging is allowlisted: only method, path without query, status, duration and a
safe error label are emitted. Headers, proofs, keys, bodies and secret-rich
exception strings are never logged.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import urlsplit

import requests

from .errors import DeviceEnrollmentError
from .identity import DeviceIdentity
from .proof import DEVICE_TYP, build_proof, serialize_body

logger = logging.getLogger("device_enrollment.transport")

_REDIRECT_STATUS = {301, 302, 303, 307, 308}
_MAX_BODY_CHARS = 65536


@dataclass
class PreparedRequest:
    method: str
    url: str
    target: str
    body: bytes
    headers: dict[str, str]


@dataclass
class HttpResult:
    status_code: int | None
    body_text: str = ""
    transport_error: str | None = None
    is_redirect: bool = False

    @property
    def ok(self) -> bool:
        return self.status_code is not None and 200 <= self.status_code < 300

    @property
    def ambiguous(self) -> bool:
        """Ambiguous outcomes are retried via recover, never by rotating keys."""
        return self.transport_error is not None or (
            self.status_code is not None and self.status_code >= 500
        )

    def json_body(self) -> dict[str, Any] | None:
        try:
            data = json.loads(self.body_text)
        except (ValueError, TypeError):
            return None
        return data if isinstance(data, dict) else None

    def error_code(self) -> str | None:
        data = self.json_body()
        if not data:
            return None
        errors = data.get("errors")
        if isinstance(errors, dict):
            code = errors.get("code")
            if isinstance(code, str):
                return code
        return None


def prepare(
    identity: DeviceIdentity,
    method: str,
    url: str,
    payload: dict[str, Any],
    typ: str,
    subject: str,
    *,
    audience: str | None = None,
) -> PreparedRequest:
    """Serialize the body once, sign those exact bytes and build the request."""
    split = urlsplit(url)
    if split.query:
        raise DeviceEnrollmentError("signed targets must not include a query string")
    target = split.path
    body = serialize_body(payload)
    token = build_proof(
        identity.private_key,
        identity.fingerprint,
        audience or identity.audience,
        method,
        target,
        body,
        typ,
        subject,
    )
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"DeviceProof {token}",
    }
    return PreparedRequest(method=method, url=url, target=target, body=body, headers=headers)


def _safe_path(target: str) -> str:
    return target.split("?", 1)[0] or "/"


def _emit_log(
    log: logging.Logger | None,
    method: str,
    target: str,
    status: int | None,
    started: float,
    error: str | None,
) -> None:
    if log is None:
        return
    duration_ms = int((time.monotonic() - started) * 1000)
    log.info(
        "device_request method=%s path=%s status=%s duration_ms=%d error=%s",
        method,
        _safe_path(target),
        status if status is not None else "error",
        duration_ms,
        error or "-",
    )


def send_prepared(
    prepared: PreparedRequest,
    session: requests.Session,
    timeout: float = 10.0,
    log: logging.Logger | None = logger,
) -> HttpResult:
    """Send exactly the prepared bytes; redirects are never followed."""
    started = time.monotonic()
    try:
        response = session.request(
            prepared.method,
            prepared.url,
            data=prepared.body,
            headers=prepared.headers,
            timeout=timeout,
            allow_redirects=False,
        )
    except requests.RequestException as exc:
        result = HttpResult(status_code=None, transport_error=type(exc).__name__)
        _emit_log(log, prepared.method, prepared.target, None, started, result.transport_error)
        return result

    text = response.text or ""
    if len(text) > _MAX_BODY_CHARS:
        text = text[:_MAX_BODY_CHARS]
    is_redirect = bool(response.is_redirect) or response.status_code in _REDIRECT_STATUS
    result = HttpResult(status_code=response.status_code, body_text=text, is_redirect=is_redirect)
    _emit_log(
        log,
        prepared.method,
        prepared.target,
        response.status_code,
        started,
        "unexpected_redirect" if is_redirect else result.error_code(),
    )
    return result


class SignedTransport:
    """Operational transport used by the ping/lote/inicio senders."""

    def __init__(
        self,
        identity_provider: Callable[[], DeviceIdentity | None],
        api_base_url: str,
        audience: str,
        *,
        session: requests.Session | None = None,
        timeout: float = 10.0,
        clock: Callable[[], float] = time.time,
        lock: threading.Lock | None = None,
    ):
        self._identity_provider = identity_provider
        self.api_base_url = (api_base_url or "").rstrip("/")
        self.audience = audience or ""
        self.session = session if session is not None else requests.Session()
        self.timeout = timeout
        self.clock = clock
        # ``requests.Session`` is not thread-safe. Telemetry and the lote senders
        # share one transport/session instance, so serialise sends.
        self._lock = lock if lock is not None else threading.Lock()
        self.last_error: str | None = None
        self.last_status_code: int | None = None
        self.last_response_text: str | None = None

    def url_for(self, path: str) -> str:
        return f"{self.api_base_url}{path}"

    def post(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        typ: str = DEVICE_TYP,
        subject: str | None = None,
    ) -> bool:
        self.last_error = None
        self.last_status_code = None
        self.last_response_text = None

        identity = self._identity_provider()
        if identity is None:
            self.last_error = "device_not_enrolled"
            return False
        if not self.api_base_url or not self.audience:
            self.last_error = "device_transport_not_configured"
            return False
        resolved_subject = subject if subject is not None else identity.dispositivo_id
        if not resolved_subject:
            self.last_error = "device_identity_incomplete"
            return False

        try:
            prepared = prepare(
                identity,
                "POST",
                self.url_for(path),
                payload,
                typ,
                resolved_subject,
                audience=self.audience,
            )
        except DeviceEnrollmentError:
            self.last_error = "device_proof_unavailable"
            return False

        # Serialise access to the shared ``requests.Session`` (not thread-safe).
        with self._lock:
            result = send_prepared(prepared, self.session, self.timeout)
        self.last_status_code = result.status_code
        if result.body_text:
            self.last_response_text = result.body_text
        if result.ok:
            return True
        if result.is_redirect:
            self.last_error = "unexpected_redirect"
        elif result.transport_error is not None:
            self.last_error = result.transport_error
        else:
            self.last_error = result.error_code() or f"http_{result.status_code}"
        return False
