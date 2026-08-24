import json
import os
import tempfile
import threading
import time
import unittest
from dataclasses import dataclass
from unittest.mock import patch

import cv2
import numpy as np

from streaming.capture import CaptureWatchdogError, OpenCVFrameCapture
from streaming.config import StreamConfig
from streaming.main import create_detector, main as streaming_main, run
from streaming.persistence import JsonlDetectionStore
from streaming.processor import FrameProcessor
from streaming.publisher import FFmpegPublisher, FakePublisher


class ConfigTests(unittest.TestCase):
    def test_configuration_reads_environment(self):
        with patch.dict(os.environ, {
            "STREAMING_SOURCE": "sample.mp4",
            "STREAMING_WIDTH": "1280",
            "STREAMING_HEIGHT": "720",
            "STREAMING_FPS": "20",
            "STREAMING_INFERENCE": "true",
            "STREAMING_ENCODER": "h264_v4l2m2m",
        }, clear=False):
            config = StreamConfig.from_env()
        self.assertEqual(config.source, "sample.mp4")
        self.assertEqual(config.fps, 20)
        self.assertTrue(config.inference_enabled)
        self.assertEqual(config.encoder, "h264_v4l2m2m")

    def test_default_output_is_horno(self):
        config = StreamConfig()
        self.assertEqual(config.output_url, "rtsp://127.0.0.1:8554/horno")
        self.assertEqual((config.width, config.height), (1280, 720))

    def test_require_hailo_rejects_missing_hef_before_loading_cpu_model(self):
        with self.assertRaises(RuntimeError):
            create_detector(StreamConfig(inference_enabled=True, require_hailo=True))


class _ClosedCapture:
    def __init__(self):
        self.released = False

    def isOpened(self):
        return False

    def release(self):
        self.released = True


class _FailingCV2:
    INTER_AREA = cv2.INTER_AREA

    def __init__(self):
        self.captures = []

    def VideoCapture(self, _source):
        capture = _ClosedCapture()
        self.captures.append(capture)
        return capture


class _ThrowingCapture:
    def isOpened(self):
        return True

    def set(self, _property, _value):
        return True

    def get(self, _property):
        return 0

    def read(self):
        raise RuntimeError("camera read failure")

    def release(self):
        self.released = True


class _ThrowingCV2:
    INTER_AREA = cv2.INTER_AREA
    CAP_PROP_FRAME_WIDTH = 1
    CAP_PROP_FRAME_HEIGHT = 2
    CAP_PROP_FPS = 3
    CAP_PROP_BUFFERSIZE = 4

    def __init__(self):
        self.capture = _ThrowingCapture()

    def VideoCapture(self, _source):
        return self.capture


class _BlockingCapture:
    def __init__(self):
        self.released = False
        self.blocked = threading.Event()

    def isOpened(self):
        return True

    def set(self, _property, _value):
        return True

    def get(self, _property):
        return 0

    def read(self):
        self.blocked.wait()
        return False, None

    def release(self):
        # Deliberately does not unblock native read: this models the unsafe
        # cancellation case and leaves process exit as the resource boundary.
        self.released = True


class _BlockingCV2:
    INTER_AREA = cv2.INTER_AREA
    CAP_PROP_FRAME_WIDTH = 1
    CAP_PROP_FRAME_HEIGHT = 2
    CAP_PROP_FPS = 3
    CAP_PROP_BUFFERSIZE = 4

    def __init__(self):
        self.capture = _BlockingCapture()

    def VideoCapture(self, _source):
        return self.capture


class CaptureTests(unittest.TestCase):
    def test_open_failure_is_retried_and_does_not_raise(self):
        fake_cv2 = _FailingCV2()
        config = StreamConfig(source="0", reconnect_initial_seconds=0, reconnect_max_seconds=0)
        capture = OpenCVFrameCapture(config, cv2_module=fake_cv2, sleep=lambda _seconds: None)
        self.assertIsNone(capture.read())
        self.assertEqual(len(fake_cv2.captures), 1)
        self.assertTrue(fake_cv2.captures[0].released)
        capture.release()

    def test_read_exception_is_recovered_without_escaping(self):
        fake_cv2 = _ThrowingCV2()
        config = StreamConfig(source="0", reconnect_initial_seconds=0, reconnect_max_seconds=0)
        capture = OpenCVFrameCapture(config, cv2_module=fake_cv2, sleep=lambda _seconds: None)
        self.assertIsNone(capture.read())
        self.assertTrue(fake_cv2.capture.released)
        capture.release()

    def test_blocked_read_trips_watchdog_and_stops_pipeline(self):
        fake_cv2 = _BlockingCV2()
        config = StreamConfig(
            source="0",
            capture_read_timeout_seconds=0.02,
            reconnect_initial_seconds=0,
            reconnect_max_seconds=0,
        )
        capture = OpenCVFrameCapture(config, cv2_module=fake_cv2, sleep=lambda _seconds: None)
        publisher = FakePublisher()
        with self.assertRaises(CaptureWatchdogError):
            run(config, capture=capture, processor=_Processor(), publisher=publisher, max_frames=1)
        self.assertTrue(capture._watchdog_tripped)
        self.assertFalse(publisher.started)

    def test_watchdog_is_controlled_exit_code_for_systemd_restart(self):
        with patch("streaming.main.run", side_effect=CaptureWatchdogError("simulated blocked read")):
            self.assertEqual(streaming_main(["--no-inference"]), 1)


