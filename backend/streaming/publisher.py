"""Publicación de frames a MediaMTX por RTSP usando ffmpeg como subproceso.

El productor nunca bloquea: `enviar()` convierte el frame a I420 y lo encola. Un
hilo escritor es el único que toca `stdin`, y un hilo supervisor es la única
autoridad que crea y mata el proceso. Esa separación es lo que evita que un
ffmpeg colgado congele la captura.

No existe reconexión nativa de salida RTSP en ffmpeg: ante cualquier corte hay
que matar el proceso y relanzarlo. Eso es exactamente lo que hace el supervisor.
"""

from __future__ import annotations

import logging
import queue
import subprocess
import threading
import time
from collections import deque
from typing import TYPE_CHECKING

import cv2

if TYPE_CHECKING:
    import numpy as np

    from backend.config import PublisherConfig, ReconnectConfig

logger = logging.getLogger(__name__)

# Objeto centinela para despertar al hilo escritor durante el apagado.
_CENTINELA = object()
_INTERVALO_VIGILANCIA = 0.1
_GRACIA_TERMINACION = 1.0
_TIMEOUT_JOIN_ESCRITOR = 0.5
_LINEAS_ERROR = 20
# -preset y -tune sólo son válidos en los encoders de x264/x265.
_ENCODERS_CON_PRESET = ("libx264", "libx265")


def construir_comando(
    publicador: PublisherConfig, ancho: int, alto: int, fps: int
) -> list[str]:
    """Arma la línea de comandos de ffmpeg a partir de la configuración.

    La entrada siempre es `rawvideo`/`yuv420p` porque `enviar()` entrega I420;
    `publisher.pixel_format` es el formato de **salida** del encoder.
    """
    gop = max(1, publicador.gop_seconds * fps)
    comando = [
        publicador.ffmpeg_executable,
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "rawvideo",
        "-pixel_format",
        "yuv420p",
        "-video_size",
        f"{ancho}x{alto}",
        "-framerate",
        str(fps),
        "-i",
        "pipe:0",
        "-an",
        "-c:v",
        publicador.encoder,
    ]
    if publicador.encoder.startswith(_ENCODERS_CON_PRESET):
        comando += ["-preset", publicador.preset, "-tune", publicador.tune]
    comando += [
        "-pix_fmt",
        publicador.pixel_format,
        "-b:v",
        publicador.bitrate,
        "-g",
        str(gop),
        "-bf",
        str(publicador.b_frames),
        "-flush_packets",
        "1",
    ]
    if publicador.rtsp_transport:
        comando += ["-rtsp_transport", publicador.rtsp_transport]
    comando += ["-f", "rtsp", publicador.output_url]
    return comando


