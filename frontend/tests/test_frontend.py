import os
import sys
import math
import subprocess
import threading
import time
from dataclasses import dataclass, replace
from types import SimpleNamespace

import numpy as np
import pytest
from PySide6.QtCore import QSize
from PySide6.QtGui import QImage, QResizeEvent
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication, QDialog

# Asegurar que la raíz del repositorio esté en el PYTHONPATH. Este archivo vive
# en ``frontend/tests``, es decir, dos niveles bajo la raíz.
project_root = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
if project_root not in sys.path:
    sys.path.append(project_root)

from frontend.app import FactoryControlApp  # noqa: E402
from frontend.config import (  # noqa: E402
    MODEL_CATALOG,
    resolve_project_path,
)
from frontend.services.models import DEFAULT_MODEL_INDEX, model_for_index  # noqa: E402
from frontend.ui.components import ListItem  # noqa: E402
from frontend.workers.detection_worker import YOLODetectionThread  # noqa: E402
from frontend.services.streaming import (  # noqa: E402
    PreviewOnlyPublisher,
    validate_stream_config,
)
from frontend.services.config_apply import plan_changes  # noqa: E402
from frontend.services.config_notify import notify_backend_reload  # noqa: E402
from frontend.ui.settings_dialog import (  # noqa: E402
    SettingsDialog,
    validate_settings,
)
import frontend.app as frontend_app  # noqa: E402
from backend.domain.entities.sensor_readings import SensorReadings  # noqa: E402
from backend.use_cases.build_lote_payload import DEFAULT_PRODUCT_ID  # noqa: E402
from smartcheck_config import (  # noqa: E402
    AppConfig,
    CaptureSettings,
    ConfigError,
    InferenceSettings,
    ModelEntry,
    PublisherSettings,
    ReconnectSettings,
    RevisionConflictError,
    StorageSettings,
    StreamSettings,
)
from streaming.config import StreamConfig  # noqa: E402


def _stream_config(width=4, height=4, fps=20):
    """``StreamConfig`` con la forma anidada del bloque ``stream``."""
    return StreamConfig(
        capture=CaptureSettings(width=width, height=height, fps=fps)
    )


@pytest.fixture(scope="session")
def qt_app():
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    return QApplication.instance() or QApplication([])


@dataclass
class Detection:
    label: str = "TCOK"
    confidence: float = 0.95
    bbox: tuple = (0, 0, 2, 2)
    id: int | None = None
    state: str = "ok"


class MockDetector:
    def get_class_names(self):
        return ["TCOK", "TCQ"]


class MockUseCase:
    def __init__(self, detections=None):
        self.detector = MockDetector()
        self.detections = detections if detections is not None else [Detection()]

    def execute(self, _frame):
        return self.detections


def test_emit_batch_metrics_is_a_real_test(qt_app):
    thread = YOLODetectionThread(
        "road.mp4", MockUseCase(),
        stream_config=_stream_config(width=4, height=4, fps=20),
    )
    thread.seen_toasts.update({1: "ok", 2: "ok", 3: "burnt", 4: "ok", 5: "burnt"})
    for sample in (
        SensorReadings(220.0, 315.0, 218.0, 312.0, 1.05),
        SensorReadings(220.0, 315.0, 218.0, 312.0, 1.15),
    ):
        thread.sensor_accumulator.add(sample)
    received = []
    thread.lote_completed_signal.connect(received.append)

    thread.emit_batch_metrics()

    assert received[0]["productoId"] == "a1b2c3d4-5678-90ab-cdef-1234567890ab"
    assert received[0]["quemados"] == 2
    assert received[0]["totalUnidades"] == 5
    assert received[0]["correctos"] + received[0]["quemados"] + received[0]["crudas"] == 5


def test_injected_capture_and_publisher_share_annotated_canonical_frame(qt_app):
    lifecycle = []

    class Capture:
        def __init__(self):
            self.frames = [np.zeros((2, 2, 3), dtype=np.uint8)]

        def isOpened(self):
            lifecycle.append("capture-opened")
            return True

        def read(self):
            if self.frames:
                return True, self.frames.pop(0)
            return False, None

        def release(self):
            lifecycle.append("capture-released")

    class Publisher:
        def __init__(self, config):
            self.config = config
            self.frames = []
            self.started = False

        def start(self):
            assert lifecycle == ["capture-opened"]
            self.started = True
            lifecycle.append("publisher-started")

        def publish(self, frame):
            assert self.started
            self.frames.append(frame.copy())
            return True

        def stop(self):
            self.started = False
            lifecycle.append("publisher-stopped")

    capture = Capture()
    publisher = None

    def make_publisher(config):
        nonlocal publisher
        publisher = Publisher(config)
        return publisher

    displayed = []
    thread = YOLODetectionThread(
        "0",
        MockUseCase([Detection(bbox=(0, 0, 2, 2))]),
        stream_config=_stream_config(width=4, height=4, fps=20),
        capture_factory=lambda _source: capture,
        publisher_factory=make_publisher,
    )
    thread.change_pixmap_signal.connect(displayed.append)
    thread.start()
    assert thread.wait(3000)
    qt_app.processEvents()

    assert lifecycle == ["capture-opened", "publisher-started", "publisher-stopped", "capture-released"]
    assert publisher is not None
    canonical = publisher.frames[0]
    assert canonical.shape == (4, 4, 3)
    assert canonical.dtype == np.uint8
    assert canonical.flags.c_contiguous
    # TCOK overlays are green in BGR and must be present in the published frame.
    assert np.any(np.all(canonical == (0, 255, 0), axis=2))
    assert displayed and displayed[0].width() == 4 and displayed[0].height() == 4


def test_odd_yuv420p_dimensions_are_rejected():
    with pytest.raises(ValueError, match="pares"):
        validate_stream_config(_stream_config(width=3, height=4, fps=20))


class FakeSignal:
    def __init__(self):
        self.callback = None

    def connect(self, callback):
        self.callback = callback

    def disconnect(self, callback=None):
        if callback is None or callback is self.callback:
            self.callback = None

    def emit(self):
        if self.callback is not None:
            self.callback()


class FakeWorker:
    def __init__(self, running=True):
        self.running = running
        self.finished = FakeSignal()
        self.change_pixmap_signal = FakeSignal()
        self.lote_completed_signal = FakeSignal()
        self.burned_toast_alert_signal = FakeSignal()
        self.stop_requested = False

    def isRunning(self):
        return self.running

    def stop(self):
        self.stop_requested = True


class FakeTimer:
    def __init__(self):
        self.started = False

    def start(self, _milliseconds):
        self.started = True

    def stop(self):
        self.started = False


def make_lifecycle_harness(worker):
    app = SimpleNamespace(
        yolo_thread=worker,
        _shutdown_thread=None,
        _pending_action=None,
        _closing_requested=False,
        _recovery_required=False,
        _shutdown_timer=FakeTimer(),
    )
    app._disconnect_worker_signals = lambda thread: None
    app._show_recovery_required = lambda: (
        setattr(app, "recovery_shown", True),
        setattr(app, "_recovery_required", True),
    )
    app._on_worker_finished = lambda: FactoryControlApp._on_worker_finished(app)
    app._on_shutdown_timeout = lambda: FactoryControlApp._on_shutdown_timeout(app)
    return app


