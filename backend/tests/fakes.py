"""Dobles de prueba compartidos por la suite de backend/tests.

Solo contiene clases y builders reutilizados por más de un módulo de test; no
define fixtures de pytest. Los dobles específicos de un único archivo (por
ejemplo las sesiones guionadas de device_enrollment) se quedan junto a sus tests.

La sección "Identidad de dispositivo" prueba el paquete device_enrollment y está
marcada para poder extraerse a device_enrollment/tests/ cuando se ejecute L10.
"""

from device_enrollment.identity import DeviceIdentity, IdentityStore


# --------------------------------------------------------------------------
# Transporte firmado (casos de uso del backend)
# --------------------------------------------------------------------------

class FakeTransport:
    """Transporte falso que registra llamadas y expone el estado de error.

    Los valores por defecto simulan un 503. Los tests que necesiten otro
    status/texto (por ejemplo 422 "unprocessable") los pasan por constructor.
    """

    def __init__(
        self,
        result: bool = True,
        last_error: str | None = "last error",
        last_status_code: int | None = 503,
        last_response_text: str | None = "service unavailable",
    ):
        self.result = result
        self.calls: list[tuple[str, dict, dict]] = []
        self.last_error = last_error
        self.last_status_code = last_status_code
        self.last_response_text = last_response_text

    def post(self, path, payload, **kwargs):
        self.calls.append((path, payload, kwargs))
        return self.result


class BareTransport:
    """Transporte mínimo sin atributos last_*: los accessors deben dar None."""

    def post(self, path, payload, **kwargs):
        return True


# --------------------------------------------------------------------------
# Casos de uso de lote (backend)
# --------------------------------------------------------------------------

class FakeSendLoteUseCase:
    """Envío de lote falso; registra en ``sent`` los payloads recibidos."""

    def __init__(
        self,
        success: bool = True,
        status_code=None,
        error=None,
        response_text=None,
        target: str = "http://central:9000/api/v1/lotes",
    ):
        self.success = success
        self.status_code = status_code
        self.error = error
        self.response_text = response_text
        self.target = target
        self.sent: list[dict] = []

    def execute(self, payload):
        self.sent.append(payload)
        return self.success

    def get_last_status_code(self):
        return self.status_code

    def get_last_error(self):
        return self.error

    def get_last_response_text(self):
        return self.response_text


def make_lote_payload(**overrides) -> dict:
    """Payload de lote válido y completo; ``overrides`` pisa claves puntuales."""
    payload = {
        "productoId": "a1b2c3d4-5678-90ab-cdef-1234567890ab",
        "turno": "mañana",
        "inicioAt": "2024-01-01T08:00:00Z",
        "finAt": "2024-01-01T09:00:00Z",
        "totalUnidades": 3,
        "correctos": 2,
        "quemados": 1,
        "crudas": 0,
        "correctosKg": 0.05,
        "quemadosKg": 0.03,
        "crudosKg": 0.0,
        "tempHorno1": 220.0,
        "tempCombHorno1": 315.0,
        "tempHorno2": 218.0,
        "tempCombHorno2": 312.0,
        "velocidadCinta": 1.1,
    }
    payload.update(overrides)
    return payload


# --------------------------------------------------------------------------
# Respuestas HTTP genéricas
# --------------------------------------------------------------------------

class FakeResponse:
    """Respuesta HTTP mínima para monkeypatch de requests y sesiones falsas."""

    def __init__(self, status_code: int = 200, text: str = "{}", is_redirect: bool = False):
        self.status_code = status_code
        self.text = text
        self.is_redirect = is_redirect


# --------------------------------------------------------------------------
# Identidad de dispositivo (device_enrollment) — extraíble en L10
# --------------------------------------------------------------------------

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
