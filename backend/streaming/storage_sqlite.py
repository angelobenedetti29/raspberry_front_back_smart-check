"""Persistencia de eventos de detección en SQLite, con rotación por tamaño.

Una fila por evento (`alta`/`cambio`/`quemada`/`baja` de pista, más un
`latido` periódico cuando no hay eventos). Rotación cuando la DB supera
`max_bytes`: cierra, mueve a `eventos.N.db` y abre una nueva.

SQLite con WAL y `synchronous=NORMAL` minimiza la pérdida de filas ante
cortes de energía del entorno de planta. La escritura es sincrónica
dentro de `registrar()` para que el `commit` acompañe al evento antes de
devolver: preferimos descartar antes que acumular backlog en memoria.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from collections.abc import Sequence
from datetime import datetime
from typing import TYPE_CHECKING

from backend.rotacion import rotar_archivo
from backend.streaming.estabilizador import EventoPista

if TYPE_CHECKING:
    from backend.config import StorageConfig

logger = logging.getLogger(__name__)

_TIMEOUT_CIERRE = 3.0
# Vocabulario de `tipo` en la DB. Coincide con el JSONL para que la misma
# definición de tipos funcione en ambos motores.
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

    La rotación cierra la DB actual, la mueve a eventos.N.db y abre una
    nueva. Con WAL se minimiza la pérdida ante cortes de energía.
    """

    def __init__(self, storage: StorageConfig, *, fuente: str, modelo: str) -> None:
        self._cfg = storage
        self._fuente = fuente
        self._modelo = modelo
        # Conexión perezosa: la abrimos al primer registrar().
        self._conn: sqlite3.Connection | None = None
        # RLock (no Lock): los helpers `_anotar_error` / `_limpiar_error`
        # también toman el lock y se invocan desde `_encolar` mientras ya lo
        # tenemos. Con `Lock` eso deadlockea en el primer evento; con RLock
        # se re-entra sin problema. El mismo lock sigue protegiendo el
        # acceso al `Connection` compartido desde el hilo escritor.
        self._lock = threading.RLock()
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
        # Todo bajo el lock: si el rename o el reopen quedaran afuera, otro
        # hilo podría encolar contra la conexión ya cerrada y perder el evento.
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                except sqlite3.Error:
                    pass
                self._conn = None
            rotar_archivo(self._cfg.path, self._cfg.max_files)
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
        minimizar la pérdida de eventos ante cortes. Reintenta la apertura
        y vigila la rotación por tamaño.
        """
        while not self._parar.is_set():
            with self._lock:
                if self._conn is None:
                    # Un reopen fallido (p. ej. disco lleno) no debe dejar el
                    # almacén descartando eventos para siempre.
                    self._abrir_db()
            if self._debe_rotar():
                logger.info("Almacén SQLite: rotando por tamaño")
                self._rotar()
            self._parar.wait(2.0)