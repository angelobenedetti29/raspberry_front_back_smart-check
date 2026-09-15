"""Static configuration shared by the frontend modules."""

WINDOW_TITLE = "SISTEMA DE CONTROL INDUSTRIAL"
WINDOW_WIDTH = 1200
WINDOW_HEIGHT = 800

DEFAULT_SOURCE = "20260323_124058.mp4"

VIDEOS_DIR_PSEUDO_PATH = "yolov11-python/data/videos"
VIDEO_EXTENSIONS = (".mp4",)

LOCAL_LOTE_ENDPOINT = "http://localhost:8000/api/lotes/finalizar"

# Ordered catalog of selectable models: label, model path, names path.
MODEL_CATALOG = [
    (
        "YOLOv11 Original (COCO)",
        "yolov11-python/yolo11n.onnx",
        "yolov11-python/data/class.names",
    ),
    (
        "YOLOv11 Tostadas V1 (Custom)",
        "yolov11-python/tostadas_v1.onnx",
        "yolov11-python/tostadas_v1.names",
    ),
    (
        "YOLOv11 Tostadas V2 (Custom)",
        "yolov11-python/tostadas_v2.onnx",
        "yolov11-python/tostadas_v2.names",
    ),
    (
        "YOLOv8s NPU (Hailo-8L COCO)",
        "/usr/share/hailo-models/yolov8s_h8l.hef",
        "yolov11-python/data/class.names",
    ),
]

NPU_MODEL_INDEX = 3
DEFAULT_MODEL_INDEX = 0
