"""Bounded asynchronous JSONL persistence with rotation and disk isolation."""

from __future__ import annotations

import json
import logging
import queue
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, cast


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class _Record:
    timestamp: str
    detections: list[Mapping[str, Any]]
    metadata: Mapping[str, Any]


class JsonlDetectionStore:
    """Write records away from the frame loop.

    Detection records have their own bounded queue and are preferred over the
    sampled no-detection queue. A disk failure is logged and retried without
    being raised to the capture/publish loop.
    """

    def __init__(
        self,
        path: str | Path,
        queue_size: int = 128,
        max_bytes: int = 10 * 1024 * 1024,
        max_files: int = 5,
        sample_no_detection_every: int = 10,
        file_opener: Callable[..., Any] = open,
    ):
        if queue_size < 1 or max_bytes < 1 or max_files < 1 or sample_no_detection_every < 0:
            raise ValueError("configuración de persistencia inválida")
        self.path = Path(path)
        self.queue_size = queue_size
        self.max_bytes = max_bytes
        self.max_files = max_files
        self.sample_no_detection_every = sample_no_detection_every
        self._file_opener = file_opener
        self._detections: queue.Queue[_Record] = queue.Queue(maxsize=queue_size)
        self._samples: queue.Queue[_Record] = queue.Queue(maxsize=queue_size)
        self._stop = threading.Event()
        self._closed = False
        self._sample_counter = 0
        self._file = None
        self._file_bytes = 0
        self.dropped_detections = 0
        self.dropped_samples = 0
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            LOGGER.error("No se pudo crear el directorio JSONL %s: %s", self.path.parent, exc)
        self._worker_thread = threading.Thread(target=self._worker, name="detection-persistence", daemon=True)
        self._worker_thread.start()

    def append(self, timestamp: str, detections: Iterable[Mapping[str, Any]], metadata: Mapping[str, Any] | None = None) -> bool:
        if self._closed:
            return False
        detection_list = list(detections)
        record = _Record(timestamp, detection_list, dict(metadata or {}))
        target = self._detections if detection_list else self._samples
        if not detection_list:
            self._sample_counter += 1
            if self.sample_no_detection_every == 0 or self._sample_counter % self.sample_no_detection_every:
                return True
        try:
            target.put_nowait(record)
            return True
        except queue.Full:
            if detection_list:
                self.dropped_detections += 1
            else:
                self.dropped_samples += 1
            LOGGER.warning("Cola JSONL llena; se descarta un registro%s", " con detecciones" if detection_list else "")
            return False

    def _ensure_file(self) -> bool:
        if self._file is not None and not self._file.closed:
            return True
        try:
            self._file = self._file_opener(self.path, "a", encoding="utf-8")
            self._file_bytes = self.path.stat().st_size if self.path.exists() else 0
            return True
        except (OSError, ValueError) as exc:
            self._file = None
            LOGGER.error("No se pudo abrir persistencia JSONL %s: %s", self.path, exc)
            return False

    def _rotate(self, incoming_bytes: int) -> bool:
        if not self._ensure_file():
            return False
        if self._file_bytes + incoming_bytes <= self.max_bytes:
            return True
        try:
            file = self._file
            if file is not None:
                file.close()
            self._file = None
            if self.max_files == 1:
                if self.path.exists():
                    self.path.unlink()
            else:
                for index in range(self.max_files - 1, 0, -1):
                    source = Path(f"{self.path}.{index - 1}") if index > 1 else self.path
                    destination = Path(f"{self.path}.{index}")
                    if source.exists():
                        if destination.exists():
                            destination.unlink()
                        source.rename(destination)
            self._file_bytes = 0
            return self._ensure_file()
        except (OSError, ValueError) as exc:
            self._file = None
            LOGGER.error("No se pudo rotar persistencia JSONL %s: %s", self.path, exc)
            return False

    def _write(self, record: _Record) -> None:
        try:
            payload = json.dumps({
                "timestamp": record.timestamp,
                "detections": record.detections,
                "metadata": dict(record.metadata),
            }, ensure_ascii=False, separators=(",", ":")) + "\n"
            encoded_size = len(payload.encode("utf-8"))
            if not self._rotate(encoded_size):
                return
            file = cast(Any, self._file)
            file.write(payload)
            file.flush()
            self._file_bytes += encoded_size
        except (OSError, TypeError, ValueError) as exc:
            LOGGER.error("Error escribiendo persistencia JSONL %s: %s", self.path, exc)
            if self._file is not None:
                try:
                    self._file.close()
                except OSError:
                    pass
            self._file = None

    def _worker(self) -> None:
        while not self._stop.is_set() or not self._detections.empty() or not self._samples.empty():
            try:
                try:
                    record = self._detections.get_nowait()
                except queue.Empty:
                    record = self._samples.get(timeout=0.1)
            except queue.Empty:
                continue
            self._write(record)
        if self._file is not None:
            try:
                self._file.close()
            except OSError:
                pass
            self._file = None

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._stop.set()
        self._worker_thread.join(timeout=2)
        if self._worker_thread.is_alive():
            LOGGER.error("El worker de persistencia no terminó dentro del timeout")

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        self.close()
