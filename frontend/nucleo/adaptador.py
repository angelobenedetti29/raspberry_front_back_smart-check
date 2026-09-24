"""Adaptador entre el servicio de streaming y el puerto de la interfaz.

Es el único módulo del frontend que importa `backend.streaming`, igual que
`proveedor.py` es el único que importa `backend.config`. Todo lo que cruza hacia
las secciones pasa por los tipos de `controlador.py`.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
from PySide6.QtGui import QImage

from backend.inference.catalogo import catalogo_resuelto
from backend.streaming.estado import ModoPublicacion
from frontend.nucleo.controlador import (
    EstadoModeloUI,
    EstadoUI,
    ModoUI,
    OpcionFuente,
    OpcionModelo,
)

if TYPE_CHECKING:
    from backend.config import AppConfig
    from backend.streaming import StreamingService


class AdaptadorStreaming:
    """Implementa `ControladorStreaming` sobre un `StreamingService`."""

    def __init__(self, config: AppConfig, servicio: StreamingService) -> None:
        self._config = config
        self._servicio = servicio

    # --- lectura ---

    def estado(self) -> EstadoUI:
        """Traduce la instantánea del backend a tipos de la interfaz.

        Los enums se mapean por valor: si alguien cambia un valor en el backend,
        esto falla ruidosamente en vez de desincronizarse en silencio.
        """
        estado = self._servicio.estado()
        return EstadoUI(
            publicando=estado.publicando,
            capturando=estado.capturando,
            modo=ModoUI(estado.modo.value),
            fuente=estado.fuente,
            fps=estado.fps,
            frames=estado.frames_publicados,
            detecciones=estado.detecciones,
            ultima_deteccion=(
                estado.ultima_deteccion.etiqueta if estado.ultima_deteccion else ""
            ),
            retraso_ms=estado.retraso_inferencia_ms,
            modelo_id=estado.modelo_id,
            modelo_etiqueta=estado.modelo_label,
            modelo_archivo=estado.modelo_path.name if estado.modelo_path else "",
            motor=estado.motor.etiqueta if estado.motor else "",
            modelo_estado=EstadoModeloUI(estado.modelo_estado.value),
            reintento_en=estado.reintento_en,
            error=estado.error,
        )

    def fuentes(self) -> tuple[OpcionFuente, ...]:
        """Fuente configurada más los archivos de `paths.videos_dir`."""
        opciones: list[OpcionFuente] = []
        vistos: set[str] = set()

        def agregar(valor: str) -> None:
            if not valor or valor in vistos:
                return
            vistos.add(valor)
            etiqueta = f"Cámara {valor}" if valor.isdigit() else valor
            opciones.append(OpcionFuente(valor=valor, etiqueta=etiqueta))

        agregar(self._config.stream.capture.source)
        for archivo in self._archivos_de_video():
            agregar(archivo.name)
        return tuple(opciones)

    def modelos(self) -> tuple[OpcionModelo, ...]:
        """Catálogo completo, con el motor resuelto y el motivo de los no usables."""
        return tuple(
            OpcionModelo(
                model_id=resuelto.model_id,
                etiqueta=resuelto.label,
                motor=resuelto.motor.etiqueta if resuelto.motor else "",
                disponible=resuelto.disponible,
                motivo=resuelto.motivo,
            )
            for resuelto in catalogo_resuelto(self._config.models.catalog)
        )

    def ultimo_frame(self) -> QImage | None:
        """Último frame listo para mostrar, ya convertido a `QImage`."""
        frame = self._servicio.ultimo_frame()
        if frame is None:
            return None
        if not frame.flags["C_CONTIGUOUS"]:
            frame = np.ascontiguousarray(frame)

        alto, ancho = frame.shape[:2]
        # `QImage` no copia el buffer: sin `.copy()` la imagen apuntaría a la
        # memoria de numpy, que el pipeline reemplaza en el frame siguiente. El
        # resultado sería basura en pantalla o directamente un segfault.
        return QImage(
            frame.data,
            ancho,
            alto,
            int(frame.strides[0]),
            QImage.Format.Format_BGR888,
        ).copy()

    # --- comandos ---

    def fijar_publicacion(self, activo: bool) -> None:
        """Publica o deja de publicar. No bloquea."""
        if activo:
            self._servicio.publicar()
        else:
            self._servicio.detener_publicacion()

    def fijar_modo(self, modo: ModoUI) -> None:
        """Cambia entre video plano y video con detecciones dibujadas."""
        self._servicio.fijar_modo(ModoPublicacion(modo.value))

    def fijar_fuente(self, valor: str) -> None:
        """Cambia la fuente: índice de cámara o archivo de `videos_dir`."""
        self._servicio.fijar_fuente(valor)

    def fijar_modelo(self, model_id: str) -> None:
        """Cambia el modelo de inferencia."""
        self._servicio.fijar_modelo(model_id)

    def solicitar_preview(self, activo: bool) -> None:
        """Pide (o deja de pedir) el frame para mostrar en la app."""
        self._servicio.solicitar_preview(activo)

    # --- internos ---

    def _archivos_de_video(self) -> tuple[Path, ...]:
        """Archivos de `videos_dir`, salteando ocultos.

        El filtro por punto no es cosmético: la carpeta tiene un `.gitkeep` que,
        sin esto, aparecería en el selector como si fuera un video.
        """
        try:
            return tuple(
                sorted(
                    (
                        entrada
                        for entrada in self._config.paths.videos_dir.iterdir()
                        if entrada.is_file() and not entrada.name.startswith(".")
                    ),
                    key=lambda entrada: entrada.name,
                )
            )
        except OSError:
            # La carpeta puede no existir: la lista queda sólo con la cámara.
            return ()
