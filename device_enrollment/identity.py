"""Ed25519 identity persistence with permissions, locking and atomic writes."""

from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .atomic import write_atomic
from .errors import IdentityCorruptError, IdentityError, LockError, NotEnrolledError
from .proof import jwk_fingerprint, public_jwk

IDENTITY_VERSION = 1
PHASE_PENDING = "pending"
PHASE_ENROLLED = "enrolled"
PRIVATE_KEY_FILENAME = "private-key.pem"
IDENTITY_FILENAME = "identity.json"
LOCK_FILENAME = ".identity.lock"


@dataclass(frozen=True)
class DeviceIdentity:
    """In-memory view of the persisted local identity."""

    private_key: Ed25519PrivateKey
    fingerprint: str
    public_jwk: dict[str, str]
    phase: str
    dispositivo_id: str | None
    enrollment_id: str | None
    api_base_url: str
    audience: str
    metadata: dict[str, Any]


class _FileLock:
    """Exclusive advisory lock held for the duration of a ``with`` block."""

    def __init__(self, path: Path):
        self._path = path
        self._fd: int | None = None

    def __enter__(self) -> "_FileLock":
        import fcntl

        flags = os.O_CREAT | os.O_RDWR
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            self._fd = os.open(str(self._path), flags, 0o600)
            fcntl.flock(self._fd, fcntl.LOCK_EX)
        except OSError as exc:
            if self._fd is not None:
                os.close(self._fd)
                self._fd = None
            raise LockError("could not acquire the identity lock") from exc
        return self

    def __exit__(self, *_exc_info: object) -> bool:
        import fcntl

        if self._fd is not None:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
            finally:
                os.close(self._fd)
                self._fd = None
        return False


