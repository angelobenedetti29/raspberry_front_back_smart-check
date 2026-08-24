"""Configuration shared by the capture, processing and publishing stages."""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from typing import Sequence


def _env(name: str, default: str) -> str:
    value = os.getenv(name)
    return default if value is None or value == "" else value


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on", "si", "sí"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} debe ser booleano (true/false)")


@dataclass(frozen=True)
class StreamConfig:
    source: str = "0"
    width: int = 1280
    height: int = 720
    fps: int = 30
    output_url: str = "rtsp://127.0.0.1:8554/horno"
    bitrate: str = "2M"
    inference_enabled: bool = False
    require_hailo: bool = False
    model_path: str | None = None
    labels_path: str | None = None
    storage_path: str = "streaming/data/detections.jsonl"
    ffmpeg_executable: str = "ffmpeg"
    encoder: str = "libx264"
    confidence_threshold: float = 0.60
    reconnect_initial_seconds: float = 1.0
    reconnect_max_seconds: float = 30.0
    loop_video: bool = True
    capture_buffer_size: int = 1
    capture_stable_frames: int = 30
    capture_read_timeout_seconds: float = 5.0
    storage_queue_size: int = 128
    storage_max_bytes: int = 10 * 1024 * 1024
    storage_max_files: int = 5
    persist_no_detection_every: int = 10
    publisher_queue_size: int = 2
    publisher_write_timeout: float = 0.5
    publisher_stable_seconds: float = 5.0
    pixel_format: str = "yuv420p"
    gop_seconds: int = 2
    b_frames: int = 0

    def __post_init__(self) -> None:
        if self.width <= 0 or self.height <= 0:
            raise ValueError("width y height deben ser mayores que cero")
        if self.fps not in (20, 30):
            raise ValueError("fps debe ser 20 o 30")
        if not self.output_url:
            raise ValueError("output_url no puede estar vacío")
        if self.reconnect_initial_seconds < 0 or self.reconnect_max_seconds < self.reconnect_initial_seconds:
            raise ValueError("backoff de reconexión inválido")
        if self.require_hailo and not self.inference_enabled:
            raise ValueError("require_hailo requiere inference_enabled")
        if self.capture_buffer_size < 1 or self.capture_stable_frames < 1:
            raise ValueError("buffer de cámara y frames estables deben ser mayores que cero")
        if self.capture_read_timeout_seconds <= 0:
            raise ValueError("capture_read_timeout_seconds debe ser mayor que cero")
        if self.storage_queue_size < 1 or self.storage_max_bytes < 1 or self.storage_max_files < 1:
            raise ValueError("cola y rotación de almacenamiento inválidas")
        if self.persist_no_detection_every < 0:
            raise ValueError("persist_no_detection_every no puede ser negativo")
        if self.publisher_queue_size < 1 or self.publisher_write_timeout <= 0:
            raise ValueError("configuración de publisher inválida")
        if self.publisher_stable_seconds < 0 or self.gop_seconds not in (1, 2) or self.b_frames < 0:
            raise ValueError("GOP, B-frames o ventana estable inválidos")
        if self.pixel_format != "yuv420p":
            raise ValueError("pixel_format debe ser yuv420p para compatibilidad WebRTC")

    @classmethod
    def from_args(cls, args: argparse.Namespace) -> "StreamConfig":
        return cls(
            source=args.source,
            width=args.width,
            height=args.height,
            fps=args.fps,
            output_url=args.output_url,
            bitrate=args.bitrate,
            inference_enabled=args.inference,
            require_hailo=args.require_hailo,
            model_path=args.model,
            labels_path=args.labels,
            storage_path=args.storage,
            ffmpeg_executable=args.ffmpeg,
            encoder=args.encoder,
            confidence_threshold=args.confidence,
            reconnect_initial_seconds=args.reconnect_initial,
            reconnect_max_seconds=args.reconnect_max,
            loop_video=args.loop_video,
            capture_buffer_size=args.capture_buffer_size,
            capture_stable_frames=args.capture_stable_frames,
            capture_read_timeout_seconds=args.capture_read_timeout,
            storage_queue_size=args.storage_queue_size,
            storage_max_bytes=args.storage_max_bytes,
            storage_max_files=args.storage_max_files,
            persist_no_detection_every=args.persist_no_detection_every,
            publisher_queue_size=args.publisher_queue_size,
            publisher_write_timeout=args.publisher_write_timeout,
            publisher_stable_seconds=args.publisher_stable_seconds,
            pixel_format=args.pixel_format,
            gop_seconds=args.gop_seconds,
            b_frames=args.b_frames,
        )

    @classmethod
    def from_env(cls) -> "StreamConfig":
        """Build configuration without importing any optional inference library."""
        parser = build_arg_parser()
        # argparse defaults are deliberately replaced by environment values here.
        args = parser.parse_args([])
        args.source = _env("STREAMING_SOURCE", args.source)
        args.width = int(_env("STREAMING_WIDTH", str(args.width)))
        args.height = int(_env("STREAMING_HEIGHT", str(args.height)))
        args.fps = int(_env("STREAMING_FPS", str(args.fps)))
        args.output_url = _env("STREAMING_OUTPUT_URL", args.output_url)
        args.bitrate = _env("STREAMING_BITRATE", args.bitrate)
        args.inference = _env_bool("STREAMING_INFERENCE", args.inference)
        args.require_hailo = _env_bool("STREAMING_REQUIRE_HAILO", args.require_hailo)
        args.model = os.getenv("STREAMING_MODEL") or args.model
        args.labels = os.getenv("STREAMING_LABELS") or args.labels
        args.storage = _env("STREAMING_STORAGE", args.storage)
        args.ffmpeg = _env("STREAMING_FFMPEG", args.ffmpeg)
        args.encoder = _env("STREAMING_ENCODER", args.encoder)
        args.confidence = float(_env("STREAMING_CONFIDENCE", str(args.confidence)))
        args.reconnect_initial = float(_env("STREAMING_RECONNECT_INITIAL", str(args.reconnect_initial)))
        args.reconnect_max = float(_env("STREAMING_RECONNECT_MAX", str(args.reconnect_max)))
        args.loop_video = _env_bool("STREAMING_LOOP_VIDEO", args.loop_video)
        args.capture_buffer_size = int(_env("STREAMING_CAPTURE_BUFFER_SIZE", str(args.capture_buffer_size)))
        args.capture_stable_frames = int(_env("STREAMING_CAPTURE_STABLE_FRAMES", str(args.capture_stable_frames)))
        args.capture_read_timeout = float(_env("STREAMING_CAPTURE_READ_TIMEOUT", str(args.capture_read_timeout)))
        args.storage_queue_size = int(_env("STREAMING_STORAGE_QUEUE_SIZE", str(args.storage_queue_size)))
        args.storage_max_bytes = int(_env("STREAMING_STORAGE_MAX_BYTES", str(args.storage_max_bytes)))
        args.storage_max_files = int(_env("STREAMING_STORAGE_MAX_FILES", str(args.storage_max_files)))
        args.persist_no_detection_every = int(_env("STREAMING_PERSIST_NO_DETECTION_EVERY", str(args.persist_no_detection_every)))
        args.publisher_queue_size = int(_env("STREAMING_PUBLISHER_QUEUE_SIZE", str(args.publisher_queue_size)))
        args.publisher_write_timeout = float(_env("STREAMING_PUBLISHER_WRITE_TIMEOUT", str(args.publisher_write_timeout)))
        args.publisher_stable_seconds = float(_env("STREAMING_PUBLISHER_STABLE_SECONDS", str(args.publisher_stable_seconds)))
        args.pixel_format = _env("STREAMING_PIXEL_FORMAT", args.pixel_format)
        args.gop_seconds = int(_env("STREAMING_GOP_SECONDS", str(args.gop_seconds)))
        args.b_frames = int(_env("STREAMING_B_FRAMES", str(args.b_frames)))
        return cls.from_args(args)

    @classmethod
    def from_cli(cls, argv: Sequence[str] | None = None) -> "StreamConfig":
        parser = build_arg_parser()
        args = parser.parse_args(argv)
        return cls.from_args(args)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Captura, procesa y publica vídeo del horno")
    parser.add_argument("--source", default=_env("STREAMING_SOURCE", "0"), help="índice de cámara o ruta de vídeo")
    parser.add_argument("--width", type=int, default=int(_env("STREAMING_WIDTH", "1280")))
    parser.add_argument("--height", type=int, default=int(_env("STREAMING_HEIGHT", "720")))
    parser.add_argument("--fps", type=int, choices=(20, 30), default=int(_env("STREAMING_FPS", "30")))
    parser.add_argument("--output-url", default=_env("STREAMING_OUTPUT_URL", "rtsp://127.0.0.1:8554/horno"))
    parser.add_argument("--bitrate", default=_env("STREAMING_BITRATE", "2M"))
    parser.add_argument("--inference", action=argparse.BooleanOptionalAction, default=_env_bool("STREAMING_INFERENCE", False))
    parser.add_argument("--require-hailo", action=argparse.BooleanOptionalAction, default=_env_bool("STREAMING_REQUIRE_HAILO", False))
    parser.add_argument("--model", default=os.getenv("STREAMING_MODEL"))
    parser.add_argument("--labels", default=os.getenv("STREAMING_LABELS"))
    parser.add_argument("--storage", default=_env("STREAMING_STORAGE", "streaming/data/detections.jsonl"))
    parser.add_argument("--ffmpeg", default=_env("STREAMING_FFMPEG", "ffmpeg"))
    parser.add_argument("--encoder", default=_env("STREAMING_ENCODER", "libx264"))
    parser.add_argument("--confidence", type=float, default=float(_env("STREAMING_CONFIDENCE", "0.60")))
    parser.add_argument("--reconnect-initial", type=float, default=float(_env("STREAMING_RECONNECT_INITIAL", "1.0")))
    parser.add_argument("--reconnect-max", type=float, default=float(_env("STREAMING_RECONNECT_MAX", "30.0")))
    parser.add_argument("--loop-video", action=argparse.BooleanOptionalAction, default=_env_bool("STREAMING_LOOP_VIDEO", True))
    parser.add_argument("--capture-buffer-size", type=int, default=int(_env("STREAMING_CAPTURE_BUFFER_SIZE", "1")))
    parser.add_argument("--capture-stable-frames", type=int, default=int(_env("STREAMING_CAPTURE_STABLE_FRAMES", "30")))
    parser.add_argument("--capture-read-timeout", type=float, default=float(_env("STREAMING_CAPTURE_READ_TIMEOUT", "5.0")))
    parser.add_argument("--storage-queue-size", type=int, default=int(_env("STREAMING_STORAGE_QUEUE_SIZE", "128")))
    parser.add_argument("--storage-max-bytes", type=int, default=int(_env("STREAMING_STORAGE_MAX_BYTES", str(10 * 1024 * 1024))))
    parser.add_argument("--storage-max-files", type=int, default=int(_env("STREAMING_STORAGE_MAX_FILES", "5")))
    parser.add_argument("--persist-no-detection-every", type=int, default=int(_env("STREAMING_PERSIST_NO_DETECTION_EVERY", "10")))
    parser.add_argument("--publisher-queue-size", type=int, default=int(_env("STREAMING_PUBLISHER_QUEUE_SIZE", "2")))
    parser.add_argument("--publisher-write-timeout", type=float, default=float(_env("STREAMING_PUBLISHER_WRITE_TIMEOUT", "0.5")))
    parser.add_argument("--publisher-stable-seconds", type=float, default=float(_env("STREAMING_PUBLISHER_STABLE_SECONDS", "5")))
    parser.add_argument("--pixel-format", default=_env("STREAMING_PIXEL_FORMAT", "yuv420p"))
    parser.add_argument("--gop-seconds", type=int, choices=(1, 2), default=int(_env("STREAMING_GOP_SECONDS", "2")))
    parser.add_argument("--b-frames", type=int, default=int(_env("STREAMING_B_FRAMES", "0")))
    return parser
