"""Adaptador entre el servicio de registro y el puerto de la interfaz.

Es el único módulo del frontend que importa `backend.device`, igual que
`adaptador.py` lo es para `backend.streaming`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from backend.device import TipoDispositivo
from frontend.nucleo.controlador_dispositivo import (
    EstadoRegistroDispositivoUI,
    EstadoRegistroUI,
    TipoDispositivoUI,
    tipo_a_ui,
)

if TYPE_CHECKING:
    from backend.device import DeviceService


class AdaptadorDispositivo:
    """Implementa `ControladorDispositivo` sobre un `DeviceService`."""

    def __init__(self, servicio: DeviceService) -> None:
        self._servicio = servicio

    def estado(self) -> EstadoRegistroDispositivoUI:
        """Traduce la instantánea del backend a tipos de la interfaz.

        El enum se mapea por valor: si cambia un valor en el backend, esto
        falla ruidosamente en vez de desincronizarse en silencio.
        """
        estado = self._servicio.estado()
        return EstadoRegistroDispositivoUI(
            estado=EstadoRegistroUI(estado.estado.value),
            hostname=estado.hostname,
            request_id=estado.request_id or "",
            device_id=estado.device_id or "",
            mensaje=estado.mensaje or "",
            tipo=tipo_a_ui(getattr(estado, "tipo", None)),
        )

    def solicitar_registro(self, tipo: TipoDispositivoUI) -> None:
        """Pide (o reintenta) el alta para `tipo`. No bloquea.

        El adaptador es el único traductor entre UI y backend: convierte el
        enum local al enum del servicio por valor.
        """
        self._servicio.solicitar_registro(TipoDispositivo(tipo.value))

    def olvidar(self) -> None:
        """Olvida las credenciales persistidas."""
        self._servicio.olvidar()
