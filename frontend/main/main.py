import sys
import os

# Añadir el directorio raíz del proyecto al sys.path para poder importar el backend
root_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

import cv2
import numpy as np
import random
import time
import threading

from datetime import datetime
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QHBoxLayout, 
                               QVBoxLayout, QPushButton, QLabel, QFrame, QProgressBar, 
                               QSpacerItem, QSizePolicy, QScrollArea, QComboBox)
from PySide6.QtCore import Qt, QThread, QTimer, Signal, Slot
from PySide6.QtGui import QImage, QPixmap

from streaming.config import StreamConfig
from streaming.publisher import FFmpegPublisher

# Backend imports (Clean Architecture)
from backend.infrastructure.ai.yolo_detector import YoloDetector, HAILO_AVAILABLE
from backend.infrastructure.iot.mock_controller import MockIoTController
from backend.infrastructure.http.requests_client import RequestsHttpClient
from backend.use_cases.detect_and_notify import DetectAndNotifyUseCase
from backend.use_cases.control_device import ControlDeviceUseCase


class PreviewOnlyPublisher:
    """Bounded no-op publisher used when streaming configuration is invalid."""

    def __init__(self, config):
        self.config = config

    def start(self):
        pass

    def publish(self, _frame):
        return True

    def stop(self):
        pass

def resolve_path(relative_path):
    """
    Resolves paths to make sure they work when executed from either:
    1. The project root (yolov11-python / raspberry_front_back_smart-check)
    2. The frontend or frontend/main directory
    3. Anywhere else, by checking alternative directory structures.
    """
    if not relative_path:
        return relative_path

    # Obtener el directorio raíz del proyecto (padre del directorio frontend/)
    root_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    # Si es una ruta absoluta y existe, devolverla directamente
    if os.path.isabs(relative_path) and os.path.exists(relative_path):
        return relative_path

    # 1. Comprobar si existe tal cual relativa al CWD
    if os.path.exists(relative_path):
        return os.path.abspath(relative_path)

    # 2. Comprobar si existe relativa a root_dir
    path_in_root = os.path.join(root_dir, relative_path)
    if os.path.exists(path_in_root):
        return path_in_root

    # Normalizar separadores
    normalized = relative_path.replace("\\", "/")

    # Si empieza con yolov11-python/, probar a quitar el prefijo
    if normalized.startswith("yolov11-python/"):
        stripped = normalized[len("yolov11-python/"):]
        # Buscar en CWD
        if os.path.exists(stripped):
            return os.path.abspath(stripped)
        # Buscar en root_dir
        path_stripped_in_root = os.path.join(root_dir, stripped)
        if os.path.exists(path_stripped_in_root):
            return path_stripped_in_root

    # Si no empieza con yolov11-python/, probar a añadir el prefijo
    else:
        prefixed = f"yolov11-python/{normalized}"
        if os.path.exists(prefixed):
            return os.path.abspath(prefixed)
        path_prefixed_in_root = os.path.join(root_dir, prefixed)
        if os.path.exists(path_prefixed_in_root):
            return path_prefixed_in_root

    # Mapeo específico para diferentes distribuciones de carpetas
    mapping = {
        "yolov11-python/yolo11n.onnx": "ai_training/models/yolo11n.onnx",
        "yolov11-python/tostadas_v1.onnx": "ai_training/models/tostadas_v1.onnx",
        "yolov11-python/tostadas_v1.names": "ai_training/models/tostadas_v1.names",
        "yolov11-python/tostadas_v2.onnx": "ai_training/models/tostadas_v2.onnx",
        "yolov11-python/tostadas_v2.names": "ai_training/models/tostadas_v2.names",
        "yolov11-python/data/class.names": "ai_training/models/class.names",
        "yolov11-python/data/videos/road.mp4": "multimedia/videos/road.mp4",
        "yolov11-python/data/videos": "multimedia/videos"
    }
    
    # Probar mapeos
    for key, val in mapping.items():
        if normalized == key or normalized == key.replace("yolov11-python/", ""):
            # 1. Comprobar val relativo a CWD
            if os.path.exists(val):
                return os.path.abspath(val)
            # 2. Comprobar val relativo a root_dir
            path_val_in_root = os.path.join(root_dir, val)
            if os.path.exists(path_val_in_root):
                return path_val_in_root
            # 3. Comprobar con prefijo yolov11-python/ en CWD
            prefixed_val = f"yolov11-python/{val}"
            if os.path.exists(prefixed_val):
                return os.path.abspath(prefixed_val)
            # 4. Comprobar con prefijo en root_dir
            path_prefixed_val_in_root = os.path.join(root_dir, prefixed_val)
            if os.path.exists(path_prefixed_val_in_root):
                return path_prefixed_val_in_root

    return relative_path


def validate_stream_config(config):
    """Validate frontend constraints in addition to StreamConfig's checks."""
    if config.pixel_format == "yuv420p" and (config.width % 2 or config.height % 2):
        raise ValueError("width y height deben ser pares para yuv420p")
    return config


