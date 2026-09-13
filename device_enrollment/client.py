"""Pending/recovery enrollment orchestration.

The flow is recover-first and never rotates a key because a response was lost:

1. Persist key then pending metadata before any network request.
2. Sign a fresh recovery proof and call ``/enrollments/recover``.
3. On 200 persist the enrolled identity.
4. On 404 redeem the invitation via ``/dispositivos/provision`` with the same key.
5. Ambiguous timeouts/5xx retry via recover; ``enrollment_unavailable`` retries
   recover once and then requires a new invitation; ``credential_revoked`` is a
   hard stop that needs a deliberate ``reset`` and a replacement invitation.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable

import requests

from .errors import (
    AlreadyEnrolledError,
    CodeRequiredError,
    CredentialRevokedError,
    DeviceEnrollmentError,
    EnrollmentRejectedError,
    IdentityCorruptError,
    NewInvitationRequiredError,
    NotEnrolledError,
)
from .identity import DeviceIdentity, IdentityStore, PHASE_ENROLLED, PHASE_PENDING
from .proof import ENROLLMENT_SUBJECT_PREFIX, ENROLLMENT_TYP
from .transport import HttpResult, prepare, send_prepared

PROVISION_PATH = "/dispositivos/provision"
RECOVER_PATH = "/dispositivos/enrollments/recover"
MAX_AMBIGUOUS_RETRIES = 2


@dataclass
class EnrollOutcome:
    status: str
    identity: DeviceIdentity
    device: dict[str, Any]


class EnrollmentClient:
    """Drives provision/recover for a single identity store."""

    def __init__(
        self,
        store: IdentityStore,
        api_base_url: str,
        audience: str,
        *,
        session: requests.Session | None = None,
        timeout: float = 10.0,
        clock: Callable[[], float] = time.time,
    ):
        self.store = store
        self.api_base_url = (api_base_url or "").rstrip("/")
        self.audience = audience or ""
        self.session = session if session is not None else requests.Session()
        self.timeout = timeout
        self.clock = clock

    # -- primitives -----------------------------------------------------------------
    def _send(
        self,
        identity: DeviceIdentity,
        path: str,
        payload: dict[str, Any],
    ) -> HttpResult:
        prepared = prepare(
            identity,
            "POST",
            self.api_base_url + path,
            payload,
            ENROLLMENT_TYP,
            ENROLLMENT_SUBJECT_PREFIX + identity.fingerprint,
            audience=self.audience,
        )
        return send_prepared(prepared, self.session, self.timeout)

    def _recover(self, identity: DeviceIdentity) -> HttpResult:
        return self._send(identity, RECOVER_PATH, {"publicKey": identity.public_jwk})

    def _provision(self, identity: DeviceIdentity, code: str) -> HttpResult:
        return self._send(
            identity,
            PROVISION_PATH,
            {"code": code, "publicKey": identity.public_jwk},
        )

    def _retry_recover(self, identity: DeviceIdentity) -> HttpResult:
        result = self._recover(identity)
        attempts = 1
        while result.ambiguous and attempts < MAX_AMBIGUOUS_RETRIES:
            result = self._recover(identity)
            attempts += 1
        return result

    def _device_from(self, result: HttpResult, identity: DeviceIdentity) -> dict[str, Any]:
        data = result.json_body()
        device = data.get("data") if data else None
        if not isinstance(device, dict):
            raise EnrollmentRejectedError(
                "server returned a malformed enrollment response",
                status=result.status_code,
            )
        server_fingerprint = device.get("keyFingerprint")
        if server_fingerprint and server_fingerprint != identity.fingerprint:
            raise IdentityCorruptError("server returned a mismatched key fingerprint")
        return device

    def _persist(self, identity: DeviceIdentity, result: HttpResult) -> EnrollOutcome:
        device = self._device_from(result, identity)
        # Record the base URL actually used by this client, not a stale value
        # carried over from a previous configuration.
        enrolled = self.store.persist_enrolled(identity, device, api_base_url=self.api_base_url)
        return EnrollOutcome("enrolled", enrolled, device)

    def _pending_identity(self, metadata: dict[str, Any] | None) -> DeviceIdentity:
        if metadata and metadata.get("phase") == PHASE_ENROLLED:
            raise AlreadyEnrolledError("device identity is already enrolled")
        if metadata and metadata.get("phase") == PHASE_PENDING:
            return self.store.load_identity()
        return self.store.initialize_pending(self.api_base_url, self.audience)

    @staticmethod
    def _resolve_code(code: str | None, code_provider: Callable[[], str] | None) -> str:
        if code:
            return code
        if code_provider is not None:
            provided = code_provider()
            if provided:
                return provided
        raise CodeRequiredError("an enrollment code is required to redeem the invitation")

    # -- public API -----------------------------------------------------------------
    def enroll(
        self,
        code: str | None = None,
        *,
        code_provider: Callable[[], str] | None = None,
    ) -> EnrollOutcome:
        """Recover-first enrollment. Reuses any pending key; never rotates on failure."""
        self.store.ensure_directory()
        with self.store.lock():
            metadata = self.store.load_metadata()
            if metadata and metadata.get("phase") == PHASE_ENROLLED:
                return EnrollOutcome("already_enrolled", self.store.load_identity(), metadata)

            identity = self._pending_identity(metadata)

            recover = self._recover(identity)
            if recover.ok:
                return self._persist(identity, recover)
            if recover.error_code() == "credential_revoked":
                raise CredentialRevokedError(
                    "the device credential was revoked",
                    code="credential_revoked",
                    status=recover.status_code,
                )
            if recover.ambiguous:
                recover = self._retry_recover(identity)
                if recover.ok:
                    return self._persist(identity, recover)
                if recover.error_code() == "credential_revoked":
                    raise CredentialRevokedError(
                        "the device credential was revoked",
                        code="credential_revoked",
                        status=recover.status_code,
                    )
                if recover.status_code != 404:
                    raise EnrollmentRejectedError(
                        "device recovery failed",
                        code=recover.error_code(),
                        status=recover.status_code,
                    )
            elif recover.status_code == 404:
                pass  # never enrolled: fall through to redemption
            elif recover.error_code() == "enrollment_unavailable":
                recover = self._recover(identity)
                if recover.ok:
                    return self._persist(identity, recover)
                if recover.error_code() == "credential_revoked":
                    raise CredentialRevokedError(
                        "the device credential was revoked",
                        code="credential_revoked",
                        status=recover.status_code,
                    )
                raise NewInvitationRequiredError(
                    "no recoverable identity and the invitation is unavailable",
                    code="enrollment_unavailable",
                    status=recover.status_code,
                )
            else:
                raise EnrollmentRejectedError(
                    "device recovery failed",
                    code=recover.error_code(),
                    status=recover.status_code,
                )

            resolved_code = self._resolve_code(code, code_provider)
            redeem = self._provision(identity, resolved_code)
            if redeem.ok:
                return self._persist(identity, redeem)
            if redeem.error_code() == "credential_revoked":
                raise CredentialRevokedError(
                    "the device credential was revoked",
                    code="credential_revoked",
                    status=redeem.status_code,
                )
            if redeem.ambiguous:
                recover = self._retry_recover(identity)
                if recover.ok:
                    return self._persist(identity, recover)
                if recover.error_code() == "credential_revoked":
                    raise CredentialRevokedError(
                        "the device credential was revoked",
                        code="credential_revoked",
                        status=recover.status_code,
                    )
                raise EnrollmentRejectedError(
                    "redemption outcome is ambiguous",
                    code=redeem.error_code(),
                    status=redeem.status_code,
                )
            if redeem.error_code() in {"enrollment_unavailable", "credential_used"}:
                recover = self._recover(identity)
                if recover.ok:
                    return self._persist(identity, recover)
                if recover.error_code() == "credential_revoked":
                    raise CredentialRevokedError(
                        "the device credential was revoked",
                        code="credential_revoked",
                        status=recover.status_code,
                    )
                raise NewInvitationRequiredError(
                    "the invitation is no longer available",
                    code=redeem.error_code(),
                    status=redeem.status_code,
                )
            raise EnrollmentRejectedError(
                "device provisioning was rejected",
                code=redeem.error_code(),
                status=redeem.status_code,
            )
        raise EnrollmentRejectedError("unreachable enrollment state")

    def recover(self) -> EnrollOutcome:
        """Proof-only recovery. Refreshes local metadata; never reactivates."""
        self.store.ensure_directory()
        with self.store.lock():
            identity = self.store.load_identity()
            result = self._recover(identity)
            if result.ok:
                return self._persist(identity, result)
            if result.error_code() == "credential_revoked":
                raise CredentialRevokedError(
                    "the device credential was revoked",
                    code="credential_revoked",
                    status=result.status_code,
                )
            if result.ambiguous:
                result = self._retry_recover(identity)
                if result.ok:
                    return self._persist(identity, result)
                if result.error_code() == "credential_revoked":
                    raise CredentialRevokedError(
                        "the device credential was revoked",
                        code="credential_revoked",
                        status=result.status_code,
                    )
            if result.status_code == 404 or result.error_code() == "enrollment_not_found":
                raise NotEnrolledError(
                    "identity was not found on the server; enroll with a new invitation"
                )
            raise EnrollmentRejectedError(
                "device recovery failed",
                code=result.error_code(),
                status=result.status_code,
            )
        raise EnrollmentRejectedError("unreachable recovery state")


__all__ = ["EnrollmentClient", "EnrollOutcome", "DeviceEnrollmentError"]
