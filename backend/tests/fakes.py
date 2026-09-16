"""Dobles de prueba compartidos por la suite de backend/tests.

Solo contiene clases y builders reutilizados por más de un módulo de test; no
define fixtures de pytest. Los dobles específicos de un único archivo (por
ejemplo las sesiones guionadas de device_enrollment) se quedan junto a sus tests.
"""

from device_enrollment.transport import SendResult

# --------------------------------------------------------------------------
# Transporte firmado (casos de uso del backend)
# --------------------------------------------------------------------------

class FakeTransport:
    """Transporte falso que registra llamadas y devuelve un ``SendResult``.

    Por defecto devuelve un resultado exitoso; los tests que necesiten otro
    status/error/texto pasan un ``SendResult`` explícito por constructor.
    """

    def __init__(self, result: SendResult | None = None):
        self.result = result if result is not None else SendResult(ok=True)
        self.calls: list[tuple[str, dict, dict]] = []

    def post(self, path, payload, **kwargs):
        self.calls.append((path, payload, kwargs))
        return self.result


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
        self.target = target
        self.sent: list[dict] = []
        self.result = SendResult(
            ok=success,
            status_code=status_code,
            error=error,
            response_text=response_text,
        )

    def execute(self, payload):
        self.sent.append(payload)
        return self.result


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
