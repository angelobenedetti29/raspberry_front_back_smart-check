"""Cliente HTTP del registro de dispositivo (sólo stdlib: urllib)."""

import json
from dataclasses import dataclass
from urllib import error, request

from backend.device.tipos import ErrorTipoDispositivo, TipoDispositivo


class ErrorRegistro(Exception):
    """Error de red, HTTP o de payload al hablar con el backend."""

    def __init__(self, mensaje: str, codigo: int | None = None) -> None:
        super().__init__(mensaje)
        self.codigo = codigo


@dataclass(frozen=True, slots=True)
class RespuestaRegistro:
    """Respuesta del alta: el backend asigna el request_id."""

    request_id: str
    status: str


@dataclass(frozen=True, slots=True)
class EstadoSolicitud:
    """Respuesta de la consulta de estado de una solicitud."""

    status: str
    device_id: str | None = None
    secret: str | None = None
    tipo: TipoDispositivo | None = None


class ClienteRegistro:
    """Habla con los endpoints de registration-requests del backend.

    Las rutas se derivan de base_url (esquema + host + puerto) y del
    endpoint configurado; este cliente no lee la config.
    """

    def __init__(self, base_url: str, endpoint: str, timeout: float = 5.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._endpoint = "/" + endpoint.strip("/")
        self._timeout = timeout

    def solicitar(self, hostname: str, tipo: TipoDispositivo) -> RespuestaRegistro:
        """POST de alta. Devuelve el request_id asignado por el backend."""
        tipo_valido = TipoDispositivo.desde(tipo)
        data = self._peticion(
            "POST", self._endpoint, {"hostname": hostname, "type": tipo_valido.value}
        )
        return RespuestaRegistro(
            request_id=_campo_str_requerido(data, "request_id"),
            status=_campo_str_opcional(data, "status") or "PENDING",
        )

    def consultar(self, request_id: str) -> EstadoSolicitud:
        """GET del estado de la solicitud. Trae device_id/secret si aprobó."""
        data = self._peticion("GET", f"{self._endpoint}/{request_id}")
        tipo_crudo = _campo_str_opcional(data, "type")
        try:
            tipo = TipoDispositivo.desde(tipo_crudo) if tipo_crudo is not None else None
        except ErrorTipoDispositivo as exc:
            raise ErrorRegistro(f"El campo 'type' del backend es inválido: {exc}") from exc
        return EstadoSolicitud(
            status=_campo_str_requerido(data, "status"),
            device_id=_campo_str_opcional(data, "device_id"),
            secret=_campo_str_opcional(data, "secret"),
            tipo=tipo,
        )

    def _peticion(self, metodo: str, path: str, payload: dict | None = None) -> dict:
        url = f"{self._base_url}{path}"
        cuerpo = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            cuerpo = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"

        req = request.Request(url, data=cuerpo, headers=headers, method=metodo)
        try:
            with request.urlopen(req, timeout=self._timeout) as resp:
                raw = resp.read()
        except error.HTTPError as exc:
            detalle = _leer_detalle(exc)
            raise ErrorRegistro(
                f"El backend respondió {exc.code} en {metodo} {url}: {detalle}",
                codigo=exc.code,
            ) from exc
        except error.URLError as exc:
            raise ErrorRegistro(
                f"No se pudo contactar el backend en {metodo} {url}: {exc.reason}"
            ) from exc
        except OSError as exc:
            raise ErrorRegistro(f"Error de red en {metodo} {url}: {exc}") from exc

        try:
            data = json.loads(raw.decode("utf-8")) if raw else {}
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ErrorRegistro(
                f"Respuesta no-JSON del backend en {metodo} {url}"
            ) from exc
        if not isinstance(data, dict):
            raise ErrorRegistro(
                f"Respuesta inesperada del backend en {metodo} {url}: "
                "se esperaba un objeto JSON"
            )
        return data


class ClienteDispositivo:
    """Operaciones del dispositivo autenticadas con su secret (Bearer)."""

    def __init__(self, base_url: str, endpoint: str, timeout: float = 5.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._endpoint = "/" + endpoint.strip("/")
        self._timeout = timeout

    def renombrar(self, secret: str, nombre: str) -> None:
        """PUT del rename. Lanza ErrorRegistro si el backend no lo acepta."""
        if not secret:
            raise ErrorRegistro("No hay secret de dispositivo para autenticar el renombre")
        cuerpo = json.dumps({"nombre": nombre}).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {secret}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        url = f"{self._base_url}{self._endpoint}"
        req = request.Request(url, data=cuerpo, headers=headers, method="PUT")
        try:
            with request.urlopen(req, timeout=self._timeout) as resp:
                resp.read()
        except error.HTTPError as exc:
            raise ErrorRegistro(
                f"El backend respondió {exc.code} al renombrar el dispositivo: {_leer_detalle(exc)}",
                codigo=exc.code,
            ) from exc
        except error.URLError as exc:
            raise ErrorRegistro(
                f"No se pudo contactar el backend al renombrar el dispositivo: {exc.reason}"
            ) from exc
        except OSError as exc:
            raise ErrorRegistro(f"Error de red al renombrar el dispositivo: {exc}") from exc


def _campo_str_requerido(data: dict, clave: str) -> str:
    """Extrae un campo string obligatorio; su ausencia es error de protocolo."""
    valor = data.get(clave)
    if not isinstance(valor, str):
        raise ErrorRegistro(
            f"El backend no devolvió el campo '{clave}' como string"
        )
    return valor


def _campo_str_opcional(data: dict, clave: str) -> str | None:
    """Extrae un campo string opcional; si falta devuelve None."""
    valor = data.get(clave)
    if valor is None:
        return None
    if not isinstance(valor, str):
        raise ErrorRegistro(f"El campo '{clave}' del backend no es un string")
    return valor


def _leer_detalle(exc: error.HTTPError) -> str:
    """Intenta leer el cuerpo del error HTTP para dar contexto."""
    try:
        raw = exc.read()
        return raw.decode("utf-8", errors="replace")[:200] if raw else "(sin cuerpo)"
    except Exception:
        return "(cuerpo ilegible)"
