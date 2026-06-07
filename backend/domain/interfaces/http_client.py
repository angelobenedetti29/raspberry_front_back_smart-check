from abc import ABC, abstractmethod
from typing import Dict, Any

class IHttpClient(ABC):
    @abstractmethod
    def post(self, url: str, payload: Dict[str, Any]) -> bool:
        """Send a POST request and return True if successful."""
        pass

    @abstractmethod
    def get(self, url: str) -> Dict[str, Any]:
        """Send a GET request and return the JSON response."""
        pass
