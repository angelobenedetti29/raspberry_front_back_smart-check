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
from pathlib import Path
from typing import TYPE_CHECKING

from backend.lote.tipos import evento_a_dict
from backend.rotacion import rotar_archivo
from backend.tiempo import ahora_iso_local

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


class ColaDLQ:
    """Cola DLQ persistente: append-only JSONL con rotación por tamaño.

    Uso:
        dlq = ColaDLQ(Path("data/dlq.jsonl"), max_bytes=5_242_880, max_files=5)
        dlq.encolar(eventos, codigo_http=503, detalle="backend caído", intento=3)
        eventos_recuperados = dlq.drenar()

    La rotación es best-effort: cuando el archivo principal supera
    `max_bytes` justo antes de escribir, se mueve a `dlq.jsonl.1` (y los
    backups se desplazan). `drenar()` lee el principal y los rotados (del
    más viejo al más nuevo), y `vaciar()` los borra todos.
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
        `backend.lote.tipos.evento_a_dict`) más el contexto del fallo.
        Si la escritura falla, marca `error` y sigue (no propaga):
        el DLQ es la red de seguridad, no debe tirar al servicio.
        """
        eventos_list = list(eventos)
        if not eventos_list:
            return
        entrada = {
            "ts": ahora_iso_local(),
            "lote_id": lote_id,
            "codigo_http": codigo_http,
            "detalle": detalle,
            "intento": intento,
            "eventos": [evento_a_dict(ev) for ev in eventos_list],
        }
        linea = json.dumps(entrada, ensure_ascii=False) + "\n"
        linea_bytes = linea.encode("utf-8")

        with self._lock:
            self._cfg.path.parent.mkdir(parents=True, exist_ok=True)
            try:
                if self._debe_rotar(len(linea_bytes)):
                    rotar_archivo(self._cfg.path, self._cfg.max_files)
                with self._cfg.path.open("a", encoding="utf-8") as fh:
                    fh.write(linea)
                self._error = ""
            except OSError as exc:
                self._error = f"No se pudo escribir en {self._cfg.path}: {exc}"
                logger.warning("DLQ: %s", exc)

    # --- lectura / drain ---

    def drenar(self) -> list[list[dict]]:
        """Lee las entradas del DLQ (rotados incluidos) y devuelve los batches.

        NO borra archivos: el caller decide si vaciarlos tras un
        reenvío exitoso (vía `vaciar()`). Esto evita perder eventos
        si el reenvío falla a mitad de camino. Se leen los rotados del
        más viejo al más nuevo (`.N … .1`) y al final el principal, para
        aproximar el orden cronológico; un evento reenviado dos veces lo
        deduplica el backend por `evento_id`.

        Devuelve una lista de batches; cada batch es la lista de eventos
        (dicts con la misma forma que `backend.lote.tipos.evento_a_dict`) que
        viajaban juntos al fallar. Si una línea tiene JSON inválido se ignora.
        """
        batches: list[list[dict]] = []
        for ruta in self._rutas_drenables():
            batches.extend(self._leer_batches(ruta))
        return batches

    def _rutas_drenables(self) -> list[Path]:
        """Rotados del más viejo al más nuevo y, al final, el principal."""
        ruta = self._cfg.path
        backups = max(0, self._cfg.max_files - 1)
        rutas = [ruta.with_name(f"{ruta.name}.{indice}") for indice in range(backups, 0, -1)]
        rutas.append(ruta)
        return rutas

    def _leer_batches(self, ruta: Path) -> list[list[dict]]:
        """Batches de un archivo; vacío si no existe o no se puede leer."""
        if not ruta.exists():
            return []
        with self._lock:
            try:
                contenido = ruta.read_text(encoding="utf-8")
            except OSError as exc:
                self._error = f"No se pudo leer {ruta}: {exc}"
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
        """Borra los archivos del DLQ: el principal y los rotados.

        Llamado tras un drain exitoso (los eventos fueron re-enviados).
        """
        with self._lock:
            for ruta in self._rutas_drenables():
                try:
                    ruta.unlink(missing_ok=True)
                except OSError as exc:
                    self._error = f"No se pudo borrar {ruta}: {exc}"
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
