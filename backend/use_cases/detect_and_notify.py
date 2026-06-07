import cv2
import numpy as np
from typing import List, Union, Dict, Any
from backend.domain.entities.detection import DetectionResult
from backend.domain.interfaces.image_detector import IImageDetector
from backend.domain.interfaces.iot_controller import IIoTController
from backend.domain.interfaces.http_client import IHttpClient

class DetectAndNotifyUseCase:
    def __init__(self, detector: IImageDetector, iot_controller: IIoTController, http_client: IHttpClient):
        self.detector = detector
        self.iot_controller = iot_controller
        self.http_client = http_client

    def execute(self, frame: np.ndarray, notification_url: str = None) -> List[DetectionResult]:
        """
        Executes the detection on a frame.
        If 'Tostada Quemada' is detected:
        1. Turns off the toaster relay ('rele_tostadora').
        2. Turns on the alarm buzzer ('alarma_buzzer').
        3. Sends an HTTP POST notification if url is provided.
        """
        detections = self.detector.detect_frame(frame)
        
        has_burned_toast = any(d.label.lower() == "tostada quemada" for d in detections)
        
        if has_burned_toast:
            print("[Use Case] !!! ALERTA: Se detectó Tostada Quemada !!!")
            
            # 1. Turn off toaster relay
            self.iot_controller.turn_off("rele_tostadora")
            
            # 2. Turn on alarm buzzer
            self.iot_controller.turn_on("alarma_buzzer")
            
            # 3. Notify external server via HTTP POST
            if notification_url:
                payload = {
                    "event": "burned_toast_detected",
                    "details": [
                        {"label": d.label, "confidence": round(d.confidence, 4)}
                        for d in detections
                    ]
                }
                self.http_client.post(notification_url, payload)
        
        return detections
