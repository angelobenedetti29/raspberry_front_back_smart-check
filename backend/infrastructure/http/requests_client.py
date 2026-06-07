import requests
from typing import Dict, Any
from backend.domain.interfaces.http_client import IHttpClient

class RequestsHttpClient(IHttpClient):
    def __init__(self, timeout: int = 5):
        self.timeout = timeout

    def post(self, url: str, payload: Dict[str, Any]) -> bool:
        try:
            print(f"[HTTP Client] Enviando POST a {url} con carga útil: {payload}")
            response = requests.post(url, json=payload, timeout=self.timeout)
            print(f"[HTTP Client] Respuesta recibida ({response.status_code}): {response.text}")
            return response.status_code in [200, 201, 202, 204]
        except Exception as e:
            print(f"[HTTP Client] Error al enviar POST a {url}: {e}")
            return False

    def get(self, url: str) -> Dict[str, Any]:
        try:
            print(f"[HTTP Client] Enviando GET a {url}")
            response = requests.get(url, timeout=self.timeout)
            if response.status_code == 200:
                return response.json()
            print(f"[HTTP Client] Respuesta GET no exitosa ({response.status_code})")
            return {}
        except Exception as e:
            print(f"[HTTP Client] Error al enviar GET a {url}: {e}")
            return {}
