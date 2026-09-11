from abc import ABC, abstractmethod
from typing import Dict, Any

class IHttpClient(ABC):
    @abstractmethod
    def post(self, url: str, payload: Dict[str, Any], headers: Dict[str, str] = None) -> bool:
        """Send a POST request and return True if successful."""
        pass
