"""Dobles de prueba de device_enrollment (identidad y enrolamiento)."""

from device_enrollment.identity import DeviceIdentity, IdentityStore


class FakeResponse:
    """Respuesta HTTP mínima para monkeypatch de requests y sesiones falsas."""

    def __init__(self, status_code: int = 200, text: str = "{}", is_redirect: bool = False):
        self.status_code = status_code
        self.text = text
        self.is_redirect = is_redirect


def make_identity_store(tmp_path) -> IdentityStore:
    """Crea un IdentityStore con su directorio listo bajo ``tmp_path/identity``."""
    store = IdentityStore(tmp_path / "identity")
    store.ensure_directory()
    return store


def device_descriptor(
    fingerprint: str,
    *,
    dispositivo_id: str = "22222222-2222-2222-2222-222222222222",
    audience: str = "https://api.example.test/api/v1",
    enrollment_id: str = "enrollment-1",
) -> dict:
    """Descriptor de enrolamiento con el fingerprint ya resuelto.

    Para provocar un mismatch de fingerprint usar ``device_descriptor("otro")``.
    """
    return {
        "enrollmentId": enrollment_id,
        "dispositivoId": dispositivo_id,
        "keyFingerprint": fingerprint,
        "authStatus": "active",
        "enrolledAt": "2026-09-12T00:00:00Z",
        "audience": audience,
    }


def make_enrolled_identity(
    tmp_path,
    *,
    # Igual a DEVICE_ID de test_device_transport.py: los tests lo dan por sentado.
    dispositivo_id: str = "11111111-1111-1111-1111-111111111111",
    api_base_url: str = "https://api.example.test/api/v1",
    audience: str = "https://api.example.test/api/v1",
) -> DeviceIdentity:
    """Crea un store con una identidad ya enrolada, lista para firmar."""
    store = make_identity_store(tmp_path)
    pending = store.initialize_pending(api_base_url, audience)
    descriptor = device_descriptor(
        pending.fingerprint, dispositivo_id=dispositivo_id, audience=audience
    )
    return store.persist_enrolled(pending, descriptor)