@dataclass
class _Detection:
    label: str
    confidence: float
    bbox: tuple[int, int, int, int]


class _Detector:
    def detect_frame(self, _frame):
        return [_Detection("tostada", 0.875, (2, 3, 10, 8))]


class ProcessorTests(unittest.TestCase):
    def test_overlay_and_jsonl_persistence_follow_detector_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            store = JsonlDetectionStore(os.path.join(directory, "detections.jsonl"))
            processor = FrameProcessor(_Detector(), store, {"source": "mock"})
            frame = np.zeros((40, 50, 3), dtype=np.uint8)
            output, detections = processor.process(frame, "2026-01-01T00:00:00+00:00")
            processor.close()

            self.assertEqual(detections[0]["bbox"], [2, 3, 10, 8])
            self.assertGreater(int(output.sum()), 0)
            with open(os.path.join(directory, "detections.jsonl"), encoding="utf-8") as saved:
                record = json.loads(saved.readline())
            self.assertEqual(record["timestamp"], "2026-01-01T00:00:00+00:00")
            self.assertEqual(record["detections"][0]["label"], "tostada")
            self.assertEqual(record["metadata"]["inference"], True)


class PersistenceTests(unittest.TestCase):
    def test_jsonl_rotates_and_keeps_bounded_number_of_files(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "detections.jsonl")
            store = JsonlDetectionStore(path, max_bytes=100, max_files=3, sample_no_detection_every=0)
            for index in range(8):
                store.append(str(index), [{"label": "x", "confidence": 1, "bbox": [0, 0, 1, 1]}], {})
            store.close()
            files = list(os.scandir(directory))
            self.assertLessEqual(len(files), 3)
            self.assertTrue(os.path.exists(path + ".1"))

    def test_disk_failure_does_not_escape_or_break_close(self):
        def failing_open(*_args, **_kwargs):
            raise OSError("read-only filesystem")

        with tempfile.TemporaryDirectory() as directory:
            store = JsonlDetectionStore(os.path.join(directory, "detections.jsonl"), file_opener=failing_open)
            self.assertTrue(store.append("now", [{"label": "x"}], {}))
            store.close()


class _FakeStdin:
    def __init__(self):
        self.read_fd, self.write_fd = os.pipe()
        self.closed = False

    def fileno(self):
        return self.write_fd

    def close(self):
        if not self.closed:
            os.close(self.write_fd)
            os.close(self.read_fd)
            self.closed = True


class _FakeProcess:
    def __init__(self):
        self.stdin = _FakeStdin()
        self.terminated = False

    def poll(self):
        return None if not self.terminated else 1

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.terminated = True

    def wait(self, timeout=None):
        return 0


class _NoProgressPublisher(FFmpegPublisher):
    def _write_frame_nonblocking(self, process, frame):
        return False


class PublisherTests(unittest.TestCase):
    def test_unprogressing_process_is_retried_and_stop_returns(self):
        processes = []

        def popen(*_args, **_kwargs):
            process = _FakeProcess()
            processes.append(process)
            return process

        config = StreamConfig(reconnect_initial_seconds=0.01, reconnect_max_seconds=0.02, publisher_write_timeout=0.05)
        publisher = _NoProgressPublisher(config, popen=popen)
        publisher.start()
        publisher.publish(np.zeros((4, 4, 3), dtype=np.uint8))
        time.sleep(0.08)
        health = publisher.health()
        publisher.stop()
        self.assertGreaterEqual(health["restart_count"], 1)
        self.assertGreaterEqual(len(processes), 1)
        self.assertFalse(publisher.is_running())

    def test_ffmpeg_command_has_web_compatible_h264_parameters(self):
        command = FFmpegPublisher(StreamConfig())._command()
        self.assertIn("yuv420p", command)
        self.assertIn("-bf", command)
        self.assertIn("-g", command)


class _Capture:
    exhausted = False

    def __init__(self):
        self.frames = [np.zeros((4, 4, 3), dtype=np.uint8), np.ones((4, 4, 3), dtype=np.uint8)]
        self.released = False

    def read(self):
        return self.frames.pop(0) if self.frames else None

    def release(self):
        self.released = True


class _Processor:
    detector = None

    def process(self, frame):
        return frame, []


class OrchestrationTests(unittest.TestCase):
    def test_run_releases_capture_and_uses_fake_publisher(self):
        capture = _Capture()
        publisher = FakePublisher()
        config = StreamConfig()
        processed = run(config, capture=capture, processor=_Processor(), publisher=publisher, max_frames=2)
        self.assertEqual(processed, 2)
        self.assertTrue(capture.released)
        self.assertFalse(publisher.started)
        self.assertEqual(len(publisher.frames), 2)


if __name__ == "__main__":
    unittest.main()
