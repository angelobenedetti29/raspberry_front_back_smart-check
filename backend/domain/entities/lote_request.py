from dataclasses import dataclass, fields
from datetime import datetime, timezone
from math import isfinite
from typing import Any, Mapping
from uuid import UUID


def utc_iso(value: datetime) -> str:
    """Serializa un datetime como ISO-8601 UTC terminado en ``Z``.

    Un datetime sin zona se interpreta como hora local (es lo que entrega el
    worker con ``datetime.now()``); uno con zona se convierte a UTC.
    """
    if value.tzinfo is None:
        value = value.astimezone()
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_uuid(value: Any) -> UUID:
    """Convierte a ``UUID`` un valor que ya lo sea o una cadena válida."""
    if isinstance(value, UUID):
        return value
    if isinstance(value, str):
        try:
            return UUID(value)
        except ValueError:
            raise ValueError(f"Formato UUID inválido: {value}") from None
    raise ValueError(f"Formato UUID inválido: {value}")


def _parse_datetime(value: Any) -> datetime:
    """Convierte a ``datetime`` un valor que ya lo sea o una cadena ISO-8601.

    Acepta el sufijo ``Z`` reemplazándolo por ``+00:00`` antes de parsear.
    """
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError(f"Formato de fecha inválido: {value}") from None
    raise ValueError(f"Formato de fecha inválido: {value}")


def _parse_int(value: Any, field: str) -> int:
    """Convierte a ``int`` un valor entero o un flotante con valor entero."""
    # Los booleanos son subclase de int: se rechazan para no aceptar True/False.
    if isinstance(value, bool):
        raise ValueError(f"El campo '{field}' debe ser un entero.")
    if isinstance(value, int):
        return value
    if isinstance(value, float) and isfinite(value) and value.is_integer():
        return int(value)
    raise ValueError(f"El campo '{field}' debe ser un entero.")


def _parse_float(value: Any, field: str) -> float:
    """Convierte a ``float`` un número finito, rechazando booleanos."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"El campo '{field}' debe ser un número.")
    number = float(value)
    if not isfinite(number):
        raise ValueError(f"El campo '{field}' debe ser un número finito.")
    return number


@dataclass
class LoteRequest:
    """DTO explícito del contrato del central (campos camelCase).

    Decisión Y2: se mantiene como DTO de dominio a propósito; ``from_payload`` y
    ``to_payload`` viven aquí porque el esquema y el contrato HTTP son el mismo
    artefacto.
    """

    productoId: UUID
    turno: str
    inicioAt: datetime
    finAt: datetime
    totalUnidades: int
    correctos: int
    quemados: int
    crudas: int
    correctosKg: float
    quemadosKg: float
    crudosKg: float
    tempHorno1: float
    tempCombHorno1: float
    tempHorno2: float
    tempCombHorno2: float
    velocidadCinta: float

    def __post_init__(self):
        """Valida las invariantes del contrato al construir el DTO."""
        # Sin cambios: mensajes y reglas actuales (total de unidades, negativos, fechas).
        if self.inicioAt >= self.finAt:
            raise ValueError("La fecha de inicio debe ser anterior a la fecha de fin.")
        if self.correctos < 0 or self.quemados < 0 or self.crudas < 0:
            raise ValueError("Los valores de correctos, quemados y crudas no pueden ser negativos.")
        if self.correctosKg < 0 or self.quemadosKg < 0 or self.crudosKg < 0:
            raise ValueError("Los valores de correctosKg, quemadosKg y crudosKg no pueden ser negativos.")
        if self.tempHorno1 < 0 or self.tempHorno2 < 0 or self.tempCombHorno1 < 0 or self.tempCombHorno2 < 0:
            raise ValueError("Las temperaturas del horno no pueden ser negativas.")
        if self.velocidadCinta < 0:
            raise ValueError("La velocidad de la cinta no puede ser negativa.")
        if self.totalUnidades != self.correctos + self.quemados + self.crudas:
            raise ValueError("El total de unidades debe ser igual a la suma de correctos, quemados y crudas.")

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "LoteRequest":
        """Valida y normaliza el payload camelCase del central.

        Los campos faltantes se reportan en el orden de declaración de la clase.
        """
        missing = [field.name for field in fields(cls) if field.name not in payload]
        if missing:
            raise ValueError(f"Faltan campos obligatorios: {', '.join(missing)}")

        turno = payload["turno"]
        if not isinstance(turno, str) or not turno:
            raise ValueError("El campo 'turno' debe ser un texto no vacío.")

        return cls(
            productoId=_parse_uuid(payload["productoId"]),
            turno=turno,
            inicioAt=_parse_datetime(payload["inicioAt"]),
            finAt=_parse_datetime(payload["finAt"]),
            totalUnidades=_parse_int(payload["totalUnidades"], "totalUnidades"),
            correctos=_parse_int(payload["correctos"], "correctos"),
            quemados=_parse_int(payload["quemados"], "quemados"),
            crudas=_parse_int(payload["crudas"], "crudas"),
            correctosKg=_parse_float(payload["correctosKg"], "correctosKg"),
            quemadosKg=_parse_float(payload["quemadosKg"], "quemadosKg"),
            crudosKg=_parse_float(payload["crudosKg"], "crudosKg"),
            tempHorno1=_parse_float(payload["tempHorno1"], "tempHorno1"),
            tempCombHorno1=_parse_float(payload["tempCombHorno1"], "tempCombHorno1"),
            tempHorno2=_parse_float(payload["tempHorno2"], "tempHorno2"),
            tempCombHorno2=_parse_float(payload["tempCombHorno2"], "tempCombHorno2"),
            velocidadCinta=_parse_float(payload["velocidadCinta"], "velocidadCinta"),
        )

    def to_payload(self) -> dict:
        """Devuelve el dict camelCase listo para enviar (UUID str, fechas UTC ``Z``)."""
        return {
            "productoId": str(self.productoId),
            "turno": self.turno,
            "inicioAt": utc_iso(self.inicioAt),
            "finAt": utc_iso(self.finAt),
            "totalUnidades": self.totalUnidades,
            "correctos": self.correctos,
            "quemados": self.quemados,
            "crudas": self.crudas,
            "correctosKg": self.correctosKg,
            "quemadosKg": self.quemadosKg,
            "crudosKg": self.crudosKg,
            "tempHorno1": self.tempHorno1,
            "tempCombHorno1": self.tempCombHorno1,
            "tempHorno2": self.tempHorno2,
            "tempCombHorno2": self.tempCombHorno2,
            "velocidadCinta": self.velocidadCinta,
        }
