import numpy as np
from typing import List
from backend.domain.interfaces.image_detector import IImageDetector
from backend.use_cases.toast_tracker import ToastTracker, TrackedToast

class DetectAndNotifyUseCase:
    """Detecta objetos en un frame y mantiene el tracker de tostadas.

    Encapsula un detector y un ``ToastTracker`` propios; expone solo las tostadas
    visibles para que quien llama las renderice y cuente.
    """

    def __init__(self, detector: IImageDetector):
        self.detector = detector
        self.tracker = ToastTracker()

    def reset_tracker(self):
        """Reinicia el estado del tracker de tostadas interno."""
        self.tracker.reset()

    def execute(self, frame: np.ndarray) -> List[TrackedToast]:
        """Ejecuta la detección sobre un frame y actualiza el tracker.

        Devuelve las tostadas actualmente visibles, que son las que quien llama
        renderiza y cuenta.
        """
        detections = self.detector.detect_frame(frame)

        # El tracker devuelve además las tostadas recién confirmadas como
        # quemadas; ya no hay consumidor para ese segundo valor. El contrato del
        # tracker (y sus tests directos) se conservan.
        active_toasts, _ = self.tracker.update(detections)

        return active_toasts
