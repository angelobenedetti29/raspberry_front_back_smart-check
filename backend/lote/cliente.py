"""Cliente HTTP contra los endpoints de sector, productos y lotes del backend Go.

Usa sólo la stdlib (`urllib`), igual que `backend/device/cliente.py` y
`backend/monitor/cliente.py`. Convierte cualquier fallo de red o HTTP en
`ErrorLotes` para que el servicio decida si reintenta.

No maneja reintentos ni estado: eso es responsabilidad del `LoteService`.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

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

_JSON = "application/json"


class ClienteLotes:
    """Cliente de los endpoints de lotes. No reintenta: eso es del servicio."""

    def __init__(self, api: ApiConfig, timeout: float = 5.0) -> None:
        self._api = api
        self._timeout = timeout

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
        url = f"{self._api.base_url}{path}"
        headers = {"Authorization": f"Bearer {secret}", "Accept": _JSON}
        datos: bytes | None = None
        if payload is not None:
            datos = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = _JSON

        peticion = urllib.request.Request(url, data=datos, headers=headers, method=metodo)
        try:
            with urllib.request.urlopen(peticion, timeout=self._timeout) as respuesta:
                cuerpo = respuesta.read()
        except urllib.error.HTTPError as exc:
            raise ErrorLotes(exc.code, _detalle_error(exc)) from exc
        except (urllib.error.URLError, OSError) as exc:
            raise ErrorLotes(None, str(exc)) from exc

        if not cuerpo:
            raise ErrorLotes(None, "respuesta vacía del backend")
        try:
            envelope = json.loads(cuerpo)
        except json.JSONDecodeError as exc:
            raise ErrorLotes(None, "respuesta del backend no es JSON") from exc
        if not isinstance(envelope, dict):
            raise ErrorLotes(None, "respuesta del backend no es un objeto JSON")
        if not envelope.get("success", False):
            detalle = str(
                envelope.get("errors")
                or envelope.get("error")
                or envelope.get("message")
                or "respuesta sin éxito"
            )
            raise ErrorLotes(None, detalle)
        return envelope.get("data")


# --- parseo de respuestas ---


def _detalle_error(exc: urllib.error.HTTPError) -> str:
    """Extrae el detalle de error del cuerpo de un HTTPError, si se puede."""
    try:
        cuerpo = exc.read().decode("utf-8", "replace")
    except OSError:
        return str(exc.reason or "error HTTP")
    try:
        datos = json.loads(cuerpo)
    except json.JSONDecodeError:
        return cuerpo.strip() or str(exc.reason or "error HTTP")
    if isinstance(datos, dict):
        return str(
            datos.get("errors")
            or datos.get("error")
            or datos.get("message")
            or cuerpo
        )
    return cuerpo


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
