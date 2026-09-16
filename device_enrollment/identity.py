"""Ed25519 identity persistence with permissions, locking and atomic writes."""

from __future__ import annotations

import json
import os
import stat
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Self

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


class _StoreLock:
    """Re-entrant, per-instance identity lock context manager.

    ``_guard`` is a ``threading.RLock``: it provides in-process mutual exclusion
    between threads and re-entrancy for the owning thread, so nested ``lock()``
    calls (e.g. ``EnrollmentClient`` already holding the lock while calling a
    guarded mutator) never self-deadlock. The ``flock`` is only acquired at the
    outermost depth and provides cross-process exclusion. A second
    ``IdentityStore`` instance in the same process blocks on ``flock``, which is
    the desired behavior.
    """

    def __init__(self, store: IdentityStore):
        self._store = store

    def __enter__(self) -> Self:
        store = self._store
        store._guard.acquire()
        try:
            if store._lock_depth == 0:
                file_lock = _FileLock(store.lock_path)
                file_lock.__enter__()
                store._file_lock = file_lock
            store._lock_depth += 1
        except BaseException:
            store._guard.release()
            raise
        return self

    def __exit__(self, *exc_info: object) -> Literal[False]:
        store = self._store
        try:
            store._lock_depth -= 1
            if store._lock_depth == 0 and store._file_lock is not None:
                file_lock, store._file_lock = store._file_lock, None
                file_lock.__exit__(*exc_info)
        finally:
            store._guard.release()
        return False


class IdentityStore:
    """Filesystem-backed device identity store."""

    def __init__(self, identity_dir: str | os.PathLike[str]):
        self.identity_dir = Path(identity_dir)
        self.private_key_path = self.identity_dir / PRIVATE_KEY_FILENAME
        self.identity_path = self.identity_dir / IDENTITY_FILENAME
        self.lock_path = self.identity_dir / LOCK_FILENAME
        # ``_guard`` is the in-process mutex (re-entrant for its owning thread);
        # ``_file_lock`` is only held while ``_lock_depth`` is non-zero so nested
        # ``lock()`` calls never attempt a second ``flock`` on another fd.
        self._guard = threading.RLock()
        self._lock_depth = 0
        self._file_lock: _FileLock | None = None

    # -- directory -----------------------------------------------------------------
    def ensure_directory(self) -> None:
        """Create/validate the identity directory as ``0700`` service-owned."""
        if self.identity_dir.is_symlink():
            raise IdentityError("identity directory must not be a symlink")
        if not self.identity_dir.exists():
            # ``exist_ok=True`` closes the check-then-create race between two
            # processes. Parents created implicitly still inherit the process
            # umask rather than 0700; ``_validate_directory`` rejects them if that
            # leaves them too permissive.
            self.identity_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
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

    def _validate_directory_for_reset(self) -> bool:
        """Comprueba lo mínimo para que un ``reset`` sea seguro.

        A diferencia de ``_validate_directory``, aquí no se exige propietario ni
        permisos estrictos: un directorio comprometido (p. ej. permisos laxos o
        de otro usuario) igual debe poder limpiarse para no dejar la clave
        privada en disco. La validación estricta podría impedir precisamente el
        reset que se necesita tras una revocación.

        Sí se rechaza un symlink o cualquier cosa que no sea un directorio,
        porque borrar a través de ellos podría afectar archivos fuera del
        directorio de identidad.

        Devuelve ``True`` si existe un directorio real; ``False`` si no existe;
        lanza ``IdentityError`` si es un symlink, no es un directorio o no es
        accesible.
        """
        try:
            st = os.lstat(self.identity_dir)
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise IdentityError("identity directory is not accessible") from exc
        if stat.S_ISLNK(st.st_mode):
            raise IdentityError(
                "refusing to reset through a symlinked identity directory"
            )
        if not stat.S_ISDIR(st.st_mode):
            raise IdentityError("identity path is not a directory")
        return True

    def _read_secure_file(self, path: Path) -> bytes | None:
        """Read ``path`` through an fd so it cannot be swapped between check and use.

        Opening with ``O_NOFOLLOW`` (where available) prevents following a symlink
        planted after a separate ``lstat``. Validation then happens on the open
        descriptor via ``fstat`` and the bytes are read from that same descriptor,
        which closes the TOCTOU window of the previous validate-then-open read.
        """
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(path, flags)
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise IdentityCorruptError("identity file could not be opened safely") from exc
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode):
                raise IdentityCorruptError("identity file is not a regular file")
            if st.st_uid != os.geteuid():
                raise IdentityCorruptError("identity file is not owned by the service user")
            if stat.S_IMODE(st.st_mode) & 0o077:
                raise IdentityCorruptError("identity file permissions are too permissive")
            if st.st_nlink > 1:
                raise IdentityCorruptError("identity file must not be hard-linked")
            chunks: list[bytes] = []
            while True:
                chunk = os.read(fd, 65536)
                if not chunk:
                    break
                chunks.append(chunk)
            return b"".join(chunks)
        finally:
            os.close(fd)

    def lock(self) -> _StoreLock:
        """Return the re-entrant exclusive identity lock context manager."""
        return _StoreLock(self)

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
        return self._read_secure_file(self.private_key_path)

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
        raw = self._read_secure_file(self.identity_path)
        if raw is None:
            return None
        try:
            metadata = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
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
    ) -> DeviceIdentity:
        """Persist (key then metadata) a pending identity before any network call.

        An existing private key is reused so a crash between key and metadata
        writes, or a crash after a request without a response, never rotates the
        key.
        """
        with self.lock():
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
                "dispositivoId": None,
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
        if not server_fingerprint or server_fingerprint != identity.fingerprint:
            raise IdentityCorruptError("server returned a mismatched key fingerprint")
        with self.lock():
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
        """Deliberately discard the local key and metadata (no backups).

        Valida que el directorio sea real (no un symlink ni otra cosa) antes de
        borrar, pero no exige propietario ni permisos estrictos para que un
        directorio comprometido nunca bloquee una limpieza segura. Ver
        ``_validate_directory_for_reset``.
        """
        if not self._validate_directory_for_reset():
            return []
        with self.lock():
            removed: list[Path] = []
            for path in (self.private_key_path, self.identity_path):
                if path.exists() and not path.is_symlink():
                    path.unlink()
                    removed.append(path)
            return removed
