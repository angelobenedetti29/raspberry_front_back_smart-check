"""Composición de la aplicación de escritorio Factory Control.

``FactoryControlApp`` es el punto donde se unen las tres capas: los casos de uso
del backend, el panel de UI (``frontend.ui``) y el worker de captura e
inferencia. La app no construye widgets (de eso se encargan los paneles) ni
resuelve los detalles de vídeo (de eso se encarga el worker): solo cablea
señales, mantiene el estado de la sesión y coordina el ciclo de vida del hilo.
"""

import os
import logging

from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QMainWindow,
    QMessageBox,
    QWidget,
    QVBoxLayout,
    QGridLayout,
    QScrollArea,
    QFrame,
)
from PySide6.QtCore import (
    Qt,
    QObject,
    QRunnable,
    QThreadPool,
    QTimer,
    Signal,
    Slot,
)
from PySide6.QtGui import QImage

from streaming.config import StreamConfig

# Componentes del backend (Arquitectura Limpia).
from backend.infrastructure.ai.fallback_detector import FallbackDetector
from backend.infrastructure.ai.yolo_detector import YoloDetector
from backend.infrastructure.http.requests_client import (
    TIMEOUT_UNCERTAIN,
    RequestsHttpClient,
)
from backend.use_cases.build_lote_payload import DEFAULT_PRODUCT_ID
from backend.use_cases.detect_and_notify import DetectAndNotifyUseCase

from frontend.config import (
    DEFAULT_SOURCE,
    LOCAL_LOTE_ENDPOINT,
    MODEL_CATALOG,
    RESTART_EXIT_CODE,
    VIDEO_EXTENSIONS,
    VIDEOS_DIR,
    WINDOW_HEIGHT,
    WINDOW_TITLE,
    WINDOW_WIDTH,
    load,
    resolve_project_path,
)
from smartcheck_config import (
    ConfigError,
    RevisionConflictError,
    SchemaError,
    save_config,
)
from frontend.services.config_notify import notify_backend_reload
from frontend.services.config_apply import plan_changes
from frontend.services.models import (
    default_model_index,
    detect_platform,
    model_for_index,
)
from frontend.services.streaming import PreviewOnlyPublisher, validate_stream_config
from frontend.ui.alerts_panel import AlertsPanel
from frontend.ui.filters_panel import FiltersPanel
from frontend.ui.gallery_panel import GalleryPanel
from frontend.ui.settings_dialog import SettingsDialog
from frontend.ui.sidebar import Sidebar
from frontend.ui.theme import (
    RIGHT_COLUMN_MIN_WIDTH,
    SIDEBAR_MAX_WIDTH,
    SIDEBAR_MIN_WIDTH,
    build_stylesheet,
)
from frontend.ui.video_panel import VideoPanel
from frontend.workers.detection_worker import YOLODetectionThread

logger = logging.getLogger(__name__)


# Por debajo de este ancho, el layout de tres columnas se reordena en una sola
# columna apilada y desplazable (pensado para el panel 800x480 de la Raspberry).
COMPACT_BREAKPOINT = 1024
MIN_WINDOW_WIDTH = 640
MIN_WINDOW_HEIGHT = 420

# Cota de espera al cerrar para drenar los POST de lote en vuelo: no debe
# bloquear el apagado indefinidamente si el backend no responde.
LOTE_POST_DRAIN_MS = 3000

# Tiempo máximo que se espera a que el QThread de vídeo termine antes de darlo
# por abandonado y pedir recuperación manual.
WORKER_SHUTDOWN_TIMEOUT_MS = 5000

# Ancho máximo de Qt (QWIDGETSIZE_MAX) usado para "sin límite" en modo compacto.
SIDEBAR_UNCONSTRAINED_MAX_WIDTH = 16777215


def _detector_pill_state(detector):
    """Devuelve ``(texto, tono)`` para la píldora de estado del detector."""
    if getattr(detector, "use_hailo", False):
        return "Hailo NPU Activo", "on"
    if isinstance(detector, FallbackDetector):
        return f"Simulado (Error: {detector.error[:25]})", "warning"
    return "ONNX Activo", "info"


class _LotePostSignals(QObject):
    """Portador de señales del POST de lote; vive en el hilo de la GUI.

    El ``QRunnable`` emite desde el pool y Qt encola la entrega en el hilo de la
    GUI, así que el slot que actualiza las alertas nunca corre en segundo plano.
    """

    completed = Signal(dict, bool, str)


class _LotePostTask(QRunnable):
    """Envía un lote al backend fuera del hilo de la GUI.

    ``RequestsHttpClient.post`` bloquea hasta el timeout; ejecutarlo aquí evita
    que la ventana se congele mientras se registra el lote.
    """

    def __init__(self, client, url, payload, signals):
        super().__init__()
        self._client = client
        self._url = url
        self._payload = payload
        self._signals = signals

    @Slot()
    def run(self):
        success = self._client.post(self._url, self._payload)
        # Captura el error aquí, en el hilo del pool, inmediatamente después del
        # POST: ``last_error`` pertenece al request que acaba de terminar y otro
        # POST podría sobrescribirlo, así que se envía con la señal en vez de
        # que el slot lea estado compartido del cliente.
        error = getattr(self._client, "last_error", None)
        self._signals.completed.emit(self._payload, success, error)


class _ConfigReloadSignals(QObject):
    """Portador de señales del aviso de recarga; vive en el hilo de la GUI."""

    completed = Signal(bool, str)


