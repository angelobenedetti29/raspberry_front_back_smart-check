import requests
from typing import Dict, Any
from backend.domain.interfaces.http_client import IHttpClient

class RequestsHttpClient(IHttpClient):
    def __init__(self, timeout: int = 5):
        self.timeout = timeout
        self.last_error = None
        self.last_status_code = None
        self.last_response_text = None

    def post(self, url: str, payload: Dict[str, Any], headers: Dict[str, str] = None) -> bool:
        try:
            self.last_error = None
            self.last_status_code = None
            self.last_response_text = None
            print(f"[HTTP Client] Enviando POST a {url} con carga útil: {payload}")
            response = requests.post(url, json=payload, headers=headers, timeout=self.timeout)
            self.last_status_code = response.status_code
            self.last_response_text = response.text
            print(f"[HTTP Client] Respuesta recibida ({response.status_code}): {response.text}")
            if response.status_code in [200, 201, 202, 204]:
                return True

            self.last_error = f"HTTP {response.status_code}: {response.text}"
            return False
        except Exception as e:
            self.last_error = str(e)
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