class IdentityStore:
    """Filesystem-backed device identity store."""

    def __init__(self, identity_dir: str | os.PathLike[str]):
        self.identity_dir = Path(identity_dir)
        self.private_key_path = self.identity_dir / PRIVATE_KEY_FILENAME
        self.identity_path = self.identity_dir / IDENTITY_FILENAME
        self.lock_path = self.identity_dir / LOCK_FILENAME

    # -- directory -----------------------------------------------------------------
    def ensure_directory(self) -> None:
        """Create/validate the identity directory as ``0700`` service-owned."""
        if self.identity_dir.is_symlink():
            raise IdentityError("identity directory must not be a symlink")
        if not self.identity_dir.exists():
            self.identity_dir.mkdir(parents=True, mode=0o700)
            os.chmod(self.identity_dir, 0o700)
        self._validate_directory()

    def _validate_directory(self) -> None:
        try:
            st = os.lstat(self.identity_dir)
        except OSError as exc:
            raise IdentityError("identity directory is not accessible") from exc
        if stat.S_ISLNK(st.st_mode):
            raise IdentityError("identity directory must not be a symlink")
        if not stat.S_ISDIR(st.st_mode):
            raise IdentityError("identity path is not a directory")
        if st.st_uid != os.geteuid():
            raise IdentityError("identity directory is not owned by the service user")
        if stat.S_IMODE(st.st_mode) & 0o077:
            raise IdentityError("identity directory permissions are too permissive")

    def _validate_file(self, path: Path) -> None:
        st = os.lstat(path)
        if stat.S_ISLNK(st.st_mode):
            raise IdentityCorruptError("identity file must not be a symlink")
        if not stat.S_ISREG(st.st_mode):
            raise IdentityCorruptError("identity file is not a regular file")
        if st.st_uid != os.geteuid():
            raise IdentityCorruptError("identity file is not owned by the service user")
        if stat.S_IMODE(st.st_mode) & 0o077:
            raise IdentityCorruptError("identity file permissions are too permissive")

    def lock(self) -> _FileLock:
        """Return the exclusive identity lock context manager."""
        return _FileLock(self.lock_path)

    # -- private key ---------------------------------------------------------------
    @staticmethod
    def generate_private_key() -> Ed25519PrivateKey:
        return Ed25519PrivateKey.generate()

    @staticmethod
    def serialize_private_key(private_key: Ed25519PrivateKey) -> bytes:
        return private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )

    def write_private_key(self, private_key: Ed25519PrivateKey) -> None:
        if self.private_key_path.is_symlink():
            raise IdentityError("refusing to write the private key through a symlink")
        write_atomic(self.private_key_path, self.serialize_private_key(private_key), mode=0o600)

    def read_private_key_pem(self) -> bytes | None:
        if not self.private_key_path.exists():
            return None
        self._validate_file(self.private_key_path)
        return self.private_key_path.read_bytes()

    def load_private_key(self) -> Ed25519PrivateKey | None:
        pem = self.read_private_key_pem()
        if pem is None:
            return None
        try:
            key = serialization.load_pem_private_key(pem, password=None)
        except (ValueError, TypeError) as exc:
            raise IdentityCorruptError("stored private key is not a valid PKCS#8 PEM") from exc
        if not isinstance(key, Ed25519PrivateKey):
            raise IdentityCorruptError("stored private key is not Ed25519")
        return key

    # -- metadata ------------------------------------------------------------------
    def load_metadata(self) -> dict[str, Any] | None:
        if not self.identity_path.exists():
            return None
        self._validate_file(self.identity_path)
        try:
            metadata = json.loads(self.identity_path.read_text(encoding="utf-8"))
        except (ValueError, OSError) as exc:
            raise IdentityCorruptError("stored identity metadata is not valid JSON") from exc
        if not isinstance(metadata, dict) or metadata.get("version") != IDENTITY_VERSION:
            raise IdentityCorruptError("stored identity metadata has an unsupported version")
        return metadata

    def write_metadata(self, metadata: dict[str, Any]) -> None:
        if self.identity_path.is_symlink():
            raise IdentityError("refusing to write identity metadata through a symlink")
        data = json.dumps(metadata, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
        write_atomic(self.identity_path, data, mode=0o600)

    # -- high level ----------------------------------------------------------------
    def _identity(self, key: Ed25519PrivateKey, metadata: dict[str, Any]) -> DeviceIdentity:
        return DeviceIdentity(
            private_key=key,
            fingerprint=jwk_fingerprint(key.public_key()),
            public_jwk=public_jwk(key.public_key()),
            phase=str(metadata.get("phase") or ""),
            dispositivo_id=metadata.get("dispositivoId"),
            enrollment_id=metadata.get("enrollmentId"),
            api_base_url=str(metadata.get("apiBaseUrl") or ""),
            audience=str(metadata.get("audience") or ""),
            metadata=metadata,
        )

    def initialize_pending(
        self,
        api_base_url: str,
        audience: str,
        dispositivo_id: str | None = None,
    ) -> DeviceIdentity:
        """Persist (key then metadata) a pending identity before any network call.

        An existing private key is reused so a crash between key and metadata
        writes, or a crash after a request without a response, never rotates the
        key.
        """
        key = self.load_private_key()
        if key is None:
            key = self.generate_private_key()
            self.write_private_key(key)
        fingerprint = jwk_fingerprint(key.public_key())
        metadata: dict[str, Any] = {
            "version": IDENTITY_VERSION,
            "phase": PHASE_PENDING,
            "apiBaseUrl": api_base_url,
            "audience": audience,
            "keyFingerprint": fingerprint,
            "dispositivoId": dispositivo_id,
            "enrollmentId": None,
            "enrolledAt": None,
        }
        self.write_metadata(metadata)
        return self._identity(key, metadata)

    def load_identity(self) -> DeviceIdentity:
        """Load and validate the stored identity. Missing key is a hard failure."""
        self._validate_directory()
        metadata = self.load_metadata()
        key = self.load_private_key()
        if metadata is None:
            raise NotEnrolledError("no device identity is stored")
        if key is None:
            raise IdentityCorruptError("stored identity is missing its private key")
        fingerprint = jwk_fingerprint(key.public_key())
        if metadata.get("keyFingerprint") != fingerprint:
            raise IdentityCorruptError(
                "stored identity fingerprint does not match its private key"
            )
        return self._identity(key, metadata)

    def try_load_enrolled(self) -> DeviceIdentity | None:
        """Return the enrolled identity, or ``None`` if absent/invalid/not enrolled."""
        try:
            if not self.identity_path.exists():
                return None
            identity = self.load_identity()
        except (IdentityError, OSError):
            return None
        if identity.phase != PHASE_ENROLLED:
            return None
        return identity

    def persist_enrolled(
        self,
        identity: DeviceIdentity,
        device: dict[str, Any],
        *,
        api_base_url: str | None = None,
    ) -> DeviceIdentity:
        """Persist the enrolled descriptor returned by provision/recover.

        ``api_base_url`` is the base URL actually used for the current
        configuration. It overrides the value carried by ``identity`` so a
        recover/re-enroll against a reconfigured central server does not keep a
        stale URL in ``identity.json``.
        """
        server_fingerprint = device.get("keyFingerprint")
        if server_fingerprint and server_fingerprint != identity.fingerprint:
            raise IdentityCorruptError("server returned a mismatched key fingerprint")
        metadata = dict(identity.metadata)
        metadata.update(
            {
                "version": IDENTITY_VERSION,
                "phase": PHASE_ENROLLED,
                "apiBaseUrl": api_base_url or identity.api_base_url,
                "audience": device.get("audience") or identity.audience,
                "keyFingerprint": identity.fingerprint,
                "dispositivoId": device.get("dispositivoId") or identity.dispositivo_id,
                "enrollmentId": device.get("enrollmentId") or identity.enrollment_id,
                "enrolledAt": device.get("enrolledAt") or metadata.get("enrolledAt"),
            }
        )
        self.write_metadata(metadata)
        return self._identity(identity.private_key, metadata)

    def reset(self) -> list[Path]:
        """Deliberately discard the local key and metadata (no backups)."""
        removed: list[Path] = []
        for path in (self.private_key_path, self.identity_path):
            if path.exists() and not path.is_symlink():
                path.unlink()
                removed.append(path)
        return removed
