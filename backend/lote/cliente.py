"""Cliente HTTP contra los endpoints de sector, productos y lotes del backend Go.

Usa la capa HTTP compartida `backend/api` (sólo stdlib), igual que
`backend/device/cliente.py` y `backend/monitor/cliente.py`. Convierte cualquier
fallo de red, HTTP o de envelope en `ErrorLotes` para que el servicio decida si
reintenta.

No maneja reintentos ni estado: eso es responsabilidad del `LoteService`.
"""

from __future__ import annotations

from typing import Any

from backend.api import (
    ClienteHTTP,
    ErrorConexion,
    ErrorFormato,
    ErrorHttp,
    desenvolver,
)
from backend.config import ApiConfig
from backend.device.tipos import ErrorTipoDispositivo, TipoDispositivo
from backend.lote.tipos import (
    Conteos,
    DispositivoSector,
    ErrorLotes,
    EstadoLote,
    EventoDeteccion,
    Lote,
    MotivoCierre,
    Producto,
    Sector,
)


class ClienteLotes:
    """Cliente de los endpoints de lotes. No reintenta: eso es del servicio."""

    def __init__(self, api: ApiConfig, timeout: float = 5.0) -> None:
        self._api = api
        self._http = ClienteHTTP(api.base_url, timeout)

    # --- lectura ---

    def productos(self, secret: str) -> tuple[Producto, ...]:
        """Catálogo de productos del backend."""
        data = self._peticion("GET", self._api.productos_endpoint, secret)
        items = data if isinstance(data, list) else []
        return tuple(_producto(item) for item in items if isinstance(item, dict))

    def sector(self, secret: str) -> Sector:
        """Sector del dispositivo autenticado."""
        data = self._peticion("GET", self._api.dispositivos_sector_endpoint, secret)
        if not isinstance(data, dict):
            raise ErrorLotes(None, "sector: respuesta inesperada")
        return _sector(data)

    def lote_abierto(self, secret: str) -> Lote | None:
        """Lote abierto del sector, o None si no hay."""
        data = self._peticion("GET", f"{self._api.lotes_endpoint}/abierto", secret)
        if not isinstance(data, dict):
            return None
        crudo = data.get("lote")
        return _lote(crudo) if isinstance(crudo, dict) else None

    def historial(self, secret: str, limite: int = 20) -> tuple[Lote, ...]:
        """Historial de lotes del sector, más nuevos primero."""
        path = f"{self._api.lotes_endpoint}?limite={int(limite)}"
        data = self._peticion("GET", path, secret)
        items = data if isinstance(data, list) else []
        return tuple(_lote(item) for item in items if isinstance(item, dict))

    # --- escritura ---

    def abrir_lote(
        self,
        secret: str,
        *,
        idempotency_key: str,
        producto_id: str | None,
        momento: str,
    ) -> tuple[Lote, bool]:
        """Abre (o se attach a) el lote del sector. Devuelve (lote, creado)."""
        payload = {
            "idempotency_key": idempotency_key,
            "producto_id": producto_id,
            "momento": momento,
        }
        data = self._peticion(
            "POST", f"{self._api.lotes_endpoint}/inicio", secret, payload
        )
        if not isinstance(data, dict) or not isinstance(data.get("lote"), dict):
            raise ErrorLotes(None, "abrir_lote: respuesta inesperada")
        return _lote(data["lote"]), bool(data.get("creado", False))

    def reportar(
        self, secret: str, lote_id: str, eventos: list[EventoDeteccion]
    ) -> None:
        """Reporta un batch de eventos en vivo al lote."""
        payload = {"eventos": [_evento_payload(evento) for evento in eventos]}
        self._peticion(
            "POST", f"{self._api.lotes_endpoint}/{lote_id}/eventos", secret, payload
        )

    def cerrar(
        self,
        secret: str,
        lote_id: str,
        *,
        idempotency_key: str,
        motivo: MotivoCierre,
        conteos: Conteos,
        momento: str,
    ) -> Lote:
        """Cierra el lote con el conteo final autoritativo."""
        payload = {
            "idempotency_key": idempotency_key,
            "motivo": motivo.value,
            "conteos": _conteos_payload(conteos),
            "momento": momento,
        }
        data = self._peticion(
            "POST", f"{self._api.lotes_endpoint}/{lote_id}/cierre", secret, payload
        )
        if not isinstance(data, dict) or not isinstance(data.get("lote"), dict):
            raise ErrorLotes(None, "cerrar: respuesta inesperada")
        return _lote(data["lote"])

    # --- internos ---

    def _peticion(
        self,
        metodo: str,
        path: str,
        secret: str,
        payload: dict | None = None,
    ) -> Any:
        """Ejecuta la petición y devuelve el campo `data` del envelope.

        Levanta `ErrorLotes` ante fallo de red, HTTP o envelope sin éxito.
        """
        try:
            data = self._http.peticion(metodo, path, payload=payload, token=secret)
        except ErrorHttp as exc:
            raise ErrorLotes(exc.codigo, exc.detalle) from exc
        except ErrorConexion as exc:
            raise ErrorLotes(None, exc.detalle) from exc
        except ErrorFormato as exc:
            raise ErrorLotes(None, exc.detalle) from exc
        if data is None:
            raise ErrorLotes(None, "respuesta vacía del backend")
        try:
            return desenvolver(data)
        except ErrorFormato as exc:
            raise ErrorLotes(None, exc.detalle) from exc


