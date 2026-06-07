from abc import ABC, abstractmethod
from typing import List
from backend.domain.entities.detection import DetectionResult

class IImageDetector(ABC):
    @abstractmethod
    def detect(self, image_path: str) -> List[DetectionResult]:
        """Detect objects in an image file by path."""
        pass

    @abstractmethod
    def detect_frame(self, frame) -> List[DetectionResult]:
        """Detect objects in a raw OpenCV frame (numpy array)."""
        pass

    @abstractmethod
    def get_class_names(self) -> List[str]:
        """Get names of all classes the model can detect."""
        pass
