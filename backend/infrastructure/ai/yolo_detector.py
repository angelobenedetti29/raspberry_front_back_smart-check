import cv2
import logging
import os
import threading
import numpy as np
from typing import List
from backend.domain.entities.detection import DetectionResult
from backend.domain.interfaces.image_detector import IImageDetector

logger = logging.getLogger(__name__)

# Intentar importar la librería oficial de Hailo de forma segura
try:
    from hailo_platform import HEF, VDevice, ConfigureParams, InputVStreamParams, OutputVStreamParams, FormatType, InferVStreams, HailoStreamInterface
    HAILO_AVAILABLE = True
except ImportError:
    HAILO_AVAILABLE = False

class YoloDetector(IImageDetector):
    """Detector de objetos YOLO que corre en NPU Hailo o en CPU con OpenCV DNN.

    Si ``hailo_platform`` está disponible y existe el ``.hef`` compilado, redirige
    automáticamente el ``.onnx`` al ``.hef`` y ejecuta la inferencia en la NPU. En
    caso contrario usa ``cv2.dnn`` sobre el modelo ONNX. El postprocesado difiere
    entre ambos proveedores: el HEF ya trae el NMS compilado, mientras que el modelo
    ONNX devuelve las predicciones crudas y el NMS se aplica aquí.
    """

    def __init__(self, model_path: str | None = None, names_path: str | None = None, confidence_threshold: float = 0.60, nms_threshold: float = 0.4):
        """Inicializa el detector y carga el modelo y las etiquetas.

        Con ``model_path=None`` busca en ``ai_training/models`` el primer archivo
        disponible entre ``tostadas_v2.onnx``, ``tostadas_v1.onnx`` y
        ``yolo11n.onnx``. Si la NPU está disponible y existe el ``.hef``
        correspondiente, se redirige al HEF. ``names_path`` se deriva del modelo
        elegido. ``confidence_threshold`` y ``nms_threshold`` son los umbrales por
        defecto; ``class_thresholds`` los sobrescribe por clase.
        """
        # Serializa la inferencia: una única instancia se comparte entre los
        # hilos del threadpool de FastAPI, pero ``cv2.dnn`` / la NPU no son
        # seguros para uso concurrente sobre la misma red.
        self._inference_lock = threading.Lock()
        self._released = False
        # Rutas por defecto relativas a la raíz del workspace
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
                logger.info(
                    "NPU detectada. Redirigiendo %s -> %s",
                    os.path.basename(model_path),
                    os.path.basename(hef_candidate),
                )
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
        
        # Cargar las etiquetas
        self.names = []
        if os.path.exists(self.names_path):
            with open(self.names_path, "r", encoding="utf-8") as f:
                self.names = [line.strip() for line in f.readlines() if line.strip()]
        else:
            # Clases de reserva si no se encuentra el archivo
            self.names = ['Tostada Quemada', 'tostadas ok']

        # Cargar la red / HEF
        if not os.path.exists(self.model_path):
            raise FileNotFoundError(f"Model file not found at {self.model_path}")
            
        self.use_hailo = self.model_path.endswith('.hef')

        if self.use_hailo:
            if not HAILO_AVAILABLE:
                raise RuntimeError("Especificaste un modelo .hef, pero la librería 'hailo_platform' no está disponible o instalada en este sistema.")
            
            logger.info("Inicializando modelo en NPU Hailo-8L: %s", self.model_path)
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
            except Exception:
                self.release_hailo()
                raise
        else:
            logger.info("Inicializando modelo en CPU con OpenCV DNN: %s", self.model_path)
            self.net = cv2.dnn.readNet(self.model_path)

    def get_class_names(self) -> List[str]:
        """Devuelve la lista de nombres de clase cargada del archivo ``.names``."""
        return self.names

    def detect(self, image_path: str) -> List[DetectionResult]:
        """Lee la imagen de ``image_path`` y delega la detección en ``detect_frame``.

        Lanza ``FileNotFoundError`` si la ruta no existe y ``ValueError`` si
        ``cv2.imread`` no puede decodificar el archivo.
        """
        if not os.path.exists(image_path):
            raise FileNotFoundError(f"Image file not found at {image_path}")
        frame = cv2.imread(image_path)
        if frame is None:
            raise ValueError(f"Could not read image file at {image_path}")
        return self.detect_frame(frame)

    def detect_frame(self, frame) -> List[DetectionResult]:
        """Detecta objetos en un frame BGR ya cargado en memoria.

        Devuelve una lista vacía si el frame es ``None`` o si el detector fue
        liberado. El flujo depende del proveedor:

        - NPU Hailo: la entrada es ``(1, 640, 640, 3)`` en RGB y la salida ya trae
          el NMS compilado. ``out_tensor[0]`` es una lista de longitud igual al
          número de clases; cada elemento es un ``ndarray`` de forma ``(M, 5)`` con
          filas ``[ymin, xmin, ymax, xmax, confidence]`` normalizadas a ``[0, 1]``.
        - CPU (ONNX): la salida cruda es ``(1, 4 + num_clases, N)``; tras
          ``transpose((0, 2, 1))`` cada una de las ``N`` filas queda como
          ``[cx, cy, w, h, scores...]``, con la caja en píxeles del espacio 640x640
          y los scores de clase desde el índice 4. Aquí se aplica NMS manualmente.
        """
        # La inferencia (Hailo y ONNX) no es segura ante concurrencia sobre una
        # misma red/instancia compartida, así que se serializa completa.
        with self._inference_lock:
            if frame is None:
                return []

            if self._released:
                logger.warning("Detector liberado; se ignora la inferencia.")
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
                # Crear el blob de entrada reescalando los píxeles a [0, 1]
                blob = cv2.dnn.blobFromImage(frame, 1/255.0, (self.image_size, self.image_size), swapRB=True, crop=False)
                self.net.setInput(blob)
                preds = self.net.forward()
                preds = preds.transpose((0, 2, 1))  # Ajustar la forma de salida a (1, N, 4 + clases)

                x_factor = w_img / self.image_size
                y_factor = h_img / self.image_size

                rows = preds[0].shape[0]
                class_ids, confs, boxes = [], [], []

                for i in range(rows):
                    row = preds[0][i]

                    # Extraer los scores de clase a partir del índice 4
                    classes_score = row[4:]
                    # np.argmax no depende de la versión de OpenCV. cv2.minMaxLoc
                    # devuelve el pico como Point(x, y) y para un array 1D su
                    # semántica cambia según cómo OpenCV lo convierta a Mat: en 4.x
                    # lo trata como columna (índice en y) y en 5.x como fila (índice
                    # en x). Leer max_idx[1] fijaba la clase 0 en OpenCV 5, por lo
                    # que toda tostada se etiquetaba como quemada (TCQ).
                    class_id = int(np.argmax(classes_score))
                    confidence = float(classes_score[class_id])

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

                # Aplicar NMS
                # Se usa el menor umbral por clase para que ``NMSBoxes`` no descarte
                # cajas válidas antes de la supresión.
                min_thresh = min(self.class_thresholds.values()) if self.class_thresholds else self.confidence_threshold
                indexes = cv2.dnn.NMSBoxes(boxes, confs, min_thresh, self.nms_threshold)

                results = []
                # Soporta ambos formatos de NMS de OpenCV (a veces lista plana y a veces anidada)
                for i in indexes:
                    # Manejar el posible índice anidado que devuelve OpenCV
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

    def _safe_release(self, name: str, obj) -> None:
        """Cierra un contexto gestionado sin dejar que un fallo oculte el resto.

        Un fallo al liberar la NPU se registra en vez de silenciarse, porque
        indicaría que el dispositivo puede haber quedado reservado.
        """
        try:
            obj.__exit__(None, None, None)
        except Exception:
            logger.warning(
                "Fallo al liberar '%s'; se continúa con el resto de la limpieza.",
                name,
                exc_info=True,
            )

    def _release_contexts(self):
        """Cierra los contextos de inferencia, activación y ``VDevice`` en orden.

        Cada cierre se aísla vía ``_safe_release`` para liberar todo lo posible
        aunque alguno falle. No toma el lock: lo hace ``release_hailo``.
        """
        if getattr(self, 'infer_ctx', None):
            self._safe_release('infer_ctx', self.infer_ctx)
            self.infer_ctx = None
        if getattr(self, 'activation_ctx', None):
            self._safe_release('activation_ctx', self.activation_ctx)
            self.activation_ctx = None
        if getattr(self, 'vdevice', None):
            self._safe_release('vdevice', self.vdevice)
            self.vdevice = None
        self._released = True

    def release_hailo(self):
        """Cierra los contextos y deja el detector inutilizable.

        Es idempotente: marca ``_released`` para que ``detect_frame`` ignore nuevas
        inferencias. Se serializa con la inferencia usando el mismo lock, de modo
        que espera a que termine la inferencia en vuelo y ninguna otra puede
        arrancar mientras la NPU se libera; sin esto, el shutdown del lifespan
        podía desmontar los contextos con una inferencia a medias.
        """
        lock = getattr(self, '_inference_lock', None)
        if lock is None:
            # Instancia parcialmente construida (``__del__`` tras un fallo en
            # ``__init__``): no hay lock que tomar y no hay inferencia posible.
            self._release_contexts()
            return
        with lock:
            self._release_contexts()

    def __del__(self):
        """Libera la NPU al destruirse, solo si el modelo cargado era Hailo."""
        if getattr(self, 'use_hailo', False):
            self.release_hailo()
