"""Optional detector, visual overlay and detection persistence."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, cast

import cv2

from .persistence import JsonlDetectionStore


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


class FrameProcessor:
    def __init__(self, detector=None, store: JsonlDetectionStore | None = None, metadata: Mapping[str, Any] | None = None):
        self.detector = detector
        self.store = store
        self.metadata = dict(metadata or {})

    @staticmethod
    def _serialize_detection(detection: Any) -> dict[str, Any]:
        # YoloDetector returns DetectionResult(label, confidence, bbox), where bbox
        # is (x, y, width, height). Dict support keeps mocks simple without changing
        # the production detector contract.
        if is_dataclass(detection):
            value = asdict(cast(Any, detection))
        elif isinstance(detection, Mapping):
            value = dict(detection)
        else:
            value = {"label": detection.label, "confidence": detection.confidence, "bbox": detection.bbox}
        return {
            "label": str(value["label"]),
            "confidence": float(value["confidence"]),
            "bbox": [int(item) for item in value["bbox"]],
        }

    def process(self, frame, timestamp: str | None = None):
        detections = [] if self.detector is None else (self.detector.detect_frame(frame) or [])
        serialized = [self._serialize_detection(item) for item in detections]
        output = frame.copy()
        for detection in serialized:
            x, y, width, height = detection["bbox"]
            cv2.rectangle(output, (x, y), (x + width, y + height), (0, 255, 0), 2)
            text = f'{detection["label"]} {detection["confidence"]:.2f}'
            cv2.putText(output, text, (x, max(20, y - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 0), 2, cv2.LINE_AA)

        if self.store is not None:
            self.store.append(timestamp or utc_timestamp(), serialized, {
                **self.metadata,
                "width": int(frame.shape[1]),
                "height": int(frame.shape[0]),
                "inference": self.detector is not None,
            })
        return output, serialized

    def close(self) -> None:
        if self.store is not None:
            self.store.close()
