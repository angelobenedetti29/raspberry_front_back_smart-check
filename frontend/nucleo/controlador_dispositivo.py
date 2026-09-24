"""Puerto de control del registro del dispositivo y tipos que consume la UI.

Igual que `controlador.py`, las secciones no importan `backend.*`: este módulo
traduce el estado del registro a tipos propios, listos para mostrar.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol, runtime_checkable


class EstadoRegistroUI(str, Enum):
    """Estado del registro de la Raspberry frente al backend.

    Los valores coinciden con `backend.device.EstadoRegistro` a propósito:
    el adaptador traduce por valor, igual que `AdaptadorStreaming` con los
    enums del streaming. El texto que ve el usuario sale de `etiqueta`.
    """

    NO_REGISTRADO = "NO_REGISTRADO"
    PENDIENTE = "PENDIENTE"
    APROBADO = "APROBADO"
    ERROR = "ERROR"
    REVOCADO = "REVOCADO"

    @property
    def etiqueta(self) -> str:
        """Rótulo para la interfaz."""
        return {
            EstadoRegistroUI.NO_REGISTRADO: "Sin registrar",
            EstadoRegistroUI.PENDIENTE: "Esperando aprobación",
            EstadoRegistroUI.APROBADO: "Registrado",
            EstadoRegistroUI.ERROR: "Error",
            EstadoRegistroUI.REVOCADO: "Revocado",
        }[self]


class TipoDispositivoUI(str, Enum):
    """Tipo de dispositivo que el operador elige al dar de alta la Raspberry.

    Los valores coinciden a propósito con los del backend: el adaptador
    traduce por valor, igual que con `EstadoRegistroUI`.
    """

    ENTRADA_HORNO = "ENTRADA_HORNO"
    SALIDA_HORNO = "SALIDA_HORNO"

    @property
    def etiqueta(self) -> str:
        """Rótulo para la interfaz."""
        return {
            TipoDispositivoUI.ENTRADA_HORNO: "Entrada de horno",
            TipoDispositivoUI.SALIDA_HORNO: "Salida de horno",
        }[self]


@dataclass(frozen=True, slots=True)
class EstadoRegistroDispositivoUI:
    """Estado del registro de dispositivo, sin tipos del backend."""

    estado: EstadoRegistroUI
    hostname: str
    request_id: str
    """Vacío si todavía no se pidió el registro."""

    device_id: str
    """Vacío hasta que el supervisor aprueba."""

    mensaje: str
    """Detalle del último fallo. Vacío cuando no hay nada que reportar."""

    tipo: TipoDispositivoUI | None = None
    """Tipo elegido al registrar; `None` mientras el backend no lo exponga."""


@runtime_checkable
class ControladorDispositivo(Protocol):
    """Lo que la tarjeta de registro en Inicio le pide al servicio."""

    def estado(self) -> EstadoRegistroDispositivoUI: ...

    def solicitar_registro(self, tipo: TipoDispositivoUI) -> None:
        """Pide (o reintenta) el alta para el tipo indicado. No bloquea."""
        ...

    def olvidar(self) -> None:
        """Olvida las credenciales persistidas y vuelve a sin registrar."""
        ...