# --- parseo de respuestas ---


def _texto_opcional(valor: object) -> str | None:
    if valor is None:
        return None
    texto = str(valor)
    return texto or None


def _producto(raw: dict) -> Producto:
    return Producto(
        id=str(raw.get("id", "")),
        nombre=str(raw.get("nombre", "")),
        activo=bool(raw.get("activo", True)),
    )


def _dispositivo(raw: object) -> DispositivoSector | None:
    if not isinstance(raw, dict):
        return None
    try:
        tipo = TipoDispositivo.desde(raw.get("type", raw.get("tipo")))
    except ErrorTipoDispositivo:
        return None
    return DispositivoSector(
        device_id=str(raw.get("device_id", raw.get("id", ""))),
        hostname=str(raw.get("hostname", "")),
        tipo=tipo,
    )


def _conteos(raw: object) -> Conteos:
    datos = raw if isinstance(raw, dict) else {}

    def numero(clave: str) -> int | None:
        valor = datos.get(clave)
        return None if valor is None else int(valor)

    return Conteos(
        ok=numero("ok"),
        crudo=numero("crudo"),
        quemado=numero("quemado"),
        total=int(datos.get("total", 0) or 0),
    )


def _lote(raw: dict) -> Lote:
    try:
        estado = EstadoLote(str(raw.get("estado", "ABIERTO")).upper())
    except ValueError:
        estado = EstadoLote.ABIERTO
    return Lote(
        id=str(raw.get("id", "")),
        sector_id=str(raw.get("sector_id", "")),
        estado=estado,
        producto_id=_texto_opcional(raw.get("producto_id")),
        producto_nombre=_texto_opcional(raw.get("producto_nombre")),
        abierto_en=str(raw.get("abierto_en", "")),
        abierto_por=_dispositivo(raw.get("abierto_por")),
        conteos=_conteos(raw.get("conteos")),
        ultimo_evento_en=_texto_opcional(raw.get("ultimo_evento_en")),
        inactividad_segundos=float(raw.get("inactividad_segundos", 0.0) or 0.0),
        cerrado_en=_texto_opcional(raw.get("cerrado_en")),
        motivo_cierre=_texto_opcional(raw.get("motivo_cierre")),
    )


def _sector(raw: dict) -> Sector:
    try:
        tipo = TipoDispositivo.desde(raw.get("tipo", raw.get("type")))
    except ErrorTipoDispositivo as exc:
        raise ErrorLotes(None, f"sector sin tipo válido: {exc}") from exc

    companeros_raw = raw.get("companeros")
    companeros: list[DispositivoSector] = []
    if isinstance(companeros_raw, list):
        for item in companeros_raw:
            dispositivo = _dispositivo(item)
            if dispositivo is not None:
                companeros.append(dispositivo)

    return Sector(
        sector_id=str(raw.get("sector_id", "")),
        nombre=str(raw.get("nombre", "")),
        tipo=tipo,
        companeros=tuple(companeros),
    )


# --- armado de payloads ---


def _evento_payload(evento: EventoDeteccion) -> dict:
    return {
        "evento_id": evento.evento_id,
        "producto_id": evento.producto_id,
        "estado": evento.estado.value,
        "confianza": round(evento.confianza, 4),
        "pista": evento.pista,
        "frame": evento.frame,
        "modelo_id": evento.modelo_id,
        "momento": evento.momento,
    }


def _conteos_payload(conteos: Conteos) -> dict:
    return {
        "ok": conteos.ok,
        "crudo": conteos.crudo,
        "quemado": conteos.quemado,
        "total": conteos.total,
    }
