"""Persistencia de eventos de detección en SQLite, con rotación por tamaño.

Espejo del `AlmacenDetecciones` JSONL: misma interfaz, misma semántica
(una fila por evento, latido periódico cuando no hay eventos, rotación
cuando la DB supera el máximo). Cambia el motor: SQLite con WAL para
mejor resiliencia ante cortes de energía del entorno de planta.

El JSONL queda soportado vía el flag `engine` del config; la migración es
in-place con un único importador que al detectar el engine legacy lee el
eventos.jsonl existente, lo inserta en la DB nueva, y elimina el archivo.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from backend.streaming.estabilizador import EventoPista

if TYPE_CHECKING:
    from backend.config import StorageConfig

logger = logging.getLogger(__name__)

_ENCODING = "utf-8"
# Tope de espera al encolar: preferimos descartar antes que bloquear la inferencia.
_ESPERA_ENCOLADO = 0.5
_TIMEOUT_CIERRE = 3.0
# Vocabulario de `tipo` en la DB. Coincide con el JSONL para que la misma
# definición de tipos funcione en ambos motores.
_TIPO_ALTA = "alta"
_TIPO_CAMBIO = "cambio"
_TIPO_QUEMADA = "quemada"
_TIPO_BAJA = "baja"
_TIPO_LATIDO = "latido"

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS eventos (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           TEXT    NOT NULL,
    frame        INTEGER,
    modelo       TEXT,
    fuente       TEXT,
    tipo         TEXT    NOT NULL,
    pista        INTEGER,
    label        TEXT,
    confianza    REAL,
    bbox         TEXT,
    activas      INTEGER
);
CREATE INDEX IF NOT EXISTS idx_eventos_ts ON eventos(ts);
CREATE INDEX IF NOT EXISTS idx_eventos_tipo ON eventos(tipo);
CREATE INDEX IF NOT EXISTS idx_eventos_label ON eventos(label);
"""


