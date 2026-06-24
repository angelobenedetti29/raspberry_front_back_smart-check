import cv2
import numpy as np
from typing import List, Union, Dict, Any
from backend.domain.entities.detection import DetectionResult
from backend.domain.interfaces.image_detector import IImageDetector
from backend.domain.interfaces.iot_controller import IIoTController
from backend.domain.interfaces.http_client import IHttpClient
from backend.use_cases.toast_tracker import ToastTracker

class DetectAndNotifyUseCase:
    def __init__(self, detector: IImageDetector, iot_controller: IIoTController, http_client: IHttpClient):
        self.detector = detector
        self.iot_controller = iot_controller
        self.http_client = http_client
        self.tracker = ToastTracker()

    def reset_tracker(self):
        """Resets the state of the internal toast tracker."""
        self.tracker.reset()

    def execute(self, frame: np.ndarray, notification_url: str = None) -> List[Any]:
        """
        Executes the detection on a frame and updates the toast tracker.
        If a new 'Tostada Quemada' is confirmed:
        1. Turns off the toaster relay ('rele_tostadora').
        2. Turns on the alarm buzzer ('alarma_buzzer').
        3. Sends an HTTP POST notification if url is provided.
        """
        detections = self.detector.detect_frame(frame)
        
        # Update tracker
        active_toasts, newly_burnt_toasts = self.tracker.update(detections)
        
        if newly_burnt_toasts:
            burnt_ids = [t.id for t in newly_burnt_toasts]
            print(f"[Use Case] !!! ALERTA: Se detectó Tostada Quemada (IDs: {burnt_ids}) !!!")
            
            # 1. Turn off toaster relay
            self.iot_controller.turn_off("rele_tostadora")
            
            # 2. Turn on alarm buzzer
            self.iot_controller.turn_on("alarma_buzzer")
            
            # 3. Notify external server via HTTP POST
            if notification_url:
                payload = {
                    "event": "burned_toast_detected",
                    "details": [
                        {"id": t.id, "label": t.label, "confidence": round(t.confidence, 4)}
                        for t in newly_burnt_toasts
                    ]
                }
                self.http_client.post(notification_url, payload)
        
        return active_toasts

