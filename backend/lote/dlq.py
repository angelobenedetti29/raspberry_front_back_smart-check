"""Dead-letter queue (DLQ) para eventos de lote que no se pudieron reportar.

Append-only JSONL en `paths.data_dir/dlq.jsonl`, rotado por tamaño (mismo
patrón que `backend.streaming.storage_sqlite.AlmacenSQLite`, sólo que
JSONL en vez de SQLite). Cada entrada
registra el batch de eventos que falló al subirse al backend, junto con
el código HTTP y el detalle del error, así un análisis post-mortem sabe
qué se rechazó y un drain al boot puede re-enqueue de los.

Concurrencia: una sola instancia por `LoteService`; las llamadas se
serializan con un `threading.Lock`. La escritura a disco es síncrona
(no hay hilo escritor) para minimizar la pérdida ante cortes de energía,
igual que `AlmacenSQLite`.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.lote.tipos import EventoDeteccion

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _CfgDLQ:
    """Config del DLQ derivada del LoteConfig.

    Vive como dataclass propio para que `dlq.py` no importe `backend.config`
    y mantener el módulo trivialmente testeable.
    """

    path: Path
    max_bytes: int
    max_files: int


def _ahora_iso() -> str:
    """Timestamp local con offset, informativo para el audit / DLQ."""
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


class ColaDLQ:
    """Cola DLQ persistente: append-only JSONL con rotación por tamaño.

    Uso:
        dlq = ColaDLQ(Path("data/dlq.jsonl"), max_bytes=5_242_880, max_files=5)
        dlq.encolar(eventos, codigo_http=503, detalle="backend caído", intento=3)
        eventos_recuperados = dlq.drenar()

    La rotación es best-effort: cuando el archivo principal supera
    `max_bytes` justo antes de escribir, se mueve a `dlq.jsonl.1` (y los
    backups se desplazan). `drenar()` sólo lee el archivo principal; los
    rotados quedan como histórico para post-mortem.
    """

    def __init__(self, path: Path, *, max_bytes: int, max_files: int) -> None:
        if max_bytes <= 0:
            raise ValueError("max_bytes debe ser > 0")
        if max_files <= 0:
            raise ValueError("max_files debe ser > 0")
        self._cfg = _CfgDLQ(path=path, max_bytes=max_bytes, max_files=max_files)
        self._lock = threading.Lock()
        self._error = ""

    @property
    def error(self) -> str:
        """Último fallo de apertura/escritura. Vacío si el DLQ está sano."""
        with self._lock:
            return self._error

    @property
    def path(self) -> Path:
        """Ruta del archivo principal (no de los rotados)."""
        return self._cfg.path

    # --- escritura ---

    def encolar(
        self,
        eventos: Iterable[EventoDeteccion],
        *,
        codigo_http: int | None,
        detalle: str,
        intento: int,
        lote_id: str | None = None,
    ) -> None:
        """Persiste un batch fallido al DLQ.

        Serializa cada evento a un dict estable (mismo shape que
        `ClienteLotes._evento_payload`) más el contexto del fallo.
        Si la escritura falla, marca `error` y sigue (no propaga):
        el DLQ es la red de seguridad, no debe tirar al servicio.
        """
        eventos_list = list(eventos)
        if not eventos_list:
            return
        entrada = {
            "ts": _ahora_iso(),
            "lote_id": lote_id,
            "codigo_http": codigo_http,
            "detalle": detalle,
            "intento": intento,
            "eventos": [_evento_a_dict(ev) for ev in eventos_list],
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
                self._error = ""
            except OSError as exc:
                self._error = f"No se pudo escribir en {self._cfg.path}: {exc}"
                logger.warning("DLQ: %s", exc)

    # --- lectura / drain ---

    def drenar(self) -> list[list[dict]]:
        """Lee las entradas del DLQ y devuelve los batches parseados.

        NO borra el archivo: el caller decide si vaciarlo tras un
        reenvío exitoso (vía `vaciar()`). Esto evita perder eventos
        si el reenvío falla a mitad de camino.

        Devuelve una lista de batches; cada batch es la lista de eventos
        (dicts con la misma forma que `_evento_payload`) que viajaban
        juntos al fallar. Si una línea tiene JSON inválido se ignora.
        """
        if not self._cfg.path.exists():
            return []
        with self._lock:
            try:
                contenido = self._cfg.path.read_text(encoding="utf-8")
            except OSError as exc:
                self._error = f"No se pudo leer {self._cfg.path}: {exc}"
                logger.warning("DLQ: %s", exc)
                return []
        batches: list[list[dict]] = []
        for linea in contenido.splitlines():
            linea = linea.strip()
            if not linea:
                continue
            try:
                entrada = json.loads(linea)
            except json.JSONDecodeError:
                logger.warning("DLQ: línea con JSON inválido, se ignora")
                continue
            eventos = entrada.get("eventos")
            if isinstance(eventos, list):
                batches.append(eventos)
        return batches

    def vaciar(self) -> None:
        """Borra el archivo principal del DLQ.

        Llamado tras un drain exitoso (los eventos fueron re-enviados).
        Conserva los rotados como histórico.
        """
        with self._lock:
            try:
                self._cfg.path.unlink(missing_ok=True)
                self._error = ""
            except OSError as exc:
                self._error = f"No se pudo borrar {self._cfg.path}: {exc}"
                logger.warning("DLQ: %s", exc)

    # --- rotación ---

    def _tamano_actual(self) -> int:
        try:
            return self._cfg.path.stat().st_size
        except OSError:
            return 0

    def _debe_rotar(self, tamano_linea: int) -> bool:
        """True si la línea no entra en el archivo actual."""
        if self._cfg.max_bytes <= 0:
            return False
        actual = self._tamano_actual()
        return actual > 0 and actual + tamano_linea > self._cfg.max_bytes

    def _rotar(self) -> None:
        """Rota: el actual pasa a `.1`, conservando `max_files` en total."""
        ruta = self._cfg.path
        backups = max(0, self._cfg.max_files - 1)
        if backups == 0:
            ruta.unlink(missing_ok=True)
            return
        # El backup más viejo se descarta.
        ruta.with_name(f"{ruta.name}.{backups}").unlink(missing_ok=True)
        for indice in range(backups - 1, 0, -1):
            origen = ruta.with_name(f"{ruta.name}.{indice}")
            if origen.exists():
                origen.replace(ruta.with_name(f"{ruta.name}.{indice + 1}"))
        if ruta.exists():
            ruta.replace(ruta.with_name(f"{ruta.name}.1"))


def _evento_a_dict(evento: EventoDeteccion) -> dict:
    """Serializa un EventoDeteccion al mismo dict que ClienteLotes manda.

    Mismo shape (`evento_id`, `producto_id`, `estado`, `confianza`, `pista`,
    `frame`, `modelo_id`, `momento`) para que un drain + re-encolar funcione
    sin transformación.
    """
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