class _ConfigReloadTask(QRunnable):
    """Avisa al backend de la recarga de ``config.json`` fuera del hilo de la GUI.

    ``notify_backend_reload`` hace un POST que bloquea hasta su timeout; aquí se
    ejecuta en el pool para que la ventana no se congele.
    """

    def __init__(self, host, port, signals):
        super().__init__()
        self._host = host
        self._port = port
        self._signals = signals

    @Slot()
    def run(self):
        ok, detail = notify_backend_reload(self._host, self._port)
        self._signals.completed.emit(ok, detail)


class FactoryControlApp(QMainWindow):
    """Ventana principal: cablea paneles, casos de uso y worker de vídeo."""

    def __init__(self, default_source=DEFAULT_SOURCE):
        super().__init__()
        self.setWindowTitle(WINDOW_TITLE)
        self.resize(WINDOW_WIDTH, WINDOW_HEIGHT)
        self.setMinimumSize(MIN_WINDOW_WIDTH, MIN_WINDOW_HEIGHT)
        self.setStyleSheet(build_stylesheet())

        # Componentes del backend.
        self.http_client = RequestsHttpClient()

        # POST de lotes en segundo plano: el pool y su emisor se conservan en la
        # app para que las señales sigan vivas hasta que se entreguen.
        self._lote_post_signals = _LotePostSignals(self)
        self._lote_post_signals.completed.connect(self._on_lote_post_finished)
        self._lote_pool = QThreadPool(self)
        # Un solo hilo serializa los POST de lote: ``RequestsHttpClient`` guarda
        # ``last_error`` en estado compartido y cada POST puede tardar hasta su
        # timeout, así que dos POST solapados reportarían el error ajeno. También
        # conserva el orden FIFO de registro de lotes.
        self._lote_pool.setMaxThreadCount(1)

        # Aviso de recarga de configuración al backend, también fuera del hilo
        # de la GUI. Un único hilo basta: los avisos son poco frecuentes.
        self._config_reload_signals = _ConfigReloadSignals(self)
        self._config_reload_signals.completed.connect(self._on_config_reload_finished)
        self._config_pool = QThreadPool(self)
        self._config_pool.setMaxThreadCount(1)

        # Snapshot de config.json al arrancar: define producto, modelo/etiquetas
        # y umbral de confianza.
        startup_config = load()
        # Producto configurado para los lotes; si está vacío, el worker usa su
        # propio producto por defecto.
        self._producto_id = startup_config.device.producto_id or None

        # Se activa si la app debe salir con RESTART_EXIT_CODE para que run.py
        # relance el proceso completo.
        self._restart_pending = False

        # Detección automática de plataforma (Raspberry Pi con chip Hailo).
        platform = detect_platform()
        self.is_running_on_npu = platform.is_npu

        # Rutas iniciales de modelos: config.json manda; si no define rutas de
        # inferencia se cae al catálogo según la plataforma.
        catalog_model, catalog_names = model_for_index(
            default_model_index(self.is_running_on_npu)
        )
        self.current_model = startup_config.stream.inference.model_path or catalog_model
        self.current_names = startup_config.stream.inference.labels_path or catalog_names

        # Instanciar el detector (capa de infraestructura).
        try:
            self.detector = YoloDetector(
                model_path=resolve_project_path(self.current_model),
                names_path=resolve_project_path(self.current_names),
                confidence_threshold=(
                    startup_config.stream.inference.confidence_threshold
                ),
            )
        except Exception as e:
            logger.error("[GUI App] Error al inicializar detector YOLO: %s", e)
            self.detector = FallbackDetector(e)

        # Casos de uso.
        self.detect_use_case = DetectAndNotifyUseCase(self.detector)

        # Filtros de clases visibles por defecto.
        self.show_ok_toasts = True
        self.show_burnt_toasts = True

        # Estado del layout, inicializado antes de construir la UI porque
        # resizeEvent puede dispararse durante la propia construcción.
        self._compact = None

        # Construcción de la UI y primer refresco de estados.
        self._build_ui()
        self.update_filter_button_styles()

        # Estado del ciclo de vida del worker de vídeo.
        self.yolo_thread = None
        self._shutdown_thread = None
        self._pending_action = None
        self._closing_requested = False
        self._recovery_required = False
        self._shutdown_timer = QTimer(self)
        self._shutdown_timer.setSingleShot(True)
        self._shutdown_timer.timeout.connect(self._on_shutdown_timeout)

        self.play_internal_target(default_source)

    # ------------------------------------------------------------------ ui
    def _build_ui(self):
        """Compone los paneles; la construcción de widgets vive en frontend.ui."""
        central_widget = QWidget()
        central_widget.setObjectName("Root")
        self.setCentralWidget(central_widget)

        outer_layout = QVBoxLayout(central_widget)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)

        # Una única superficie de scroll global; solo muestra barras cuando el
        # escenario es más grande que el viewport (modo compacto).
        self.global_scroll = QScrollArea()
        self.global_scroll.setWidgetResizable(True)
        self.global_scroll.setFrameShape(QFrame.NoFrame)
        self.global_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        outer_layout.addWidget(self.global_scroll)

        self.stage = QWidget()
        self.stage.setObjectName("Root")
        self.stage_layout = QGridLayout(self.stage)
        self.stage_layout.setContentsMargins(16, 16, 16, 16)
        self.stage_layout.setSpacing(16)
        self.global_scroll.setWidget(self.stage)

        # --- Columna izquierda: cámara y selector de modelo ---
        self.sidebar = Sidebar(
            [entry.label for entry in MODEL_CATALOG],
            current_index=default_model_index(self.is_running_on_npu),
        )
        self.sidebar.set_detector_pill(*_detector_pill_state(self.detector))
        # Si config.json fijó una ruta de inferencia que corresponde a una
        # entrada del catálogo, el selector debe reflejarla (el detector ya usa
        # esa ruta). Sin esto mostraría el modelo de la plataforma.
        self.sidebar.set_model_index(self._catalog_index_for_path(self.current_model))
        self.sidebar.camera_toggled.connect(self.toggle_camera)
        self.sidebar.model_changed.connect(self.change_model)
        self.sidebar.settings_requested.connect(self.open_settings_dialog)

        # --- Columna central: vídeo en vivo + galería ---
        self.center_column = QWidget()
        self.center_column.setObjectName("Column")
        center_layout = QVBoxLayout(self.center_column)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(16)

        self.video_panel = VideoPanel()
        center_layout.addWidget(self.video_panel, stretch=7)

        self.gallery_panel = GalleryPanel()
        self.gallery_panel.video_selected.connect(self.play_internal_target)
        center_layout.addWidget(self.gallery_panel, stretch=3)

        # --- Columna derecha: alertas y filtros ---
        self.right_column = QWidget()
        self.right_column.setObjectName("Column")
        self.right_column.setMinimumWidth(RIGHT_COLUMN_MIN_WIDTH)
        right_layout = QVBoxLayout(self.right_column)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(16)

        self.alerts_panel = AlertsPanel()
        right_layout.addWidget(self.alerts_panel, stretch=2)

        self.filters_panel = FiltersPanel()
        self.filters_panel.filter_ok_toggled.connect(self.toggle_filter_ok)
        self.filters_panel.filter_burnt_toggled.connect(self.toggle_filter_burnt)
        right_layout.addWidget(self.filters_panel, stretch=1)

        # Coloca los tres bloques según el ancho inicial de la ventana.
        self._apply_breakpoint(self.width() < COMPACT_BREAKPOINT)

        self._load_gallery()

    def _apply_breakpoint(self, compact):
        """Ordena los paneles para el ancho actual sin reconstruir widgets.

        Ancho: sidebar | (vídeo + galería) | (alertas + filtros).
        Compacto: todo apilado en una columna dentro del scroll global.
        """
        grid = self.stage_layout
        for widget in (self.sidebar, self.center_column, self.right_column):
            grid.removeWidget(widget)
        for column in range(3):
            grid.setColumnStretch(column, 0)
        for row in range(3):
            grid.setRowStretch(row, 0)

        if compact:
            self.sidebar.setMinimumWidth(0)
            self.sidebar.setMaximumWidth(SIDEBAR_UNCONSTRAINED_MAX_WIDTH)
            grid.addWidget(self.sidebar, 0, 0, 1, 1)
            grid.addWidget(self.center_column, 1, 0, 1, 1)
            grid.addWidget(self.right_column, 2, 0, 1, 1)
            grid.setColumnStretch(0, 1)
            grid.setRowStretch(1, 1)
        else:
            self.sidebar.setMinimumWidth(SIDEBAR_MIN_WIDTH)
            self.sidebar.setMaximumWidth(SIDEBAR_MAX_WIDTH)
            grid.addWidget(self.sidebar, 0, 0, 1, 1)
            grid.addWidget(self.center_column, 0, 1, 1, 1)
            grid.addWidget(self.right_column, 0, 2, 1, 1)
            grid.setColumnStretch(1, 7)
            grid.setColumnStretch(2, 3)
            grid.setRowStretch(0, 1)

        self._compact = compact

    def resizeEvent(self, event):
        """Reordena los paneles al cruzar el punto de ruptura compacto."""
        super().resizeEvent(event)
        # Qt puede entregar un resize durante __init__, antes de _build_ui().
        if not hasattr(self, "stage_layout"):
            return
        compact = event.size().width() < COMPACT_BREAKPOINT
        if compact != self._compact:
            self._apply_breakpoint(compact)

    def _load_gallery(self):
        """Carga en la galería los vídeos disponibles en VIDEOS_DIR."""
        videos_path = resolve_project_path(VIDEOS_DIR)
        names = []
        if os.path.exists(videos_path):
            names = sorted(
                f for f in os.listdir(videos_path) if f.endswith(VIDEO_EXTENSIONS)
            )
        if names:
            self.gallery_panel.set_videos(names)
        else:
            self.gallery_panel.show_empty(
                "Sin vídeos", f"No se encontró la ruta: {videos_path}"
            )

    # -------------------------------------------------------------- filters
    def toggle_filter_ok(self):
        """Alterna la visibilidad de las etiquetas de tostadas OK."""
        self.show_ok_toasts = not self.show_ok_toasts
        self.update_filter_button_styles()
        if self.yolo_thread is not None:
            self.yolo_thread.show_ok_toasts = self.show_ok_toasts

    def toggle_filter_burnt(self):
        """Alterna la visibilidad de las etiquetas de tostadas quemadas."""
        self.show_burnt_toasts = not self.show_burnt_toasts
        self.update_filter_button_styles()
        if self.yolo_thread is not None:
            self.yolo_thread.show_burnt_toasts = self.show_burnt_toasts

    def update_filter_button_styles(self):
        """Sincroniza los chips de filtro con el estado de la aplicación."""
        self.filters_panel.update_state(self.show_ok_toasts, self.show_burnt_toasts)

    # ---------------------------------------------------------------- alerts
    def add_alert_log(self, message, tone="danger"):
        """Agrega un aviso al historial de alertas."""
        self.alerts_panel.add_alert(message, tone=tone)

    # ---------------------------------------------------------------- modelo
    def change_model(self, index, *, stream_config=None, publisher_factory=None,
                     model_path=None, names_path=None, confidence_threshold=None,
                     source_override=None):
        """Cambia el modelo activo y reinicia el vídeo en curso, si lo hay.

        Un índice fuera del catálogo no cambia nada: el selector solo emite
        posiciones válidas. Además del selector, ``_apply_config_plan`` puede
        pasar rutas de modelo/etiquetas, umbral y una fuente nueva
        (``source_override``) cuando el cambio proviene del editor de
        configuración; en ese caso no se consulta el catálogo estático.
        """
        if self._recovery_required:
            self._show_recovery_required()
            return

        active_worker = (
            (self.yolo_thread is not None and self.yolo_thread.isRunning())
            or self._shutdown_thread is not None
        )
        if stream_config is None:
            stream_config, publisher_factory = self._stream_setup(
                allow_preview_fallback=not active_worker
            )
            if stream_config is None:
                return

        if model_path is None or names_path is None:
            if 0 <= index < len(MODEL_CATALOG):
                self.current_model, self.current_names = model_for_index(index)
                logger.info(
                    "[INFO] Modelo cambiado a: %s", MODEL_CATALOG[index].label
                )
            model_path = resolve_project_path(self.current_model)
            names_path = resolve_project_path(self.current_names)
        else:
            # Rutas explícitas: se conservan como modelo activo para los
            # próximos arranques.
            self.current_model, self.current_names = model_path, names_path

        start_reference = source_override
        active_source = None
        if (
            start_reference is None
            and self.yolo_thread is not None
            and self.yolo_thread.isRunning()
        ):
            # Conservar la fuente exacta que está activa en vez de reconstruir
            # un nombre de la galería después del apagado asíncrono.
            active_source = self.yolo_thread.source_file
            if active_source != "0":
                active_source = os.path.abspath(active_source)

        def finish():
            self._finish_model_change(
                model_path,
                names_path,
                active_source,
                stream_config,
                publisher_factory,
                confidence_threshold=confidence_threshold,
                start_reference=start_reference,
            )

        has_worker = (
            (self.yolo_thread is not None and self.yolo_thread.isRunning())
            or self._shutdown_thread is not None
        )
        if has_worker:
            self._request_thread_shutdown(pending_action=finish)
            return
        finish()

    def _finish_model_change(self, model_path, names_path, active_source,
                             stream_config, publisher_factory,
                             confidence_threshold=None, start_reference=None):
        """Aplica el cambio de modelo una vez terminó el worker anterior.

        Solo se invoca como callback de un worker en ejecución o directamente
        cuando no hay ninguno activo. El timeout limpia la acción pendiente, así
        que esta liberación no puede competir con una captura viva.
        ``start_reference`` (fuente nueva pedida por la configuración) tiene
        prioridad sobre ``active_source`` (fuente activa que se conserva).
        """
        if self.detector is not None:
            logger.info("[GUI App] Liberando recursos del detector anterior...")
            try:
                self.detector.release_hailo()
            except Exception as e:
                logger.error("[GUI App] Error al liberar NPU: %s", e)
            self.detector = None

        detector_kwargs = {"model_path": model_path, "names_path": names_path}
        if confidence_threshold is not None:
            detector_kwargs["confidence_threshold"] = confidence_threshold
        try:
            self.detector = YoloDetector(**detector_kwargs)
        except Exception as e:
            logger.error("[GUI App] Error al cambiar detector YOLO: %s", e)
            self.detector = FallbackDetector(e)

        self.detect_use_case = DetectAndNotifyUseCase(self.detector)
        self.sidebar.set_detector_pill(*_detector_pill_state(self.detector))

        if start_reference is not None:
            self._start_internal_target(
                start_reference,
                stream_config=stream_config,
                publisher_factory=publisher_factory,
                model_path=model_path,
                names_path=names_path,
            )
        elif active_source is not None:
            self._start_internal_target(
                active_source,
                source_path_override=active_source,
                stream_config=stream_config,
                publisher_factory=publisher_factory,
                model_path=model_path,
                names_path=names_path,
            )

    # ------------------------------------------------- ciclo de vida worker
    def _disconnect_worker_signals(self, thread):
        """Desconecta los consumidores de UI mientras un worker se apaga."""
        for signal in (
            thread.change_pixmap_signal,
            thread.lote_completed_signal,
            thread.burned_toast_alert_signal,
        ):
            try:
                signal.disconnect()
            except (TypeError, RuntimeError):
                pass

    def _show_recovery_required(self):
        """Deja la app en un estado seguro que exige intervención del operador."""
        self._recovery_required = True
        message = "Se requiere recuperación: no se pudo detener el vídeo anterior"
        logger.error("[GUI App] %s", message)
        self.video_panel.show_recovery(message)
        self.add_alert_log(message)

    def _on_shutdown_timeout(self):
        """Da por fallido el apagado del worker si no terminó a tiempo."""
        thread = self._shutdown_thread
        if thread is None:
            return
        # Una señal finished puede quedar en cola detrás de este evento del timer.
        if not thread.isRunning():
            self._on_worker_finished()
            return

        # No se libera ni se termina a la fuerza una captura nativa que puede
        # estar bloqueada. Mantener yolo_thread es deliberado: no se creará un
        # reemplazo hasta que el operador recupere este worker aún en ejecución.
        try:
            thread.finished.disconnect(self._on_worker_finished)
        except (TypeError, RuntimeError):
            pass
        # Un worker que superó el timeout nunca emitirá ``finished``, así que sus
        # señales se desconectan aquí para que no siga enviando frames, alertas
        # ni lotes al backend desde un hilo abandonado.
        self._disconnect_worker_signals(thread)
        self._pending_action = None
        self._shutdown_thread = None
        self._show_recovery_required()

    @Slot()
    def _on_worker_finished(self):
        """Ejecuta la acción pendiente (o cierra) una vez terminó el worker."""
        thread = self._shutdown_thread
        if thread is None:
            return
        self._shutdown_timer.stop()
        try:
            thread.finished.disconnect(self._on_worker_finished)
        except (TypeError, RuntimeError):
            pass
        self._disconnect_worker_signals(thread)
        self._shutdown_thread = None
        if self.yolo_thread is thread:
            self.yolo_thread = None

        pending_action = self._pending_action
        self._pending_action = None
        closing = self._closing_requested
        self._closing_requested = False
        if closing:
            # El segundo closeEvent solo se acepta después de que QThread emita
            # finished, nunca mientras su captura nativa pueda seguir viva.
            self.close()
        elif pending_action is not None and not self._recovery_required:
            pending_action()

    def _request_thread_shutdown(self, pending_action=None, closing=False):
        """Pide la parada y encola trabajo hasta que el QThread haya terminado."""
        if self._recovery_required and pending_action is not None:
            self._show_recovery_required()
            return False

        if self._shutdown_thread is not None:
            self._pending_action = pending_action
            self._closing_requested = self._closing_requested or closing
            return False

        thread = self.yolo_thread
        if thread is None:
            if pending_action is not None and not closing:
                pending_action()
            return True

        if not thread.isRunning():
            # Ya terminó: no queda lote residual, así que aquí sí se pueden
            # soltar los consumidores de UI.
            self._disconnect_worker_signals(thread)
            if self.yolo_thread is thread:
                self.yolo_thread = None
            if pending_action is not None and not closing:
                pending_action()
            return True

        # Las señales siguen conectadas hasta que QThread emita ``finished``:
        # ``run()`` emite el lote residual justo antes de terminar y debe
        # llegar al backend. ``_on_worker_finished`` las desconecta después.
        self._shutdown_thread = thread
        self._pending_action = pending_action
        self._closing_requested = closing
        thread.finished.connect(self._on_worker_finished)
        thread.stop()  # No bloquea; capture.release sigue en worker.run().
        self._shutdown_timer.start(WORKER_SHUTDOWN_TIMEOUT_MS)
        return False

    # ----------------------------------------------------------------- vídeo
    def toggle_camera(self, checked):
        """Enciende la cámara del dispositivo o apaga la fuente actual."""
        if checked:
            self.play_internal_target("0")
            return

        self.video_panel.show_off("Cámara apagada")
        self._request_thread_shutdown()

    def play_internal_target(self, video_name):
        """Reproduce una fuente (cámara o vídeo) esperando al worker anterior."""
        if self._recovery_required:
            self._show_recovery_required()
            return

        active_worker = (
            (self.yolo_thread is not None and self.yolo_thread.isRunning())
            or self._shutdown_thread is not None
        )
        stream_config, publisher_factory = self._stream_setup(
            allow_preview_fallback=not active_worker
        )
        if stream_config is None:
            return

        def start_target():
            self._start_internal_target(
                video_name,
                stream_config=stream_config,
                publisher_factory=publisher_factory,
            )

        self._request_thread_shutdown(pending_action=start_target)

    def _show_video_start_error(self, message):
        """Registra y muestra en el panel de vídeo un error de arranque."""
        logger.error("[GUI App] %s", message)
        self.video_panel.show_error(message)

    def _show_streaming_disabled(self, error, preview_only):
        """Avisa de que el streaming RTSP queda deshabilitado."""
        if preview_only:
            message = (
                "Streaming deshabilitado (configuración inválida); "
                "vista local en modo preview"
            )
        else:
            message = (
                "Streaming deshabilitado: configuración inválida; "
                "se conserva la fuente activa"
            )
        logger.warning("[GUI App] %s: %s", message, error)
        if preview_only:
            self._show_video_start_error(message)
        self.add_alert_log(message, tone="warning")

    def _stream_config_from_app(self):
        """Construye ``StreamConfig`` desde la sección stream de ``config.json``.

        ``config.json`` es la única fuente: no se releen variables de entorno ni
        argumentos de línea de comandos. Se lee la configuración fresca para que
        un cambio reciente del archivo se refleje al (re)arrancar el worker.
        """
        return StreamConfig.from_app_config(load().stream)

    def _stream_setup(self, allow_preview_fallback):
        """Resuelve la configuración de streaming y su publisher.

        Devuelve ``(config, publisher_factory)``; si la configuración de
        ``config.json`` es inválida y no se permite el modo preview, devuelve
        ``(None, None)``.
        """
        try:
            config = validate_stream_config(self._stream_config_from_app())
            return config, None
        except Exception as exc:
            self._show_streaming_disabled(exc, allow_preview_fallback)
            if not allow_preview_fallback:
                return None, None
            # StreamConfig() es una configuración preview válida (dimensiones pares).
            return validate_stream_config(StreamConfig()), PreviewOnlyPublisher

    def _validated_stream_config(self, stream_config=None):
        """Valida la configuración recibida (o la de config.json) sin lanzar."""
        try:
            config = (
                stream_config
                if stream_config is not None
                else self._stream_config_from_app()
            )
            return validate_stream_config(config)
        except Exception as exc:
            self._show_video_start_error(f"Error de configuración de vídeo: {exc}")
            return None

    def _start_internal_target(self, video_name, source_path_override=None,
                               stream_config=None, model_path=None,
                               names_path=None, publisher_factory=None):
        """Resuelve la fuente, valida modelo/etiquetas y arranca el worker."""
        if source_path_override is not None:
            # Fuente ya resuelta por un cambio de modelo en curso.
            source_path = source_path_override
            if source_path != "0":
                self.sidebar.set_camera_checked(False)
        elif video_name != "0":
            self.sidebar.set_camera_checked(False)
            if os.path.isabs(video_name):
                # Ruta absoluta explícita, por ejemplo un vídeo fuera del repo.
                source_path = video_name
            elif "/" in video_name or os.sep in video_name:
                # Ruta relativa al repositorio: "multimedia/videos/road.mp4".
                source_path = resolve_project_path(video_name)
            else:
                # Nombre suelto: se busca en la carpeta de vídeos del proyecto.
                source_path = os.path.join(resolve_project_path(VIDEOS_DIR), video_name)
        else:
            source_path = "0"

        stream_config = self._validated_stream_config(stream_config)
        if stream_config is None:
            return

        resolved_model = (
            model_path if model_path is not None
            else resolve_project_path(self.current_model)
        )
        resolved_names = (
            names_path if names_path is not None
            else resolve_project_path(self.current_names)
        )
        if not os.path.exists(resolved_model) or not os.path.exists(resolved_names):
            self._show_video_start_error(
                "Error: No se encontró el modelo o las etiquetas\n"
                f"Cargar: {os.path.basename(resolved_model)}"
            )
            return

        self.video_panel.show_connecting()
        session_name = (
            os.path.basename(source_path)
            if source_path != "0"
            else "Cámara del dispositivo"
        )
        self.video_panel.set_session_meta(session_name)

        self.yolo_thread = YOLODetectionThread(
            source_path,
            self.detect_use_case,
            stream_config=stream_config,
            publisher_factory=publisher_factory,
            producto_id=self._producto_id,
        )
        self.yolo_thread.show_ok_toasts = self.show_ok_toasts
        self.yolo_thread.show_burnt_toasts = self.show_burnt_toasts
        self.yolo_thread.change_pixmap_signal.connect(self.update_image)
        self.yolo_thread.burned_toast_alert_signal.connect(self.add_alert_log)
        self.yolo_thread.lote_completed_signal.connect(self.handle_lote_completed)
        self.yolo_thread.start()

    @Slot(dict)
    def handle_lote_completed(self, payload):
        """Encola el POST del lote sin bloquear el hilo de la GUI.

        El endpoint se relee de ``config.json`` en cada lote: el diálogo de
        configuración puede haberlo cambiado en caliente. Si la config no se
        puede leer, se usa la constante de arranque como respaldo.
        """
        logger.info("[GUI App] Lote completado. Enviando POST con payload: %s", payload)

        try:
            endpoint = load().api.lote_endpoint
        except (ConfigError, SchemaError):
            endpoint = LOCAL_LOTE_ENDPOINT

        task = _LotePostTask(
            self.http_client,
            endpoint,
            payload,
            self._lote_post_signals,
        )
        self._lote_pool.start(task)

    @Slot(dict, bool, str)
    def _on_lote_post_finished(self, payload, success, error):
        """Refleja en las alertas el resultado del POST ya resuelto."""
        if success:
            logger.info(
                "[GUI App] Lote registrado exitosamente en el servidor central "
                "a través del backend."
            )
            self.add_alert_log(
                "¡LOTE REGISTRADO! "
                f"Unidades: {payload['totalUnidades']} "
                f"(OK: {payload['correctos']}, Q: {payload['quemados']}, C: {payload['crudas']})",
                tone="info",
            )
            return

        # ``error`` viaja con la señal desde el propio request; ya no se lee
        # ``self.http_client.last_error``, que otro POST pudo sobrescribir.
        logger.error("[GUI App] Error al registrar el lote: %s", error)
        if error == TIMEOUT_UNCERTAIN:
            # El central pudo registrar el lote: se avisa sin afirmar un fallo.
            self.add_alert_log(
                "Envío de lote sin confirmar: no se pudo confirmar; "
                "el lote pudo haberse registrado",
                tone="warning",
            )
        else:
            self.add_alert_log(f"Error al enviar lote: {str(error)[:50]}")

    @Slot(QImage)
    def update_image(self, qt_image):
        """Muestra en el panel de vídeo un frame enviado por el worker."""
        self.video_panel.show_image(qt_image)

    # --------------------------------------------------------- configuración
    def _notify_backend_reload_async(self, host=None, port=None):
        """Pide al backend recargar ``config.json`` sin bloquear el hilo de la GUI.

        El aviso debe ir al backend que está CORRIENDO, no al recién guardado:
        si el guardado cambió ``api.host``/``api.port``, el proceso vivo sigue
        escuchando en los valores viejos, así que el llamador pasa el snapshot
        previo. Sin argumentos se usa la configuración vigente.

        El POST corre en ``_config_pool``; el resultado se entrega en el hilo de
        la GUI vía ``_ConfigReloadSignals``.
        """
        if host is None or port is None:
            current = load()
            host = current.api.host
            port = current.api.port
        task = _ConfigReloadTask(host, port, self._config_reload_signals)
        self._config_pool.start(task)

    @Slot(bool, str)
    def _on_config_reload_finished(self, ok, detail):
        """Registra el resultado del aviso de recarga al backend."""
        if ok:
            logger.info("[GUI App] Backend notificado: recarga de configuración OK")
        else:
            logger.warning(
                "[GUI App] No se pudo notificar la recarga al backend: %s", detail
            )

    # ------------------------------------------------- editor de configuración
    def open_settings_dialog(self):
        """Abre el editor de configuración y aplica el plan si se guarda.

        Corre entero en el hilo de la GUI: lee el snapshot con ``load()``, abre
        el diálogo modal y, al aceptar, guarda con control de revisión y
        delega la aplicación de los cambios en ``_apply_config_plan``. Nunca
        bloquea esperando al worker; el diálogo solo se abre si no hay una
        transición de hilos en curso.
        """
        if self._restart_pending:
            QMessageBox.information(
                self,
                "Configuración",
                "Hay un reinicio pendiente. Esperá a que la aplicación se reinicie.",
            )
            return
        if self._shutdown_thread is not None:
            QMessageBox.information(
                self,
                "Configuración",
                "Hay un cambio de vídeo en curso. Esperá unos segundos y "
                "volvé a intentar.",
            )
            return

        try:
            snapshot = load()
        except (ConfigError, SchemaError) as exc:
            QMessageBox.warning(
                self,
                "Configuración",
                f"No se pudo leer config.json:\n{exc}",
            )
            return

        dialog = SettingsDialog(snapshot, self)
        if dialog.exec() != QDialog.Accepted:
            return

        new_config = dialog.result_config()
        try:
            new_revision = save_config(
                new_config, expected_revision=snapshot.revision
            )
        except RevisionConflictError:
            QMessageBox.warning(
                self,
                "Configuración",
                "La configuración cambió en otro proceso. Reabrí el diálogo "
                "para ver los valores actuales.",
            )
            return
        except (SchemaError, ConfigError) as exc:
            QMessageBox.warning(
                self,
                "Configuración",
                f"No se pudo guardar la configuración:\n{exc}",
            )
            return

        plan = plan_changes(snapshot, new_revision)
        # El aviso va al backend que está corriendo (host/puerto del snapshot),
        # no al que se acaba de guardar.
        self._notify_backend_reload_async(snapshot.api.host, snapshot.api.port)
        self._apply_config_plan(plan, new_revision)

    def _apply_config_plan(self, plan, new_cfg):
        """Aplica el plan de cambios devuelto por ``plan_changes``.

        Cada sección se resuelve por separado: ``detector`` y ``pipeline``
        reinician componentes en un solo apagado del worker, ``hot`` propaga lo
        que no exige reinicio y ``restart_app`` pide un reinicio completo.
        """
        detector_changed = bool(plan.get("detector"))
        pipeline_changed = bool(plan.get("pipeline"))
        if detector_changed or pipeline_changed:
            self._restart_streaming_components(plan, new_cfg)
        self._apply_hot_config(plan, new_cfg)
        if plan.get("restart_app"):
            self._prompt_restart_required()

    def _restart_streaming_components(self, plan, new_cfg):
        """Reconstruye detector y/o pipeline respetando el orden de apagado.

        Si el plan incluye ``detector`` se reutiliza ``change_model`` con las
        rutas del modelo activo tomadas de ``new_cfg.models.catalog``. Si solo
        cambia el ``pipeline``, se conserva el detector y se reemplaza el
        worker. En ambos casos la fuente se preserva salvo que el plan cambie
        ``stream.capture.source``.
        """
        source_changed = "stream.capture.source" in plan.get("pipeline", [])
        try:
            stream_config = validate_stream_config(
                StreamConfig.from_app_config(new_cfg.stream)
            )
        except Exception as exc:
            self._show_streaming_disabled(exc, preview_only=False)
            return

        if plan.get("detector"):
            entry = self._active_model_entry(new_cfg)
            # Las rutas de inferencia de config.json tienen prioridad; si no
            # están seteadas, se cae a la entrada del catálogo.
            model_path = new_cfg.stream.inference.model_path or entry.model_path
            names_path = new_cfg.stream.inference.labels_path or entry.names_path
            self.change_model(
                self._catalog_index_for(entry.model_id),
                stream_config=stream_config,
                model_path=resolve_project_path(model_path),
                names_path=resolve_project_path(names_path),
                confidence_threshold=(
                    new_cfg.stream.inference.confidence_threshold
                ),
                source_override=(
                    new_cfg.stream.capture.source if source_changed else None
                ),
            )
            return

        # Solo pipeline: se conserva el detector y se recrea el worker.
        active_source = None
        if self.yolo_thread is not None and self.yolo_thread.isRunning():
            active_source = self.yolo_thread.source_file
            if active_source != "0":
                active_source = os.path.abspath(active_source)

        start_reference = (
            new_cfg.stream.capture.source if source_changed else None
        )
        start_override = active_source if not source_changed else None
        if start_reference is None and start_override is None:
            # No hay una fuente viva que reiniciar y la configurada no cambió.
            return

        def start():
            if start_reference is not None:
                self._start_internal_target(
                    start_reference, stream_config=stream_config
                )
            else:
                self._start_internal_target(
                    start_override,
                    source_path_override=start_override,
                    stream_config=stream_config,
                )

        self._request_thread_shutdown(pending_action=start)

    def _active_model_entry(self, new_cfg):
        """Entrada del catálogo que corresponde al modelo activo.

        Se busca por las rutas del modelo en uso para no pisar una selección
        hecha desde el selector lateral. Si no se encuentra (por ejemplo tras
        editar el catálogo), se cae al modelo por defecto configurado.
        """
        wanted = self.current_model
        wanted_abs = (
            wanted if os.path.isabs(wanted) else resolve_project_path(wanted)
        )
        for entry in new_cfg.models.catalog:
            entry_abs = (
                entry.model_path
                if os.path.isabs(entry.model_path)
                else resolve_project_path(entry.model_path)
            )
            if entry.model_path == wanted or entry_abs == wanted_abs:
                return entry
        return new_cfg.models.model_by_id(new_cfg.models.default_model_id)

    @staticmethod
    def _catalog_index_for(model_id):
        """Posición de ``model_id`` en el catálogo estático, o -1 si no está."""
        for index, entry in enumerate(MODEL_CATALOG):
            if entry.model_id == model_id:
                return index
        return -1

    @staticmethod
    def _catalog_index_for_path(model_path):
        """Posición del catálogo cuya ruta de modelo coincide, o -1.

        Compara en forma absoluta para que una ruta relativa de ``config.json``
        y la del catálogo se consideren iguales. Se usa al arrancar para alinear
        el selector con el modelo de inferencia fijado en la configuración.
        """
        if not model_path:
            return -1
        wanted = (
            model_path
            if os.path.isabs(model_path)
            else resolve_project_path(model_path)
        )
        for index, entry in enumerate(MODEL_CATALOG):
            entry_path = (
                entry.model_path
                if os.path.isabs(entry.model_path)
                else resolve_project_path(entry.model_path)
            )
            if entry.model_path == model_path or entry_path == wanted:
                return index
        return -1

    def _apply_hot_config(self, plan, new_cfg):
        """Aplica los cambios que no exigen reiniciar el worker.

        ``device.producto_id`` se propaga al worker vivo para que el lote en
        curso ya use el producto nuevo; si no hay worker, el valor queda listo
        para el próximo arranque. El resto de las hojas ``hot`` (p. ej.
        ``api.lote_endpoint``) se releen dinámicamente donde se usan, así que no
        requieren acción aquí.
        """
        self._producto_id = new_cfg.device.producto_id or None
        if self.yolo_thread is not None:
            self.yolo_thread.producto_id = self._producto_id or DEFAULT_PRODUCT_ID

    def _prompt_restart_required(self):
        """Ofrece reiniciar la aplicación para aplicar cambios de modelo/rutas."""
        if self._confirm_restart():
            self.request_full_restart()

    def _confirm_restart(self):
        """Pregunta al operador si quiere reiniciar ahora. Devuelve su decisión."""
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Information)
        box.setWindowTitle("Reinicio requerido")
        box.setText("Requiere reiniciar la aplicación")
        box.setInformativeText(
            "Los cambios en modelos o rutas se aplican al reiniciar el proceso."
        )
        restart_button = box.addButton("Reiniciar ahora", QMessageBox.AcceptRole)
        box.addButton("Más tarde", QMessageBox.RejectRole)
        box.exec()
        return box.clickedButton() is restart_button

    def request_full_restart(self):
        """Pide un reinicio total de la aplicación.

        Marca la intención y cierra la ventana; cuando ``closeEvent`` acepta el
        cierre (tras ordenar el worker de vídeo) la app sale con
        ``RESTART_EXIT_CODE`` para que ``run.py`` relance el proceso.
        """
        self._restart_pending = True
        # Si el worker sigue vivo, closeEvent ignora el cierre y lo reintenta al
        # terminar; solo se sale del bucle cuando el cierre fue aceptado.
        if self.close():
            self._exit_for_restart()

    @staticmethod
    def _exit_for_restart():
        """Termina el bucle de la aplicación con el código de reinicio."""
        app = QApplication.instance()
        if app is not None:
            app.exit(RESTART_EXIT_CODE)

    def closeEvent(self, event):
        """Cierra la ventana solo cuando el worker de vídeo ya terminó."""
        # El cierre es asíncrono: QThread debe terminar antes de que Qt destruya
        # la ventana y la captura que posee el worker.
        if self.yolo_thread is not None and self.yolo_thread.isRunning():
            self._request_thread_shutdown(closing=True)
            event.ignore()
            return
        self._request_thread_shutdown(closing=True)
        # Drena los POST de lote en vuelo antes de aceptar el cierre: el
        # QThreadPool se destruye al cerrar y descartaría una tarea pendiente sin
        # procesar su señal. La espera es acotada para que el apagado nunca se
        # cuelgue si el backend no responde.
        if not self._lote_pool.waitForDone(LOTE_POST_DRAIN_MS):
            logger.warning(
                "[GUI App] El lote en vuelo no pudo confirmarse antes de "
                "cerrar; se descarta su resultado."
            )
        # Mismo drenaje acotado para el aviso de recarga de configuración.
        if not self._config_pool.waitForDone(LOTE_POST_DRAIN_MS):
            logger.warning(
                "[GUI App] La notificación de recarga no pudo confirmarse antes "
                "de cerrar; se descarta su resultado."
            )
        event.accept()
        # Si el cierre venía de una petición de reinicio, salir con el código
        # que run.py interpreta como "relanzar todo".
        if self._restart_pending:
            self._exit_for_restart()