def test_invalid_initial_config_starts_preview_only(qt_app, monkeypatch):
    def invalid_config(_stream_settings):
        raise ValueError("capture.width inválido")

    monkeypatch.setattr(
        frontend_app.StreamConfig, "from_app_config", staticmethod(invalid_config)
    )
    messages = []
    app = SimpleNamespace()
    app._show_streaming_disabled = lambda error, preview_only: messages.append(
        (str(error), preview_only)
    )
    # Enlaza la construcción real para que el monkeypatch de from_app_config se
    # ejecute (y no un AttributeError del SimpleNamespace).
    app._stream_config_from_app = lambda: FactoryControlApp._stream_config_from_app(
        app
    )
    config, publisher_factory = FactoryControlApp._stream_setup(app, True)

    assert config.capture.width % 2 == 0 and config.capture.height % 2 == 0
    assert publisher_factory is PreviewOnlyPublisher
    assert messages and messages[0][1] is True

    class Capture:
        def __init__(self):
            self.frames = [np.zeros((2, 2, 3), dtype=np.uint8)]

        def isOpened(self):
            return True

        def read(self):
            if self.frames:
                return True, self.frames.pop(0)
            return False, None

        def release(self):
            self.released = True

    capture = Capture()
    displayed = []
    thread = YOLODetectionThread(
        "0", MockUseCase(), stream_config=config,
        capture_factory=lambda _source: capture,
        publisher_factory=publisher_factory,
    )
    thread.change_pixmap_signal.connect(displayed.append)
    thread.start()
    assert thread.wait(3000)
    qt_app.processEvents()
    assert displayed and displayed[0].width() == config.capture.width
    assert isinstance(thread.publisher, PreviewOnlyPublisher)
    assert not hasattr(thread.publisher, "frames")


def test_invalid_config_does_not_stop_or_replace_active_worker(monkeypatch):
    def invalid_config(_stream_settings):
        raise ValueError("capture.height impar")

    monkeypatch.setattr(
        frontend_app.StreamConfig, "from_app_config", staticmethod(invalid_config)
    )
    worker = FakeWorker()
    requests = []
    messages = []
    app = SimpleNamespace(
        yolo_thread=worker,
        _shutdown_thread=None,
        _recovery_required=False,
    )
    app._show_streaming_disabled = lambda error, preview_only: messages.append(
        (str(error), preview_only)
    )
    app._stream_setup = lambda allow_preview_fallback: FactoryControlApp._stream_setup(
        app, allow_preview_fallback
    )
    app._stream_config_from_app = lambda: FactoryControlApp._stream_config_from_app(
        app
    )
    app._request_thread_shutdown = lambda **kwargs: requests.append(kwargs)

    FactoryControlApp.play_internal_target(app, "0")

    assert requests == []
    assert app.yolo_thread is worker
    assert worker.stop_requested is False
    assert messages and messages[0][1] is False


def test_pending_source_starts_only_after_old_worker_finished():
    old_worker = FakeWorker()
    app = make_lifecycle_harness(old_worker)
    started = []

    FactoryControlApp._request_thread_shutdown(app, pending_action=lambda: started.append("new"))
    assert old_worker.stop_requested
    assert started == []

    old_worker.running = False
    old_worker.finished.emit()
    assert started == ["new"]


def test_shutdown_timeout_suppresses_replacement_and_retains_old_reference():
    old_worker = FakeWorker()
    app = make_lifecycle_harness(old_worker)
    started = []

    FactoryControlApp._request_thread_shutdown(app, pending_action=lambda: started.append("new"))
    app._on_shutdown_timeout()

    assert started == []
    assert app.yolo_thread is old_worker
    assert app._recovery_required is True


class StubDetector:
    """Hardware-free stand-in for YoloDetector (no model is ever loaded)."""

    instances = []

    def __init__(self, model_path=None, names_path=None, confidence_threshold=None):
        self.model_path = model_path
        self.names_path = names_path
        self.confidence_threshold = confidence_threshold
        self.use_hailo = False
        StubDetector.instances.append(self)

    def detect_frame(self, frame):
        return []

    def get_class_names(self):
        return ["TCOK", "TCQ"]

    def release_hailo(self):
        pass


def test_factory_control_app_constructs_offscreen(qt_app, monkeypatch):
    monkeypatch.setattr(
        FactoryControlApp, "play_internal_target", lambda self, video_name: None
    )
    monkeypatch.setattr(
        frontend_app,
        "detect_platform",
        lambda: SimpleNamespace(is_raspberry_pi=False, is_npu=False),
    )
    monkeypatch.setattr(frontend_app, "YoloDetector", StubDetector)
    StubDetector.instances.clear()

    app = FactoryControlApp()

    # The hardware-free stub must be the detector actually used.
    assert StubDetector.instances
    assert app.detector is StubDetector.instances[-1]
    assert app.detector.model_path is not None
    assert app.video_panel.video_label is not None
    assert app.sidebar.model_selector.count() == 4
    # Only the functional camera toggle remains in navigation.
    assert len(app.sidebar.nav_buttons) == 1
    assert app.sidebar.nav_buttons[0].isCheckable()
    assert app.filters_panel.btn_filter_ok is not None
    assert app.filters_panel.btn_filter_burnt is not None
    assert app.alerts_panel.list_layout is not None
    assert app.gallery_panel.list_layout is not None

    # La píldora del detector ya refleja el estado real al arrancar (no se
    # queda en "Inicializando" hasta el primer cambio de modelo).
    assert app.sidebar.detector_pill.text() == "ONNX Activo"
    assert app.sidebar.detector_pill.property("tone") == "info"

    # The UI is composed from dedicated panels that own their widgets.
    for panel_name in (
        "sidebar",
        "video_panel",
        "gallery_panel",
        "alerts_panel",
        "filters_panel",
    ):
        assert getattr(app, panel_name) is not None, panel_name

    # Control signals are wired to the application handlers.
    app.sidebar.nav_buttons[0].setChecked(False)
    app.sidebar.nav_buttons[0].click()
    assert app.sidebar.nav_buttons[0].isChecked()

    assert app.show_ok_toasts is True
    app.filters_panel.btn_filter_ok.click()
    assert app.show_ok_toasts is False
    app.filters_panel.btn_filter_ok.click()
    assert app.show_ok_toasts is True

    # The layout adapts at the compact breakpoint (800x480 panel support).
    # Resize events are only delivered to a shown widget.
    app.show()
    qt_app.processEvents()
    assert app._compact is False
    app.resize(800, 480)
    qt_app.processEvents()
    assert app._compact is True
    app.resize(1200, 800)
    qt_app.processEvents()
    assert app._compact is False
    app.hide()

    app.close()


def test_resize_before_ui_build_does_not_crash(qt_app, monkeypatch):
    # Qt puede entregar un resize durante __init__, antes de que exista el
    # stage_layout; resizeEvent debe ignorarlo en vez de reventar.
    app, _calls = _build_hermetic_app(monkeypatch)
    del app.stage_layout

    app.resizeEvent(QResizeEvent(QSize(500, 400), QSize(1200, 800)))

    assert not hasattr(app, "stage_layout")
    app.close()


def test_resolve_project_path_maps_repo_assets():
    model_path = resolve_project_path("ai_training/models/yolo11n.onnx")
    assert os.path.isabs(model_path)
    assert os.path.exists(model_path)
    assert os.path.basename(model_path) == "yolo11n.onnx"

    video_path = resolve_project_path("multimedia/videos/road.mp4")
    assert os.path.exists(video_path)
    assert os.path.basename(video_path) == "road.mp4"


def test_resolve_project_path_keeps_absolute_and_empty_paths():
    # El .hef de la NPU Hailo es una ruta absoluta del sistema y no se reescribe.
    absolute = "/usr/share/hailo-models/yolov8s_h8l.hef"
    assert resolve_project_path(absolute) == absolute
    assert resolve_project_path("") == ""


class _OneShotCapture:
    def __init__(self, frames):
        self._frames = list(frames)
        self.released = False

    def isOpened(self):
        return True

    def read(self):
        if self._frames:
            return True, self._frames.pop(0)
        return False, None

    def release(self):
        self.released = True


class _RecordingPublisher:
    def __init__(self, config):
        self.config = config

    def start(self):
        pass

    def publish(self, _frame):
        return True

    def stop(self):
        pass


