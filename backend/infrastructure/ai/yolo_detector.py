import cv2
import os
import numpy as np
from typing import List
from backend.domain.entities.detection import DetectionResult
from backend.domain.interfaces.image_detector import IImageDetector

class YoloDetector(IImageDetector):
    def __init__(self, model_path: str = None, names_path: str = None, confidence_threshold: float = 0.25, nms_threshold: float = 0.4):
        # Default paths relative to workspace root
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        
        if model_path is None:
            model_path = os.path.join(base_dir, "ai_training", "models", "tostadas.onnx")
            # If tostadas.onnx doesn't exist, try yolo11n.onnx
            if not os.path.exists(model_path):
                model_path = os.path.join(base_dir, "ai_training", "models", "yolo11n.onnx")

        if names_path is None:
            names_path = os.path.join(base_dir, "ai_training", "models", "tostadas.names")
            if not os.path.exists(names_path):
                names_path = os.path.join(base_dir, "ai_training", "models", "class.names")

        self.model_path = model_path
        self.names_path = names_path
        self.confidence_threshold = confidence_threshold
        self.nms_threshold = nms_threshold
        
        self.image_size = 640
        
        # Load names
        self.names = []
        if os.path.exists(self.names_path):
            with open(self.names_path, "r", encoding="utf-8") as f:
                self.names = [line.strip() for line in f.readlines() if line.strip()]
        else:
            # Fallback classes if file not found
            self.names = ['Tostada Quemada', 'tostadas ok']

        # Load net
        if not os.path.exists(self.model_path):
            raise FileNotFoundError(f"Model file not found at {self.model_path}")
            
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

            if confidence > self.confidence_threshold:
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
        indexes = cv2.dnn.NMSBoxes(boxes, confs, self.confidence_threshold, self.nms_threshold)
        
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
