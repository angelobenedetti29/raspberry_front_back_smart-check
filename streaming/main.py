"""Executable orchestration for the standalone streaming pipeline."""

from __future__ import annotations

import signal
import threading
import logging
from typing import Any

from .capture import CaptureWatchdogError, OpenCVFrameCapture
from .config import StreamConfig
from .persistence import JsonlDetectionStore
from .processor import FrameProcessor
from .publisher import FFmpegPublisher


def create_detector(config: StreamConfig):
    if not config.inference_enabled:
        return None
    if config.require_hailo and (config.model_path is None or not config.model_path.lower().endswith(".hef")):
        raise RuntimeError("STREAMING_REQUIRE_HAILO=true exige STREAMING_MODEL apuntando a un archivo .hef")
    # Import lazily: local simulation and tests do not need Hailo, ONNX or a model.
    from backend.infrastructure.ai.yolo_detector import YoloDetector
    kwargs: dict[str, Any] = {"confidence_threshold": config.confidence_threshold}
    if config.model_path is not None:
        kwargs["model_path"] = config.model_path
    if config.labels_path is not None:
        kwargs["names_path"] = config.labels_path
    detector = YoloDetector(**kwargs)
    if config.require_hailo and not getattr(detector, "use_hailo", False):
        if hasattr(detector, "release_hailo"):
            detector.release_hailo()
        raise RuntimeError("STREAMING_REQUIRE_HAILO=true requiere que YoloDetector use un modelo .hef/Hailo")
    return detector


def run(config: StreamConfig, capture=None, processor=None, publisher=None, max_frames: int | None = None) -> int:
    capture = OpenCVFrameCapture(config) if capture is None else capture
    owns_processor = processor is None
    if processor is None:
        detector = create_detector(config)
        store = JsonlDetectionStore(
            config.storage_path,
            queue_size=config.storage_queue_size,
            max_bytes=config.storage_max_bytes,
            max_files=config.storage_max_files,
            sample_no_detection_every=config.persist_no_detection_every,
        )
        processor = FrameProcessor(detector, store, {"source": config.source})
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
                    detector = getattr(processor, "detector", None)
                    try:
                        if detector is not None and hasattr(detector, "release_hailo"):
                            detector.release_hailo()
                    finally:
                        for sig, handler in old_handlers.items():
                            signal.signal(sig, handler)
    return processed


def main(argv=None) -> int:
    try:
        config = StreamConfig.from_cli(argv)
        run(config)
    except CaptureWatchdogError as exc:
        logging.getLogger("streaming").critical("Watchdog de captura: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
