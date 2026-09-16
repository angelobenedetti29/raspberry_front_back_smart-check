"""Expected, non-secret failures raised by the enrollment library."""

from __future__ import annotations


class DeviceEnrollmentError(Exception):
    """Base class for expected enrollment failures."""


class ConfigurationError(DeviceEnrollmentError):
    """The local configuration is missing or invalid."""


class IdentityError(DeviceEnrollmentError):
    """Base class for local identity storage failures."""


class IdentityCorruptError(IdentityError):
    """Stored key/metadata is missing, malformed or inconsistent."""


class NotEnrolledError(IdentityError):
    """No usable local identity exists yet."""


class LockError(DeviceEnrollmentError):
    """The exclusive identity lock could not be acquired."""


class CodeRequiredError(DeviceEnrollmentError):
    """An invitation code is required to continue."""


class EnrollmentRejectedError(DeviceEnrollmentError):
    """The server rejected an enrollment request."""

    def __init__(self, message: str, code: str | None = None, status: int | None = None):
        super().__init__(message)
        self.code = code
        self.status = status


class NewInvitationRequiredError(EnrollmentRejectedError):
    """The invitation is gone; a new one must be issued by an operator."""


class CredentialRevokedError(EnrollmentRejectedError):
    """The credential was revoked/replaced; a deliberate reset is required."""
