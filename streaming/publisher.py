"""Non-blocking FFmpeg RTSP publisher and a test double."""

from __future__ import annotations

import os
import queue
import select
import subprocess
import threading
import time
from typing import Any

from .config import StreamConfig


class FFmpegPublisher:
    """Publish frames through a bounded queue and a non-blocking stdin pipe.

    FFmpeg's stderr is inherited (``stderr=None``), so systemd/journald keeps
    encoder and RTSP diagnostics. The capture loop only calls ``put_nowait``;
    all pipe I/O happens in the worker and has a finite progress timeout.
    """

    def __init__(self, config: StreamConfig, popen: Any = subprocess.Popen, monotonic=time.monotonic):
        self.config = config
        self._popen = popen
        self._monotonic = monotonic
        self._process = None
        self._queue: queue.Queue[Any] = queue.Queue(maxsize=config.publisher_queue_size)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._next_start_at = 0.0
        self._restart_backoff = config.reconnect_initial_seconds
        self._process_started_at: float | None = None
        self._last_progress: float | None = None
        self._restart_count = 0
        self._last_error: str | None = None

    def _command(self) -> list[str]:
        gop = self.config.fps * self.config.gop_seconds
        return [
            self.config.ffmpeg_executable,
            "-hide_banner", "-loglevel", "warning",
            "-f", "rawvideo", "-pix_fmt", "bgr24",
            "-video_size", f"{self.config.width}x{self.config.height}",
            "-framerate", str(self.config.fps), "-i", "-",
            "-an", "-c:v", self.config.encoder, "-pix_fmt", self.config.pixel_format,
            "-g", str(gop), "-bf", str(self.config.b_frames),
            "-b:v", self.config.bitrate,
            "-f", "rtsp", "-rtsp_transport", "tcp", self.config.output_url,
        ]

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._worker, name="ffmpeg-publisher", daemon=True)
        self._thread.start()

    def _schedule_restart(self, reason: str) -> None:
        now = self._monotonic()
        self._last_error = reason
        self._restart_count += 1
        self._next_start_at = now + self._restart_backoff
        self._restart_backoff = min(
            max(self._restart_backoff * 2, self.config.reconnect_initial_seconds),
            self.config.reconnect_max_seconds,
        )

    def _start_process(self) -> bool:
        if self._stop.is_set():
            return False
        process = None
        try:
            process = self._popen(
                self._command(),
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=None,  # inherit stderr so journald retains FFmpeg diagnostics
            )
            if process.stdin is None:
                raise OSError("FFmpeg stdin no disponible")
            os.set_blocking(process.stdin.fileno(), False)
            with self._lock:
                self._process = process
                self._process_started_at = self._monotonic()
            return True
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            self._last_error = str(exc)
            try:
                if process is not None:
                    process.terminate()
            except (AttributeError, OSError):
                pass
            self._schedule_restart(str(exc))
            return False

    def _close_process(self) -> None:
        with self._lock:
            process, self._process = self._process, None
            self._process_started_at = None
        if process is None:
            return
        try:
            if process.stdin is not None:
                process.stdin.close()
        except (AttributeError, OSError, ValueError):
            pass
        try:
            process.terminate()
            process.wait(timeout=1)
        except (AttributeError, OSError, subprocess.TimeoutExpired):
            try:
                process.kill()
            except (AttributeError, OSError):
                pass

    def _write_frame_nonblocking(self, process, frame) -> bool:
        try:
            data = memoryview(frame.tobytes())
            fd = process.stdin.fileno()
        except (AttributeError, OSError, ValueError):
            return False
        sent = 0
        deadline = self._monotonic() + self.config.publisher_write_timeout
        while sent < len(data):
            if self._stop.is_set():
                return False
            remaining = deadline - self._monotonic()
            if remaining <= 0:
                return False
            try:
                _, writable, _ = select.select([], [fd], [], min(0.1, remaining))
            except (OSError, ValueError):
                return False
            if not writable:
                continue
            try:
                written = os.write(fd, data[sent:])
            except BlockingIOError:
                continue
            except (BrokenPipeError, OSError, ValueError):
                return False
            if written <= 0:
                return False
            sent += written
        return True

    def _worker(self) -> None:
        try:
            while not self._stop.is_set():
                if self._process is None:
                    delay = self._next_start_at - self._monotonic()
                    if delay > 0:
                        self._stop.wait(min(delay, 0.2))
                        continue
                    if not self._start_process():
                        continue
                process = self._process
                if process is None:
                    continue
                try:
                    if getattr(process, "poll", lambda: None)() is not None:
                        self._close_process()
                        self._schedule_restart("FFmpeg terminó inesperadamente")
                        continue
                except (OSError, ValueError):
                    self._close_process()
                    self._schedule_restart("no se pudo consultar el estado de FFmpeg")
                    continue
                try:
                    frame = self._queue.get(timeout=0.1)
                except queue.Empty:
                    continue
                if not self._write_frame_nonblocking(process, frame):
                    self._close_process()
                    self._schedule_restart("FFmpeg sin progreso o pipe rota")
                    continue
                now = self._monotonic()
                self._last_progress = now
                if self._process_started_at is not None and now - self._process_started_at >= self.config.publisher_stable_seconds:
                    self._restart_backoff = self.config.reconnect_initial_seconds
        finally:
            self._close_process()

    def publish(self, frame) -> bool:
        if self._stop.is_set() or self._thread is None or not self._thread.is_alive():
            self.start()
        try:
            self._queue.put_nowait(frame)
            return True
        except queue.Full:
            try:
                self._queue.get_nowait()
                self._queue.put_nowait(frame)
            except queue.Empty:
                return False
            return True

    def health(self) -> dict[str, Any]:
        with self._lock:
            process = self._process
            process_alive = process is not None and getattr(process, "poll", lambda: None)() is None
        worker_alive = self._thread is not None and self._thread.is_alive()
        if not worker_alive:
            state = "stopped"
        elif process_alive:
            state = "running"
        else:
            state = "reconnecting"
        return {
            "state": state,
            "process_alive": process_alive,
            "worker_alive": worker_alive,
            "queue_depth": self._queue.qsize(),
            "last_progress": self._last_progress,
            "restart_count": self._restart_count,
            "last_error": self._last_error,
        }

    def is_running(self) -> bool:
        return bool(self.health()["process_alive"] and self.health()["worker_alive"])

    def stop(self) -> None:
        self._stop.set()
        # Closing stdin/process makes stop independent of a producer or a full pipe.
        self._close_process()
        if self._thread is not None:
            self._thread.join(timeout=max(2.0, self.config.publisher_write_timeout + 1.0))
            self._thread = None
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break

    close = stop


class FakePublisher:
    """In-memory publisher for local runs and unit tests; never invokes FFmpeg."""

    def __init__(self):
        self.frames = []
        self.started = False

    def start(self) -> None:
        self.started = True

    def publish(self, frame) -> bool:
        self.frames.append(frame.copy())
        return True

    def health(self) -> dict[str, Any]:
        return {"state": "running" if self.started else "stopped", "process_alive": self.started, "worker_alive": self.started}

    def stop(self) -> None:
        self.started = False

    close = stop
