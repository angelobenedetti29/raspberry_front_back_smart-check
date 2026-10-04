"""Puerto de control del streaming y tipos que consume la interfaz.

Las secciones no importan `backend.*`: este módulo traduce el estado del backend
a tipos propios, con primitivos listos para mostrar. Es la misma frontera que ya
existe con `ProveedorEstado`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol, runtime_checkable

from PySide6.QtGui import QImage


class ModoUI(str, Enum):
    """Qué se publica: el video tal cual o con las cajas dibujadas."""

    PLANO = "plano"
    INFERENCIA = "inferencia"

    @property
    def etiqueta(self) -> str:
        """Rótulo para la interfaz."""
        return "Video plano" if self is ModoUI.PLANO else "Con detecciones"


class EstadoModeloUI(str, Enum):
    """Fase de carga del modelo de inferencia."""

    CARGANDO = "cargando"
    LISTO = "listo"
    ERROR = "error"

    @property
    def etiqueta(self) -> str:
        """Rótulo para la interfaz."""
        return {
            EstadoModeloUI.CARGANDO: "Cargando",
            EstadoModeloUI.LISTO: "Listo",
            EstadoModeloUI.ERROR: "Error",
        }[self]


@dataclass(frozen=True, slots=True)
class OpcionFuente:
    """Una fuente elegible: cámara o archivo de `paths.videos_dir`."""

    valor: str
    """Lo que se le pasa al servicio (`"0"`, `"t1.mp4"`)."""

    etiqueta: str
    """Lo que se muestra (`"Cámara 0"`, `"t1.mp4"`)."""


@dataclass(frozen=True, slots=True)
class OpcionModelo:
    """Un modelo del catálogo, con el motor que **realmente** se usaría."""

    model_id: str
    etiqueta: str
    motor: str
    """Rótulo del motor resuelto (`"CPU"`, `"NPU"`), vacío si no hay motor."""

    disponible: bool
    motivo: str
    """Por qué no se puede usar. Vacío cuando está disponible."""


@dataclass(frozen=True, slots=True)
class EstadoUI:
    """Estado del streaming listo para mostrar, sin tipos del backend."""

    publicando: bool
    capturando: bool
    modo: ModoUI
    fuente: str
    fps: float
    frames: int
    detecciones: int
    activas: int
    ultima_deteccion: str
    """Formateada para mostrar (`"TCOK 0.82"`), vacía si todavía no hay."""

    retraso_ms: int
    """Retraso de la inferencia respecto del frame capturado."""

    modelo_id: str | None
    modelo_etiqueta: str
    modelo_archivo: str
    """Archivo cargado de verdad: puede diferir del pedido por el auto-redirect."""

    motor: str
    modelo_estado: EstadoModeloUI
    reintento_en: float
    """Segundos hasta el próximo reintento de captura o publicación."""

    error: str
    """Vacío cuando no hay nada que reportar."""


@runtime_checkable
class ControladorStreaming(Protocol):
    """Lo que la sección En vivo puede pedirle al servicio de streaming.

    Los métodos de cambio no bloquean: encolan la intención y el pipeline la
    aplica. Una sección nunca debe esperar a que el streaming termine algo.
    """

    def estado(self) -> EstadoUI: ...

    def fuentes(self) -> tuple[OpcionFuente, ...]: ...

    def modelos(self) -> tuple[OpcionModelo, ...]: ...

    def ultimo_frame(self) -> QImage | None:
        """Último frame para mostrar, o None si no hay preview activo."""
        ...

    def fijar_publicacion(self, activo: bool) -> None: ...

    def fijar_modo(self, modo: ModoUI) -> None: ...

    def fijar_fuente(self, valor: str) -> None: ...

    def fijar_modelo(self, model_id: str) -> None: ...

    def solicitar_preview(self, activo: bool) -> None: ...