class _FakeSensorProvider:
    def __init__(self, samples):
        self._samples = list(samples)

    def read(self):
        return self._samples.pop(0)


class _ExplodingSensorProvider:
    def read(self):
        raise RuntimeError("sensor offline")


class _LoopingCapture:
    """Captura sin fin: el worker solo termina cuando se le pide parar."""

    def __init__(self):
        self.released = False
        self._stop = threading.Event()

    def isOpened(self):
        return True

    def read(self):
        self._stop.wait(0.005)
        return True, np.zeros((2, 2, 3), dtype=np.uint8)

    def release(self):
        self.released = True
        self._stop.set()


def _run_thread_until_done(qt_app, detections, sensor_provider, frame_count=2):
    capture = _OneShotCapture(
        [np.zeros((2, 2, 3), dtype=np.uint8) for _ in range(frame_count)]
    )
    displayed = []
    received = []
    thread = YOLODetectionThread(
        "0",
        MockUseCase(detections),
        stream_config=_stream_config(width=4, height=4, fps=20),
        capture_factory=lambda _source: capture,
        publisher_factory=_RecordingPublisher,
        sensor_provider=sensor_provider,
    )
    thread.change_pixmap_signal.connect(displayed.append)
    thread.lote_completed_signal.connect(received.append)
    thread.start()
    assert thread.wait(3000)
    qt_app.processEvents()
    return thread, capture, received


def test_worker_uses_injected_sensor_provider_and_averages(qt_app):
    sample1 = SensorReadings(200.0, 300.0, 210.0, 310.0, 1.0)
    sample2 = SensorReadings(220.0, 320.0, 230.0, 330.0, 1.2)
    provider = _FakeSensorProvider([sample1, sample2])

    thread, capture, received = _run_thread_until_done(
        qt_app,
        [Detection(id=1, state="ok", bbox=(0, 0, 2, 2))],
        provider,
    )

    assert capture.released is True
    assert thread.sensor_accumulator.count == 2
    assert received, "the worker must emit a lote once toasts were seen"
    payload = received[0]
    assert payload["tempHorno1"] == round((sample1.tempHorno1 + sample2.tempHorno1) / 2, 2)
    assert payload["velocidadCinta"] == round(
        (sample1.velocidadCinta + sample2.velocidadCinta) / 2, 2
    )


def test_sensor_read_failure_does_not_abort_loop_or_emit_nan(qt_app):
    thread, capture, received = _run_thread_until_done(
        qt_app,
        [Detection(id=1, state="ok", bbox=(0, 0, 2, 2))],
        _ExplodingSensorProvider(),
    )

    assert capture.released is True
    assert thread.sensor_accumulator.count == 0
    assert received, "the worker must still emit a lote after the loop completes"
    payload = received[0]
    assert not any(
        isinstance(value, float) and math.isnan(value) for value in payload.values()
    )
    # Fallback sensor values are used when no samples could be read.
    assert payload["tempHorno1"] == 220.0


def test_residual_lote_is_delivered_on_stop(qt_app, monkeypatch):
    # Al detener el worker, ``run()`` emite el lote acumulado justo antes de
    # terminar; las señales deben seguir conectadas hasta ``finished``.
    app, _calls = _build_hermetic_app(monkeypatch)
    app.add_alert_log = lambda *args, **kwargs: None
    app.http_client = _FakeHttpClient(success=True)

    capture = _LoopingCapture()
    received = []
    thread = YOLODetectionThread(
        "0",
        MockUseCase([Detection(id=7, state="ok", bbox=(0, 0, 2, 2))]),
        stream_config=_stream_config(width=4, height=4, fps=20),
        capture_factory=lambda _source: capture,
        publisher_factory=_RecordingPublisher,
    )
    thread.lote_completed_signal.connect(received.append)
    app.yolo_thread = thread
    thread.start()

    for _ in range(400):
        if thread.seen_toasts:
            break
        qt_app.processEvents()
        time.sleep(0.005)
    assert thread.seen_toasts, "el worker debió registrar al menos una tostada"

    app._request_thread_shutdown(pending_action=lambda: None)

    assert thread.wait(3000)
    for _ in range(20):
        qt_app.processEvents()
        time.sleep(0.005)

    assert received, "el lote residual debe entregarse al detener el worker"
    assert received[-1]["totalUnidades"] == 1
    assert app.yolo_thread is None

    app.close()


def test_sensor_accumulator_averages_whole_lote_and_state_stays_constant(qt_app):
    sample_count = 20
    provider = _FakeSensorProvider(
        [
            SensorReadings(100.0 + i, 315.0, 218.0, 312.0, 1.0 + i)
            for i in range(sample_count)
        ]
    )

    thread, capture, received = _run_thread_until_done(
        qt_app,
        [Detection(id=1, state="ok", bbox=(0, 0, 2, 2))],
        provider,
        frame_count=sample_count,
    )

    assert capture.released is True
    # El conteo crece con los frames, pero el estado solo guarda sumas + conteo:
    # memoria constante aunque el lote sea largo.
    assert thread.sensor_accumulator.count == sample_count
    assert all(
        not isinstance(value, (list, tuple, dict, set))
        for value in thread.sensor_accumulator.__dict__.values()
    )
    assert received, "el lote se emite con todas las muestras del lote"
    # Promedio del lote completo, no solo de la cola.
    expected = round(sum(100.0 + i for i in range(sample_count)) / sample_count, 2)
    assert received[0]["tempHorno1"] == expected


def test_alerted_ids_are_bounded(qt_app, monkeypatch):
    import frontend.workers.detection_worker as worker_module

    monkeypatch.setattr(worker_module, "MAX_ALERTED_IDS", 2)
    thread = YOLODetectionThread(
        "road.mp4", MockUseCase(),
        stream_config=_stream_config(width=4, height=4, fps=20),
    )

    assert thread._remember_alerted_id(1) is True
    assert thread._remember_alerted_id(2) is True
    assert thread._remember_alerted_id(3) is True
    assert list(thread.alerted_ids) == [2, 3]
    assert thread._remember_alerted_id(3) is False


# ---------------------------------------------------------------------------
# Phase 3: panel-extraction contracts
# ---------------------------------------------------------------------------
def _build_hermetic_app(monkeypatch, config=None):
    """Construct the app with hardware-free stubs and a recording navigator.

    Con ``config`` se inyecta un ``AppConfig`` concreto en ``frontend_app.load``
    para ejercitar el arranque con una configuración dada.
    """
    calls = []
    monkeypatch.setattr(
        FactoryControlApp,
        "play_internal_target",
        lambda self, video_name: calls.append(video_name),
    )
    monkeypatch.setattr(
        frontend_app,
        "detect_platform",
        lambda: SimpleNamespace(is_raspberry_pi=False, is_npu=False),
    )
    if config is not None:
        monkeypatch.setattr(frontend_app, "load", lambda: config)
    StubDetector.instances.clear()
    monkeypatch.setattr(frontend_app, "YoloDetector", StubDetector)
    app = FactoryControlApp()
    return app, calls


def test_model_for_index_falls_back_for_invalid_indices():
    expected = (
        MODEL_CATALOG[DEFAULT_MODEL_INDEX].model_path,
        MODEL_CATALOG[DEFAULT_MODEL_INDEX].names_path,
    )
    assert model_for_index(DEFAULT_MODEL_INDEX) == expected
    # Negative indices must not wrap around via Python indexing.
    for bad_index in (-1, -4, len(MODEL_CATALOG), len(MODEL_CATALOG) + 10):
        assert model_for_index(bad_index) == expected


