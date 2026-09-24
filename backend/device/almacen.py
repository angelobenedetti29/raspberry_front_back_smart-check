"""Persistencia del estado de registro del dispositivo en device.json."""

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from backend.device.tipos import ErrorTipoDispositivo, TipoDispositivo


class ErrorAlmacen(Exception):
    """Error al leer o escribir el archivo de estado del dispositivo."""


@dataclass(frozen=True, slots=True)
class EstadoDispositivo:
    """Estado persistido de la Raspberry frente al backend.

    request_id existe desde que se pide el registro; device_id y secret sólo
    se completan cuando el backend aprueba (único momento en que se entrega
    el secret). status refleja el último valor conocido: PENDING/APPROVED.
    """

    hostname: str
    request_id: str
    status: str
    device_id: str | None = None
    secret: str | None = None
    created_at: str | None = None
    tipo: TipoDispositivo | None = None


class AlmacenDispositivo:
    """Lee y escribe device.json de forma atómica.

    La ruta se recibe desde afuera (la deriva el llamador del ROOT del
    proyecto); el almacén no conoce la config.
    """

    def __init__(self, path: Path) -> None:
        self._path = path

    @property
    def path(self) -> Path:
        return self._path

    def cargar(self) -> EstadoDispositivo | None:
        """Devuelve el estado guardado, o None si no hay archivo.

        Un archivo corrupto o con campos faltantes se trata como ausente:
        el dispositivo vuelve a registrarse en vez de arrastrar basura.
        """
        if not self._path.is_file():
            return None
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(data, dict):
            return None
        try:
            return EstadoDispositivo(
                hostname=data["hostname"],
                request_id=data["request_id"],
                status=data["status"],
                # `tipo` es obligatorio: sin él (archivo legado) se degrada a
                # None y el dispositivo se re-registra para fijar su tipo.
                tipo=TipoDispositivo.desde(data["tipo"]),
                device_id=data.get("device_id"),
                secret=data.get("secret"),
                created_at=data.get("created_at"),
            )
        except (KeyError, ErrorTipoDispositivo):
            return None

    def guardar(self, estado: EstadoDispositivo) -> None:
        """Escribe el estado atómicamente (tmp + os.replace).

        El secret queda en texto plano con permisos 600: es el único lugar
        donde vive en la Raspberry y no debe ser legible por otros usuarios.
        """
        if estado.tipo is not None and not isinstance(estado.tipo, TipoDispositivo):
            raise ErrorAlmacen(
                f"Tipo de dispositivo inválido en el estado: {estado.tipo!r}"
            )
        payload = asdict(estado)
        if estado.tipo is not None:
            payload["tipo"] = estado.tipo.value
        payload = json.dumps(payload, indent=2, ensure_ascii=False)
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(
                dir=self._path.parent, prefix=".device-", suffix=".tmp"
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as tmp:
                    tmp.write(payload)
                os.chmod(tmp_name, 0o600)
                os.replace(tmp_name, self._path)
            except BaseException:
                Path(tmp_name).unlink(missing_ok=True)
                raise
        except OSError as exc:
            raise ErrorAlmacen(
                f"No se pudo escribir el estado del dispositivo en {self._path}: {exc}"
            ) from exc

    def borrar(self) -> None:
        """Elimina el archivo de estado (p. ej. para forzar un re-registro)."""
        try:
            self._path.unlink(missing_ok=True)
        except OSError as exc:
            raise ErrorAlmacen(
                f"No se pudo borrar el estado del dispositivo en {self._path}: {exc}"
            ) from exc
