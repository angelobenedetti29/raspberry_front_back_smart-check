"""Cliente HTTP del registro de dispositivo (usa la capa compartida backend.api)."""

from dataclasses import dataclass

from backend.api import ClienteHTTP, ErrorConexion, ErrorFormato, ErrorHttp
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
    # Token para el push de renombre Go→Pi; no es el secret del dispositivo.
    rename_token: str | None = None


class ClienteRegistro:
    """Habla con los endpoints de registration-requests del backend.

    Las rutas se derivan de base_url (esquema + host + puerto) y del
    endpoint configurado; este cliente no lee la config.
    """

    def __init__(
        self,
        base_url: str,
        endpoint: str,
        timeout: float = 5.0,
        url: str | None = None,
    ) -> None:
        self._http = ClienteHTTP(base_url, timeout)
        self._endpoint = "/" + endpoint.strip("/")
        # URL completa (esquema + host + puerto) con la que el Go alcanza el
        # servidor de renombre de esta Pi. None/vacío = no se anuncia (el Go
        # no podrá pushear).
        self._url = url

    def solicitar(self, hostname: str, tipo: TipoDispositivo) -> RespuestaRegistro:
        """POST de alta. Devuelve el request_id asignado por el backend."""
        tipo_valido = TipoDispositivo.desde(tipo)
        payload: dict = {"hostname": hostname, "type": tipo_valido.value}
        # `rename_url` es obligatorio al alta: DeviceService.solicitar_registro
        # ya rechazó con error si device.public_url está vacío. Este `if` queda como
        # defensa por si el cliente se invoca desde otro flujo: en ese caso el
        # backend recibe la alta sin rename_url y la guarda como NULL, igual
        # que los firmwares viejos que nunca seteaban este campo.
        url = (self._url or "").strip()
        if url:
            payload["rename_url"] = url
        data = self._peticion("POST", self._endpoint, payload)
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
            rename_token=_campo_str_opcional(data, "rename_token"),
        )

    def _peticion(self, metodo: str, path: str, payload: dict | None = None) -> dict:
        try:
            data = self._http.peticion(metodo, path, payload=payload)
        except ErrorHttp as exc:
            raise ErrorRegistro(
                f"El backend respondió {exc.codigo} en {exc.metodo} {exc.url}: {exc.detalle}",
                codigo=exc.codigo,
            ) from exc
        except ErrorConexion as exc:
            raise ErrorRegistro(
                f"No se pudo contactar el backend en {exc.metodo} {exc.url}: {exc.detalle}"
            ) from exc
        except ErrorFormato as exc:
            raise ErrorRegistro(str(exc)) from exc
        if data is None:
            data = {}
        if not isinstance(data, dict):
            raise ErrorRegistro(
                f"Respuesta inesperada del backend en {metodo} {path}: "
                "se esperaba un objeto JSON"
            )
        return data


class ClienteDispositivo:
    """Operaciones del dispositivo autenticadas con su secret (Bearer)."""

    def __init__(self, base_url: str, endpoint: str, timeout: float = 5.0) -> None:
        self._http = ClienteHTTP(base_url, timeout)
        self._endpoint = "/" + endpoint.strip("/")

    def renombrar(self, secret: str, nombre: str) -> None:
        """PUT del rename. Lanza ErrorRegistro si el backend no lo acepta."""
        if not secret:
            raise ErrorRegistro("No hay secret de dispositivo para autenticar el renombre")
        try:
            self._http.peticion(
                "PUT", self._endpoint, payload={"nombre": nombre}, token=secret, parsear=False
            )
        except ErrorHttp as exc:
            raise ErrorRegistro(
                f"El backend respondió {exc.codigo} al renombrar el dispositivo: {exc.detalle}",
                codigo=exc.codigo,
            ) from exc
        except ErrorConexion as exc:
            raise ErrorRegistro(
                f"No se pudo contactar el backend al renombrar el dispositivo: {exc.detalle}"
            ) from exc

    def actualizar_rename_url(self, secret: str, nueva_url: str) -> None:
        """PATCH de la `rename_url` de un dispositivo ya aprobado.

        El operador puede editar `device.public_url` desde Configuración mientras la
        Pi ya está aprobada; este método le avisa al Go que la nueva URL es
        con la que tiene que mandar los renombres. Autentica con el `secret`
        del dispositivo (igual que `renombrar` y `ping`). Si el backend todavía
        no expone el endpoint, devuelve `ErrorRegistro` con `codigo=404` (o
        `405` si responde pero el método no es el esperado) y `DeviceService`
        lo reintenta con backoff infinito.
        """
        if not secret:
            raise ErrorRegistro(
                "No hay secret de dispositivo para autenticar la actualización de rename_url"
            )
        url = nueva_url.strip()
        if not url:
            raise ErrorRegistro("No se puede sincronizar una rename_url vacía con el backend")
        try:
            self._http.peticion(
                "PATCH",
                self._endpoint,
                payload={"rename_url": url},
                token=secret,
                parsear=False,
            )
        except ErrorHttp as exc:
            raise ErrorRegistro(
                f"El backend respondió {exc.codigo} al actualizar rename_url: {exc.detalle}",
                codigo=exc.codigo,
            ) from exc
        except ErrorConexion as exc:
            raise ErrorRegistro(
                f"No se pudo contactar el backend al actualizar rename_url: {exc.detalle}"
            ) from exc


def _campo_str_requerido(data: dict, clave: str) -> str:
    """Extrae un campo string obligatorio; su ausencia es error de protocolo."""
    valor = data.get(clave)
    if not isinstance(valor, str):
        raise ErrorRegistro(f"El backend no devolvió el campo '{clave}' como string")
    return valor


def _campo_str_opcional(data: dict, clave: str) -> str | None:
    """Extrae un campo string opcional; si falta devuelve None."""
    valor = data.get(clave)
    if valor is None:
        return None
    if not isinstance(valor, str):
        raise ErrorRegistro(f"El campo '{clave}' del backend no es un string")
    return valor