QSS = """
QMainWindow {
    background-color: #D8BFD8;
}

#Sidebar {
    background-color: #1A0B2E;
    border-top-right-radius: 15px;
    border-bottom-right-radius: 15px;
}

#Sidebar QLabel {
    color: #E0B0FF;
    font-family: 'Segoe UI', Roboto, sans-serif;
    font-weight: bold;
}

#Sidebar QPushButton {
    background-color: transparent;
    color: #E0B0FF;
    font-family: 'Segoe UI', Roboto, sans-serif;
    font-size: 14px;
    font-weight: bold;
    text-align: left;
    padding: 12px 20px;
    border: 2px solid transparent;
    border-radius: 10px;
    margin: 5px 10px;
}

#Sidebar QPushButton:hover {
    background-color: #2D1B4E;
    border: 2px solid #B026FF;
    color: #FFFFFF;
}

/* Feedback Visual para la Cámara Activa (Checkeable) */
#Sidebar QPushButton:checked {
    background-color: #2D1B4E;
    border: 2px solid #00FFCC; /* Borde más intenso para indicar activo */
    color: #FFFFFF;
}

/* Estilización Premium para el Selector de Modelos (QComboBox) */
QComboBox {
    background-color: #2D1B4E;
    border: 2px solid #5A4080;
    border-radius: 8px;
    padding: 8px 12px;
    color: #00FFCC;
    font-family: 'Segoe UI', Roboto, sans-serif;
    font-size: 13px;
    font-weight: bold;
    margin: 5px 10px;
}

QComboBox:hover {
    border: 2px solid #B026FF;
    color: #FFFFFF;
}

QComboBox QAbstractItemView {
    background-color: #1A0B2E;
    color: #E0B0FF;
    selection-background-color: #B026FF;
    selection-color: white;
    border: 1px solid #5A4080;
    border-radius: 5px;
}

#BtnLogout {
    background-color: rgba(176, 38, 255, 0.1);
    border: 1px solid #B026FF;
    text-align: center;
}

#BtnLogout:hover {
    background-color: #B026FF;
    color: white;
}

#VideoFrame {
    background-color: #2A2A35;
    border: 2px solid #5A4080;
    border-radius: 15px;
}

#GalleryFrame {
    background-color: #1A0B2E;
    border: 1px solid #5A4080;
    border-radius: 10px;
}

/* ScrollBar Vertical Customization */
QScrollBar:vertical {
    border: none;
    background: #1A0B2E;
    width: 10px;
    border-radius: 5px;
    margin: 0px 0px 0px 0px;
}
QScrollBar::handle:vertical {
    background: #B026FF;
    min-height: 20px;
    border-radius: 5px;
}
QScrollBar::handle:vertical:hover {
    background: #A45EE5;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    border: none;
    background: none;
}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
    background: none;
}

/* Video Buttons */
.VideoBtn {
    background-color: rgba(26, 11, 46, 0.7);
    color: #E0B0FF;
    font-family: 'Segoe UI', Roboto, sans-serif;
    font-size: 14px;
    font-weight: 600;
    text-align: left;
    padding: 10px 15px;
    border: 1px solid transparent;
    border-radius: 10px;
    margin: 2px 5px;
}

.VideoBtn:hover {
    background-color: #A45EE5;
    border: 1px solid #D8BFD8;
    color: white;
}

.VideoBtn:pressed {
    background-color: #7B2CBF;
    border: 2px solid #00FFCC;
    color: white;
}

.Card {
    background-color: #1A0B2E;
    border: 1px solid #7B2CBF;
    border-radius: 15px;
}

.Card QLabel {
    color: #E0B0FF;
    font-family: 'Segoe UI', Roboto, sans-serif;
}

QProgressBar {
    background-color: #2D1B4E;
    border-radius: 5px;
    color: transparent;
    height: 10px;
}

QProgressBar::chunk {
    background-color: #00FFCC;
    border-radius: 5px;
}
"""

