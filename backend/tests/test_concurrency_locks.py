"""Serialización de estado compartido bajo concurrencia.

Dos singletons del backend se invocan desde el threadpool de FastAPI sobre el
mismo objeto. Estos tests fuerzan solapamiento real entre hilos con un
``threading.Event`` y comprueban que el lock impide que la inferencia
(``YoloDetector``) y las mutaciones del tracker (``ToastTracker``) se pisen.
"""

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np

from backend.domain.entities.detection import DetectionResult
from backend.infrastructure.ai.yolo_detector import YoloDetector
from backend.use_cases.toast_tracker import ToastTracker

BBOX = (10, 10, 20, 20)


class _InstrumentedNet:
    """Red falsa de OpenCV DNN que detecta cuántos hilos entran a la vez."""

    def __init__(self, hold_seconds: float = 0.01):
        self._guard = threading.Lock()
        self._active = 0
        self._hold_seconds = hold_seconds
        self.max_concurrent = 0

    def _enter(self) -> None:
        with self._guard:
            self._active += 1
            self.max_concurrent = max(self.max_concurrent, self._active)

    def _exit(self) -> None:
        with self._guard:
            self._active -= 1

    def setInput(self, blob) -> None:
        self._enter()

    def forward(self):
        # Mantiene el hilo "dentro" de la red para dar ventana al solapamiento
        # si no existiera serialización.
        time.sleep(self._hold_seconds)
        self._exit()
        return np.ones((1, 5, 1), dtype=np.float32)


def _run_concurrently(worker, count):
    """Lanza ``count`` hilos que esperan un evento común y recoge sus futuros.

    ``future.result()`` propaga cualquier excepción de un hilo al test.
    """
    start = threading.Event()

    def runner():
        start.wait()
        worker()

    executor = ThreadPoolExecutor(max_workers=count)
    futures = [executor.submit(runner) for _ in range(count)]
    start.set()
    executor.shutdown(wait=True)
    return futures


def test_detect_frame_serializes_inference(monkeypatch):
    net = _InstrumentedNet()
    monkeypatch.setattr("cv2.dnn.readNet", lambda path: net)
    monkeypatch.setattr(
        "cv2.dnn.blobFromImage",
        lambda *args, **kwargs: np.zeros((1, 3, 640, 640), dtype=np.float32),
    )

    detector = YoloDetector(model_path=__file__)
    frame = np.zeros((64, 64, 3), dtype=np.uint8)

    results: list = []

    def worker():
        results.append(detector.detect_frame(frame))

    n_threads = 8
    futures = _run_concurrently(worker, n_threads)
    for future in futures:
        future.result(timeout=10)

    assert len(results) == n_threads
    # El lock debe garantizar que nunca hay dos inferencias simultáneas.
    assert net.max_concurrent == 1


class _BlockingNet:
    """Red falsa que deja la inferencia bloqueada hasta que el test la libera."""

    def __init__(self, entered: threading.Event, release: threading.Event):
        self._entered = entered
        self._release = release

    def setInput(self, blob) -> None:
        self._entered.set()

    def forward(self):
        self._release.wait(5)
        return np.ones((1, 5, 1), dtype=np.float32)


def test_release_hailo_waits_for_in_flight_inference(monkeypatch):
    """El shutdown no puede desmontar la NPU con una inferencia en vuelo."""
    entered = threading.Event()
    release = threading.Event()
    monkeypatch.setattr("cv2.dnn.readNet", lambda path: _BlockingNet(entered, release))
    monkeypatch.setattr(
        "cv2.dnn.blobFromImage",
        lambda *args, **kwargs: np.zeros((1, 3, 640, 640), dtype=np.float32),
    )

    detector = YoloDetector(model_path=__file__)
    frame = np.zeros((64, 64, 3), dtype=np.uint8)

    detect_done = threading.Event()
    release_done = threading.Event()

    def run_detect():
        detector.detect_frame(frame)
        detect_done.set()

    def run_release():
        detector.release_hailo()
        release_done.set()

    detect_thread = threading.Thread(target=run_detect, daemon=True)
    detect_thread.start()
    assert entered.wait(5), "la inferencia no llegó a la red"

    release_thread = threading.Thread(target=run_release, daemon=True)
    release_thread.start()
    # Con la inferencia en vuelo, release_hailo debe esperar el lock: ni termina
    # ni marca el detector como liberado.
    assert not release_done.wait(0.2)
    assert detector._released is False

    release.set()
    assert detect_done.wait(5)
    assert release_done.wait(5)
    # La liberación ocurrió después de la inferencia, no durante.
    assert detector._released is True
    detect_thread.join(5)
    release_thread.join(5)


def test_toast_tracker_update_serializes_state(monkeypatch):
    tracker = ToastTracker()

    # Instrumenta el final de ``update`` para medir cuántos hilos lo ejecutan a
    # la vez; el lock debe mantenerlo en 1.
    original_collect = tracker._collect_results
    guard = threading.Lock()
    active = {"count": 0, "max": 0}

    def instrumented_collect():
        with guard:
            active["count"] += 1
            active["max"] = max(active["max"], active["count"])
        time.sleep(0.005)
        try:
            return original_collect()
        finally:
            with guard:
                active["count"] -= 1

    monkeypatch.setattr(tracker, "_collect_results", instrumented_collect)

    detections = [DetectionResult("TCOK", 0.9, BBOX)]

    def worker():
        for _ in range(20):
            tracker.update(detections)

    n_threads = 8
    futures = _run_concurrently(worker, n_threads)
    for future in futures:
        future.result(timeout=10)

    assert active["max"] == 1
    # Estado final consistente: una sola tostada (id 1) y contador coherente.
    assert set(tracker.tracked_toasts.keys()) == {1}
    assert tracker.next_id == 2
    assert tracker.tracked_toasts[1].bbox == BBOX
