"""Los dos lazos del servicio de streaming y el estado compartido entre ellos.

El lazo de producción nunca infiere y el de inferencia nunca publica. Esa
separación es lo que permite que cargar un modelo (segundos, con un HEF) o
inferir en CPU (~25 ms por frame, medido) no corte el stream.

Reparto de estado, que es la clave para leer este archivo:

- `_captura`, `_publicador`, `_fuente_actual`, `_publicando_aplicado`,
  `_numero_frame`, `_ventana_*`: sólo los toca el lazo de producción.
- `_detector`, `_almacen`: sólo los toca el lazo de inferencia.
- Slots, banderas de intención y `_errores`: compartidos, siempre bajo `_lock`.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Sequence
from dataclasses import replace
from typing import TYPE_CHECKING

from backend.config import ModelEntry
from backend.inference import Detector, ErrorCargaModelo, ResultadoDeteccion
from backend.inference.catalogo import construir_detector
from backend.streaming.anotador import dibujar
from backend.streaming.capture import CapturaOpenCV
from backend.streaming.estabilizador import EstabilizadorDetecciones, EventoPista
from backend.streaming.estado import EstadoModelo, ModoPublicacion, RegistroEstado
from backend.streaming.publisher import PublicadorFFmpeg
from backend.streaming.storage import AlmacenDetecciones
from backend.streaming.suscripcion import EventoObservado, RegistroSuscriptores

if TYPE_CHECKING:
    import numpy as np

    from backend.config import AppConfig

logger = logging.getLogger(__name__)

_ESPERA_OCIOSO = 0.1
_ESPERA_SIN_FRAME = 0.02
_INTERVALO_CONSOLIDACION = 1.0
_TIMEOUT_JOIN = 5.0


class Pipeline:
    """Motor del servicio: un lazo de producción y uno de inferencia."""

    def __init__(
        self,
        config: AppConfig,
        registro: RegistroEstado,
        suscriptores: RegistroSuscriptores,
    ) -> None:
        self._config = config
        self._registro = registro
        self._suscriptores = suscriptores
        self._lock = threading.Lock()
        self._parar = threading.Event()
        self._frame_nuevo = threading.Event()

        # --- slots compartidos ---
        # (timestamp, número de frame, frame crudo) para el lazo de inferencia.
        self._slot_inferencia: tuple[float, int, np.ndarray] | None = None
        # (detecciones, retraso_ms) que deja el lazo de inferencia.
        self._slot_detecciones: tuple[list[ResultadoDeteccion], int] | None = None
        # Último frame listo para mostrar (anotado si el modo es inferencia).
        self._slot_preview: np.ndarray | None = None
        self._errores: dict[str, str] = {}

        # --- intención de la interfaz (latest-wins) ---
        self._desea_publicar = True
        self._desea_preview = False
        # Arranca anotando: el operador ve el estado de las tostadas sin tener que
        # cambiar de modo. Mientras el modelo carga se ve el video plano.
        self._modo = ModoPublicacion.INFERENCIA
        self._fuente_pendiente: str | None = None
        self._modelo_pendiente: str | None = None

        # --- aplicado por el lazo de producción ---
        self._captura: CapturaOpenCV | None = None
        self._publicador: PublicadorFFmpeg | None = None
        self._fuente_actual: str = config.stream.capture.source
        self._publicando_aplicado = False
        self._numero_frame = 0
        self._ventana_frames = 0
        self._ventana_inicio = time.monotonic()

        # --- dueño el lazo de inferencia ---
        self._detector: Detector | None = None
        self._almacen: AlmacenDetecciones | None = None
        self._estabilizador = EstabilizadorDetecciones()
        self._modelo_actual: str | None = None
        # Para el cierre de lote: segundos desde la última detección.
        self._inicio_monotonic = time.monotonic()
        self._ultima_deteccion_monotonic: float | None = None

        self._hilo_produccion: threading.Thread | None = None
        self._hilo_inferencia: threading.Thread | None = None

    # --- ciclo de vida ---

    def iniciar(self) -> None:
        """Arranca los dos lazos. Idempotente."""
        if self._hilo_produccion is not None:
            return
        self._parar.clear()
        self._modelo_pendiente = self._config.models.default_model_id
        self._hilo_produccion = threading.Thread(
            target=self._bucle_produccion, name="streaming-produccion", daemon=True
        )
        self._hilo_inferencia = threading.Thread(
            target=self._bucle_inferencia, name="streaming-inferencia", daemon=True
        )
        self._hilo_produccion.start()
        self._hilo_inferencia.start()

    def detener(self) -> None:
        """Señala el fin y espera a los lazos. Bloquea hasta el timeout."""
        self._parar.set()
        self._frame_nuevo.set()
        for hilo in (self._hilo_produccion, self._hilo_inferencia):
            if hilo is not None and hilo.is_alive():
                hilo.join(timeout=_TIMEOUT_JOIN)
        self._hilo_produccion = None
        self._hilo_inferencia = None

    # --- comandos de la interfaz ---

    def publicar(self) -> None:
        """Pide publicar. No bloquea: lo aplica el lazo de producción."""
        with self._lock:
            self._desea_publicar = True

    def detener_publicacion(self) -> None:
        """Pide dejar de publicar. No bloquea: lo aplica el lazo de producción."""
        with self._lock:
            self._desea_publicar = False

    def fijar_modo(self, modo: ModoPublicacion) -> None:
        """Cambia el modo. No reinicia nada: sólo decide si se dibuja."""
        with self._lock:
            self._modo = modo
        self._registro.actualizar(modo=modo)

    def fijar_fuente(self, source: str) -> None:
        """Pide cambiar de fuente. La reapertura la hace el lazo de producción."""
        with self._lock:
            self._fuente_pendiente = source

    def fijar_modelo(self, model_id: str) -> None:
        """Pide cambiar de modelo. La carga la hace el lazo de inferencia."""
        with self._lock:
            self._modelo_pendiente = model_id

    def solicitar_preview(self, activo: bool) -> None:
        """La sección En vivo pide (o deja de pedir) el frame para mostrar.

        Sin preview pedido y sin publicación, el lazo suelta la cámara: en la Pi
        decodificar 720p30 para nadie es CPU tirada.
        """
        with self._lock:
            self._desea_preview = activo
            if not activo:
                self._slot_preview = None

    def ultimo_frame(self) -> np.ndarray | None:
        """Último frame listo para mostrar, o None.

        Se devuelve la referencia sin copiar: el lazo de producción nunca muta
        un frame ya publicado, sólo lo reemplaza.
        """
        with self._lock:
            return self._slot_preview

    # --- lazo de producción ---

    def _bucle_produccion(self) -> None:
        try:
            while not self._parar.is_set():
                self._aplicar_fuente_pendiente()
                self._reconciliar_publicacion()
                if self._activo():
                    self._producir_frame()
                else:
                    self._cerrar_captura()
                    self._parar.wait(_ESPERA_OCIOSO)
                self._consolidar()
        finally:
            self._cerrar_captura()
            if self._publicador is not None:
                self._publicador.detener()
                self._publicador = None
            self._publicando_aplicado = False
            with self._lock:
                self._slot_preview = None
                self._slot_inferencia = None
            # Sin esto la instantánea quedaba diciendo que seguía publicando
            # después de que el lazo terminó.
            self._registro.actualizar(
                publicando=False, capturando=False, fps=0.0, reintento_en=0.0
            )

    def _activo(self) -> bool:
        """True si hay que capturar: publicar o mostrar alcanza."""
        with self._lock:
            return self._desea_publicar or self._desea_preview

    def _producir_frame(self) -> None:
        """Captura, anota si corresponde, deja el frame y publica."""
        self._asegurar_captura()
        captura = self._captura
        if captura is None:
            self._parar.wait(_ESPERA_SIN_FRAME)
            return

        frame = captura.leer()
        if frame is None:
            self._parar.wait(_ESPERA_SIN_FRAME)
            return

        with self._lock:
            modo = self._modo
            self._numero_frame += 1
            numero = self._numero_frame
            detecciones = self._slot_detecciones[0] if self._slot_detecciones else []
        self._ventana_frames += 1

        # El original queda limpio para el lazo de inferencia: dibujar sobre él
        # le metería cajas al detector. Sin detecciones no hay copia que hacer.
        if modo is ModoPublicacion.INFERENCIA and detecciones:
            anotado = frame.copy()
            dibujar(anotado, detecciones)
        else:
            anotado = frame

        with self._lock:
            self._slot_inferencia = (time.monotonic(), numero, frame)
            # Sólo se guarda para mostrar si alguien va a mirar: el frame
            # anotado ya se usó para publicar y retenerlo cuando nadie mira es
            # memoria al pedo.
            if self._desea_preview:
                self._slot_preview = anotado
        self._frame_nuevo.set()

        if self._publicando_aplicado and self._publicador is not None:
            if self._publicador.enviar(anotado):
                self._registro.sumar("frames_publicados")

    def _reconciliar_publicacion(self) -> None:
        """Arranca o detiene el publicador según la intención de la interfaz."""
        with self._lock:
            desea = self._desea_publicar
        if desea == self._publicando_aplicado:
            return

        if desea:
            if self._publicador is None:
                self._publicador = PublicadorFFmpeg(
                    self._config.stream.publisher,
                    self._config.stream.reconnect,
                    self._config.stream.capture.width,
                    self._config.stream.capture.height,
                    self._config.stream.capture.fps,
                )
            self._publicador.iniciar()
        elif self._publicador is not None:
            self._publicador.detener()
            self._publicador = None

        self._publicando_aplicado = desea

    def _asegurar_captura(self) -> None:
        """Abre la captura si no lo está."""
        if self._captura is not None:
            return
        try:
            captura = CapturaOpenCV(
                replace(self._config.stream.capture, source=self._fuente_actual),
                self._config.stream.reconnect,
                self._config.paths.videos_dir,
            )
        except ValueError as exc:
            self._reportar_error("captura", str(exc))
            return
        self._captura = captura
        self._registro.actualizar(fuente=captura.fuente.descripcion)

    def _cerrar_captura(self) -> None:
        if self._captura is not None:
            self._captura.cerrar()
            self._captura = None

    def _aplicar_fuente_pendiente(self) -> None:
        with self._lock:
            pendiente = self._fuente_pendiente
            self._fuente_pendiente = None
        if pendiente is None or pendiente == self._fuente_actual:
            return
        self._cerrar_captura()
        self._fuente_actual = pendiente
        self._reportar_error("captura", "")
        logger.info("Streaming: fuente cambiada a %s", pendiente)

    def _consolidar(self) -> None:
        """Vuelca a la instantánea los hechos que cambian lento (fps, errores)."""
        ahora = time.monotonic()
        transcurrido = ahora - self._ventana_inicio
        if transcurrido < _INTERVALO_CONSOLIDACION:
            return
        self._ventana_inicio = ahora
        fps = self._ventana_frames / transcurrido
        self._ventana_frames = 0

        captura = self._captura
        publicador = self._publicador
        limite = self._config.stream.capture.read_timeout_seconds
        capturando = captura is not None and captura.tiempo_sin_frames() <= limite

        espera = 0.0
        if captura is not None:
            espera = max(espera, captura.espera_restante())
        if publicador is not None:
            espera = max(espera, publicador.espera_restante())

        self._registro.actualizar(
            fps=fps,
            capturando=capturando,
            publicando=publicador is not None and publicador.publicando,
            reconexiones=(captura.reconexiones if captura else 0)
            + (publicador.reconexiones if publicador else 0),
            reintento_en=espera,
        )

        if captura is not None and not capturando:
            self._reportar_error(
                "captura",
                f"Sin frames hace {captura.tiempo_sin_frames():.0f}s "
                f"({captura.fuente.descripcion})",
            )
        else:
            self._reportar_error("captura", "")

        self._reportar_error(
            "publicacion", publicador.ultimo_error if publicador else ""
        )
        self._reportar_error("almacen", self._almacen.error if self._almacen else "")

    # --- lazo de inferencia ---

    def _bucle_inferencia(self) -> None:
        try:
            while not self._parar.is_set():
                self._aplicar_modelo_pendiente()
                # Sin frame nuevo no se re-infere el anterior: si la captura se
                # corta, el último frame con detecciones mantendría viva la
                # inactividad y el lote nunca cerraría.
                if not self._frame_nuevo.wait(timeout=0.2):
                    continue
                self._frame_nuevo.clear()
                if self._parar.is_set():
                    break
                self._inferir()
        finally:
            with self._lock:
                self._slot_detecciones = None
            if self._almacen is not None:
                self._almacen.detener()
                self._almacen = None
            if self._detector is not None:
                self._detector.liberar()
                self._detector = None

    def _aplicar_modelo_pendiente(self) -> None:
        """Carga el modelo pedido. Corre en este lazo, nunca en el de producción."""
        with self._lock:
            pendiente = self._modelo_pendiente
            self._modelo_pendiente = None
        if pendiente is None:
            return

        entrada = self._entrada(pendiente)
        if entrada is None:
            self._reportar_error("modelo", f"Modelo desconocido: {pendiente}")
            return

        # Se invalida el slot antes de cargar: mejor publicar un instante sin
        # cajas que con las cajas del modelo anterior.
        with self._lock:
            self._slot_detecciones = None
        # Las pistas pertenecen al modelo anterior: no deben contaminar al nuevo.
        # Los `baja` del reinicio se cierran con el modelo VIEJO: se drenan acá y
        # se difunden antes de actualizar `_modelo_actual`.
        self._estabilizador.reiniciar()
        eventos_reinicio = self._estabilizador.eventos()
        if eventos_reinicio:
            if self._almacen is not None:
                self._almacen.registrar(
                    self._numero_frame, eventos_reinicio, activas=0
                )
            self._difundir_eventos(eventos_reinicio, self._numero_frame)
        self._registro.actualizar(
            modelo_id=entrada.model_id,
            modelo_label=entrada.label,
            modelo_estado=EstadoModelo.CARGANDO,
        )

        if self._detector is not None:
            self._detector.liberar()
            self._detector = None

        try:
            detector = construir_detector(entrada, self._config.stream.inference)
        except ErrorCargaModelo as exc:
            self._registro.actualizar(
                motor=None, modelo_path=None, modelo_estado=EstadoModelo.ERROR
            )
            self._reportar_error("modelo", str(exc))
            logger.error("Streaming: no se pudo cargar %s: %s", pendiente, exc)
            return

        self._detector = detector
        self._modelo_actual = entrada.model_id
        self._registro.actualizar(
            motor=detector.motor,
            modelo_path=detector.modelo_path,
            modelo_estado=EstadoModelo.LISTO,
        )
        self._reportar_error("modelo", "")
        self._asegurar_almacen(entrada.model_id)
        logger.info("Streaming: modelo %s listo (%s)", entrada.model_id, detector.motor.etiqueta)

    def _asegurar_almacen(self, model_id: str) -> None:
        """Reabre el almacén para el modelo actual: cada línea lo nombra."""
        if self._almacen is not None:
            self._almacen.detener()
            self._almacen = None

        almacen = AlmacenDetecciones(
            self._config.stream.storage,
            fuente=self._fuente_actual,
            modelo=model_id,
        )
        try:
            almacen.iniciar()
        except OSError as exc:
            self._reportar_error("almacen", f"No se pudo iniciar el almacén: {exc}")
            logger.error("Streaming: almacén no disponible: %s", exc)
            return
        self._almacen = almacen

    def _inferir(self) -> None:
        with self._lock:
            pendiente = self._slot_inferencia
        if pendiente is None or self._detector is None:
            return

        instante, numero_frame, frame = pendiente
        crudas = self._detector.detectar_frame(frame)
        detecciones = self._estabilizador.estabilizar(crudas)
        eventos = self._estabilizador.eventos()
        retraso_ms = int((time.monotonic() - instante) * 1000)

        self._difundir_eventos(eventos, numero_frame)

        with self._lock:
            self._slot_detecciones = (detecciones, retraso_ms)
            if detecciones:
                # Sólo se refresca con detecciones: la inactividad es lo que mide
                # el cierre de lote.
                self._ultima_deteccion_monotonic = time.monotonic()

        self._registro.actualizar(
            ultima_deteccion=detecciones[0] if detecciones else None,
            retraso_inferencia_ms=retraso_ms,
        )
        if detecciones:
            self._registro.sumar("detecciones", len(detecciones))

        if self._almacen is not None:
            self._almacen.registrar(numero_frame, eventos, activas=len(detecciones))

    def _difundir_eventos(
        self, eventos: Sequence[EventoPista], numero_frame: int
    ) -> None:
        """Ofrece los eventos a cada suscriptor. Nunca bloquea la inferencia."""
        if not eventos:
            return
        suscriptores = self._suscriptores.instantanea()
        if not suscriptores:
            return
        modelo_id = self._modelo_actual
        for evento in eventos:
            observado = EventoObservado(
                evento=evento, modelo_id=modelo_id, numero_frame=numero_frame
            )
            for cola in suscriptores:
                cola.ofrecer(observado)

    def segundos_sin_detecciones(self) -> float:
        """Segundos desde la última inferencia con ≥1 detección (o el arranque)."""
        with self._lock:
            referencia = self._ultima_deteccion_monotonic
        if referencia is None:
            referencia = self._inicio_monotonic
        return max(0.0, time.monotonic() - referencia)

    # --- auxiliares ---

    def _entrada(self, model_id: str) -> ModelEntry | None:
        for entrada in self._config.models.catalog:
            if entrada.model_id == model_id:
                return entrada
        return None

    def _reportar_error(self, origen: str, texto: str) -> None:
        """Sostiene los errores por origen, para que uno no tape al otro."""
        with self._lock:
            if texto:
                self._errores[origen] = texto
            else:
                self._errores.pop(origen, None)
            mensaje = " | ".join(self._errores.values())
        self._registro.actualizar(error=mensaje)
