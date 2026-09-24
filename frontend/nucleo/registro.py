"""Registro explícito y ordenado de las secciones navegables."""

from __future__ import annotations

from frontend.nucleo.contrato import GrupoSeccion, SeccionBase
from frontend.nucleo.controlador_dispositivo import TipoDispositivoUI
from frontend.secciones.configuracion import Configuracion
from frontend.secciones.en_vivo import EnVivo
from frontend.secciones.horno_cinta import HornoYCinta
from frontend.secciones.inicio import Inicio
from frontend.secciones.lotes import Lotes
from frontend.secciones.metricas import Metricas

# Las comunes primero y las específicas de un tipo al final: el rail respeta
# este orden y sólo cambia la visibilidad según el tipo de dispositivo.
SECCIONES: tuple[type[SeccionBase], ...] = (
    Inicio,
    EnVivo,
    Metricas,
    Lotes,
    Configuracion,
    HornoYCinta,
)


def secciones_visibles(
    tipo: TipoDispositivoUI | None,
) -> tuple[type[SeccionBase], ...]:
    """Secciones del rail que corresponden al tipo de dispositivo actual.

    Las comunes están siempre; las de ENTRADA y SALIDA sólo cuando el
    dispositivo registrado es de ese tipo. Se preserva el orden de
    `SECCIONES` para que los índices del rail sean estables.
    """
    visibles: list[type[SeccionBase]] = []
    for clase in SECCIONES:
        if clase.grupo is GrupoSeccion.COMUN:
            visibles.append(clase)
        elif clase.grupo is GrupoSeccion.ENTRADA and tipo is TipoDispositivoUI.ENTRADA_HORNO:
            visibles.append(clase)
        elif clase.grupo is GrupoSeccion.SALIDA and tipo is TipoDispositivoUI.SALIDA_HORNO:
            visibles.append(clase)
    return tuple(visibles)
