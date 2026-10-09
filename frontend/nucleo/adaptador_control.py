"""Adaptador del subsistema de control para la UI (SCA-400).

El **único** módulo del frontend que importa `backend.control`. Traduce el
`dict` que devuelve `ControlService.estado()` a `EstadoControlUI` y los
comandos de la UI (cambiar modo, aplazar, aplicar pendiente) a llamadas
del servicio de backend. Las secciones sólo conocen el Protocol
`ControladorControl` y los dataclasses de `controlador_control.py`.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from backend.control.tipos import (
    LecturaHorno,
    ModoControl,
    SetpointPropuesto,
)

from frontend.nucleo.controlador_control import (
    ControladorControl,
    EstadoControlUI,
    LecturaHornoUI,
    ModoControlUI,
    SetpointUI,
)

if TYPE_CHECKING:
    from backend.control import ControlService

logger = logging.getLogger(__name__)


class AdaptadorControl:
    """Implementa `ControladorControl` sobre un `ControlService` del backend."""

    def __init__(self, servicio: "ControlService") -> None:
        self._servicio = servicio

    # --- lectura ---------------------------------------------------------

    def estado(self) -> EstadoControlUI:
        """Snapshot inmutable del control, traducido a dataclasses UI."""
        snap = self._servicio.estado()
        return EstadoControlUI(
            habilitado=snap["habilitado"],
            enganchado=snap["enganchado"],
            modo=_a_modo(snap["modo"]),
            horno2_habilitado=snap["horno2_habilitado"],
            ultimo_setpoint=_a_setpoint(snap["ultimo_setpoint"]),
            ultima_lectura=_a_lectura(snap["ultima_lectura"]),
            propuesta_pendiente=_a_setpoint(snap["propuesta_pendiente"]),
            aplazar_siguiente=snap["aplazar_siguiente"],
            ultimo_error=snap["ultimo_error"],
            backend_ok=snap["backend_ok"],
            publicaciones_ok=snap["publicaciones_ok"],
            publicaciones_error=snap["publicaciones_error"],
            aplicaciones_ok=snap["aplicaciones_ok"],
            aplicaciones_error=snap["aplicaciones_error"],
        )

    # --- comandos --------------------------------------------------------

    def cambiar_modo(self, modo: ModoControlUI) -> None:
        """Cambio de modo desde la UI: push al backend + local."""
        self._servicio.cambiar_modo(ModoControl(modo.value))

    def aplazar_siguiente(self) -> None:
        """Operario pidió revisar la próxima propuesta antes de aplicarla."""
        self._servicio.aplazar_siguiente()

    def aplicar_pendiente(self) -> bool:
        """Aplica la propuesta pendiente guardada. True si la confirmación OK."""
        return self._servicio.aplicar_pendiente()


# --- helpers de traducción --------------------------------------------------


def _a_modo(modo_str: str) -> ModoControlUI:
    """Mapea el string del backend al enum UI (valores compartidos)."""
    try:
        return ModoControlUI(modo_str)
    except ValueError:
        # Si el backend agrega un modo nuevo, caemos a MANUAL en la UI en
        # lugar de explotar. El siguiente poll lo recogerá de nuevo.
        logger.warning("adaptador_control: modo desconocido %r", modo_str)
        return ModoControlUI.MANUAL


def _a_lectura(payload: LecturaHorno | None) -> LecturaHornoUI | None:
    """Copia campo por campo. None → None."""
    if payload is None:
        return None
    return LecturaHornoUI(
        temp_horno_1=payload.temp_horno_1,
        temp_horno_2=payload.temp_horno_2,
        temp_comb_1=payload.temp_comb_1,
        temp_comb_2=payload.temp_comb_2,
        humedad_horno=payload.humedad_horno,
        temp_ambiente=payload.temp_ambiente,
        humedad_ambiente=payload.humedad_ambiente,
        velocidad_cinta=payload.velocidad_cinta,
        temp_objetivo=payload.temp_objetivo,
        vel_objetivo=payload.vel_objetivo,
        confianza_deteccion=payload.confianza_deteccion,
    )


def _a_setpoint(payload: SetpointPropuesto | None) -> SetpointUI | None:
    """Copia campo por campo. None → None."""
    if payload is None:
        return None
    return SetpointUI(
        temp_horno_1=payload.temp_horno_1,
        temp_horno_2=payload.temp_horno_2,
        velocidad_cinta=payload.velocidad_cinta,
        aplicado_por=payload.aplicado_por,
        ventana=payload.ventana,
        decision_id=payload.decision_id,
    )