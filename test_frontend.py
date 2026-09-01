import os
import sys
from dataclasses import dataclass
from types import SimpleNamespace

import numpy as np
import pytest
from PySide6.QtCore import QCoreApplication

# Asegurar que el path del proyecto esté en el PYTHONPATH.
project_root = os.path.dirname(os.path.abspath(__file__))
if project_root not in sys.path:
    sys.path.append(project_root)

from frontend.main.main import (  # noqa: E402
    FactoryControlApp,
    PreviewOnlyPublisher,
    YOLODetectionThread,
    validate_stream_config,
)
import frontend.main.main as frontend_main  # noqa: E402
from streaming.config import StreamConfig  # noqa: E402


@pytest.fixture(scope="session")
def qt_app():
    return QCoreApplication.instance() or QCoreApplication([])


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
    thread.temperatures_horno1.extend([219.5, 220.5])
    thread.velocidades_cinta.extend([1.05, 1.15])
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
        self.iot_status_changed_signal = FakeSignal()
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

    monkeypatch.setattr(frontend_main.StreamConfig, "from_env", staticmethod(invalid_config))
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

    monkeypatch.setattr(frontend_main.StreamConfig, "from_env", staticmethod(invalid_config))
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