class PublicadorFFmpeg:
    """Publica frames BGR a un servidor RTSP, con reintento automático.

    Es dueño de tres hilos: el productor es el pipeline (que llama a `enviar()`),
    más un escritor y un supervisor propios.
    """

    def __init__(
        self,
        publicador: PublisherConfig,
        reconexion: ReconnectConfig,
        ancho: int,
        alto: int,
        fps: int,
    ) -> None:
        self._pub = publicador
        self._recon = reconexion
        self._ancho = ancho
        self._alto = alto
        self._fps = fps
        self._cola: queue.Queue[object] = queue.Queue(maxsize=max(1, publicador.queue_size))
        self._lock = threading.Lock()
        self._lock_stderr = threading.Lock()
        self._stderr: deque[str] = deque(maxlen=_LINEAS_ERROR)
        self._parar = threading.Event()
        self._proceso: subprocess.Popen[bytes] | None = None
        self._escritor: threading.Thread | None = None
        self._supervisor: threading.Thread | None = None
        self._lector_stderr: threading.Thread | None = None
        # Frames encolados que todavía no se confirmaron escritos: es la señal
        # con la que el supervisor distingue "cuelgue" de "stream al día".
        self._pendientes = 0
        self._ultima_escritura = 0.0
        self._arrancado_en = 0.0
        self._espera = float(reconexion.initial_seconds)
        self._reintentar_en = 0.0
        self._reconexiones = 0
        self._error = ""
        self._activo = False
        # Identifica al escritor vigente. Al retirar un proceso se incrementa, y
        # un escritor viejo que quedó bloqueado en write() sale en cuanto puede
        # en vez de competir por la cola con el nuevo. Dos escritores sobre el
        # mismo stdin entrelazarían bytes y corromperían el stream.
        self._generacion = 0

    # --- hechos observables ---

    @property
    def publicando(self) -> bool:
        """True si hay un ffmpeg vivo escribiendo."""
        with self._lock:
            return self._proceso is not None and self._proceso.poll() is None

    @property
    def reconexiones(self) -> int:
        """Cantidad de reinicios de ffmpeg (no cuenta el arranque inicial)."""
        with self._lock:
            return self._reconexiones

    def espera_restante(self) -> float:
        """Segundos hasta el próximo intento (0.0 = ya toca)."""
        with self._lock:
            return max(0.0, self._reintentar_en - time.monotonic())

    @property
    def ultimo_error(self) -> str:
        """Motivo del último fallo, con las últimas líneas que escupió ffmpeg."""
        with self._lock_stderr:
            detalle = " | ".join(self._stderr)
        with self._lock:
            error = self._error
        if error and detalle:
            return f"{error} :: {detalle}"
        return error or detalle

    # --- ciclo de vida ---

    def iniciar(self) -> None:
        """Lanza ffmpeg y sus hilos. Idempotente."""
        if self._activo:
            return
        self._parar.clear()
        self._activo = True
        with self._lock:
            self._reintentar_en = 0.0
        self._lanzar()
        self._supervisor = threading.Thread(
            target=self._bucle_supervisor, name="publisher-supervisor", daemon=True
        )
        self._supervisor.start()
        logger.info("Publisher: publicando en %s", self._pub.output_url)

    def enviar(self, frame_bgr: np.ndarray) -> bool:
        """Convierte el frame a I420 y lo encola. Nunca bloquea.

        Devuelve False si se descartó (no hay proceso vivo o la cola no dio
        lugar). Bajo backpressure se tira el frame más viejo: en vivo, lo viejo
        no sirve.
        """
        if not self.publicando:
            return False

        i420 = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2YUV_I420)
        dato = i420.tobytes()
        with self._lock:
            self._pendientes += 1

        while True:
            try:
                self._cola.put_nowait(dato)
                return True
            except queue.Full:
                pass
            # Hacer lugar descartando el más viejo, que ya no se escribirá.
            try:
                self._cola.get_nowait()
            except queue.Empty:
                continue
            with self._lock:
                self._pendientes = max(0, self._pendientes - 1)

    def detener(self) -> None:
        """Detiene la publicación y libera todos los recursos. Idempotente."""
        if not self._activo:
            return
        self._activo = False
        self._parar.set()

        # Primero el supervisor: si sigue vivo, podría relanzar mientras cerramos.
        if self._supervisor is not None:
            self._supervisor.join(timeout=5.0 + self._pub.write_timeout)
            self._supervisor = None
        self._cerrar_proceso()
        self._vaciar_cola()
        with self._lock:
            self._error = ""
        logger.info("Publisher: detenido")

    # --- hilo escritor ---

    def _bucle_escritor(self, generacion: int) -> None:
        """Único lugar que escribe en el stdin de ffmpeg."""
        while True:
            try:
                dato = self._cola.get(timeout=0.2)
            except queue.Empty:
                if self._parar.is_set() or not self._vigente(generacion):
                    return
                continue

            if dato is _CENTINELA or not self._vigente(generacion):
                return

            with self._lock:
                proceso = self._proceso
                stdin = (
                    proceso.stdin
                    if proceso is not None and proceso.poll() is None
                    else None
                )

            if stdin is None:
                # El proceso ya no está: se descarta el frame y se sigue drenando.
                self._confirmar_escritura()
                continue

            try:
                stdin.write(dato)  # type: ignore[arg-type]
                stdin.flush()
            except (BrokenPipeError, OSError, ValueError) as exc:
                # El supervisor detecta el proceso muerto y relanza; acá sólo se
                # registra, porque no hay nada que reintentar a este nivel.
                with self._lock:
                    self._error = f"Escritura a ffmpeg falló: {exc}"
                self._confirmar_escritura()
                logger.warning("Publisher: escritura falló: %s", exc)
                continue

            self._confirmar_escritura()

    def _vigente(self, generacion: int) -> bool:
        """True si `generacion` sigue siendo el escritor activo."""
        with self._lock:
            return self._generacion == generacion

    def _confirmar_escritura(self) -> None:
        """Marca un frame como ya resuelto (escrito o descartado)."""
        with self._lock:
            self._pendientes = max(0, self._pendientes - 1)
            self._ultima_escritura = time.monotonic()

    # --- hilo de stderr ---

    def _bucle_stderr(self, proceso: subprocess.Popen[bytes]) -> None:
        """Drena stderr para que el buffer no se llene y bloquee a ffmpeg."""
        if proceso.stderr is None:
            return
        try:
            for linea in proceso.stderr:
                texto = linea.decode("utf-8", errors="replace").rstrip()
                if texto:
                    with self._lock_stderr:
                        self._stderr.append(texto)
        except (OSError, ValueError):
            return

    # --- hilo supervisor ---

    def _bucle_supervisor(self) -> None:
        """Detecta cuelgues y muertes, y reinicia. Única autoridad de spawn/kill."""
        while not self._parar.is_set():
            time.sleep(_INTERVALO_VIGILANCIA)

            with self._lock:
                proceso = self._proceso
                vivo = proceso is not None and proceso.poll() is None
                # Sólo hay cuelgue si hay algo pendiente que no se escribió: un
                # stream al día no debe matarse solo.
                colgado = vivo and self._pendientes > 0 and (
                    time.monotonic() - self._ultima_escritura > self._pub.write_timeout
                )
                estable = vivo and (
                    time.monotonic() - self._arrancado_en >= self._pub.stable_seconds
                )
                if estable and self._espera != float(self._recon.initial_seconds):
                    self._espera = float(self._recon.initial_seconds)
                    logger.info("Publisher: enlace estable; backoff reiniciado")

            if self._parar.is_set():
                return

            if colgado:
                logger.warning("Publisher: ffmpeg dejó de escribir; reiniciando")
                self._reiniciar("ffmpeg dejó de escribir")
            elif not vivo:
                codigo = "sin proceso" if proceso is None else f"código {proceso.returncode}"
                self._reiniciar(f"ffmpeg terminó ({codigo})")

    def _reiniciar(self, motivo: str) -> None:
        """Mata lo que haya, espera el backoff y relanza."""
        with self._lock:
            self._reconexiones += 1
            self._error = motivo
            espera = self._espera
            self._reintentar_en = time.monotonic() + espera

        self._cerrar_proceso()
        logger.warning("Publisher: reintentando en %.1fs (%s)", espera, motivo)

        # Espera interrumpible: detener() no debe quedarse esperando el backoff.
        if self._parar.wait(timeout=espera):
            return

        with self._lock:
            self._espera = min(espera * 2, float(self._recon.max_seconds))
        self._lanzar()

    def _lanzar(self) -> None:
        """Crea el proceso y sus hilos auxiliares."""
        comando = construir_comando(self._pub, self._ancho, self._alto, self._fps)
        try:
            proceso = subprocess.Popen(
                comando,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                bufsize=0,
            )
        except (OSError, ValueError) as exc:
            with self._lock:
                self._error = (
                    f"No se pudo ejecutar {self._pub.ffmpeg_executable!r}: {exc}"
                )
                self._arrancado_en = time.monotonic()
            logger.error("Publisher: %s", self._error)
            return

        with self._lock:
            self._proceso = proceso
            self._generacion += 1
            generacion = self._generacion
            self._pendientes = 0
            self._ultima_escritura = time.monotonic()
            self._arrancado_en = time.monotonic()
        self._vaciar_cola()

        self._escritor = threading.Thread(
            target=self._bucle_escritor,
            args=(generacion,),
            name="publisher-escritor",
            daemon=True,
        )
        self._escritor.start()
        self._lector_stderr = threading.Thread(
            target=self._bucle_stderr, args=(proceso,), name="publisher-stderr", daemon=True
        )
        self._lector_stderr.start()

    def _cerrar_proceso(self) -> None:
        """Retira el proceso actual y sus hilos. Idempotente.

        El orden importa: se invalida la generación para que el escritor viejo no
        compita por la cola, se mata el proceso (eso desbloquea un `write()`
        trabado con EPIPE) y recién después se joinea el escritor y se cierran
        los pipes. Cerrar un pipe mientras el escritor lo usa es la carrera
        clásica de este diseño.
        """
        with self._lock:
            proceso = self._proceso
            self._proceso = None
            self._generacion += 1

        if self._escritor is not None:
            self._despertar_escritor()

        if proceso is not None and proceso.poll() is None:
            proceso.terminate()
            try:
                proceso.wait(timeout=_GRACIA_TERMINACION)
            except subprocess.TimeoutExpired:
                proceso.kill()
                proceso.wait(timeout=_GRACIA_TERMINACION)

        if self._escritor is not None:
            self._escritor.join(timeout=_TIMEOUT_JOIN_ESCRITOR)
            self._escritor = None

        if proceso is not None and proceso.stdin is not None:
            try:
                proceso.stdin.close()
            except OSError:
                pass

        if self._lector_stderr is not None:
            self._lector_stderr.join(timeout=_GRACIA_TERMINACION)
            self._lector_stderr = None

    def _despertar_escritor(self) -> None:
        """Mete el centinela para que el escritor salga aunque la cola esté vacía."""
        try:
            self._cola.put_nowait(_CENTINELA)
        except queue.Full:
            try:
                self._cola.get_nowait()
            except queue.Empty:
                pass
            try:
                self._cola.put_nowait(_CENTINELA)
            except queue.Full:
                pass

    def _vaciar_cola(self) -> None:
        """Vacía la cola: tras un reinicio, los frames viejos ya no sirven."""
        while True:
            try:
                self._cola.get_nowait()
            except queue.Empty:
                return
