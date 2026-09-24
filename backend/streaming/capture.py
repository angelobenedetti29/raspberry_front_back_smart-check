"""Captura de frames desde una webcam o un archivo de video.

Entrega siempre frames BGR normalizados a ``capture.width x capture.height``: el
pipeline puede cambiar de fuente sin que el publicador se entere, porque el pipe
a ffmpeg nunca cambia de tamaño.

No tiene hilo propio. El hilo de producción del pipeline llama a `leer()`; por
eso `cerrar()` sólo se debe invocar después de que ese hilo terminó.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import cv2

if TYPE_CHECKING:
    import numpy as np

    from backend.config import CaptureConfig, ReconnectConfig

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Fuente:
    """Fuente de captura resuelta desde `stream.capture.source`."""

    camara: bool
    indice: int | None
    archivo: Path | None
    descripcion: str


def resolver_fuente(source: str, videos_dir: Path) -> Fuente:
    """Resuelve `stream.capture.source` a cámara o archivo.

    Un valor numérico es el índice de una cámara; cualquier otra cosa es el
    nombre de un archivo dentro de `videos_dir`. No se admiten rutas fuera de esa
    carpeta: un `source` absoluto o con `..` es un error, no una ruta libre.

    Lanza `ValueError` si el valor no es un índice ni un nombre contenido en
    `videos_dir`.
    """
    valor = source.strip()
    if valor.isdigit():
        return Fuente(
            camara=True, indice=int(valor), archivo=None, descripcion=f"Cámara {valor}"
        )

    carpeta = videos_dir.resolve()
    archivo = (carpeta / valor).resolve()
    try:
        archivo.relative_to(carpeta)
    except ValueError as exc:
        raise ValueError(
            "'stream.capture.source' debe ser un índice de cámara o el nombre de "
            f"un archivo dentro de {carpeta}: {source!r}"
        ) from exc

    return Fuente(camara=False, indice=None, archivo=archivo, descripcion=archivo.name)


class CapturaOpenCV:
    """Captura BGR con normalización de tamaño y reconexión con backoff.

    Expone hechos (reconexiones, tiempo sin frames, espera pendiente) y no toma
    decisiones de estado: quién decide qué mostrar es el pipeline.
    """

    def __init__(
        self,
        captura: CaptureConfig,
        reconexion: ReconnectConfig,
        videos_dir: Path,
    ) -> None:
        self._captura = captura
        self._reconexion = reconexion
        self._fuente = resolver_fuente(captura.source, videos_dir)
        self._cap: cv2.VideoCapture | None = None
        self._reconexiones = 0
        self._frames_ok = 0
        self._ultimo_frame_ok = time.monotonic()
        self._espera = float(reconexion.initial_seconds)
        self._reintentar_en = 0.0
        self._objetivo = time.monotonic()

    # --- hechos observables ---

    @property
    def fuente(self) -> Fuente:
        """Fuente resuelta que está capturando (o intentando capturar)."""
        return self._fuente

    @property
    def reconexiones(self) -> int:
        """Veces que se perdió una fuente abierta y hubo que reabrirla."""
        return self._reconexiones

    @property
    def estable(self) -> bool:
        """True cuando se acumularon `capture.stable_frames` frames válidos seguidos."""
        return self._frames_ok >= self._captura.stable_frames

    def espera_restante(self) -> float:
        """Segundos hasta el próximo intento de apertura (0.0 = ya toca)."""
        return max(0.0, self._reintentar_en - time.monotonic())

    def tiempo_sin_frames(self) -> float:
        """Segundos desde el último frame válido."""
        return time.monotonic() - self._ultimo_frame_ok

    # --- ciclo de vida ---

    def abrir(self) -> bool:
        """Abre la fuente. Devuelve False si no se pudo (queda programada la reintento)."""
        self._soltar()
        if self._fuente.camara:
            assert self._fuente.indice is not None
            origen: int | str = self._fuente.indice
        else:
            assert self._fuente.archivo is not None
            origen = str(self._fuente.archivo)
        cap = cv2.VideoCapture(origen)
        if not cap.isOpened():
            cap.release()
            logger.warning("Captura: no se pudo abrir %s", self._fuente.descripcion)
            self._programar_reintento()
            return False

        if self._fuente.camara:
            # Son pedidos al driver: si no los respeta, `_normalizar` igual corrige.
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, self._captura.width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self._captura.height)
            cap.set(cv2.CAP_PROP_FPS, self._captura.fps)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, self._captura.buffer_size)

        self._cap = cap
        self._frames_ok = 0
        self._ultimo_frame_ok = time.monotonic()
        # Arranca el pacing desde ahora: sin esto, un archivo leído tras una
        # pausa larga sale a ráfagas para "recuperar" el tiempo perdido.
        self._objetivo = time.monotonic()
        return True

    def leer(self) -> np.ndarray | None:
        """Devuelve el próximo frame normalizado, o None si todavía no hay.

        Un None no es un error por sí solo: puede ser backoff esperando o un EOF
        rebobinando. El pipeline decide qué hacer con eso usando
        `espera_restante()` y `tiempo_sin_frames()`.
        """
        ahora = time.monotonic()
        if ahora < self._reintentar_en:
            return None
        if self._cap is None and not self.abrir():
            return None

        assert self._cap is not None
        ok, frame = self._cap.read()
        if not ok:
            frame = self._recuperar_tras_fallo()
            if frame is None:
                return None

        self._frames_ok += 1
        self._ultimo_frame_ok = time.monotonic()
        if self._frames_ok == self._captura.stable_frames:
            # Fuente recuperada: el próximo corte vuelve a esperar poco.
            self._espera = float(self._reconexion.initial_seconds)

        self._esperar_turno()
        return self._normalizar(frame)

    def cerrar(self) -> None:
        """Libera la cámara o el archivo. Idempotente."""
        self._soltar()

    # --- internos ---

    def _soltar(self) -> None:
        """Suelta el `VideoCapture` sin tocar contadores."""
        if self._cap is not None:
            self._cap.release()
            self._cap = None

    def _recuperar_tras_fallo(self) -> np.ndarray | None:
        """Intenta recuperar la lectura tras un fallo.

        Un EOF de archivo con `loop_video` se rebobina y se reintenta en el acto:
        devolver None obligaría a esperar la llamada siguiente y costaría un frame
        perdido en cada vuelta del video. Cualquier otro fallo es fuente perdida.
        """
        if (
            self._fuente.archivo is not None
            and self._captura.loop_video
            and self._cap is not None
        ):
            self._cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ok, frame = self._cap.read()
            if ok:
                return frame

        self._reconexiones += 1
        self._frames_ok = 0
        self._soltar()
        self._programar_reintento()
        return None

    def _programar_reintento(self) -> None:
        """Agenda la próxima apertura con backoff exponencial acotado."""
        self._reintentar_en = time.monotonic() + self._espera
        self._espera = min(self._espera * 2, float(self._reconexion.max_seconds))

    def _esperar_turno(self) -> None:
        """Pacea un archivo a `capture.fps`.

        La webcam se auto-regula; un archivo se leería a máxima velocidad y
        rompería el tiempo real, porque el pipe a ffmpeg no tiene `-re` y no se
        regula solo.
        """
        if self._fuente.camara:
            return

        self._objetivo += 1.0 / self._captura.fps
        espera = self._objetivo - time.monotonic()
        if espera > 0:
            time.sleep(espera)
        else:
            # Vamos atrasados: resincronizar en vez de leer a ráfagas.
            self._objetivo = time.monotonic()

    def _normalizar(self, frame: np.ndarray) -> np.ndarray:
        """Escala el frame a la resolución configurada."""
        alto, ancho = frame.shape[:2]
        destino = (self._captura.width, self._captura.height)
        if (ancho, alto) == destino:
            return frame

        interpolacion = (
            cv2.INTER_AREA if ancho > destino[0] or alto > destino[1] else cv2.INTER_LINEAR
        )
        return cv2.resize(frame, destino, interpolation=interpolacion)
