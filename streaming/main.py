"""Executable orchestration for the standalone streaming pipeline."""

from __future__ import annotations

import signal
import threading
import logging
from typing import Any

from smartcheck_config import load_config

from .capture import CaptureWatchdogError, OpenCVFrameCapture
from .config import StreamConfig
from .persistence import JsonlDetectionStore
from .processor import FrameProcessor
from .publisher import FFmpegPublisher


def create_detector(config: StreamConfig):
    if not config.inference.enabled:
        return None
    if config.inference.require_hailo and (config.inference.model_path is None or not config.inference.model_path.lower().endswith(".hef")):
        raise RuntimeError(
            "stream.inference.require_hailo=true exige "
            "stream.inference.model_path apuntando a un archivo .hef"
        )
    # import lazy
    from backend.infrastructure.ai.yolo_detector import YoloDetector
    kwargs: dict[str, Any] = {"confidence_threshold": config.inference.confidence_threshold}
    if config.inference.model_path is not None:
        kwargs["model_path"] = config.inference.model_path
    if config.inference.labels_path is not None:
        kwargs["names_path"] = config.inference.labels_path
    detector = YoloDetector(**kwargs)
    if config.inference.require_hailo and not getattr(detector, "use_hailo", False):
        if hasattr(detector, "release_hailo"):
            detector.release_hailo()
        raise RuntimeError(
            "stream.inference.require_hailo=true requiere que YoloDetector "
            "cargue un modelo .hef/Hailo"
        )
    return detector


def run(config: StreamConfig, capture=None, processor=None, publisher=None, max_frames: int | None = None) -> int:
    capture = OpenCVFrameCapture(config) if capture is None else capture
    owns_processor = processor is None
    detector = None
    if processor is None:
        detector = create_detector(config)
        store = JsonlDetectionStore(
            config.storage.path,
            queue_size=config.storage.queue_size,
            max_bytes=config.storage.max_bytes,
            max_files=config.storage.max_files,
            sample_no_detection_every=config.storage.persist_no_detection_every,
        )
        processor = FrameProcessor(detector, store, {"source": config.capture.source})
    publisher = FFmpegPublisher(config) if publisher is None else publisher
    stop_requested = False

    def request_stop(_signum, _frame):
        nonlocal stop_requested
        stop_requested = True

    # Signal handlers can only be installed by the main thread. Dependency
    # injection is also used by callers running the pipeline in a worker thread.
    old_handlers = {}
    if threading.current_thread() is threading.main_thread():
        old_handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
        for sig in old_handlers:
            signal.signal(sig, request_stop)
    processed = 0
    try:
        publisher.start()
        while not stop_requested and (max_frames is None or processed < max_frames):
            frame = capture.read()
            if frame is None:
                if getattr(capture, "exhausted", False):
                    break
                continue
            output, _detections = processor.process(frame)
            publisher.publish(output)
            processed += 1
    finally:
        try:
            publisher.stop()
        finally:
            try:
                capture.release()
            finally:
                try:
                    if owns_processor:
                        processor.close()
                finally:
                    # Only release a detector created by this run. An injected
                    # processor owns its detector and is responsible for it.
                    try:
                        if detector is not None and hasattr(detector, "release_hailo"):
                            detector.release_hailo()
                    finally:
                        for sig, handler in old_handlers.items():
                            signal.signal(sig, handler)
    return processed


def main(argv=None) -> int:
    """Punto de entrada del proceso de streaming.

    ``argv`` se conserva por compatibilidad de firma, pero se ignora: la
    configuración proviene exclusivamente de ``config.json`` a través de
    ``smartcheck_config.load_config``.
    """
    try:
        config = StreamConfig.from_app_config(load_config().config.stream)
        run(config)
    except CaptureWatchdogError as exc:
        logging.getLogger("streaming").critical("Watchdog de captura: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
