from dataclasses import dataclass
from typing import Tuple

@dataclass
class DetectionResult:
    label: str
    confidence: float
    bbox: Tuple[int, int, int, int]  # x, y, width, height
