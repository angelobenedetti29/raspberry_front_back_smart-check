"""Servicio de streaming: captura -> inferencia -> publicación -> persistencia.

`StreamingService` cumple el contrato `Service` (start no bloqueante, stop
bloqueante) y expone los comandos que usa la interfaz. La concurrencia vive en
`pipeline.py`.
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

from backend.config import ModelEntry
from backend.streaming.estado import ModoPublicacion, RegistroEstado
from backend.streaming.pipeline import Pipeline
from backend.streaming.suscripcion import ColaEventos, RegistroSuscriptores

if TYPE_CHECKING:
    import numpy as np

    from backend.config import AppConfig
    from backend.streaming.estado import EstadoStreaming

logger = logging.getLogger(__name__)


def _entrada_por_defecto(config: AppConfig) -> ModelEntry:
    """Entrada del catálogo que corresponde a `models.default_model_id`."""
    for entrada in config.models.catalog:
        if entrada.model_id == config.models.default_model_id:
            return entrada
    # El loader ya valida esta referencia; esto es una red de seguridad.
    raise RuntimeError(
        f"'models.default_model_id' referencia un modelo inexistente: "
        f"{config.models.default_model_id}"
    )


class StreamingService:
    """Servicio continuo que captura, infiere y publica hacia MediaMTX.

    Arranca publicando: el stream se consume desde fuera de esta aplicación, así
    que no puede depender de que alguien esté mirando la sección En vivo. Los
    comandos de la interfaz sirven para detenerlo y reanudarlo.

    `start()` lanza los lazos y vuelve de inmediato. `stop()` señala el fin y
    espera a que terminen, sin propagar excepciones: un fallo al cerrar no debe
    cortar la cadena de apagado de `run.py`.
    """

    name = "streaming"

    def __init__(self, config: AppConfig) -> None:
        self._config = config
        entrada = _entrada_por_defecto(config)
        self._registro = RegistroEstado(
            fuente=config.stream.capture.source,
            modelo_id=entrada.model_id,
            modelo_label=entrada.label,
        )
        self._lock = threading.Lock()
        self._pipeline: Pipeline | None = None
        # Registro de suscriptores de eventos: vive en el servicio, así la
        # suscripción sobrevive a un stop/start del streaming.
        self._suscriptores = RegistroSuscriptores()

    # --- contrato Service ---

    def start(self) -> None:
        """Arranca el servicio. No bloquea. Idempotente.

        Crea un `Pipeline` nuevo en vez de reiniciar el anterior: así `start()`
        después de `stop()` funciona sin lógica de reset.
        """
        with self._lock:
            if self._pipeline is not None:
                return
            pipeline = Pipeline(self._config, self._registro, self._suscriptores)
            self._pipeline = pipeline
        pipeline.iniciar()
        logger.info("streaming: servicio iniciado")

    def stop(self) -> None:
        """Detiene los lazos y espera. Bloquea hasta el timeout de `Pipeline`."""
        with self._lock:
            pipeline = self._pipeline
            self._pipeline = None
        if pipeline is None:
            return
        try:
            pipeline.detener()
        except Exception:
            logger.exception("streaming: error al detener el servicio")
        logger.info("streaming: servicio detenido")

    # --- comandos de la interfaz ---

    def publicar(self) -> None:
        """Pide publicar."""
        self._exigir_pipeline().publicar()

    def detener_publicacion(self) -> None:
        """Pide dejar de publicar."""
        self._exigir_pipeline().detener_publicacion()

    def fijar_modo(self, modo: ModoPublicacion) -> None:
        """Cambia entre video plano y video con las cajas dibujadas."""
        self._exigir_pipeline().fijar_modo(modo)

    def fijar_fuente(self, source: str) -> None:
        """Cambia la fuente: índice de cámara o archivo de `paths.videos_dir`."""
        self._exigir_pipeline().fijar_fuente(source)

    def fijar_modelo(self, model_id: str) -> None:
        """Cambia el modelo de inferencia por su id del catálogo."""
        self._exigir_pipeline().fijar_modelo(model_id)

    def solicitar_preview(self, activo: bool) -> None:
        """Avisa si la sección En vivo necesita el frame para mostrar.

        Tolerar que el servicio no esté arrancado es a propósito: la sección
        puede salir después de que el servicio se detuvo.
        """
        with self._lock:
            pipeline = self._pipeline
        if pipeline is not None:
            pipeline.solicitar_preview(activo)

    # --- lectura ---

    def estado(self) -> EstadoStreaming:
        """Última instantánea del runtime. Responde incluso antes de arrancar."""
        return self._registro.instantanea()

    def ultimo_frame(self) -> np.ndarray | None:
        """Último frame listo para mostrar, o None si no hay preview activo."""
        with self._lock:
            pipeline = self._pipeline
        return None if pipeline is None else pipeline.ultimo_frame()

    # --- suscripción de eventos ---

    def suscribir_eventos(self, maxsize: int = 512) -> ColaEventos:
        """Crea y registra un buzón de eventos de pista.

        La suscripción vive en el servicio, no en el pipeline: sobrevive a un
        stop/start del streaming.
        """
        cola = ColaEventos(maxsize)
        self._suscriptores.agregar(cola)
        return cola

    def desuscribir_eventos(self, cola: ColaEventos) -> None:
        """Quita un buzón registrado."""
        self._suscriptores.quitar(cola)

    def segundos_sin_detecciones(self) -> float | None:
        """Segundos desde la última detección, o None si el streaming no corre."""
        with self._lock:
            pipeline = self._pipeline
        return None if pipeline is None else pipeline.segundos_sin_detecciones()

    def _exigir_pipeline(self) -> Pipeline:
        with self._lock:
            pipeline = self._pipeline
        if pipeline is None:
            raise RuntimeError("El servicio de streaming no está arrancado")
        return pipeline