def test_gallery_selection_triggers_play_internal_target(qt_app, monkeypatch):
    app, calls = _build_hermetic_app(monkeypatch)
    calls.clear()

    app.gallery_panel.set_videos(["alpha.mp4", "beta.mp4"])
    qt_app.processEvents()

    items = [
        app.gallery_panel.list_layout.itemAt(i).widget()
        for i in range(app.gallery_panel.list_layout.count())
    ]
    items = [widget for widget in items if isinstance(widget, ListItem)]
    assert [widget.text() for widget in items] == ["alpha.mp4", "beta.mp4"]

    items[1].click()
    assert calls == ["beta.mp4"]

    app.close()


def test_update_image_sets_live_state_and_pixmap(qt_app, monkeypatch):
    app, _calls = _build_hermetic_app(monkeypatch)
    app.show()
    qt_app.processEvents()

    image = QImage(6, 4, QImage.Format_RGB888)
    image.fill(0x336699)
    app.update_image(image)
    qt_app.processEvents()

    assert app.video_panel._state == "live"
    assert app.video_panel.status_pill.text() == "En vivo"
    assert app.video_panel.status_pill.property("tone") == "on"
    pixmap = app.video_panel.video_label.pixmap()
    assert pixmap is not None and not pixmap.isNull()

    app.close()


def test_alerts_empty_state_and_add_alert(qt_app, monkeypatch):
    app, _calls = _build_hermetic_app(monkeypatch)

    assert app.alerts_panel._list.empty.isHidden() is False
    initial_count = app.alerts_panel.list_layout.count()

    app.add_alert_log("x")

    assert app.alerts_panel._list.empty.isHidden() is True
    assert app.alerts_panel.list_layout.count() == initial_count + 1
    first_entry = app.alerts_panel.list_layout.itemAt(0).widget()
    assert first_entry is not None and first_entry.text().endswith("x")

    app.close()


def test_model_selector_matches_catalog_and_invokes_change_model(qt_app, monkeypatch):
    seen = []
    monkeypatch.setattr(
        FactoryControlApp, "change_model", lambda self, index: seen.append(index)
    )
    app, _calls = _build_hermetic_app(monkeypatch)

    assert app.sidebar.model_selector.count() == 4
    assert [
        app.sidebar.model_selector.itemText(i)
        for i in range(app.sidebar.model_selector.count())
    ] == [entry.label for entry in MODEL_CATALOG]

    app.sidebar.model_selector.setCurrentIndex(2)
    assert seen == [2]

    app.close()


def test_video_panel_states_map_to_expected_pill(qt_app, monkeypatch):
    app, _calls = _build_hermetic_app(monkeypatch)

    app._show_video_start_error("boom")
    assert app.video_panel._state == "error"
    assert app.video_panel.status_pill.text() == "Error"
    assert app.video_panel.status_pill.property("tone") == "danger"

    app._show_recovery_required()
    assert app.video_panel._state == "recovery"
    assert app.video_panel.status_pill.text() == "Recuperación"
    assert app.video_panel.status_pill.property("tone") == "warning"

    app.toggle_camera(False)
    assert app.video_panel._state == "off"
    assert app.video_panel.status_pill.text() == "Detenida"
    assert app.video_panel.status_pill.property("tone") == "off"

    app.close()


def test_frontend_main_import_and_cli_help():
    import importlib

    module = importlib.import_module("frontend.main")
    assert module is not None

    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    commands = [
        [sys.executable, "-m", "frontend.main", "--help"],
        [sys.executable, os.path.join(project_root, "frontend", "main.py"), "--help"],
    ]
    for command in commands:
        result = subprocess.run(
            command, cwd=project_root, capture_output=True, text=True, env=env
        )
        assert result.returncode == 0, result.stderr
        assert "usage" in result.stdout.lower()


class _FakeHttpClient:
    """Records POST calls without touching the network."""

    def __init__(self, success=True, last_error=None):
        self.success = success
        self.last_error = last_error
        self.calls = []

    def post(self, url, payload, headers=None):
        self.calls.append((url, payload, headers))
        return self.success


def _drain_lote_posts(app, qt_app):
    """Espera el POST en segundo plano y entrega su señal en el hilo de la GUI."""
    assert app._lote_pool.waitForDone(3000)
    qt_app.processEvents()


def test_handle_lote_completed_success_and_failure(qt_app, monkeypatch):
    app, _calls = _build_hermetic_app(monkeypatch)
    # El endpoint se relee de config.json en cada lote; se fija la config para
    # que la URL sea determinista.
    config = AppConfig()
    monkeypatch.setattr(frontend_app, "load", lambda: config)
    logged = []
    app.add_alert_log = lambda message, tone="danger": logged.append((message, tone))

    payload = {"totalUnidades": 3, "correctos": 2, "quemados": 1, "crudas": 0}

    app.http_client = _FakeHttpClient(success=True)
    app.handle_lote_completed(payload)
    _drain_lote_posts(app, qt_app)

    assert app.http_client.calls
    assert app.http_client.calls[0][0] == config.api.lote_endpoint
    assert app.http_client.calls[0][1] == payload
    assert logged and "LOTE REGISTRADO" in logged[-1][0]
    assert logged[-1][1] == "info"

    logged.clear()
    app.http_client = _FakeHttpClient(success=False, last_error="servidor caido")
    app.handle_lote_completed(payload)
    _drain_lote_posts(app, qt_app)

    assert logged and "Error al enviar lote" in logged[-1][0]
    assert "servidor caido" in logged[-1][0]

    app.close()


def test_handle_lote_completed_posts_off_gui_thread(qt_app, monkeypatch):
    # El POST debe correr en el pool: bloquear el hilo de la GUI congela la UI.
    app, _calls = _build_hermetic_app(monkeypatch)
    app.add_alert_log = lambda *args, **kwargs: None
    gui_thread = threading.get_ident()
    seen = {}

    class RecordingClient:
        last_error = None

        def post(self, url, payload, headers=None):
            seen["thread"] = threading.get_ident()
            return True

    app.http_client = RecordingClient()
    app.handle_lote_completed(
        {"totalUnidades": 1, "correctos": 1, "quemados": 0, "crudas": 0}
    )
    _drain_lote_posts(app, qt_app)

    assert "thread" in seen
    assert seen["thread"] != gui_thread

    app.close()


def test_handle_lote_completed_timeout_reports_uncertain(qt_app, monkeypatch):
    from backend.infrastructure.http.requests_client import TIMEOUT_UNCERTAIN

    app, _calls = _build_hermetic_app(monkeypatch)
    logged = []
    app.add_alert_log = lambda message, tone="danger": logged.append((message, tone))
    app.http_client = _FakeHttpClient(success=False, last_error=TIMEOUT_UNCERTAIN)

    app.handle_lote_completed(
        {"totalUnidades": 1, "correctos": 1, "quemados": 0, "crudas": 0}
    )
    _drain_lote_posts(app, qt_app)

    assert logged
    assert "no se pudo confirmar" in logged[-1][0]
    assert "pudo haberse registrado" in logged[-1][0]
    assert logged[-1][1] == "warning"

    app.close()


