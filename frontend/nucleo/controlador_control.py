"""Puerto (Protocol + dataclasses UI) del subsistema de control (SCA-400).

La sección "Horno y Cinta" del frontend consume un `ControladorControl`
para pintar temperaturas, setpoints, modo y operar (cambiar modo, aprobar
la propuesta pendiente). La supervisión siempre espera la aprobación del
operario. Las dataclasses son propias de la UI:
no filtran tipos de `backend.control` para que la sección no importe el
backend. El adaptador (`AdaptadorControl`) es el único módulo del frontend
que conoce la forma del dict de `ControlService.estado()`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol, runtime_checkable


class ModoControlUI(str, Enum):
    """Espejo UI de `backend.control.tipos.ModoControl`.

    El valor (`"MANUAL"`/`"SUPERVISION"`/`"AUTOMATICO"`) matchea el enum
    del backend para que el adaptador pueda mapear uno a otro sin perder
    información.
    """

    MANUAL = "MANUAL"
    SUPERVISION = "SUPERVISION"
    AUTOMATICO = "AUTOMATICO"


# Etiquetas en español para los botones grandes del selector de modo.
ETIQUETAS_MODO: dict[ModoControlUI, str] = {
    ModoControlUI.MANUAL: "Manual",
    ModoControlUI.SUPERVISION: "Supervisión",
    ModoControlUI.AUTOMATICO: "Automático",
}


@dataclass(frozen=True, slots=True)
class LecturaHornoUI:
    """Lectura instantánea de los sensores del horno, para la UI.

    Todos los campos son `float | None`: los sensores reales pueden no
    existir (humedad, ambiente) o no llegar en una lectura puntual.
    La UI muestra "—" cuando el valor es None.
    """

    temp_horno_1: float | None
    temp_comb_1: float | None
    humedad_horno: float | None
    temp_ambiente: float | None
    humedad_ambiente: float | None
    velocidad_cinta: float | None
    temp_objetivo: float | None
    vel_objetivo: float | None
    confianza_deteccion: float | None


@dataclass(frozen=True, slots=True)
class SetpointUI:
    """Setpoint propuesto o aplicado, espejado para la UI."""

    temp_horno_1: float
    velocidad_cinta: float
    aplicado_por: str
    ventana: str | None
    decision_id: str | None


@dataclass(frozen=True, slots=True)
class EstadoControlUI:
    """Snapshot inmutable del subsistema de control para la UI."""

    habilitado: bool
    enganchado: bool
    modo: ModoControlUI
    ultimo_setpoint: SetpointUI | None
    ultima_lectura: LecturaHornoUI | None
    propuesta_pendiente: SetpointUI | None
    aplazar_siguiente: bool
    ultimo_error: str
    backend_ok: bool


@runtime_checkable
class ControladorControl(Protocol):
    """Puerto que consume la sección "Horno y Cinta".

    El adaptador implementa este Protocol traduciendo el `dict` de
    `ControlService.estado()` a `EstadoControlUI` y los comandos UI a
    llamadas del servicio.
    """

    def estado(self) -> EstadoControlUI: ...

    def cambiar_modo(self, modo: ModoControlUI) -> None: ...

    def aplazar_siguiente(self) -> None: ...

    def aplicar_pendiente(self) -> bool: ...