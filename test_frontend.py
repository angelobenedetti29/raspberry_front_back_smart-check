import os
import sys
import math
import subprocess
from dataclasses import dataclass
from types import SimpleNamespace

import numpy as np
import pytest
from PySide6.QtCore import QSize
from PySide6.QtGui import QImage, QResizeEvent
from PySide6.QtWidgets import QApplication

# Asegurar que el path del proyecto esté en el PYTHONPATH.
project_root = os.path.dirname(os.path.abspath(__file__))
if project_root not in sys.path:
    sys.path.append(project_root)

from frontend.app import FactoryControlApp  # noqa: E402
from frontend.config import (  # noqa: E402
    LOCAL_LOTE_ENDPOINT,
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
import frontend.app as frontend_app  # noqa: E402
from backend.domain.entities.sensor_readings import SensorReadings  # noqa: E402
from streaming.config import StreamConfig  # noqa: E402


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
        stream_config=StreamConfig(width=4, height=4, fps=20),
    )
    thread.seen_toasts.update({1: "ok", 2: "ok", 3: "burnt", 4: "ok", 5: "burnt"})
    thread.sensor_samples.extend([
        SensorReadings(220.0, 315.0, 218.0, 312.0, 1.05),
        SensorReadings(220.0, 315.0, 218.0, 312.0, 1.15),
    ])
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
        stream_config=StreamConfig(width=4, height=4, fps=20),
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
        validate_stream_config(StreamConfig(width=3, height=4, fps=20))


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
    def invalid_config():
        raise ValueError("STREAMING_WIDTH inválido")

    monkeypatch.setattr(frontend_app.StreamConfig, "from_env", staticmethod(invalid_config))
    messages = []
    app = SimpleNamespace()
    app._show_streaming_disabled = lambda error, preview_only: messages.append(
        (str(error), preview_only)
    )
    config, publisher_factory = FactoryControlApp._stream_setup(app, True)

    assert config.width % 2 == 0 and config.height % 2 == 0
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
    assert displayed and displayed[0].width() == config.width
    assert isinstance(thread.publisher, PreviewOnlyPublisher)
    assert not hasattr(thread.publisher, "frames")


def test_invalid_config_does_not_stop_or_replace_active_worker(monkeypatch):
    def invalid_config():
        raise ValueError("STREAMING_HEIGHT impar")

    monkeypatch.setattr(frontend_app.StreamConfig, "from_env", staticmethod(invalid_config))
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

    def __init__(self, model_path=None, names_path=None):
        self.model_path = model_path
        self.names_path = names_path
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


def _run_thread_until_done(qt_app, detections, sensor_provider, frame_count=2):
    capture = _OneShotCapture(
        [np.zeros((2, 2, 3), dtype=np.uint8) for _ in range(frame_count)]
    )
    displayed = []
    received = []
    thread = YOLODetectionThread(
        "0",
        MockUseCase(detections),
        stream_config=StreamConfig(width=4, height=4, fps=20),
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
    assert thread.sensor_samples == [sample1, sample2]
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
    assert thread.sensor_samples == []
    assert received, "the worker must still emit a lote after the loop completes"
    payload = received[0]
    assert not any(
        isinstance(value, float) and math.isnan(value) for value in payload.values()
    )
    # Fallback sensor values are used when no samples could be read.
    assert payload["tempHorno1"] == 220.0


# ---------------------------------------------------------------------------
# Phase 3: panel-extraction contracts
# ---------------------------------------------------------------------------
def _build_hermetic_app(monkeypatch):
    """Construct the app with hardware-free stubs and a recording navigator."""
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


def test_handle_lote_completed_success_and_failure(qt_app, monkeypatch):
    app, _calls = _build_hermetic_app(monkeypatch)
    logged = []
    app.add_alert_log = lambda message, tone="danger": logged.append((message, tone))

    payload = {"totalUnidades": 3, "correctos": 2, "quemados": 1, "crudas": 0}

    app.http_client = _FakeHttpClient(success=True)
    app.handle_lote_completed(payload)

    assert app.http_client.calls
    assert app.http_client.calls[0][0] == LOCAL_LOTE_ENDPOINT
    assert app.http_client.calls[0][1] == payload
    assert logged and "LOTE REGISTRADO" in logged[-1][0]
    assert logged[-1][1] == "info"

    logged.clear()
    app.http_client = _FakeHttpClient(success=False, last_error="servidor caido")
    app.handle_lote_completed(payload)

    assert logged and "Error al enviar lote" in logged[-1][0]
    assert "servidor caido" in logged[-1][0]

    app.close()
