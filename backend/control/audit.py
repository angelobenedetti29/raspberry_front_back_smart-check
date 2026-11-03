"""Audit log append-only de decisiones de control (SCA-400).

Persiste cada decisión aplicada por el `ControlService` (o descartada por
dedup / pendiente de aprobación por SUPERVISION / con fallo de ack al
backend) a un JSONL rotado por tamaño. El `decision_id` del backend es la llave de
reconciliación: si el log rotó, el post-mortem puede cruzar este archivo
con el backend para reconstruir qué se aplicó al PLC.

Concurrencia: una sola instancia por `ControlService`. Escritura
síncrona para minimizar la pérdida ante cortes de energía (mismo
principio que `AlmacenSQLite` y `ColaDLQ`).
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from backend.rotacion import rotar_archivo
from backend.tiempo import ahora_iso_local

if TYPE_CHECKING:
    from backend.control.tipos import SetpointPropuesto

logger = logging.getLogger(__name__)


# Resultados posibles del ciclo de decisión. El string viaja literal al
# JSONL para que un parser externo (jq, log shipper) pueda filtrar.
ResultadoDecision = Literal[
    "aplicada",  # el setpoint se aplicó al PLC y el ack al backend fue OK
    "descartada_dup",  # mismo decision_id dentro del TTL: no se aplica
    "pendiente_aprobacion",  # SUPERVISION: espera aprobación del operario
    "fallo_ack",  # se aplicó al PLC pero el ack al backend falló
]


@dataclass(frozen=True, slots=True)
class _CfgAudit:
    path: Path
    max_bytes: int
    max_files: int


class AuditorConsignas:
    """Escribe entradas de audit append-only a un archivo JSONL rotado.

    Uso:
        auditor = AuditorConsignas(Path("data/control_audit.jsonl"),
                                   max_bytes=5_242_880, max_files=5)
        auditor.decision_aplicada(lote_id="L-1", propuesta=setpoint,
                                  resultado="aplicada")

    La rotación es best-effort: si la línea no entra en el archivo
    principal, se mueve a `.1` (y los backups se desplazan). El
    esquema del archivo es estable: un objeto JSON por línea con
    campos fijos para que un parser externo lo consuma.
    """

    def __init__(self, path: Path, *, max_bytes: int, max_files: int) -> None:
        if max_bytes <= 0:
            raise ValueError("max_bytes debe ser > 0")
        if max_files <= 0:
            raise ValueError("max_files debe ser > 0")
        self._cfg = _CfgAudit(path=path, max_bytes=max_bytes, max_files=max_files)
        self._lock = threading.Lock()

    # --- escritura ---

    def decision_aplicada(
        self,
        lote_id: str,
        propuesta: SetpointPropuesto,
        *,
        resultado: ResultadoDecision,
        detalle: str = "",
    ) -> None:
        """Persiste una decisión de control al audit log.

        `resultado` indica el camino que tomó: aplicada, descartada por
        dedup, pendiente de aprobación en SUPERVISION, o aplicada al PLC
        con fallo de ack al backend. `detalle` agrega contexto libre (ej:
        "ttl expirado" para un descartada_dup).
        """
        entrada = {
            "ts": ahora_iso_local(),
            "lote_id": lote_id,
            "decision_id": propuesta.decision_id,
            "aplicado_por": propuesta.aplicado_por,
            "ventana": propuesta.ventana,
            "resultado": resultado,
            "detalle": detalle,
            "temp_horno_1": round(propuesta.temp_horno_1, 3),
            "velocidad_cinta": round(propuesta.velocidad_cinta, 4),
        }
        linea = json.dumps(entrada, ensure_ascii=False) + "\n"
        linea_bytes = linea.encode("utf-8")

        with self._lock:
            self._cfg.path.parent.mkdir(parents=True, exist_ok=True)
            try:
                if self._debe_rotar(len(linea_bytes)):
                    self._rotar()
                with self._cfg.path.open("a", encoding="utf-8") as fh:
                    fh.write(linea)
            except OSError as exc:
                logger.warning("Audit: %s", exc)

    # --- rotación ---

    def _tamano_actual(self) -> int:
        try:
            return self._cfg.path.stat().st_size
        except OSError:
            return 0

    def _debe_rotar(self, tamano_linea: int) -> bool:
        if self._cfg.max_bytes <= 0:
            return False
        actual = self._tamano_actual()
        return actual > 0 and actual + tamano_linea > self._cfg.max_bytes

    def _rotar(self) -> None:
        """Rota: el actual pasa a `.1`, conservando `max_files` en total."""
        rotar_archivo(self._cfg.path, self._cfg.max_files)