class AlmacenSQLite:
    """Escribe eventos de detección en SQLite (WAL) con rotación por tamaño.

    Mantiene la interfaz pública de AlmacenDetecciones para que el pipeline
    no cambie. La rotación cierra la DB actual, la mueve a eventos.N.db
    y abre una nueva. Con WAL se minimiza la pérdida ante cortes.
    """

    def __init__(self, storage: StorageConfig, *, fuente: str, modelo: str) -> None:
        self._cfg = storage
        self._fuente = fuente
        self._modelo = modelo
        # Conexión perezosa: la abrimos al primer registrar().
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.Lock()
        self._parar = threading.Event()
        self._hilo: threading.Thread | None = None
        self._frames_sin_eventos = 0
        self._error = ""

    @property
    def error(self) -> str:
        """Último fallo de escritura/apertura. Vacío si el almacén está sano."""
        with self._lock:
            return self._error

    # --- ciclo de vida ---

    def iniciar(self) -> None:
        """Crea el directorio y la DB, arranca el hilo escritor. Idempotente."""
        if self._hilo is not None:
            return
        self._cfg.path.parent.mkdir(parents=True, exist_ok=True)
        self._abrir_db()
        self._parar.clear()
        self._hilo = threading.Thread(target=self._bucle, name="storage", daemon=True)
        self._hilo.start()
        logger.info("Almacén SQLite: escribiendo en %s", self._cfg.path)

    def _abrir_db(self) -> None:
        try:
            self._conn = sqlite3.connect(
                str(self._cfg.path),
                check_same_thread=False,
                timeout=10.0,
            )
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.executescript(_SCHEMA_SQL)
            self._conn.commit()
        except sqlite3.Error as exc:
            self._anotar_error(f"No se pudo abrir {self._cfg.path}: {exc}")
            logger.exception("Almacén SQLite: %s", exc)
            self._conn = None

    def registrar(
        self,
        numero_frame: int,
        eventos: Sequence[EventoPista],
        activas: int,
    ) -> None:
        """Encola los eventos del frame para escritura asíncrona."""
        if self._hilo is None:
            return

        if eventos:
            self._frames_sin_eventos = 0
            for ev in eventos:
                self._encolar(self._serializar_evento(numero_frame, ev))
        else:
            self._frames_sin_eventos += 1
            cada = max(1, self._cfg.persist_no_detection_every)
            if self._frames_sin_eventos % cada:
                return
            self._encolar(self._serializar_latido(numero_frame, activas))

    def _encolar(self, fila: tuple) -> None:
        """Encola una fila para inserción en el hilo escritor."""
        with self._lock:
            if self._conn is None:
                return
            try:
                self._conn.execute(
                    """
                    INSERT INTO eventos
                        (ts, frame, modelo, fuente, tipo, pista, label, confianza, bbox, activas)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    fila,
                )
                self._conn.commit()
                self._limpiar_error()
            except sqlite3.Error as exc:
                self._anotar_error(f"No se pudo insertar: %s" % exc)
                logger.warning("Almacén SQLite: %s", exc)

    def detener(self) -> None:
        """Cierra la DB y espera al hilo."""
        self._parar.set()
        if self._hilo is not None:
            self._hilo.join(timeout=_TIMEOUT_CIERRE)
            self._hilo = None
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                except sqlite3.Error:
                    logger.warning("Almacén SQLite: fallo al cerrar %s", self._cfg.path)
                self._conn = None
        logger.info("Almacén SQLite: detenido")

    # --- serialización ---

    @staticmethod
    def _ts() -> str:
        return datetime.now().astimezone().isoformat(timespec="milliseconds")

    def _serializar_evento(self, numero_frame: int, evento: EventoPista) -> tuple:
        return (
            self._ts(),
            numero_frame,
            self._modelo,
            self._fuente,
            evento.tipo,
            evento.pista_id,
            evento.label,
            round(evento.confianza, 4),
            json.dumps(list(evento.bbox), ensure_ascii=False),
            None,
        )

    def _serializar_latido(self, numero_frame: int, activas: int) -> tuple:
        return (
            self._ts(),
            numero_frame,
            self._modelo,
            self._fuente,
            _TIPO_LATIDO,
            None,
            None,
            None,
            None,
            activas,
        )

    # --- rotación ---

    def _tamano_actual(self) -> int:
        try:
            return self._cfg.path.stat().st_size
        except OSError:
            return 0

    def _debe_rotar(self) -> bool:
        if self._cfg.max_bytes <= 0:
            return False
        return self._tamano_actual() > self._cfg.max_bytes

    def _rotar(self) -> None:
        ruta = self._cfg.path
        backups = max(0, self._cfg.max_files - 1)
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                except sqlite3.Error:
                    pass
                self._conn = None
        if backups == 0:
            ruta.unlink(missing_ok=True)
        else:
            ruta.with_name(f"{ruta.name}.{backups}").unlink(missing_ok=True)
            for indice in range(backups - 1, 0, -1):
                origen = ruta.with_name(f"{ruta.name}.{indice}")
                if origen.exists():
                    origen.replace(ruta.with_name(f"{ruta.name}.{indice + 1}"))
            if ruta.exists():
                ruta.replace(ruta.with_name(f"{ruta.name}.1"))
        self._abrir_db()

    # --- manejo de errores ---

    def _anotar_error(self, texto: str) -> None:
        with self._lock:
            self._error = texto

    def _limpiar_error(self) -> None:
        with self._lock:
            self._error = ""

    # --- bucle (placeholder para mantener la firma; la escritura es sincrónica) ---

    def _bucle(self) -> None:
        """Hilo dormido: la escritura es sincrónica en registrar() para
        minimizar la pérdida de eventos ante cortes. Sólo vigila la
        rotación por tamaño.
        """
        while not self._parar.is_set():
            if self._debe_rotar():
                logger.info("Almacén SQLite: rotando por tamaño")
                self._rotar()
            self._parar.wait(2.0)


def importar_jsonl_legacy(path: Path) -> int:
    """Importa un eventos.jsonl legacy al SQLite destino.

    Devuelve la cantidad de filas importadas. El .jsonl queda borrado
    después de una importación exitosa para no duplicar.

    Pensado para llamarse una sola vez desde una migración controlada
    (no se ejecuta automáticamente).
    """
    if not path.exists():
        return 0
    target = path.with_suffix(".db")
    conn = sqlite3.connect(str(target), timeout=10.0)
    conn.executescript(_SCHEMA_SQL)
    n = 0
    try:
        with path.open(encoding=_ENCODING) as f:
            for linea in f:
                linea = linea.strip()
                if not linea:
                    continue
                try:
                    d = json.loads(linea)
                except json.JSONDecodeError:
                    continue
                conn.execute(
                    """
                    INSERT INTO eventos
                        (ts, frame, modelo, fuente, tipo, pista, label, confianza, bbox, activas)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        d.get("ts"),
                        d.get("frame"),
                        d.get("modelo"),
                        d.get("fuente"),
                        d.get("tipo"),
                        d.get("pista"),
                        d.get("label"),
                        d.get("confianza"),
                        json.dumps(d.get("bbox"), ensure_ascii=False) if d.get("bbox") is not None else None,
                        d.get("activas"),
                    ),
                )
                n += 1
        conn.commit()
        path.unlink()
        logger.info("Almacén: importados %d eventos desde %s", n, path)
    finally:
        conn.close()
    return n