"""Proveedores de estado del sistema.

`ProveedorEstado` es el puerto que consume la interfaz. `ProveedorServicio` arma
el snapshot combinando la configuración con el estado runtime real del
streaming: los niveles describen lo que está pasando, no lo que está
configurado.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from frontend.nucleo.controlador import EstadoModeloUI
from frontend.nucleo.estado import EstadoItem, NivelEstado, SnapshotSistema

if TYPE_CHECKING:
    from backend.config import AppConfig

    from frontend.nucleo.controlador import ControladorStreaming, EstadoUI


@runtime_checkable
class ProveedorEstado(Protocol):
    """Puerto: entrega un snapshot del estado del sistema."""

    def snapshot(self) -> SnapshotSistema: ...


class ProveedorServicio:
    """Snapshot que combina la configuración con el runtime del streaming.

    Es el único módulo del frontend que importa `backend.config`.
    """

    def __init__(
        self,
        config: AppConfig,
        controlador: ControladorStreaming,
    ) -> None:
        self._config = config
        self._controlador = controlador

    def snapshot(self) -> SnapshotSistema:
        """Arma las filas de estado con la instantánea actual del streaming."""
        config = self._config
        estado = self._controlador.estado()
        captura = config.stream.capture

        items = (
            self._fila_captura(estado, captura.width, captura.height),
            self._fila_inferencia(estado),
            self._fila_publicacion(estado, config.stream.publisher.output_url),
            EstadoItem(
                clave="api",
                etiqueta="Backend",
                nivel=NivelEstado.ACTIVO,
                detalle=config.api.base_url,
            ),
        )

        evaluables = [
            item for item in items if item.nivel is not NivelEstado.SIN_DATO
        ]
        activos = sum(1 for item in evaluables if item.nivel is NivelEstado.ACTIVO)

        return SnapshotSistema(
            items=items,
            # El modelo activo es el del runtime: si el operador eligió otro en el
            # selector, mostrar el configurado sería mentir.
            modelo_activo=estado.modelo_etiqueta or estado.modelo_id or "sin modelo",
            resumen=f"{activos}/{len(evaluables)} activos",
        )

    def _fila_captura(self, estado: EstadoUI, ancho: int, alto: int) -> EstadoItem:
        """Captura: publicar sin frames es el caso que hay que saber mirar."""
        detalle = f"{estado.fuente} · {ancho}×{alto}"
        if estado.fps > 0:
            detalle += f" · {estado.fps:.0f} fps"

        if estado.capturando:
            nivel = NivelEstado.ACTIVO
        elif estado.publicando:
            nivel = NivelEstado.ATENCION
            detalle += " · sin frames"
        else:
            nivel = NivelEstado.INACTIVO

        return EstadoItem(
            clave="captura", etiqueta="Captura", nivel=nivel, detalle=detalle
        )

    def _fila_inferencia(self, estado: EstadoUI) -> EstadoItem:
        """Inferencia: mientras carga no es ni logro ni problema, así que SIN_DATO."""
        etiqueta_modelo = estado.modelo_etiqueta or "sin modelo"

        if estado.modelo_estado is EstadoModeloUI.LISTO:
            nivel = NivelEstado.ACTIVO
            partes = [etiqueta_modelo]
            if estado.motor:
                partes.append(estado.motor)
            partes.append(estado.ultima_deteccion or "sin detecciones")
            if estado.retraso_ms:
                partes.append(f"{estado.retraso_ms} ms")
            detalle = " · ".join(partes)
        elif estado.modelo_estado is EstadoModeloUI.ERROR:
            nivel = NivelEstado.ATENCION
            # El mensaje completo vive en la sección En vivo; acá alcanza el aviso.
            detalle = f"{etiqueta_modelo} · no se pudo cargar"
        else:
            nivel = NivelEstado.SIN_DATO
            detalle = f"{etiqueta_modelo} · cargando"

        return EstadoItem(
            clave="inferencia", etiqueta="Inferencia", nivel=nivel, detalle=detalle
        )

    def _fila_publicacion(self, estado: EstadoUI, url: str) -> EstadoItem:
        """Publicación: reintentando es atención, detenida es inactivo."""
        detalle = url or "sin destino"

        if estado.publicando:
            nivel = NivelEstado.ACTIVO
        elif estado.reintento_en > 0:
            nivel = NivelEstado.ATENCION
            detalle += f" · reintentando en {estado.reintento_en:.0f}s"
        else:
            nivel = NivelEstado.INACTIVO

        return EstadoItem(
            clave="publicacion", etiqueta="Publicación", nivel=nivel, detalle=detalle
        )
