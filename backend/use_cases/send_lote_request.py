from backend.domain.interfaces.http_client import IHttpClient


class SendLoteRequestUseCase:
    def __init__(self, http_client: IHttpClient, central_server_url: str, api_key: str):
        self.http_client = http_client
        self.central_server_url = central_server_url.rstrip("/")
        self.api_key = api_key

    def execute(self, payload: dict) -> bool:
        headers = {
            "Content-Type": "application/json",
            "X-API-Key": self.api_key,
        }
        url = f"{self.central_server_url}/api/v1/lotes"
        print(f"[SendLoteRequestUseCase] Enviando lote request a {url} con payload: {payload}")
        return self.http_client.post(url, payload, headers=headers)

    def get_last_error(self):
        return getattr(self.http_client, "last_error", None)

    def get_last_status_code(self):
        return getattr(self.http_client, "last_status_code", None)

    def get_last_response_text(self):
        return getattr(self.http_client, "last_response_text", None)