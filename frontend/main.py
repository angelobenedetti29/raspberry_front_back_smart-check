import sys
import os
import cv2
import numpy as np
import random
import time
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QHBoxLayout, 
                               QVBoxLayout, QPushButton, QLabel, QFrame, QProgressBar, 
                               QSpacerItem, QSizePolicy, QScrollArea, QComboBox)
from PySide6.QtCore import Qt, QThread, Signal, Slot
from PySide6.QtGui import QImage, QPixmap

# Backend imports (Clean Architecture)
from backend.infrastructure.ai.yolo_detector import YoloDetector
from backend.infrastructure.iot.mock_controller import MockIoTController
from backend.infrastructure.http.requests_client import RequestsHttpClient
from backend.use_cases.detect_and_notify import DetectAndNotifyUseCase
from backend.use_cases.control_device import ControlDeviceUseCase

def resolve_path(relative_path):
    """
    Resolves paths to make sure they work when executed from either:
    1. The project root (yolov11-python)
    2. The parent directory of the project root (test_tesis_00)
    3. Anywhere else, by checking alternative directory structures.
    """
    if not relative_path:
        return relative_path

    # Try exact path first
    if os.path.exists(relative_path):
        return relative_path

    # Standardize path separators
    normalized = relative_path.replace("\\", "/")

    # If it starts with yolov11-python/ but we are already in yolov11-python, strip the prefix
    if normalized.startswith("yolov11-python/"):
        stripped = normalized[len("yolov11-python/"):]
        if os.path.exists(stripped):
            return stripped

    # If we are in parent directory and need to prepend yolov11-python/
    if not normalized.startswith("yolov11-python/"):
        prefixed = f"yolov11-python/{normalized}"
        if os.path.exists(prefixed):
            return prefixed

    # Specific mapping for different directory layouts inside yolov11-python
    mapping = {
        "yolov11-python/yolo11n.onnx": "ai_training/models/yolo11n.onnx",
        "yolov11-python/tostadas.onnx": "ai_training/models/tostadas.onnx",
        "yolov11-python/data/class.names": "ai_training/models/class.names",
        "yolov11-python/data/tostadas.names": "ai_training/models/tostadas.names",
        "yolov11-python/data/videos/road.mp4": "multimedia/videos/road.mp4",
        "yolov11-python/data/videos": "multimedia/videos"
    }
    
    # Try mapping
    for key, val in mapping.items():
        if normalized == key or normalized == key.replace("yolov11-python/", ""):
            # Check val directly
            if os.path.exists(val):
                return val
            # Check with prefix
            prefixed_val = f"yolov11-python/{val}"
            if os.path.exists(prefixed_val):
                return prefixed_val

    return relative_path

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

    def __init__(self, source_file, detect_use_case):
        super().__init__()
        self.source_file = resolve_path(source_file)
        self.detect_use_case = detect_use_case
        self.running = True

    def run(self):
        cv_source = 0 if self.source_file == "0" else self.source_file
        cap = cv2.VideoCapture(cv_source)
        
        if not cap.isOpened():
            print(f"No se pudo abrir la fuente de video: {cv_source}")
            return

        # Generar colores de clases dinámicamente
        try:
            class_names = self.detect_use_case.detector.get_class_names()
        except Exception:
            class_names = []
            
        colors = {name: (random.randint(0, 255), random.randint(0, 255), random.randint(0, 255)) for name in class_names}

        while self.running:
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

            # Filtrar si se detectaron tostadas quemadas para emitir alertas e indicar cambios en IoT
            burned_dets = [d for d in detections if "quemada" in d.label.lower()]
            if len(burned_dets) > 0:
                max_conf = max(d.confidence for d in burned_dets)
                self.burned_toast_alert_signal.emit(f"¡ALERTA TOSTADA QUEMADA! (Conf: {max_conf:.2f})")
                self.iot_status_changed_signal.emit()

            # Dibujar rectángulos y etiquetas
            for det in detections:
                left, top, width, height = det.bbox
                label = det.label
                confidence = det.confidence
                
                # Resaltar tostada quemada en Rojo, otras clases en Verde (o dinámico)
                if "quemada" in label.lower():
                    color = (0, 0, 255) # Rojo en BGR
                else:
                    color = colors.get(label, (0, 255, 0)) # Verde o dinámico en BGR
                    
                cv2.rectangle(image, (left, top), (left + width, top + height), color, 2)
                label_text = f"{label} {confidence:.2f}"
                cv2.putText(image, label_text, (left, top - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)

            rgb_image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
            h, w, ch = rgb_image.shape
            bytes_per_line = ch * w
            qt_image = QImage(rgb_image.data, w, h, bytes_per_line, QImage.Format_RGB888)
            
            self.change_pixmap_signal.emit(qt_image)

        cap.release()

    def stop(self):
        self.running = False
        self.wait()


class FactoryControlApp(QMainWindow):
    def __init__(self, default_source="road.mp4"):
        super().__init__()
        self.setWindowTitle("SISTEMA DE CONTROL INDUSTRIAL - PYSIDE6")
        self.resize(1200, 800)
        self.setStyleSheet(QSS)
        
        # Inicializar componentes del Backend (Clean Architecture)
        self.iot_controller = MockIoTController()
        self.http_client = RequestsHttpClient()
        
        # Rutas iniciales de modelos
        self.current_model = "yolov11-python/yolo11n.onnx"
        self.current_names = "yolov11-python/data/class.names"
        
        # Instanciar el detector de YOLO (Capa de infraestructura)
        try:
            model_path = resolve_path(self.current_model)
            names_path = resolve_path(self.current_names)
            self.detector = YoloDetector(model_path=model_path, names_path=names_path)
            self.detector_status = "ONNX Activo"
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
        self.model_selector.addItem("YOLOv11 Tostadas (Custom)")
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

        # CARD 2: SERVIDOR STATUS
        card_server = QFrame()
        card_server.setProperty("class", "Card")
        server_layout = QVBoxLayout(card_server)
        server_title = QLabel("SERVIDOR STATUS")
        server_title.setStyleSheet("font-weight: bold; font-size: 12px;")
        server_layout.addWidget(server_title)
        
        for stat, value in [("Server Status:", 75), ("Gestión Eventos:", 45), ("Alertas:", 15)]:
            stat_layout = QHBoxLayout()
            lbl = QLabel(stat)
            lbl.setStyleSheet("font-size: 11px;")
            stat_layout.addWidget(lbl)
            
            bar = QProgressBar()
            bar.setValue(value)
            bar.setTextVisible(False)
            bar.setFixedHeight(8)
            stat_layout.addWidget(bar)
            server_layout.addLayout(stat_layout)
            
        info_panel_layout.addWidget(card_server, stretch=1)

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
        self.play_internal_target(default_source)

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
        if index == 0:
            self.current_model = "yolov11-python/yolo11n.onnx"
            self.current_names = "yolov11-python/data/class.names"
            print("[INFO] Frente cambiado al Modelo Original (YOLOv11 COCO)")
        elif index == 1:
            self.current_model = "yolov11-python/tostadas.onnx"
            self.current_names = "yolov11-python/data/tostadas.names"
            print("[INFO] Frente cambiado al Modelo de Tostadas (Personalizado)")
            
        # Re-instanciar detector y caso de uso
        model_path = resolve_path(self.current_model)
        names_path = resolve_path(self.current_names)
        try:
            self.detector = YoloDetector(model_path=model_path, names_path=names_path)
            self.detector_status = "ONNX Activo"
        except Exception as e:
            print(f"[GUI App] Error al cambiar detector YOLO: {e}")
            class MockDetector:
                def detect_frame(self, frame): return []
                def get_class_names(self): return ["Tostada Quemada", "tostadas ok"]
            self.detector = MockDetector()
            self.detector_status = f"Simulado (Error: {str(e)[:25]})"

        self.detect_use_case = DetectAndNotifyUseCase(self.detector, self.iot_controller, self.http_client)

        # Reiniciar hilo
        if self.yolo_thread is not None and self.yolo_thread.isRunning():
            source = self.yolo_thread.source_file
            if source == "0":
                self.play_internal_target("0")
            else:
                video_name = os.path.basename(source)
                self.play_internal_target(video_name)

    # Lógica Botón Cámara (Manejo de estado)
    def toggle_camera(self, checked):
        if checked:
            self.play_internal_target("0")
        else:
            if self.yolo_thread is not None and self.yolo_thread.isRunning():
                self.yolo_thread.stop()
            self.video_label.clear()
            self.video_label.setText("Cámara Apagada")
            self.video_label.setStyleSheet("background-color: #1E1E28; border-radius: 10px; color: #E0B0FF;")

    def play_internal_target(self, video_name):
        if self.yolo_thread is not None and self.yolo_thread.isRunning():
            self.yolo_thread.stop()
            
        if video_name != "0":
            self.nav_buttons[0].setChecked(False)
            videos_dir = resolve_path("yolov11-python/data/videos")
            if os.path.isabs(video_name) or video_name.startswith("multimedia/videos") or video_name.startswith("yolov11-python/"):
                source_path = resolve_path(video_name)
            else:
                source_path = os.path.join(videos_dir, video_name)
        else:
            source_path = "0"
            
        resolved_model = resolve_path(self.current_model)
        resolved_names = resolve_path(self.current_names)
        if not os.path.exists(resolved_model) or not os.path.exists(resolved_names):
            self.video_label.clear()
            self.video_label.setText(f"Error: No se encontró el modelo o las etiquetas\nCargar: {os.path.basename(resolved_model)}")
            self.video_label.setStyleSheet("background-color: #2A1B2E; border-radius: 10px; color: #FF4500; font-weight: bold; text-align: center;")
            return
            
        self.yolo_thread = YOLODetectionThread(source_path, self.detect_use_case)
        self.yolo_thread.change_pixmap_signal.connect(self.update_image)
        self.yolo_thread.iot_status_changed_signal.connect(self.update_iot_status_labels)
        self.yolo_thread.burned_toast_alert_signal.connect(self.add_alert_log)
        self.yolo_thread.start()

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
        if self.yolo_thread is not None and self.yolo_thread.isRunning():
            self.yolo_thread.stop()
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
