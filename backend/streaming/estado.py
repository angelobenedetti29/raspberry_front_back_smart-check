"""Estado runtime del servicio de streaming.

El pipeline y el publicador actualizan un `RegistroEstado` desde sus hilos; la
interfaz sólo consume instantáneas inmutables, así que nunca ve un estado a
medio escribir.
"""

from __future__ import annotations

from dataclasses import dataclass, fields, replace
from enum import Enum
from pathlib import Path
from threading import Lock

from backend.inference import MotorInferencia, ResultadoDeteccion


class ModoPublicacion(str, Enum):
    """Qué se dibuja sobre el frame que se publica.

    El modo sólo controla el dibujo: la inferencia corre en ambos casos.
    """

    PLANO = "plano"
    INFERENCIA = "inferencia"


class EstadoModelo(str, Enum):
    """Fase de carga del detector."""

    CARGANDO = "cargando"
    LISTO = "listo"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class EstadoStreaming:
    """Fotografía inmutable del runtime, lista para mostrar."""

    publicando: bool
    modo: ModoPublicacion
    capturando: bool
    fuente: str
    frames_publicados: int
    fps: float
    detecciones: int
    activas: int
    ultima_deteccion: ResultadoDeteccion | None
    # Si la inferencia va más lenta que la captura, las cajas publicadas
    # corresponden a un frame anterior. Se expone para no mentir en la UI.
    retraso_inferencia_ms: int
    modelo_id: str | None
    modelo_label: str
    # Motor y ruta efectivamente cargados: pueden no coincidir con lo pedido
    # cuando el auto-redirect .onnx -> .hef entra en juego.
    motor: MotorInferencia | None
    modelo_path: Path | None
    modelo_estado: EstadoModelo
    reconexiones: int
    # Segundos que faltan para el próximo intento de publicación (0.0 = ahora).
    reintento_en: float
    # Vacío cuando no hay error. Es el único lugar donde se ve una falla de
    # captura, publicación o carga de modelo.
    error: str


_CAMPOS = frozenset(campo.name for campo in fields(EstadoStreaming))
_CONTADORES = frozenset({"frames_publicados", "detecciones", "reconexiones"})


class RegistroEstado:
    """Acumulador mutable y thread-safe del estado runtime.

    Sólo el hilo de producción y el de inferencia lo escriben. Cada escritura se
    toma completa bajo lock, y `instantanea()` devuelve un objeto inmutable que
    se puede leer sin lock y sin riesgo de ver un estado parcial.
    """

    def __init__(
        self,
        fuente: str,
        modelo_id: str | None,
        modelo_label: str,
        modo: ModoPublicacion = ModoPublicacion.INFERENCIA,
    ) -> None:
        self._lock = Lock()
        self._estado = EstadoStreaming(
            publicando=False,
            modo=modo,
            capturando=False,
            fuente=fuente,
            frames_publicados=0,
            fps=0.0,
            detecciones=0,
            activas=0,
            ultima_deteccion=None,
            retraso_inferencia_ms=0,
            modelo_id=modelo_id,
            modelo_label=modelo_label,
            motor=None,
            modelo_path=None,
            modelo_estado=EstadoModelo.CARGANDO,
            reconexiones=0,
            reintento_en=0.0,
            error="",
        )

    def actualizar(self, **campos: object) -> None:
        """Cambia campos puntuales bajo lock.

        Falla ante un campo desconocido en vez de ignorarlo: un typo silencioso
        daría un estado que nunca se actualiza.
        """
        desconocidos = set(campos) - _CAMPOS
        if desconocidos:
            raise ValueError(
                f"Campos de estado desconocidos: {', '.join(sorted(desconocidos))}"
            )
        with self._lock:
            self._estado = replace(self._estado, **campos)

    def sumar(self, campo: str, delta: int = 1) -> None:
        """Incrementa un contador (`frames_publicados`, `detecciones`, `reconexiones`)."""
        if campo not in _CONTADORES:
            raise ValueError(f"'{campo}' no es un contador de estado")
        with self._lock:
            self._estado = replace(
                self._estado, **{campo: getattr(self._estado, campo) + delta}
            )

    def instantanea(self) -> EstadoStreaming:
        """Devuelve el estado actual. Es inmutable, así que no hace falta copiarlo."""
        with self._lock:
            return self._estado
