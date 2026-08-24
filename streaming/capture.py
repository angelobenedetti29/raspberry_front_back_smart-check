"""OpenCV capture with retry/backoff and deterministic resource cleanup."""

from __future__ import annotations

import time
import queue
import threading
from typing import Any

import cv2

from .config import StreamConfig


class CaptureWatchdogError(RuntimeError):
    """Raised when a native capture read stops producing frames."""


class OpenCVFrameCapture:
    def __init__(self, config: StreamConfig, cv2_module: Any = cv2, sleep=time.sleep):
        self.config = config
        self.cv2 = cv2_module
        self._sleep = sleep
        self._capture = None
        self._backoff = config.reconnect_initial_seconds
        self._next_retry = 0.0
        self._released = False
        self._is_file = not self._is_camera_source(config.source)
        self._exhausted = False
        self._stable_frames = 0
        self.effective_properties: dict[str, float | int] = {}
        self._read_queue: queue.Queue[tuple[str, Any]] = queue.Queue(maxsize=1)
        self._reader_stop = threading.Event()
        self._reader_thread: threading.Thread | None = None
        self._last_heartbeat: float | None = None
        self._watchdog_tripped = False

    @staticmethod
    def _is_camera_source(source: str) -> bool:
        try:
            int(str(source))
            return True
        except (TypeError, ValueError):
            return False

    @property
    def exhausted(self) -> bool:
        return self._exhausted

    def _source_value(self) -> int | str:
        return int(self.config.source) if self._is_camera_source(self.config.source) else self.config.source

    def _schedule_retry(self) -> None:
        self._next_retry = time.monotonic() + self._backoff
        self._backoff = min(max(self._backoff * 2, self.config.reconnect_initial_seconds), self.config.reconnect_max_seconds)

    def _configure_device(self, capture) -> None:
        if not self._is_camera_source(self.config.source):
            return
        properties = (
            ("width", getattr(self.cv2, "CAP_PROP_FRAME_WIDTH", None), self.config.width),
            ("height", getattr(self.cv2, "CAP_PROP_FRAME_HEIGHT", None), self.config.height),
            ("fps", getattr(self.cv2, "CAP_PROP_FPS", None), self.config.fps),
            ("buffer_size", getattr(self.cv2, "CAP_PROP_BUFFERSIZE", None), self.config.capture_buffer_size),
        )
        for name, property_id, requested in properties:
            if property_id is None:
                continue
            try:
                capture.set(property_id, requested)
            except Exception:
                # Drivers differ in which properties they accept. The effective
                # values below remain the source of truth for observability.
                pass
            try:
                self.effective_properties[name] = capture.get(property_id)
            except Exception:
                self.effective_properties[name] = requested

    def _open(self) -> bool:
        if self._released or self._exhausted:
            return False
        now = time.monotonic()
        if now < self._next_retry:
            self._sleep(self._next_retry - now)
        capture = None
        try:
            capture = self.cv2.VideoCapture(self._source_value())
            if not capture.isOpened():
                capture.release()
                self._schedule_retry()
                return False
            self._configure_device(capture)
            self._capture = capture
            self._next_retry = 0.0
            self._start_reader(capture)
            return True
        except Exception:
            if capture is not None:
                try:
                    capture.release()
                except Exception:
                    pass
            self._schedule_retry()
            return False

    def _start_reader(self, capture) -> None:
        while True:
            try:
                self._read_queue.get_nowait()
            except queue.Empty:
                break
        self._reader_stop.clear()
        self._last_heartbeat = time.monotonic()
        self._reader_thread = threading.Thread(
            target=self._reader_loop,
            args=(capture,),
            name="opencv-capture-reader",
            daemon=True,
        )
        self._reader_thread.start()

    def _offer_read_result(self, result: tuple[str, Any]) -> None:
        if self._reader_stop.is_set():
            return
        try:
            self._read_queue.get_nowait()
        except queue.Empty:
            pass
        try:
            self._read_queue.put_nowait(result)
        except queue.Full:
            # The consumer is behind; keep only the newest frame/result.
            pass

    def _reader_loop(self, capture) -> None:
        while not self._reader_stop.is_set():
            try:
                ok, frame = capture.read()
            except Exception as exc:
                self._last_heartbeat = time.monotonic()
                self._offer_read_result(("exception", exc))
                return
            self._last_heartbeat = time.monotonic()
            if not ok or frame is None:
                self._offer_read_result(("failure", None))
                return
            self._offer_read_result(("frame", frame))

    def read(self):
        """Return the next BGR frame, or ``None`` during a reconnect attempt."""
        if self._released or self._exhausted:
            return None
        if self._watchdog_tripped:
            raise CaptureWatchdogError("watchdog de captura ya activado; se requiere reinicio del proceso")
        if self._capture is None and not self._open():
            return None
        capture = self._capture
        if capture is None:
            return None
        try:
            result_type, result = self._read_queue.get(timeout=self.config.capture_read_timeout_seconds)
        except queue.Empty as exc:
            self._watchdog_tripped = True
            self._reader_stop.set()
            heartbeat_age = (
                "desconocida" if self._last_heartbeat is None
                else f"{time.monotonic() - self._last_heartbeat:.2f}s"
            )
            raise CaptureWatchdogError(
                f"sin frames de {self.config.source} durante {self.config.capture_read_timeout_seconds:.2f}s "
                f"(heartbeat: {heartbeat_age}); terminando para que systemd reinicie"
            ) from exc
        if result_type == "exception":
            self._handle_read_failure(capture)
            return None
        if result_type == "failure":
            self._handle_read_failure(capture)
            if self._is_file and not self.config.loop_video:
                self._exhausted = True
            return None
        frame = result
        self._stable_frames += 1
        if self._stable_frames >= self.config.capture_stable_frames:
            self._backoff = self.config.reconnect_initial_seconds
        if frame.shape[1] != self.config.width or frame.shape[0] != self.config.height:
            frame = self.cv2.resize(frame, (self.config.width, self.config.height), interpolation=self.cv2.INTER_AREA)
        return frame  # OpenCV delivers BGR and it is kept as BGR throughout the pipeline.

    def _handle_read_failure(self, capture) -> None:
        self._reader_stop.set()
        try:
            capture.release()
        except Exception:
            pass
        self._capture = None
        self._stable_frames = 0
        self._schedule_retry()

    def release(self) -> None:
        self._released = True
        self._reader_stop.set()
        if self._watchdog_tripped:
            # A native read is still blocked in the daemon reader. It cannot be
            # cancelled safely; process exit is the resource boundary.
            self._capture = None
            return
        if self._capture is not None:
            try:
                self._capture.release()
            except Exception:
                pass
            self._capture = None

    close = release
