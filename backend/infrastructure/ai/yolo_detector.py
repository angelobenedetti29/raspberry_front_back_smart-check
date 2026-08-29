import cv2
import os
import numpy as np
from typing import List
from backend.domain.entities.detection import DetectionResult
from backend.domain.interfaces.image_detector import IImageDetector

# Intentar importar la librería oficial de Hailo de forma segura
try:
    from hailo_platform import HEF, VDevice, ConfigureParams, InputVStreamParams, OutputVStreamParams, FormatType, InferVStreams, HailoStreamInterface
    HAILO_AVAILABLE = True
except ImportError:
    HAILO_AVAILABLE = False

class YoloDetector(IImageDetector):
    def __init__(self, model_path: str = None, names_path: str = None, confidence_threshold: float = 0.60, nms_threshold: float = 0.4):
        # Default paths relative to workspace root
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        
        if model_path is None:
            # Intentar cargar tostadas_v2.onnx primero, luego tostadas_v1.onnx, y finalmente yolo11n.onnx
            model_path = os.path.join(base_dir, "ai_training", "models", "tostadas_v2.onnx")
            if not os.path.exists(model_path):
                model_path = os.path.join(base_dir, "ai_training", "models", "tostadas_v1.onnx")
                if not os.path.exists(model_path):
                    model_path = os.path.join(base_dir, "ai_training", "models", "yolo11n.onnx")

        # Redirigir automáticamente de .onnx a .hef si hay NPU (Hailo) disponible y existe el archivo compilado .hef
        if HAILO_AVAILABLE and model_path is not None and model_path.endswith('.onnx'):
            hef_candidate = model_path[:-5] + '.hef'
            if os.path.exists(hef_candidate):
                print(f"[YoloDetector] NPU detectada. Redirigiendo {os.path.basename(model_path)} -> {os.path.basename(hef_candidate)}")
                model_path = hef_candidate

        if names_path is None:
            # Intentar cargar las etiquetas que correspondan al modelo seleccionado
            if "tostadas_v2" in model_path:
                names_path = os.path.join(base_dir, "ai_training", "models", "tostadas_v2.names")
            elif "tostadas_v1" in model_path:
                names_path = os.path.join(base_dir, "ai_training", "models", "tostadas_v1.names")
            else:
                names_path = os.path.join(base_dir, "ai_training", "models", "class.names")

        self.model_path = model_path
        self.names_path = names_path
        self.confidence_threshold = confidence_threshold
        self.nms_threshold = nms_threshold
        
        # Umbrales específicos por clase para optimizar detección
        self.class_thresholds = {
            "tcq": 0.30,
            "tostada quemada": 0.30,
            "tcok": 0.60,
            "tostadas ok": 0.60
        }
        
        self.image_size = 640
        
        # Load names
        self.names = []
        if os.path.exists(self.names_path):
            with open(self.names_path, "r", encoding="utf-8") as f:
                self.names = [line.strip() for line in f.readlines() if line.strip()]
        else:
            # Fallback classes if file not found
            self.names = ['Tostada Quemada', 'tostadas ok']

        # Load net / HEF
        if not os.path.exists(self.model_path):
            raise FileNotFoundError(f"Model file not found at {self.model_path}")
            
        self.use_hailo = self.model_path.endswith('.hef')

        if self.use_hailo:
            if not HAILO_AVAILABLE:
                raise RuntimeError("Especificaste un modelo .hef, pero la librería 'hailo_platform' no está disponible o instalada en este sistema.")
            
            print(f"[YoloDetector] Inicializando modelo en NPU Hailo-8L: {self.model_path}")
            self.hef = HEF(self.model_path)
            self.vdevice = VDevice().__enter__()
            
            try:
                # Configurar el dispositivo PCIe con la red HEF
                configure_params = ConfigureParams.create_from_hef(self.hef, interface=HailoStreamInterface.PCIe)
                self.network_group = self.vdevice.configure(self.hef, configure_params)[0]
                
                # Obtener nombres de streams virtuales de entrada y salida
                self.input_vstream_info = self.hef.get_input_vstream_infos()[0]
                self.output_vstream_info = self.hef.get_output_vstream_infos()[0]
                
                # Configurar parámetros (UINT8 para entrada y FLOAT32 para salida)
                self.input_params = InputVStreamParams.make(self.network_group, format_type=FormatType.UINT8)
                self.output_params = OutputVStreamParams.make(self.network_group, format_type=FormatType.FLOAT32)
                
                # Activar el grupo de red
                self.activation_ctx = self.network_group.activate()
                self.activation_ctx.__enter__()
                
                # Inicializar la tubería de inferencia
                self.infer_ctx = InferVStreams(self.network_group, self.input_params, self.output_params)
                self.infer_pipeline = self.infer_ctx.__enter__()
            except Exception as e:
                self.release_hailo()
                raise e
        else:
            print(f"[YoloDetector] Inicializando modelo en CPU con OpenCV DNN: {self.model_path}")
            self.net = cv2.dnn.readNet(self.model_path)

    def get_class_names(self) -> List[str]:
        return self.names

    def detect(self, image_path: str) -> List[DetectionResult]:
        if not os.path.exists(image_path):
            raise FileNotFoundError(f"Image file not found at {image_path}")
        frame = cv2.imread(image_path)
        if frame is None:
            raise ValueError(f"Could not read image file at {image_path}")
        return self.detect_frame(frame)

    def detect_frame(self, frame) -> List[DetectionResult]:
        if frame is None:
            return []

        h_img, w_img, _ = frame.shape
        
        if self.use_hailo:
            # --- FLUJO DE INFERENCIA EN NPU HAILO-8L ---
            # Preprocesamiento: redimensionar a 640x640 y convertir de BGR a RGB
            img_resized = cv2.resize(frame, (self.image_size, self.image_size), interpolation=cv2.INTER_NEAREST)
            img_rgb = cv2.cvtColor(img_resized, cv2.COLOR_BGR2RGB)
            
            # La NPU espera una forma (1, 640, 640, 3)
            input_data = np.expand_dims(img_rgb, axis=0)
            outputs = self.infer_pipeline.infer({self.input_vstream_info.name: input_data})
            out_tensor = outputs[self.output_vstream_info.name]
            
            # El postprocesamiento NMS ya viene compilado dentro del HEF.
            # out_tensor[0] contiene una lista de longitud N (clases), 
            # donde cada elemento es un ndarray de forma (M, 5) -> [ymin, xmin, ymax, xmax, confidence]
            detections = out_tensor[0]
            results = []
            
            for cid in range(len(detections)):
                class_detections = detections[cid]
                for det in class_detections:
                    ymin, xmin, ymax, xmax, confidence = det
                    label = self.names[cid] if cid < len(self.names) else f"class_{cid}"
                    thresh = self.class_thresholds.get(label.lower(), self.confidence_threshold)
                    
                    if confidence >= thresh:
                        # Convertir coordenadas normalizadas a píxeles
                        left = int(xmin * w_img)
                        top = int(ymin * h_img)
                        width = int((xmax - xmin) * w_img)
                        height = int((ymax - ymin) * h_img)
                        
                        results.append(
                            DetectionResult(
                                label=label,
                                confidence=float(confidence),
                                bbox=(left, top, width, height)
                            )
                        )
            return results
        else:
            # --- FLUJO DE INFERENCIA EN CPU (ONNX) ---
            # Create blob
            blob = cv2.dnn.blobFromImage(frame, 1/255.0, (self.image_size, self.image_size), swapRB=True, crop=False)
            self.net.setInput(blob)
            preds = self.net.forward()
            preds = preds.transpose((0, 2, 1))  # Adjust output shape
            
            x_factor = w_img / self.image_size
            y_factor = h_img / self.image_size
            
            rows = preds[0].shape[0]
            class_ids, confs, boxes = [], [], []

            for i in range(rows):
                row = preds[0][i]
                
                # Extract scores starting from index 4
                classes_score = row[4:]
                _, _, _, max_idx = cv2.minMaxLoc(classes_score)
                class_id = max_idx[1]
                confidence = classes_score[class_id]

                label = self.names[class_id] if class_id < len(self.names) else f"class_{class_id}"
                thresh = self.class_thresholds.get(label.lower(), self.confidence_threshold)

                if confidence > thresh:
                    confs.append(float(confidence))
                    class_ids.append(int(class_id))
                    
                    # BBox coords (center_x, center_y, width, height)
                    x, y, w, h = row[0].item(), row[1].item(), row[2].item(), row[3].item()
                    left = int((x - 0.5 * w) * x_factor)
                    top = int((y - 0.5 * h) * y_factor)
                    width = int(w * x_factor)
                    height = int(h * y_factor)
                    boxes.append([left, top, width, height])

            # Apply NMS
            min_thresh = min(self.class_thresholds.values()) if self.class_thresholds else self.confidence_threshold
            indexes = cv2.dnn.NMSBoxes(boxes, confs, min_thresh, self.nms_threshold)
            
            results = []
            # Support both OpenCV NMS formats (sometimes a flat list, sometimes nested list)
            for i in indexes:
                # Handle possible nested index returned by OpenCV
                idx = i[0] if isinstance(i, (list, np.ndarray)) else i
                
                label = self.names[class_ids[idx]] if class_ids[idx] < len(self.names) else f"class_{class_ids[idx]}"
                results.append(
                    DetectionResult(
                        label=label,
                        confidence=confs[idx],
                        bbox=tuple(boxes[idx])
                    )
                )
                
            return results

    def release_hailo(self):
        # Liberar los contextos de forma ordenada y segura
        if hasattr(self, 'infer_ctx') and self.infer_ctx:
            try:
                self.infer_ctx.__exit__(None, None, None)
            except Exception:
                pass
            self.infer_ctx = None
        if hasattr(self, 'activation_ctx') and self.activation_ctx:
            try:
                self.activation_ctx.__exit__(None, None, None)
            except Exception:
                pass
            self.activation_ctx = None
        if hasattr(self, 'vdevice') and self.vdevice:
            try:
                self.vdevice.__exit__(None, None, None)
            except Exception:
                pass
            self.vdevice = None

    def __del__(self):
        if getattr(self, 'use_hailo', False):
            self.release_hailo()