class YOLODetectionThread(QThread):
    change_pixmap_signal = Signal(QImage)
    iot_status_changed_signal = Signal()
    burned_toast_alert_signal = Signal(str)
    lote_completed_signal = Signal(dict)

    def __init__(self, source_file, detect_use_case, stream_config=None,
                 capture_factory=None, publisher_factory=None):
        super().__init__()
        self.source_file = resolve_path(source_file)
        self.detect_use_case = detect_use_case
        self.stream_config = validate_stream_config(
            stream_config if stream_config is not None else StreamConfig.from_env()
        )
        self.capture_factory = capture_factory or cv2.VideoCapture
        self.publisher_factory = publisher_factory or FFmpegPublisher
        self.capture = None
        self.publisher = None
        self.running = True
        self._stop_event = threading.Event()
        self.show_ok_toasts = True
        self.show_burnt_toasts = True
        
        # Reset tracker and alert records when starting a new stream
        if hasattr(self.detect_use_case, "reset_tracker"):
            self.detect_use_case.reset_tracker()
        self.alerted_ids = set()

        # Acumuladores de métricas del lote
        self.inicio_at = datetime.now()
        self.seen_toasts = {}
        self.temperatures_horno1 = []
        self.temperatures_comb1 = []
        self.temperatures_horno2 = []
        self.temperatures_comb2 = []
        self.velocidades_cinta = []

    def run(self):
        cv_source = 0 if self.source_file == "0" else self.source_file
        cap = None
        publisher = None
        try:
            # Both resources belong to the worker.  In particular, the GUI never
            # releases a capture while OpenCV may still be inside read().
            cap = self.capture = self.capture_factory(cv_source)
            publisher = self.publisher = self.publisher_factory(self.stream_config)

            if not cap.isOpened():
                print(f"No se pudo abrir la fuente de video: {cv_source}")
                return

            # The publisher must not be started until the source is usable.
            if self._stop_event.is_set():
                return
            try:
                publisher.start()
            except Exception as exc:
                # A local preview is still useful when FFmpeg is unavailable.
                print(f"[Thread] No se pudo iniciar el publisher RTSP: {exc}")

            self.frame_time = 1.0 / self.stream_config.fps

            # Generar colores de clases de forma determinista
            try:
                class_names = self.detect_use_case.detector.get_class_names()
            except Exception:
                class_names = []

            colors = {name: (0, 255, 0) for name in class_names}  # Todas las tostadas OK en verde

            self.last_toast_seen_time = time.time()

            while self.running and not self._stop_event.is_set():
                start_time = time.time()
                ret, frame = cap.read()
                if not ret:
                    break

                image = frame.copy()
                # Ejecutar el Caso de Uso de Detección e IoT
                try:
                    detections = self.detect_use_case.execute(image)
                except Exception as e:
                    print(f"[Thread] Error al ejecutar inferencia YOLO: {e}")
                    detections = []

                # Registrar tostadas vistas y su estado final
                has_visible_toasts = False
                for det in detections:
                    toast_id = getattr(det, "id", None)
                    state = getattr(det, "state", "unknown")
                    if toast_id is not None:
                        has_visible_toasts = True
                        if toast_id not in self.seen_toasts:
                            self.seen_toasts[toast_id] = state
                        elif state == "burnt":
                            self.seen_toasts[toast_id] = "burnt"

                # Simular lecturas de sensores en tiempo real
                self.temperatures_horno1.append(220.0 + random.uniform(-1.5, 1.5))
                self.temperatures_comb1.append(315.0 + random.uniform(-2.0, 2.0))
                self.temperatures_horno2.append(218.0 + random.uniform(-1.5, 1.5))
                self.temperatures_comb2.append(312.0 + random.uniform(-2.0, 2.0))
                self.velocidades_cinta.append(1.10 + random.uniform(-0.05, 0.05))

                # Lógica de cierre automático por inactividad
                if has_visible_toasts:
                    self.last_toast_seen_time = time.time()
                elif self.seen_toasts and (time.time() - self.last_toast_seen_time > 10.0):
                    print("[Thread] Inactividad detectada (10s sin tostadas). Finalizando y enviando lote actual...")
                    self.emit_batch_metrics()
                    self.inicio_at = datetime.now()
                    self.seen_toasts.clear()
                    self.temperatures_horno1.clear()
                    self.temperatures_comb1.clear()
                    self.temperatures_horno2.clear()
                    self.temperatures_comb2.clear()
                    self.velocidades_cinta.clear()
                    self.last_toast_seen_time = time.time()

                # Filtrar tostadas quemadas activas cuya alerta no ha sido emitida por este hilo
                new_alerts = False
                for det in detections:
                    toast_id = getattr(det, "id", None)
                    state = getattr(det, "state", "unknown")

                    if toast_id is not None:
                        if state == "burnt" and toast_id not in self.alerted_ids:
                            self.alerted_ids.add(toast_id)
                            self.burned_toast_alert_signal.emit(f"¡ALERTA TOSTADA #{toast_id} QUEMADA! (Conf: {det.confidence:.2f})")
                            new_alerts = True
                    else:
                        is_burnt = "quemada" in det.label.lower() or det.label.lower() == "tcq"
                        if is_burnt:
                            current_time = time.time()
                            if not hasattr(self, "_last_mock_alert_time") or (current_time - self._last_mock_alert_time) > 5.0:
                                self._last_mock_alert_time = current_time
                                self.burned_toast_alert_signal.emit(f"¡ALERTA TOSTADA QUEMADA! (Conf: {det.confidence:.2f})")
                                new_alerts = True

                if new_alerts:
                    self.iot_status_changed_signal.emit()

                # Dibujar rectángulos y etiquetas
                for det in detections:
                    left, top, width, height = det.bbox
                    label = det.label
                    confidence = det.confidence
                    toast_id = getattr(det, "id", None)
                    state = getattr(det, "state", "unknown")

                    is_burnt = state == "burnt" or "quemada" in label.lower() or label.lower() == "tcq"
                    if is_burnt and not self.show_burnt_toasts:
                        continue
                    if not is_burnt and not self.show_ok_toasts:
                        continue

                    color = (0, 0, 255) if is_burnt else colors.get(label, (0, 255, 0))
                    cv2.rectangle(image, (left, top), (left + width, top + height), color, 2)
                    if toast_id is not None:
                        label_text = f"Tostada #{toast_id} ({label}) {confidence:.2f}"
                    else:
                        label_text = f"{label} {confidence:.2f}"
                    cv2.putText(image, label_text, (left, top - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)

                # This is the one canonical frame for both RTSP and the local preview.
                output = cv2.resize(
                    image,
                    (self.stream_config.width, self.stream_config.height),
                    interpolation=cv2.INTER_AREA,
                )
                if output.ndim == 2:
                    output = cv2.cvtColor(output, cv2.COLOR_GRAY2BGR)
                elif output.ndim != 3 or output.shape[2] != 3:
                    raise ValueError("la salida de vídeo no tiene tres canales BGR")
                if output.dtype != np.uint8:
                    output = output.astype(np.uint8)
                output = np.ascontiguousarray(output)

                try:
                    published = publisher.publish(output)
                    if published is False:
                        print("[Thread] Publisher RTSP rechazó un frame; continúa la vista local")
                except Exception as exc:
                    print(f"[Thread] Error publicando frame RTSP: {exc}")

                rgb_image = cv2.cvtColor(output, cv2.COLOR_BGR2RGB)
                h, w, ch = rgb_image.shape
                qt_image = QImage(
                    rgb_image.data, w, h, ch * w, QImage.Format_RGB888
                ).copy()
                self.change_pixmap_signal.emit(qt_image)

                # Event.wait is interruptible, unlike time.sleep.
                elapsed = time.time() - start_time
                sleep_time = self.frame_time - elapsed
                if sleep_time > 0:
                    self._stop_event.wait(sleep_time)
        except Exception as exc:
            print(f"[Thread] Error en el procesamiento de video: {exc}")
        finally:
            # Keep this order: stop any FFmpeg activity before releasing input.
            if publisher is not None:
                try:
                    publisher.stop()
                except Exception as exc:
                    print(f"[Thread] Error al detener el publisher RTSP: {exc}")
            if cap is not None:
                try:
                    cap.release()
                except Exception as exc:
                    print(f"[Thread] Error al liberar la captura: {exc}")

        if self.seen_toasts:
            self.emit_batch_metrics()

    def emit_batch_metrics(self):
        if not self.seen_toasts:
            return

        fin_at = datetime.now()
        
        # Contar correctos, quemados, crudas
        quemados_count = 0
        correctos_count = 0
        crudas_count = 0
        
        for t_id, state in self.seen_toasts.items():
            if state == "burnt":
                quemados_count += 1
            else:
                correctos_count += 1
                
        # Simular una cantidad pequeña de crudas a partir del total de correctos
        if correctos_count > 0:
            crudas_count = random.randint(0, min(3, correctos_count // 10 + 1))
            correctos_count -= crudas_count
            
        total_unidades = correctos_count + quemados_count + crudas_count
        
        # Calcular pesos en Kg (usando un estimado de 0.5 Kg por tostada)
        # PESO PROMEDIO PARA UNA TOSTADA
        weight_per_toast = 0.025
        correctos_kg = round(correctos_count * weight_per_toast, 2)
        quemados_kg = round(quemados_count * weight_per_toast, 2)
        crudos_kg = round(crudas_count * weight_per_toast, 2)
        
        # Calcular promedios de sensores
        # Como leemos la info del horno?
        temp_h1 = round(sum(self.temperatures_horno1) / len(self.temperatures_horno1), 2) if self.temperatures_horno1 else 220.0
        temp_c1 = round(sum(self.temperatures_comb1) / len(self.temperatures_comb1), 2) if self.temperatures_comb1 else 315.0
        temp_h2 = round(sum(self.temperatures_horno2) / len(self.temperatures_horno2), 2) if self.temperatures_horno2 else 218.0
        temp_c2 = round(sum(self.temperatures_comb2) / len(self.temperatures_comb2), 2) if self.temperatures_comb2 else 312.0
        vel_cinta = round(sum(self.velocidades_cinta) / len(self.velocidades_cinta), 2) if self.velocidades_cinta else 1.10
        
        # Determinar turno
        hour = self.inicio_at.hour
        if 6 <= hour < 14:
            turno = "mañana"
        elif 14 <= hour < 19:
            turno = "tarde"
        else:
            turno = "noche"
            
        payload = {
            "productoId": "a1b2c3d4-5678-90ab-cdef-1234567890ab",
            "turno": turno,
            "inicioAt": self.inicio_at.isoformat() + "Z",
            "finAt": fin_at.isoformat() + "Z",
            "totalUnidades": total_unidades,
            "correctos": correctos_count,
            "quemados": quemados_count,
            "crudas": crudas_count,
            "correctosKg": correctos_kg,
            "quemadosKg": quemados_kg,
            "crudosKg": crudos_kg,
            "tempHorno1": temp_h1,
            "tempCombHorno1": temp_c1,
            "tempHorno2": temp_h2,
            "tempCombHorno2": temp_c2,
            "velocidadCinta": vel_cinta
        }
        
        self.lote_completed_signal.emit(payload)

    def stop(self):
        self.running = False
        self._stop_event.set()


class FactoryControlApp(QMainWindow):
    def __init__(self, default_source="road.mp4"):
        super().__init__()
        self.setWindowTitle("SISTEMA DE CONTROL INDUSTRIAL - PYSIDE6")
        self.resize(1200, 800)
        self.setStyleSheet(QSS)
        
        # Inicializar componentes del Backend (Clean Architecture)
        self.iot_controller = MockIoTController()
        self.http_client = RequestsHttpClient()
        # Detección automática de plataforma (Raspberry Pi con chip Hailo)
        is_pi = False
        try:
            if os.path.exists('/sys/firmware/devicetree/base/model'):
                with open('/sys/firmware/devicetree/base/model', 'r') as f:
                    is_pi = 'raspberry pi' in f.read().lower()
        except Exception:
            pass

        self.is_running_on_npu = is_pi and HAILO_AVAILABLE

        # Rutas iniciales de modelos según la plataforma
        if self.is_running_on_npu:
            self.current_model = "/usr/share/hailo-models/yolov8s_h8l.hef"
            self.current_names = "yolov11-python/data/class.names"
            print("[INFO] Raspberry Pi con NPU detectada. Usando YOLOv8s HEF por defecto.")
        else:
            self.current_model = "yolov11-python/yolo11n.onnx"
            self.current_names = "yolov11-python/data/class.names"
            print("[INFO] PC o simulador detectado. Usando YOLOv11 ONNX por defecto.")
        
        # Instanciar el detector de YOLO (Capa de infraestructura)
        try:
            model_path = resolve_path(self.current_model)
            names_path = resolve_path(self.current_names)
            self.detector = YoloDetector(model_path=model_path, names_path=names_path)
            self.detector_status = "Hailo NPU Activo" if getattr(self.detector, 'use_hailo', False) else "ONNX Activo"
        except Exception as e:
            print(f"[GUI App] Error al inicializar detector YOLO: {e}")
            class MockDetector:
                def detect_frame(self, frame): return []
                def get_class_names(self): return ["Tostada Quemada", "tostadas ok"]
            self.detector = MockDetector()
            self.detector_status = f"Simulado (Error: {str(e)[:25]})"

        # Instanciar Casos de Uso
        self.detect_use_case = DetectAndNotifyUseCase(self.detector, self.iot_controller, self.http_client)
        self.control_device_use_case = ControlDeviceUseCase(self.iot_controller)
        
        # Filtrado de clases visible por defecto
        self.show_ok_toasts = True
        self.show_burnt_toasts = True
        
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        main_layout = QHBoxLayout(central_widget)
        main_layout.setContentsMargins(0, 0, 20, 20)
        main_layout.setSpacing(20)

        # 2. BARRA LATERAL IZQUIERDA (SIDEBAR)
        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(250)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(15, 30, 15, 30)

        logo_label = QLabel("FACTORY CONTROL\nPYSIDE6")
        logo_label.setAlignment(Qt.AlignCenter)
        logo_label.setStyleSheet("font-size: 18px; margin-bottom: 20px;")
        sidebar_layout.addWidget(logo_label)

        self.nav_buttons = []
        nav_titles = ["📸 Cámara en Vivo", "📊 Estadísticas", "💻 Datos del PC", "👥 Usuarios"]
        for i, btn_text in enumerate(nav_titles):
            btn = QPushButton(btn_text)
            if i == 0:
                btn.setCheckable(True)
                btn.clicked.connect(self.toggle_camera)
            sidebar_layout.addWidget(btn)
            self.nav_buttons.append(btn)

        sidebar_layout.addSpacerItem(QSpacerItem(20, 20, QSizePolicy.Minimum, QSizePolicy.Fixed))

        # --- SELECCIONADOR DE MODELOS ---
        model_label = QLabel("🧠 MODELO ACTIVO")
        model_label.setAlignment(Qt.AlignLeft)
        model_label.setStyleSheet("font-size: 12px; margin-left: 12px; color: #E0B0FF;")
        sidebar_layout.addWidget(model_label)

        self.model_selector = QComboBox()
        self.model_selector.addItem("YOLOv11 Original (COCO)")
        self.model_selector.addItem("YOLOv11 Tostadas V1 (Custom)")
        self.model_selector.addItem("YOLOv11 Tostadas V2 (Custom)")
        self.model_selector.addItem("YOLOv8s NPU (Hailo-8L COCO)")
        self.model_selector.setCurrentIndex(3 if getattr(self, 'is_running_on_npu', False) else 0)
        self.model_selector.currentIndexChanged.connect(self.change_model)
        sidebar_layout.addWidget(self.model_selector)

        sidebar_layout.addSpacerItem(QSpacerItem(20, 40, QSizePolicy.Minimum, QSizePolicy.Expanding))

        user_label = QLabel("USUARIO: ADMIN")
        user_label.setAlignment(Qt.AlignCenter)
        user_label.setStyleSheet("font-size: 14px; margin-bottom: 10px; color: white;")
        sidebar_layout.addWidget(user_label)

        logout_btn = QPushButton("CERRAR SESIÓN")
        logout_btn.setObjectName("BtnLogout")
        sidebar_layout.addWidget(logout_btn)

        main_layout.addWidget(sidebar)

        # 3. ÁREA CENTRAL Y PANEL DERECHO
        content_layout = QHBoxLayout()
        content_layout.setContentsMargins(0, 20, 0, 0)
        content_layout.setSpacing(20)

        # ÁREA DE VIDEO CENTRAL + LISTA DE VIDEOS
        video_area_layout = QVBoxLayout()
        video_area_layout.setSpacing(15)

        video_frame = QFrame()
        video_frame.setObjectName("VideoFrame")
        video_layout = QVBoxLayout(video_frame)
        video_layout.setContentsMargins(15, 15, 15, 15)
        
        self.video_label = QLabel()
        self.video_label.setAlignment(Qt.AlignCenter)
        self.video_label.setStyleSheet("background-color: #1E1E28; border-radius: 10px;")
        video_layout.addWidget(self.video_label)
        
        video_area_layout.addWidget(video_frame, stretch=7)

        # GALERÍA DE VIDEOS (QScrollArea)
        gallery_frame = QFrame()
        gallery_frame.setObjectName("GalleryFrame")
        gallery_layout = QVBoxLayout(gallery_frame)
        gallery_layout.setContentsMargins(15, 10, 15, 10)
        
        gallery_title = QLabel("GALERÍA DE VIDEOS")
        gallery_title.setStyleSheet("color: #E0B0FF; font-weight: bold; font-size: 14px;")
        gallery_layout.addWidget(gallery_title)

        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setStyleSheet("background-color: transparent; border: none;")
        
        scroll_content = QWidget()
        scroll_content.setStyleSheet("background-color: transparent;")
        self.videos_layout = QVBoxLayout(scroll_content)
        self.videos_layout.setAlignment(Qt.AlignTop)
        
        # Escaneo de Carpeta usando os
        videos_path = resolve_path("yolov11-python/data/videos")
        if os.path.exists(videos_path):
            videos = [f for f in os.listdir(videos_path) if f.endswith('.mp4')]
            for video in videos:
                btn = QPushButton(video)
                btn.setProperty("class", "VideoBtn")
                btn.clicked.connect(lambda checked=False, btn_ref=btn: self.play_internal_target(btn_ref.text()))
                self.videos_layout.addWidget(btn)
        else:
            no_videos_lbl = QLabel(f"No se encontró la ruta: {videos_path}")
            no_videos_lbl.setStyleSheet("color: gray;")
            self.videos_layout.addWidget(no_videos_lbl)

        scroll_area.setWidget(scroll_content)
        gallery_layout.addWidget(scroll_area)

        video_area_layout.addWidget(gallery_frame, stretch=3)

        content_layout.addLayout(video_area_layout, stretch=7)

        # PANEL DERECHO DE INFORMACIÓN
        info_panel_layout = QVBoxLayout()
        info_panel_layout.setSpacing(15)

        # CARD 1: HISTORIAL DE ALERTAS
        card_alerts = QFrame()
        card_alerts.setProperty("class", "Card")
        alerts_layout = QVBoxLayout(card_alerts)
        alerts_title = QLabel("🚨 HISTORIAL DE ALERTAS")
        alerts_title.setStyleSheet("font-weight: bold; font-size: 13px; color: #FF3333;")
        alerts_layout.addWidget(alerts_title)

        scroll_alerts = QScrollArea()
        scroll_alerts.setWidgetResizable(True)
        scroll_alerts.setStyleSheet("background-color: transparent; border: none;")
        
        scroll_alerts_content = QWidget()
        scroll_alerts_content.setStyleSheet("background-color: transparent;")
        self.alerts_log_layout = QVBoxLayout(scroll_alerts_content)
        self.alerts_log_layout.setAlignment(Qt.AlignTop)
        
        self.no_alerts_lbl = QLabel("No se detectan tostadas quemadas.\nEsperando...")
        self.no_alerts_lbl.setStyleSheet("color: #888888; font-size: 12px; font-style: italic;")
        self.alerts_log_layout.addWidget(self.no_alerts_lbl)
        
        scroll_alerts.setWidget(scroll_alerts_content)
        alerts_layout.addWidget(scroll_alerts)
        
        info_panel_layout.addWidget(card_alerts, stretch=2)

        # CARD 2: FILTRO DE DETECCIONES
        card_filter = QFrame()
        card_filter.setProperty("class", "Card")
        filter_layout = QVBoxLayout(card_filter)
        filter_title = QLabel("🔍 FILTRO DE DETECCIONES")
        filter_title.setStyleSheet("font-weight: bold; font-size: 13px; color: #00FFCC;")
        filter_layout.addWidget(filter_title)
        
        # Botón Filtro Tostadas OK
        self.btn_filter_ok = QPushButton()
        self.btn_filter_ok.clicked.connect(self.toggle_filter_ok)
        filter_layout.addWidget(self.btn_filter_ok)
        
        # Botón Filtro Tostadas Quemadas
        self.btn_filter_burnt = QPushButton()
        self.btn_filter_burnt.clicked.connect(self.toggle_filter_burnt)
        filter_layout.addWidget(self.btn_filter_burnt)
        
        self.update_filter_button_styles()
        
        info_panel_layout.addWidget(card_filter, stretch=1)

        # CARD 3: DISPOSITIVOS IOT
        card_iot = QFrame()
        card_iot.setProperty("class", "Card")
        iot_layout = QVBoxLayout(card_iot)
        iot_title = QLabel("🔌 DISPOSITIVOS IOT")
        iot_title.setStyleSheet("font-weight: bold; font-size: 13px; color: #00FFCC;")
        iot_layout.addWidget(iot_title)

        # Relé Tostadora Row
        toaster_layout = QHBoxLayout()
        self.toaster_lbl = QLabel("Relé Tostadora: APAGADO")
        self.toaster_lbl.setStyleSheet("font-size: 11px; color: #E0B0FF;")
        toaster_layout.addWidget(self.toaster_lbl)
        
        self.toaster_btn = QPushButton("Encender")
        self.toaster_btn.clicked.connect(self.toggle_toaster)
        toaster_layout.addWidget(self.toaster_btn)
        iot_layout.addLayout(toaster_layout)

        # Buzzer Alarma Row
        alarm_layout = QHBoxLayout()
        self.alarm_lbl = QLabel("Buzzer Alarma: APAGADO")
        self.alarm_lbl.setStyleSheet("font-size: 11px; color: #E0B0FF;")
        alarm_layout.addWidget(self.alarm_lbl)
        
        self.alarm_btn = QPushButton("Encender")
        self.alarm_btn.clicked.connect(self.toggle_alarm)
        alarm_layout.addWidget(self.alarm_btn)
        iot_layout.addLayout(alarm_layout)

        info_panel_layout.addWidget(card_iot, stretch=1)

        content_layout.addLayout(info_panel_layout, stretch=3)
        main_layout.addLayout(content_layout)

        # Actualizar estilo visual inicial de los labels y botones de IoT
        self.update_iot_status_labels()

        self.yolo_thread = None
        self._shutdown_thread = None
        self._pending_action = None
        self._closing_requested = False
        self._recovery_required = False
        self._shutdown_timer = QTimer(self)
        self._shutdown_timer.setSingleShot(True)
        self._shutdown_timer.timeout.connect(self._on_shutdown_timeout)
        self.play_internal_target(default_source)

    def toggle_filter_ok(self):
        self.show_ok_toasts = not self.show_ok_toasts
        self.update_filter_button_styles()
        if self.yolo_thread is not None:
            self.yolo_thread.show_ok_toasts = self.show_ok_toasts

    def toggle_filter_burnt(self):
        self.show_burnt_toasts = not self.show_burnt_toasts
        self.update_filter_button_styles()
        if self.yolo_thread is not None:
            self.yolo_thread.show_burnt_toasts = self.show_burnt_toasts

    def update_filter_button_styles(self):
        # Botón Tostadas OK
        if self.show_ok_toasts:
            self.btn_filter_ok.setText("Tostadas OK: VISIBLES")
            self.btn_filter_ok.setStyleSheet("""
                QPushButton {
                    background-color: rgba(0, 255, 204, 0.15);
                    color: #00FFCC;
                    border: 1px solid #00FFCC;
                    border-radius: 5px;
                    padding: 5px 10px;
                    font-weight: bold;
                }
                QPushButton:hover {
                    background-color: rgba(0, 255, 204, 0.25);
                }
            """)
        else:
            self.btn_filter_ok.setText("Tostadas OK: OCULTAS")
            self.btn_filter_ok.setStyleSheet("""
                QPushButton {
                    background-color: rgba(255, 255, 255, 0.05);
                    color: #888888;
                    border: 1px solid #555555;
                    border-radius: 5px;
                    padding: 5px 10px;
                    font-weight: bold;
                }
                QPushButton:hover {
                    background-color: rgba(255, 255, 255, 0.1);
                }
            """)

        # Botón Tostadas Quemadas
        if self.show_burnt_toasts:
            self.btn_filter_burnt.setText("Tostadas Quemadas: VISIBLES")
            self.btn_filter_burnt.setStyleSheet("""
                QPushButton {
                    background-color: rgba(0, 255, 204, 0.15);
                    color: #00FFCC;
                    border: 1px solid #00FFCC;
                    border-radius: 5px;
                    padding: 5px 10px;
                    font-weight: bold;
                }
                QPushButton:hover {
                    background-color: rgba(0, 255, 204, 0.25);
                }
            """)
        else:
            self.btn_filter_burnt.setText("Tostadas Quemadas: OCULTAS")
            self.btn_filter_burnt.setStyleSheet("""
                QPushButton {
                    background-color: rgba(255, 255, 255, 0.05);
                    color: #888888;
                    border: 1px solid #555555;
                    border-radius: 5px;
                    padding: 5px 10px;
                    font-weight: bold;
                }
                QPushButton:hover {
                    background-color: rgba(255, 255, 255, 0.1);
                }
            """)

    def toggle_toaster(self):
        is_on = self.iot_controller.get_status("rele_tostadora")
        if is_on:
            self.control_device_use_case.turn_off_device("rele_tostadora")
        else:
            self.control_device_use_case.turn_on_device("rele_tostadora")
        self.update_iot_status_labels()

    def toggle_alarm(self):
        is_on = self.iot_controller.get_status("alarma_buzzer")
        if is_on:
            self.control_device_use_case.turn_off_device("alarma_buzzer")
        else:
            self.control_device_use_case.turn_on_device("alarma_buzzer")
        self.update_iot_status_labels()

    def update_iot_status_labels(self):
        # Actualizar Relé Tostadora
        toaster_on = self.iot_controller.get_status("rele_tostadora")
        if toaster_on:
            self.toaster_lbl.setText("Relé Tostadora: ENCENDIDO")
            self.toaster_lbl.setStyleSheet("font-size: 11px; color: #FF3333; font-weight: bold;")
            self.toaster_btn.setText("Apagar")
            self.toaster_btn.setStyleSheet("""
                QPushButton {
                    background-color: #D9534F;
                    color: white;
                    border: 1px solid #D9534F;
                    border-radius: 5px;
                    padding: 5px 10px;
                    font-weight: bold;
                }
                QPushButton:hover {
                    background-color: #C9302C;
                }
            """)
        else:
            self.toaster_lbl.setText("Relé Tostadora: APAGADO")
            self.toaster_lbl.setStyleSheet("font-size: 11px; color: #E0B0FF;")
            self.toaster_btn.setText("Encender")
            self.toaster_btn.setStyleSheet("""
                QPushButton {
                    background-color: #2D1B4E;
                    color: #00FFCC;
                    border: 1px solid #00FFCC;
                    border-radius: 5px;
                    padding: 5px 10px;
                    font-weight: bold;
                }
                QPushButton:hover {
                    background-color: #00FFCC;
                    color: #1A0B2E;
                }
            """)

        # Actualizar Buzzer de Alarma
        alarm_on = self.iot_controller.get_status("alarma_buzzer")
        if alarm_on:
            self.alarm_lbl.setText("Buzzer Alarma: ENCENDIDO")
            self.alarm_lbl.setStyleSheet("font-size: 11px; color: #FF3333; font-weight: bold;")
            self.alarm_btn.setText("Apagar")
            self.alarm_btn.setStyleSheet("""
                QPushButton {
                    background-color: #D9534F;
                    color: white;
                    border: 1px solid #D9534F;
                    border-radius: 5px;
                    padding: 5px 10px;
                    font-weight: bold;
                }
                QPushButton:hover {
                    background-color: #C9302C;
                }
            """)
        else:
            self.alarm_lbl.setText("Buzzer Alarma: APAGADO")
            self.alarm_lbl.setStyleSheet("font-size: 11px; color: #E0B0FF;")
            self.alarm_btn.setText("Encender")
            self.alarm_btn.setStyleSheet("""
                QPushButton {
                    background-color: #2D1B4E;
                    color: #00FFCC;
                    border: 1px solid #00FFCC;
                    border-radius: 5px;
                    padding: 5px 10px;
                    font-weight: bold;
                }
                QPushButton:hover {
                    background-color: #00FFCC;
                    color: #1A0B2E;
                }
            """)

    def add_alert_log(self, message):
        if hasattr(self, 'no_alerts_lbl') and self.no_alerts_lbl is not None:
            self.no_alerts_lbl.deleteLater()
            self.no_alerts_lbl = None
            
        t = time.strftime('%H:%M:%S')
        log_lbl = QLabel(f"[{t}] {message}")
        log_lbl.setWordWrap(True)
        log_lbl.setStyleSheet("color: #FF3333; font-size: 11px; font-weight: bold; margin-bottom: 2px;")
        self.alerts_log_layout.insertWidget(0, log_lbl)

    # Lógica de cambio de modelo dinámico
    def change_model(self, index):
        if self._recovery_required:
            self._show_recovery_required()
            return

        active_worker = (
            (self.yolo_thread is not None and self.yolo_thread.isRunning())
            or self._shutdown_thread is not None
        )
        stream_config, publisher_factory = self._stream_setup(
            allow_preview_fallback=not active_worker
        )
        if stream_config is None:
            return

        if index == 0:
            self.current_model = "yolov11-python/yolo11n.onnx"
            self.current_names = "yolov11-python/data/class.names"
            print("[INFO] Frente cambiado al Modelo Original (YOLOv11 COCO)")
        elif index == 1:
            self.current_model = "yolov11-python/tostadas_v1.onnx"
            self.current_names = "yolov11-python/tostadas_v1.names"
            print("[INFO] Frente cambiado al Modelo de Tostadas V1 (Personalizado)")
        elif index == 2:
            self.current_model = "yolov11-python/tostadas_v2.onnx"
            self.current_names = "yolov11-python/tostadas_v2.names"
            print("[INFO] Frente cambiado al Modelo de Tostadas V2 (Personalizado)")
        elif index == 3:
            self.current_model = "/usr/share/hailo-models/yolov8s_h8l.hef"
            self.current_names = "yolov11-python/data/class.names"
            print("[INFO] Frente cambiado al Modelo Acelerado por NPU (YOLOv8s Hailo-8L COCO)")
            
        model_path = resolve_path(self.current_model)
        names_path = resolve_path(self.current_names)

        # Keep the exact active source, rather than reconstructing a gallery
        # filename from it after the asynchronous shutdown.
        active_source = None
        if self.yolo_thread is not None and self.yolo_thread.isRunning():
            active_source = self.yolo_thread.source_file
            if active_source != "0":
                active_source = os.path.abspath(active_source)

        if active_source is not None:
            self._request_thread_shutdown(
                pending_action=lambda: self._finish_model_change(
                    model_path, names_path, active_source,
                    stream_config, publisher_factory,
                )
            )
            return

        self._finish_model_change(
            model_path, names_path, active_source,
            stream_config, publisher_factory,
        )

    def _finish_model_change(self, model_path, names_path, active_source,
                             stream_config, publisher_factory):
        """Apply a model change only after the previous worker has finished."""
        # This method is only used as the finished callback for a running
        # worker, or directly when no worker is active.  A timeout clears the
        # pending action, so this release cannot race a live capture.
        if hasattr(self, 'detector') and self.detector is not None:
            print("[GUI App] Liberando recursos del detector anterior...")
            if hasattr(self.detector, 'release_hailo'):
                try:
                    self.detector.release_hailo()
                except Exception as e:
                    print(f"[GUI App] Error al liberar NPU: {e}")
            del self.detector
            self.detector = None
            
        try:
            self.detector = YoloDetector(model_path=model_path, names_path=names_path)
            self.detector_status = "Hailo NPU Activo" if getattr(self.detector, 'use_hailo', False) else "ONNX Activo"
        except Exception as e:
            print(f"[GUI App] Error al cambiar detector YOLO: {e}")
            class MockDetector:
                def detect_frame(self, frame): return []
                def get_class_names(self): return ["Tostada Quemada", "tostadas ok"]
            self.detector = MockDetector()
            self.detector_status = f"Simulado (Error: {str(e)[:25]})"

        self.detect_use_case = DetectAndNotifyUseCase(self.detector, self.iot_controller, self.http_client)

        if active_source is not None:
            self._start_internal_target(
                active_source,
                source_path_override=active_source,
                stream_config=stream_config,
                publisher_factory=publisher_factory,
                model_path=model_path,
                names_path=names_path,
            )

    def _disconnect_worker_signals(self, thread):
        """Disconnect UI consumers while a worker is winding down."""
        for signal in (
            thread.change_pixmap_signal,
            thread.lote_completed_signal,
            thread.iot_status_changed_signal,
            thread.burned_toast_alert_signal,
        ):
            try:
                signal.disconnect()
            except (TypeError, RuntimeError):
                pass

    def _show_recovery_required(self):
        self._recovery_required = True
        message = "Se requiere recuperación: no se pudo detener el vídeo anterior"
        print(f"[GUI App] {message}")
        if hasattr(self, "video_label"):
            self.video_label.clear()
            self.video_label.setText(message)
            self.video_label.setStyleSheet(
                "background-color: #2A1B2E; border-radius: 10px; "
                "color: #FF4500; font-weight: bold;"
            )
        if hasattr(self, "add_alert_log") and hasattr(self, "alerts_log_layout"):
            self.add_alert_log(message)

    def _on_shutdown_timeout(self):
        thread = self._shutdown_thread
        if thread is None:
            return
        # A finished signal can be queued behind this timer event.
        if not thread.isRunning():
            self._on_worker_finished()
            return

        # Do not release or terminate a potentially blocked native capture.
        # Keeping yolo_thread is deliberate: no replacement may be created
        # until the operator recovers this still-running worker.
        try:
            thread.finished.disconnect(self._on_worker_finished)
        except (TypeError, RuntimeError):
            pass
        self._pending_action = None
        self._shutdown_thread = None
        self._show_recovery_required()

    @Slot()
    def _on_worker_finished(self):
        thread = self._shutdown_thread
        if thread is None:
            return
        self._shutdown_timer.stop()
        try:
            thread.finished.disconnect(self._on_worker_finished)
        except (TypeError, RuntimeError):
            pass
        self._disconnect_worker_signals(thread)
        self._shutdown_thread = None
        if self.yolo_thread is thread:
            self.yolo_thread = None

        pending_action = self._pending_action
        self._pending_action = None
        closing = self._closing_requested
        self._closing_requested = False
        if closing:
            # The second closeEvent is accepted only after QThread has emitted
            # finished, never while its native capture may still be running.
            self.close()
        elif pending_action is not None and not self._recovery_required:
            pending_action()

    def _request_thread_shutdown(self, pending_action=None, closing=False):
        """Request stop and queue work until the old QThread has finished."""
        if self._recovery_required and pending_action is not None:
            self._show_recovery_required()
            return False

        if self._shutdown_thread is not None:
            self._pending_action = pending_action
            self._closing_requested = self._closing_requested or closing
            return False

        thread = self.yolo_thread
        if thread is None:
            if pending_action is not None and not closing:
                pending_action()
            return True

        self._disconnect_worker_signals(thread)
        if not thread.isRunning():
            if self.yolo_thread is thread:
                self.yolo_thread = None
            if pending_action is not None and not closing:
                pending_action()
            return True

        self._shutdown_thread = thread
        self._pending_action = pending_action
        self._closing_requested = closing
        thread.finished.connect(self._on_worker_finished)
        thread.stop()  # Nonblocking; capture.release remains in worker.run().
        self._shutdown_timer.start(5000)
        return False

    # Lógica Botón Cámara (Manejo de estado)
    def toggle_camera(self, checked):
        if checked:
            self.play_internal_target("0")
            return

        self.video_label.clear()
        self.video_label.setText("Cámara Apagada")
        self.video_label.setStyleSheet("background-color: #1E1E28; border-radius: 10px; color: #E0B0FF;")
        self._request_thread_shutdown()

    def play_internal_target(self, video_name):
        if self._recovery_required:
            self._show_recovery_required()
            return

        active_worker = (
            (self.yolo_thread is not None and self.yolo_thread.isRunning())
            or self._shutdown_thread is not None
        )
        stream_config, publisher_factory = self._stream_setup(
            allow_preview_fallback=not active_worker
        )
        if stream_config is None:
            return

        def start_target():
            self._start_internal_target(
                video_name,
                stream_config=stream_config,
                publisher_factory=publisher_factory,
            )

        self._request_thread_shutdown(pending_action=start_target)

    def _show_video_start_error(self, message):
        print(f"[GUI App] {message}")
        self.video_label.clear()
        self.video_label.setText(message)
        self.video_label.setStyleSheet(
            "background-color: #2A1B2E; border-radius: 10px; "
            "color: #FF4500; font-weight: bold; text-align: center;"
        )

    def _show_streaming_disabled(self, error, preview_only):
        if preview_only:
            message = (
                "Streaming deshabilitado (configuración inválida); "
                "vista local en modo preview"
            )
        else:
            message = (
                "Streaming deshabilitado: configuración inválida; "
                "se conserva la fuente activa"
            )
        print(f"[GUI App] {message}: {error}")
        if hasattr(self, "video_label") and preview_only:
            self._show_video_start_error(message)
        if hasattr(self, "add_alert_log") and hasattr(self, "alerts_log_layout"):
            self.add_alert_log(message)

    def _stream_setup(self, allow_preview_fallback):
        try:
            config = validate_stream_config(StreamConfig.from_env())
            return config, None
        except Exception as exc:
            self._show_streaming_disabled(exc, allow_preview_fallback)
            if not allow_preview_fallback:
                return None, None
            # StreamConfig() is a known-good, even-dimension preview config.
            return validate_stream_config(StreamConfig()), PreviewOnlyPublisher

    def _validated_stream_config(self, stream_config=None):
        try:
            config = stream_config if stream_config is not None else StreamConfig.from_env()
            return validate_stream_config(config)
        except Exception as exc:
            self._show_video_start_error(f"Error de configuración de vídeo: {exc}")
            return None

    def _start_internal_target(self, video_name, source_path_override=None,
                               stream_config=None, model_path=None,
                               names_path=None, publisher_factory=None):
        if source_path_override is not None:
            source_path = source_path_override
            if source_path != "0":
                self.nav_buttons[0].setChecked(False)
        elif video_name != "0":
            self.nav_buttons[0].setChecked(False)
            videos_dir = resolve_path("yolov11-python/data/videos")
            if os.path.isabs(video_name) or video_name.startswith("multimedia/videos") or video_name.startswith("yolov11-python/"):
                source_path = resolve_path(video_name)
            else:
                source_path = os.path.join(videos_dir, video_name)
        else:
            source_path = "0"

        stream_config = self._validated_stream_config(stream_config)
        if stream_config is None:
            return

        resolved_model = model_path if model_path is not None else resolve_path(self.current_model)
        resolved_names = names_path if names_path is not None else resolve_path(self.current_names)
        if not os.path.exists(resolved_model) or not os.path.exists(resolved_names):
            self._show_video_start_error(
                f"Error: No se encontró el modelo o las etiquetas\nCargar: {os.path.basename(resolved_model)}"
            )
            return

        self.yolo_thread = YOLODetectionThread(
            source_path,
            self.detect_use_case,
            stream_config=stream_config,
            publisher_factory=publisher_factory,
        )
        self.yolo_thread.show_ok_toasts = self.show_ok_toasts
        self.yolo_thread.show_burnt_toasts = self.show_burnt_toasts
        self.yolo_thread.change_pixmap_signal.connect(self.update_image)
        self.yolo_thread.iot_status_changed_signal.connect(self.update_iot_status_labels)
        self.yolo_thread.burned_toast_alert_signal.connect(self.add_alert_log)
        self.yolo_thread.lote_completed_signal.connect(self.handle_lote_completed)
        self.yolo_thread.start()

    @Slot(dict)
    def handle_lote_completed(self, payload):
        print(f"[GUI App] Lote completado. Enviando POST con payload: {payload}")
        url = "http://localhost:8000/api/lotes/finalizar"
        
        # Enviar petición HTTP POST al backend local
        success = self.http_client.post(url, payload)
        if success:
            print("[GUI App] Lote registrado exitosamente en el servidor central a través del backend.")
            self.add_alert_log(f"¡LOTE REGISTRADO! Unidades: {payload['totalUnidades']} (OK: {payload['correctos']}, Q: {payload['quemados']}, C: {payload['crudas']})")
        else:
            print(f"[GUI App] Error al registrar el lote: {self.http_client.last_error}")
            self.add_alert_log(f"Error al enviar lote: {str(self.http_client.last_error)[:50]}")

    @Slot(QImage)
    def update_image(self, qt_image):
        pixmap = QPixmap.fromImage(qt_image).scaled(
            self.video_label.width(), 
            self.video_label.height(), 
            Qt.KeepAspectRatio, 
            Qt.SmoothTransformation
        )
        self.video_label.setPixmap(pixmap)

    def closeEvent(self, event):
        # A close request is asynchronous: QThread must finish before Qt may
        # destroy the window and its worker-owned capture.
        if self.yolo_thread is not None and self.yolo_thread.isRunning():
            self._request_thread_shutdown(closing=True)
            event.ignore()
            return
        self._request_thread_shutdown(closing=True)
        event.accept()

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Ejecutar la interfaz gráfica de detección")
    parser.add_argument(
        "--source", 
        type=str, 
        default="road.mp4", 
        help="Fuente del video: '0' para la cámara de la Raspberry Pi, o la ruta de un video"
    )
    args, unknown = parser.parse_known_args()

    app = QApplication(sys.argv)
    window = FactoryControlApp(default_source=args.source)
    window.show()
    sys.exit(app.exec())
