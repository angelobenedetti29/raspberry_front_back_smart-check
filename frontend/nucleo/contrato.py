"""Contrato de las secciones aisladas y contexto compartido."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Callable

from PySide6.QtWidgets import QWidget

from frontend.nucleo.bus import BusApp
from frontend.nucleo.controlador import ControladorStreaming
from frontend.nucleo.controlador_config import ControladorConfig
from frontend.nucleo.controlador_dispositivo import ControladorDispositivo
from frontend.nucleo.controlador_lotes import ControladorLotes
from frontend.nucleo.controlador_monitor import ControladorMonitor
from frontend.nucleo.proveedor import ProveedorEstado

if TYPE_CHECKING:
    from backend.config import AppConfig


class GrupoSeccion(str, Enum):
    """Ubicación de una sección según el tipo de dispositivo.

    COMUN está siempre en el rail; ENTRADA y SALIDA sólo cuando el
    dispositivo registrado es del tipo correspondiente.
    """

    COMUN = "comun"
    ENTRADA = "entrada"
    SALIDA = "salida"


@dataclass(frozen=True, slots=True)
class ContextoApp:
    """Dependencias que el shell inyecta en cada sección."""

    config: AppConfig
    bus: BusApp
    proveedor: ProveedorEstado
    controlador: ControladorStreaming
    controlador_dispositivo: ControladorDispositivo
    controlador_monitor: ControladorMonitor
    controlador_config: ControladorConfig
    controlador_lotes: ControladorLotes
    navegar: Callable[[str], None]


class SeccionBase(QWidget):
    """Base de toda sección: metadata, ciclo de vida y acceso al contexto.

    Las secciones no importan `backend.*` ni otras secciones: hablan con el
    shell por `self.ctx` (config, bus, proveedor y `navegar`).
    """

    id: str = ""
    titulo: str = ""
    icono: str = ""
    # Por defecto toda sección es común: sólo las específicas de un tipo de
    # dispositivo sobreescriben este atributo.
    grupo: GrupoSeccion = GrupoSeccion.COMUN

    def __init__(self, ctx: ContextoApp, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx

    def al_entrar(self) -> None:
        """La sección pasa a estar visible."""

    def al_salir(self) -> None:
        """La sección deja de estar visible."""

    def al_cerrar(self) -> None:
        """La aplicación se está cerrando: liberar recursos."""