class _SequencedHttpClient:
    """Cliente falso que entrega un ``(success, last_error)`` distinto por POST."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.last_error = None
        self.calls = []

    def post(self, url, payload, headers=None):
        success, error = self._responses.pop(0)
        self.last_error = error
        self.calls.append((url, payload))
        return success


def test_lote_error_comes_from_own_request_not_shared_client_state(qt_app, monkeypatch):
    # Dos POST fallan por motivos distintos: cada alerta debe reflejar el error
    # de su propio request, no el ``last_error`` compartido que dejó el último.
    app, _calls = _build_hermetic_app(monkeypatch)
    logged = []
    app.add_alert_log = lambda message, tone="danger": logged.append((message, tone))

    client = _SequencedHttpClient(
        [
            (False, "error_uno"),
            (False, "error_dos"),
        ]
    )
    app.http_client = client

    payload_a = {"totalUnidades": 1, "correctos": 1, "quemados": 0, "crudas": 0}
    payload_b = {"totalUnidades": 2, "correctos": 0, "quemados": 2, "crudas": 0}
    app.handle_lote_completed(payload_a)
    app.handle_lote_completed(payload_b)
    _drain_lote_posts(app, qt_app)

    assert len(client.calls) == 2
    messages = [message for message, _tone in logged]
    # El primer slot debe conservar SU error aunque el segundo POST ya haya
    # sobrescrito ``last_error`` con "error_dos".
    assert "error_uno" in messages[0]
    assert "error_dos" not in messages[0]
    assert any("error_dos" in message for message in messages[1:])

    app.close()


def test_lote_pool_serializes_posts_and_never_overlaps(qt_app, monkeypatch):
    # ``setMaxThreadCount(1)`` debe impedir que dos POST se solapen sobre el
    # estado compartido del cliente.
    app, _calls = _build_hermetic_app(monkeypatch)
    app.add_alert_log = lambda *args, **kwargs: None

    release = threading.Event()
    first_started = threading.Event()

    class OverlapDetectingClient:
        last_error = None

        def __init__(self):
            self._lock = threading.Lock()
            self.in_flight = 0
            self.max_in_flight = 0
            self.started = 0
            self.finished = 0

        def post(self, url, payload, headers=None):
            with self._lock:
                self.in_flight += 1
                self.started += 1
                self.max_in_flight = max(self.max_in_flight, self.in_flight)
                if self.started == 1:
                    first_started.set()
            release.wait(5)
            with self._lock:
                self.in_flight -= 1
                self.finished += 1
            return True

    client = OverlapDetectingClient()
    app.http_client = client

    for total in (1, 2):
        app.handle_lote_completed(
            {"totalUnidades": total, "correctos": total, "quemados": 0, "crudas": 0}
        )

    assert first_started.wait(2), "el primer POST debió arrancar"
    # El primer POST sigue bloqueado: con un solo hilo el segundo no puede
    # entrar. Si el pool permitiera solape, ``started`` llegaría a 2.
    time.sleep(0.1)
    assert client.started == 1
    assert client.max_in_flight == 1

    release.set()
    _drain_lote_posts(app, qt_app)

    assert client.started == 2
    assert client.max_in_flight == 1
    assert client.finished == 2

    app.close()


def test_close_event_waits_for_in_flight_lote_post(qt_app, monkeypatch):
    # Al cerrar, el POST en vuelo debe drenarse (bounded) antes de aceptar el
    # cierre para que su resultado no se pierda con el QThreadPool.
    app, _calls = _build_hermetic_app(monkeypatch)
    logged = []
    app.add_alert_log = lambda message, tone="danger": logged.append((message, tone))

    release = threading.Event()
    started = threading.Event()

    class BlockingClient:
        last_error = None

        def post(self, url, payload, headers=None):
            started.set()
            release.wait(5)
            return True

    app.http_client = BlockingClient()
    app.handle_lote_completed(
        {"totalUnidades": 1, "correctos": 1, "quemados": 0, "crudas": 0}
    )
    assert started.wait(2), "el POST debió arrancar"

    # Liberar el POST desde otro hilo mientras closeEvent espera en waitForDone.
    threading.Thread(
        target=lambda: (time.sleep(0.05), release.set()), daemon=True
    ).start()

    start = time.monotonic()
    assert app.close() is True
    elapsed = time.monotonic() - start

    qt_app.processEvents()
    assert elapsed < 2.0
    assert logged and "LOTE REGISTRADO" in logged[-1][0]


def test_close_event_does_not_hang_on_stuck_lote_post(qt_app, monkeypatch):
    # Un POST atascado no debe colgar el cierre más allá de la cota.
    monkeypatch.setattr(frontend_app, "LOTE_POST_DRAIN_MS", 150)
    app, _calls = _build_hermetic_app(monkeypatch)
    app.add_alert_log = lambda *args, **kwargs: None

    stuck = threading.Event()
    started = threading.Event()

    class StuckClient:
        last_error = None

        def post(self, url, payload, headers=None):
            started.set()
            stuck.wait(5)
            return True

    app.http_client = StuckClient()
    app.handle_lote_completed(
        {"totalUnidades": 1, "correctos": 1, "quemados": 0, "crudas": 0}
    )
    assert started.wait(2), "el POST debió arrancar"

    start = time.monotonic()
    assert app.close() is True
    elapsed = time.monotonic() - start

    assert elapsed < 1.5, "el cierre esperó más que la cota"
    assert elapsed >= 0.1, "el cierre debió esperar la cota acotada"

    # Liberar para que el pool no cuelgue el teardown del test.
    stuck.set()
    assert app._lote_pool.waitForDone(2000)


def test_shutdown_timeout_disconnects_abandoned_worker_signals():
    # Tras el timeout el worker nunca emitirá ``finished``; sus señales deben
    # desconectarse para que no siga enviando frames/alertas/lotes.
    old_worker = FakeWorker()
    app = make_lifecycle_harness(old_worker)
    app._disconnect_worker_signals = (
        lambda thread: FactoryControlApp._disconnect_worker_signals(app, thread)
    )
    received = []
    old_worker.change_pixmap_signal.connect(lambda: received.append("frame"))
    old_worker.lote_completed_signal.connect(lambda: received.append("lote"))
    old_worker.burned_toast_alert_signal.connect(lambda: received.append("toast"))

    FactoryControlApp._request_thread_shutdown(app)
    app._on_shutdown_timeout()

    old_worker.change_pixmap_signal.emit()
    old_worker.lote_completed_signal.emit()
    old_worker.burned_toast_alert_signal.emit()

    assert received == []
    assert app._recovery_required is True


# ---------------------------------------------------------------------------
# Configuración unificada: plan_changes y notificación al backend
# ---------------------------------------------------------------------------
_EMPTY_PLAN = {"detector": [], "pipeline": [], "restart_app": [], "hot": []}


def test_plan_changes_is_empty_for_identical_configs():
    base = AppConfig()
    assert plan_changes(base, base) == _EMPTY_PLAN


def test_plan_changes_classifies_each_taxonomy_bucket():
    base = AppConfig()
    new = replace(
        base,
        revision=base.revision + 1,  # los metadatos no cuentan como cambios
        stream=replace(
            base.stream,
            capture=replace(base.stream.capture, width=1920),
            inference=replace(base.stream.inference, confidence_threshold=0.9),
            publisher=replace(base.stream.publisher,
                              output_url="rtsp://nuevo:8554/entrada",
                              queue_size=8),
        ),
        device=replace(base.device, api_base_url="http://central"),
        models=replace(base.models, default_model_id="tostadas-v1"),
        paths=replace(base.paths, videos_dir="videos"),
        api=replace(base.api, port=9000),
    )

    plan = plan_changes(base, new)

    assert plan["detector"] == ["stream.inference.confidence_threshold"]
    assert plan["pipeline"] == [
        "stream.capture.width",
        "stream.publisher.output_url",
        "stream.publisher.queue_size",
    ]
    assert plan["restart_app"] == [
        "device.api_base_url",
        "models.default_model_id",
        "paths.videos_dir",
        "api.port",
    ]
    assert plan["hot"] == []


def test_plan_changes_detector_flags_and_pipeline_capture():
    base = AppConfig()
    new = replace(
        base,
        stream=replace(
            base.stream,
            capture=replace(base.stream.capture, source="camara.mp4", fps=20),
            inference=replace(
                base.stream.inference,
                enabled=True,
                require_hailo=True,
            ),
        ),
    )

    plan = plan_changes(base, new)

    assert plan["pipeline"] == ["stream.capture.source", "stream.capture.fps"]
    assert plan["detector"] == [
        "stream.inference.enabled",
        "stream.inference.require_hailo",
    ]
    assert plan["restart_app"] == []
    assert plan["hot"] == []


def test_plan_changes_frozen_stream_fields_are_pipeline():
    """Almacenamiento, reconexión y ajustes de captura/publicación los congela
    el worker al construirse: deben reiniciar el pipeline, no quedar en hot."""
    base = AppConfig()
    new = replace(
        base,
        stream=replace(
            base.stream,
            capture=replace(
                base.stream.capture,
                loop_video=False,
                buffer_size=4,
                stable_frames=10,
                read_timeout_seconds=2.0,
            ),
            publisher=replace(
                base.stream.publisher,
                queue_size=4,
                write_timeout=1.0,
                stable_seconds=1.0,
            ),
            storage=replace(base.stream.storage, path="otro/detections.jsonl"),
            reconnect=replace(base.stream.reconnect, max_seconds=60.0),
        ),
    )

    plan = plan_changes(base, new)

    assert plan["pipeline"] == [
        "stream.capture.loop_video",
        "stream.capture.buffer_size",
        "stream.capture.stable_frames",
        "stream.capture.read_timeout_seconds",
        "stream.publisher.queue_size",
        "stream.publisher.write_timeout",
        "stream.publisher.stable_seconds",
        "stream.storage.path",
        "stream.reconnect.max_seconds",
    ]
    assert plan["detector"] == []
    assert plan["restart_app"] == []
    assert plan["hot"] == []


def test_plan_changes_only_product_and_lote_endpoint_are_hot():
    """Único hot real: lo que la app aplica en vivo. El host/puerto de la API y
    el resto de device exigen reinicio (H2/M3)."""
    base = AppConfig()
    new = replace(
        base,
        device=replace(base.device, producto_id="P1", horno_id="H1"),
        api=replace(
            base.api,
            host="10.0.0.5",
            port=9000,
            lote_endpoint="http://127.0.0.1:9000/api/lotes/finalizar",
        ),
    )

    plan = plan_changes(base, new)

    assert plan["hot"] == ["device.producto_id", "api.lote_endpoint"]
    assert plan["restart_app"] == ["device.horno_id", "api.host", "api.port"]
    assert plan["detector"] == []
    assert plan["pipeline"] == []


class _FakeResponse:
    def __init__(self, status_code):
        self.status_code = status_code
        self.ok = 200 <= status_code < 300


def test_notify_backend_reload_returns_ok_and_never_raises(monkeypatch):
    import frontend.services.config_notify as notify_module

    calls = []

    def fake_post(url, timeout=None):
        calls.append((url, timeout))
        return _FakeResponse(200)

    monkeypatch.setattr(notify_module.requests, "post", fake_post)
    ok, detail = notify_backend_reload("127.0.0.1", 8000)

    assert ok is True
    assert calls == [("http://127.0.0.1:8000/api/config/reload", 1.5)]
    assert "200" in detail

    monkeypatch.setattr(
        notify_module.requests,
        "post",
        lambda url, timeout=None: _FakeResponse(503),
    )
    ok, detail = notify_backend_reload("127.0.0.1", 8000)
    assert ok is False
    assert "503" in detail

    def boom(url, timeout=None):
        raise ConnectionError("sin ruta al backend")

    monkeypatch.setattr(notify_module.requests, "post", boom)
    ok, detail = notify_backend_reload("127.0.0.1", 8000)
    assert ok is False
    assert "sin ruta al backend" in detail


# ---------------------------------------------------------------------------
# Editor de configuración: validación pura, result_config y plan de aplicación
# ---------------------------------------------------------------------------
def test_validate_settings_accepts_default_config():
    assert validate_settings(AppConfig()) == []


def test_validate_settings_rejects_invalid_stream_fields():
    base = AppConfig()
    bad = replace(
        base,
        stream=replace(
            base.stream,
            capture=replace(base.stream.capture, width=3),
            publisher=replace(
                base.stream.publisher, output_url="ftp://servidor/salida"
            ),
            inference=replace(
                base.stream.inference, confidence_threshold=1.5
            ),
        ),
    )
    text = " ".join(validate_settings(bad))
    assert "pares" in text
    assert "rtsp://" in text
    assert "confianza" in text


def test_validate_settings_rejects_out_of_range_fps_and_port():
    # ``CaptureSettings`` valida fps al construirse, así que para ejercitar
    # ``validate_settings`` se usa un namespace con la forma anidada esperada.
    base = AppConfig()
    capture = SimpleNamespace(
        source=base.stream.capture.source,
        width=base.stream.capture.width,
        height=base.stream.capture.height,
        fps=25,
        loop_video=base.stream.capture.loop_video,
        buffer_size=base.stream.capture.buffer_size,
        stable_frames=base.stream.capture.stable_frames,
        read_timeout_seconds=base.stream.capture.read_timeout_seconds,
    )
    config = SimpleNamespace(
        stream=SimpleNamespace(
            capture=capture,
            publisher=base.stream.publisher,
            inference=base.stream.inference,
            storage=base.stream.storage,
            reconnect=base.stream.reconnect,
        ),
        api=SimpleNamespace(
            host=base.api.host,
            port=0,
            lote_endpoint=base.api.lote_endpoint,
        ),
        models=base.models,
    )
    text = " ".join(validate_settings(config))
    assert "FPS" in text
    assert "puerto" in text


def test_validate_settings_detects_catalog_inconsistencies():
    base = AppConfig()
    duplicate_id = ModelEntry("Primera", "a.onnx", "a.names", "repetido")
    second = ModelEntry("Segunda", "b.onnx", "b.names", "repetido")
    bad = replace(
        base,
        models=replace(
            base.models,
            catalog=(duplicate_id, second),
            default_model_id="no-existe",
            npu_model_id="tampoco",
        ),
    )
    text = " ".join(validate_settings(bad))
    assert "repetido" in text
    assert "por defecto" in text
    assert "NPU" in text


def test_validate_settings_detects_empty_catalog_and_missing_paths():
    base = AppConfig()
    bad = replace(
        base,
        models=replace(
            base.models,
            catalog=(
                ModelEntry(label="", model_path="", names_path="", model_id=""),
            ),
        ),
    )
    text = " ".join(validate_settings(bad))
    assert "etiqueta" in text
    assert "ruta de modelo" in text
    assert "ruta de etiquetas" in text
    assert "identificador" in text


def test_settings_dialog_result_config_returns_modified_copy(qt_app):
    snapshot = AppConfig()
    dialog = SettingsDialog(snapshot)

    dialog.width_spin.setValue(1920)
    dialog.confidence_spin.setValue(0.85)
    dialog.pub_queue_spin.setValue(9)
    dialog.reconnect_max_spin.setValue(60.0)
    dialog.horno_edit.setText("HORNO-1")
    dialog.producto_edit.setText("PROD-7")

    new_config = dialog.result_config()

    assert new_config.stream.capture.width == 1920
    assert new_config.stream.publisher.queue_size == 9
    assert new_config.stream.reconnect.max_seconds == 60.0
    assert new_config.stream.inference.confidence_threshold == 0.85
    assert new_config.device.horno_id == "HORNO-1"
    assert new_config.device.producto_id == "PROD-7"
    # La revisión se conserva para que save_config haga el control de conflicto.
    assert new_config.revision == snapshot.revision
    # El snapshot original no se muta.
    assert snapshot.stream.capture.width == 1280
    assert snapshot.stream.publisher.queue_size == 2
    assert snapshot.device.horno_id == ""

    # La copia debe poder serializarse y reparsearse sin perder nada.
    assert AppConfig.from_dict(new_config.to_dict()) == new_config

    dialog.close()


def test_settings_dialog_catalog_editor_adds_valid_entry(qt_app):
    dialog = SettingsDialog(AppConfig())
    initial = len(dialog.result_config().models.catalog)

    dialog.catalog_label_edit.setText("Modelo nuevo")
    dialog.catalog_id_edit.setText("modelo-nuevo")
    dialog.catalog_model_edit.setText("ai_training/models/nuevo.onnx")
    dialog.catalog_names_edit.setText("ai_training/models/nuevo.names")
    dialog._on_catalog_add()

    catalog = dialog.result_config().models.catalog
    assert len(catalog) == initial + 1
    assert any(entry.model_id == "modelo-nuevo" for entry in catalog)
    dialog.close()


def test_settings_dialog_catalog_editor_rejects_duplicate_id(qt_app):
    dialog = SettingsDialog(AppConfig())
    existing_id = dialog._catalog[0].model_id
    duplicate = ModelEntry("Otra", "x.onnx", "x.names", existing_id)

    error = dialog._validate_catalog_entry(duplicate)

    assert error is not None and existing_id in error
    dialog.close()


def test_settings_dialog_accept_blocks_invalid_config(qt_app, monkeypatch):
    import frontend.ui.settings_dialog as settings_module

    warnings = []
    monkeypatch.setattr(
        settings_module.QMessageBox,
        "warning",
        lambda *args, **kwargs: warnings.append(args),
    )
    dialog = SettingsDialog(AppConfig())
    dialog.width_spin.setValue(3)  # impar: yuv420p lo rechaza
    dialog.accept()

    assert warnings
    assert dialog.result() != QDialog.Accepted
    dialog.close()


def test_settings_dialog_wheel_does_not_change_value_controls(qt_app):
    """La rueda no debe editar los controles: se reserva para el scroll."""
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent
    from PySide6.QtWidgets import QAbstractSpinBox, QComboBox

    dialog = SettingsDialog(AppConfig())

    controls = []
    for control_type in (QComboBox, QAbstractSpinBox):
        controls.extend(dialog.findChildren(control_type))
    assert controls, "el diálogo debe contener controles de valor"

    for control in controls:
        is_combo = isinstance(control, QComboBox)
        before = control.currentIndex() if is_combo else control.value()
        event = QWheelEvent(
            QPointF(5, 5),
            QPointF(5, 5),
            QPoint(0, 0),
            QPoint(0, -120),
            Qt.NoButton,
            Qt.NoModifier,
            Qt.NoScrollPhase,
            False,
        )
        QApplication.sendEvent(control, event)

        after = control.currentIndex() if is_combo else control.value()
        assert after == before, f"{type(control).__name__} cambió con la rueda"
        # El filtro ignora el evento para que lo tome el QScrollArea.
        assert not event.isAccepted()

    dialog.close()


def test_sidebar_settings_button_emits_request(qt_app, monkeypatch):
    app, _calls = _build_hermetic_app(monkeypatch)

    # Se desconecta el slot real para no abrir un diálogo modal en el test.
    app.sidebar.settings_requested.disconnect()
    spy = QSignalSpy(app.sidebar.settings_requested)

    assert app.sidebar.settings_btn.toolTip()
    app.sidebar.settings_btn.click()

    assert spy.count() == 1
    app.close()


# ---------------------------------------------------------------------------
# Aplicación del plan de cambios
# ---------------------------------------------------------------------------
def test_apply_config_plan_pipeline_restarts_worker(qt_app, monkeypatch):
    app, _calls = _build_hermetic_app(monkeypatch)
    worker = FakeWorker()
    worker.source_file = "road.mp4"
    app.yolo_thread = worker

    started = []
    app._start_internal_target = lambda *args, **kwargs: started.append((args, kwargs))

    base = AppConfig()
    new_cfg = replace(
        base,
        stream=replace(
            base.stream,
            publisher=replace(
                base.stream.publisher, output_url="http://nuevo:8554/entrada"
            ),
        ),
    )
    plan = {
        "detector": [],
        "pipeline": ["stream.publisher.output_url"],
        "restart_app": [],
        "hot": [],
    }

    app._apply_config_plan(plan, new_cfg)

    assert worker.stop_requested is True
    assert app._shutdown_thread is worker

    # El reinicio ocurre recién cuando el worker anterior termina.
    worker.running = False
    worker.finished.emit()

    assert started, "el pipeline debió reiniciarse tras apagar el worker"
    assert app._shutdown_thread is None
    app.close()


def test_apply_config_plan_detector_uses_catalog_paths_and_threshold(qt_app, monkeypatch):
    app, _calls = _build_hermetic_app(monkeypatch)
    captured = {}

    def fake_change_model(index, **kwargs):
        captured["index"] = index
        captured.update(kwargs)

    app.change_model = fake_change_model

    base = AppConfig()
    new_cfg = replace(
        base,
        stream=replace(
            base.stream,
            inference=replace(base.stream.inference, confidence_threshold=0.9),
        ),
    )
    plan = {
        "detector": ["stream.inference.confidence_threshold"],
        "pipeline": [],
        "restart_app": [],
        "hot": [],
    }

    app._apply_config_plan(plan, new_cfg)

    assert captured["confidence_threshold"] == 0.9
    entry = base.models.model_by_id(base.models.default_model_id)
    assert captured["model_path"] == resolve_project_path(entry.model_path)
    assert captured["names_path"] == resolve_project_path(entry.names_path)
    assert captured["source_override"] is None
    app.close()


def test_apply_config_plan_hot_updates_product(qt_app, monkeypatch):
    app, _calls = _build_hermetic_app(monkeypatch)

    base = AppConfig()
    new_cfg = replace(base, device=replace(base.device, producto_id="PROD-99"))
    plan = {
        "detector": [],
        "pipeline": [],
        "restart_app": [],
        "hot": ["device.producto_id"],
    }

    app._apply_config_plan(plan, new_cfg)

    assert app._producto_id == "PROD-99"
    app.close()


def test_apply_config_plan_restart_app_triggers_full_restart(qt_app, monkeypatch):
    app, _calls = _build_hermetic_app(monkeypatch)
    restarts = []
    app.request_full_restart = lambda: restarts.append(True)
    monkeypatch.setattr(app, "_confirm_restart", lambda: True)

    base = AppConfig()
    new_cfg = replace(
        base, models=replace(base.models, default_model_id="tostadas-v1")
    )
    plan = {
        "detector": [],
        "pipeline": [],
        "restart_app": ["models.default_model_id"],
        "hot": [],
    }

    app._apply_config_plan(plan, new_cfg)

    assert restarts == [True]
    app.close()


# ---------------------------------------------------------------------------
# Flujo del editor de configuración (H2, M1, L1b, M2)
# ---------------------------------------------------------------------------
class _AcceptedSettingsDialog:
    """Diálogo falso que acepta sin mostrar UI."""

    def __init__(self, snapshot, parent=None):
        self._snapshot = snapshot

    def exec(self):
        return QDialog.Accepted

    def result_config(self):
        return self._snapshot


class _ReturnsConfigDialog:
    """Diálogo falso que acepta devolviendo una configuración concreta."""

    def __init__(self, config, parent=None):
        self._config = config

    def exec(self):
        return QDialog.Accepted

    def result_config(self):
        return self._config


def test_open_settings_stale_revision_warns_and_does_not_apply(qt_app, monkeypatch):
    app, _calls = _build_hermetic_app(monkeypatch)

    warnings = []
    monkeypatch.setattr(
        frontend_app.QMessageBox,
        "warning",
        lambda *args, **kwargs: warnings.append(args),
    )

    applied = []
    notified = []
    app._apply_config_plan = lambda plan, cfg: applied.append((plan, cfg))
    app._notify_backend_reload_async = lambda *args, **kwargs: notified.append(True)
    monkeypatch.setattr(frontend_app, "load", lambda: AppConfig())
    monkeypatch.setattr(frontend_app, "SettingsDialog", _AcceptedSettingsDialog)

    def stale_save(config, *, expected_revision=None, **kwargs):
        raise RevisionConflictError("revisión vieja")

    monkeypatch.setattr(frontend_app, "save_config", stale_save)

    app.open_settings_dialog()

    assert warnings, "debía avisarse del conflicto de revisión"
    assert applied == []
    assert notified == []
    app.close()


def test_open_settings_guard_blocks_while_worker_transitions(qt_app, monkeypatch):
    app, _calls = _build_hermetic_app(monkeypatch)
    app._shutdown_thread = FakeWorker()

    messages = []
    monkeypatch.setattr(
        frontend_app.QMessageBox,
        "information",
        lambda *args, **kwargs: messages.append(args),
    )

    opened = []
    saved = []
    monkeypatch.setattr(frontend_app, "SettingsDialog", _AcceptedSettingsDialog)
    monkeypatch.setattr(frontend_app, "load", lambda: opened.append(True) or AppConfig())
    monkeypatch.setattr(
        frontend_app,
        "save_config",
        lambda *args, **kwargs: saved.append(True) or AppConfig(),
    )

    app.open_settings_dialog()

    assert messages, "debía avisarse que hay un cambio en curso"
    assert opened == []
    assert saved == []
    app.close()


def test_open_settings_notifies_previous_backend_host_port(qt_app, monkeypatch):
    """H2: el reload debe avisar al backend que está corriendo (snapshot), no al
    host/puerto recién guardado, donde todavía no escucha nadie."""
    app, _calls = _build_hermetic_app(monkeypatch)

    base = AppConfig()
    snapshot = replace(base, api=replace(base.api, host="127.0.0.1", port=8000))
    new_cfg = replace(snapshot, api=replace(snapshot.api, host="10.0.0.5", port=9000))

    monkeypatch.setattr(frontend_app, "load", lambda: snapshot)
    monkeypatch.setattr(
        frontend_app,
        "SettingsDialog",
        lambda snapshot, parent=None: _ReturnsConfigDialog(new_cfg),
    )
    monkeypatch.setattr(
        frontend_app,
        "save_config",
        lambda config, *, expected_revision=None, **kwargs: config,
    )

    notified = []
    app._notify_backend_reload_async = (
        lambda host=None, port=None: notified.append((host, port))
    )
    applied = []
    app._apply_config_plan = lambda plan, cfg: applied.append((plan, cfg))

    app.open_settings_dialog()

    assert notified == [("127.0.0.1", 8000)]
    assert applied, "el plan debió aplicarse tras guardar"
    app.close()


def test_handle_lote_completed_uses_current_lote_endpoint(qt_app, monkeypatch):
    """H2: el endpoint de lotes se relee de config.json en cada lote."""
    app, _calls = _build_hermetic_app(monkeypatch)
    app.add_alert_log = lambda *args, **kwargs: None
    dynamic_endpoint = "http://127.0.0.1:9100/api/lotes/finalizar"
    cfg = replace(
        AppConfig(), api=replace(AppConfig().api, lote_endpoint=dynamic_endpoint)
    )
    monkeypatch.setattr(frontend_app, "load", lambda: cfg)

    app.http_client = _FakeHttpClient(success=True)
    app.handle_lote_completed(
        {"totalUnidades": 1, "correctos": 1, "quemados": 0, "crudas": 0}
    )
    _drain_lote_posts(app, qt_app)

    assert app.http_client.calls[0][0] == dynamic_endpoint
    app.close()


def test_apply_hot_config_propagates_product_to_live_worker(qt_app, monkeypatch):
    """M1: el producto nuevo llega al worker en marcha, que lo copió al nacer."""
    app, _calls = _build_hermetic_app(monkeypatch)
    worker = FakeWorker()
    worker.producto_id = "viejo"
    app.yolo_thread = worker

    base = AppConfig()
    new_cfg = replace(base, device=replace(base.device, producto_id="PROD-99"))
    app._apply_hot_config({"hot": ["device.producto_id"]}, new_cfg)

    assert app._producto_id == "PROD-99"
    assert worker.producto_id == "PROD-99"

    # Producto vacío: el worker cae al producto por defecto del payload.
    empty_cfg = replace(base, device=replace(base.device, producto_id=""))
    app._apply_hot_config({"hot": ["device.producto_id"]}, empty_cfg)

    assert app._producto_id is None
    assert worker.producto_id == DEFAULT_PRODUCT_ID
    worker.running = False
    app.close()


def test_app_startup_detector_uses_config_paths_and_threshold(qt_app, monkeypatch):
    """M2a: el detector arranca con umbral y rutas de config.json."""
    base = AppConfig()
    cfg = replace(
        base,
        stream=replace(
            base.stream,
            inference=replace(
                base.stream.inference,
                model_path="ai_training/models/tostadas_v2.onnx",
                labels_path="ai_training/models/tostadas_v2.names",
                confidence_threshold=0.42,
            ),
        ),
    )

    app, _calls = _build_hermetic_app(monkeypatch, config=cfg)

    detector = StubDetector.instances[-1]
    assert detector.confidence_threshold == 0.42
    assert detector.model_path == resolve_project_path(
        "ai_training/models/tostadas_v2.onnx"
    )
    assert detector.names_path == resolve_project_path(
        "ai_training/models/tostadas_v2.names"
    )
    # El selector debe reflejar el modelo activo de config.json, no el de la
    # plataforma: si no, el operador vería un modelo distinto del que corre.
    assert app.sidebar.model_selector.currentText() == "YOLOv11 Tostadas V2 (Custom)"
    app.close()


def test_sidebar_set_model_index_does_not_emit_model_changed(qt_app, monkeypatch):
    """Alinear el selector al arrancar no debe disparar un cambio de modelo."""
    app, _calls = _build_hermetic_app(monkeypatch)

    app.sidebar.model_selector.setCurrentIndex(0)
    spy = QSignalSpy(app.sidebar.model_changed)
    app.sidebar.set_model_index(2)

    assert app.sidebar.model_selector.currentIndex() == 2
    assert spy.count() == 0
    app.close()


def test_apply_config_plan_detector_prefers_config_paths(qt_app, monkeypatch):
    """M2b: si config.json define rutas de inferencia, el plan las usa en vez
    del catálogo."""
    app, _calls = _build_hermetic_app(monkeypatch)
    captured = {}
    app.change_model = lambda index, **kwargs: captured.update(index=index, **kwargs)

    base = AppConfig()
    new_cfg = replace(
        base,
        stream=replace(
            base.stream,
            inference=replace(
                base.stream.inference,
                model_path="ai_training/models/tostadas_v2.onnx",
                labels_path="ai_training/models/tostadas_v2.names",
                confidence_threshold=0.8,
            ),
        ),
    )
    plan = {
        "detector": [
            "stream.inference.model_path",
            "stream.inference.labels_path",
        ],
        "pipeline": [],
        "restart_app": [],
        "hot": [],
    }

    app._apply_config_plan(plan, new_cfg)

    assert captured["model_path"] == resolve_project_path(
        "ai_training/models/tostadas_v2.onnx"
    )
    assert captured["names_path"] == resolve_project_path(
        "ai_training/models/tostadas_v2.names"
    )
    assert captured["confidence_threshold"] == 0.8
    app.close()


def test_open_settings_invalid_config_warns_and_does_not_open(qt_app, monkeypatch):
    """L1b: un config.json ilegible no debe lanzar dentro del slot Qt."""
    app, _calls = _build_hermetic_app(monkeypatch)

    warnings = []
    monkeypatch.setattr(
        frontend_app.QMessageBox,
        "warning",
        lambda *args, **kwargs: warnings.append(args),
    )

    def boom():
        raise ConfigError("JSON inválido")

    monkeypatch.setattr(frontend_app, "load", boom)
    opened = []
    monkeypatch.setattr(
        frontend_app, "SettingsDialog", lambda *args, **kwargs: opened.append(True)
    )

    app.open_settings_dialog()

    assert warnings, "debía avisarse del error de lectura"
    assert opened == []
    app.close()
