"""Persistencia de eventos de detección en JSON Lines, con rotación.

Una línea por evento (no por frame): `alta`, `cambio`, `quemada` y `baja` de
cada pista, más un latido periódico. Contar productos es agrupar los `baja` por
su `label` final.

El hilo de inferencia nunca escribe: encola y sigue. Un hilo escritor propio es
el único que toca el archivo, y rota cuando el tamaño supera `max_bytes`.
"""

from __future__ import annotations

import json
import logging
import queue
import threading
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from backend.streaming.estabilizador import EventoPista

if TYPE_CHECKING:
    from backend.config import StorageConfig

logger = logging.getLogger(__name__)

_CENTINELA = object()
_ENCODING = "utf-8"
# Tope de espera al encolar: preferimos descartar antes que bloquear la inferencia.
_ESPERA_ENCOLADO = 0.5
_TIMEOUT_CIERRE = 3.0
# Vocabulario de `tipo` en el archivo de eventos.
_TIPO_ALTA = "alta"
_TIPO_CAMBIO = "cambio"
_TIPO_QUEMADA = "quemada"
_TIPO_BAJA = "baja"
_TIPO_LATIDO = "latido"


class AlmacenDetecciones:
    """Escribe eventos de detección a un archivo JSON Lines con rotación.

    Cada evento es una línea JSON autocontenida
    `{"ts", "frame", "modelo", "fuente", "tipo", "pista", "label", "confianza",
    "bbox"}`; el latido usa `{"ts", "frame", "modelo", "fuente", "tipo",
    "activas"}`.
    """

    def __init__(self, storage: StorageConfig, *, fuente: str, modelo: str) -> None:
        self._cfg = storage
        self._fuente = fuente
        self._modelo = modelo
        self._cola: queue.Queue[object] = queue.Queue(
            maxsize=max(1, storage.queue_size)
        )
        self._parar = threading.Event()
        self._hilo: threading.Thread | None = None
        self._frames_sin_eventos = 0
        self._lock = threading.Lock()
        self._error = ""

    @property
    def error(self) -> str:
        """Último fallo de escritura/apertura. Vacío si el almacén está sano."""
        with self._lock:
            return self._error

    # --- ciclo de vida ---

    def iniciar(self) -> None:
        """Crea el directorio de salida y arranca el hilo escritor. Idempotente."""
        if self._hilo is not None:
            return
        self._cfg.path.parent.mkdir(parents=True, exist_ok=True)
        self._parar.clear()
        self._hilo = threading.Thread(target=self._bucle, name="storage", daemon=True)
        self._hilo.start()
        logger.info("Almacén: escribiendo en %s", self._cfg.path)

    def registrar(
        self,
        numero_frame: int,
        eventos: Sequence[EventoPista],
        activas: int,
    ) -> None:
        """Encola los eventos del frame.

        Si no hubo eventos sólo se escribe un latido cada
        `persist_no_detection_every` frames: sin ese latido, un período sin
        eventos es indistinguible de un sistema caído. `activas` es cuántas
        pistas seguían visibles en el frame.
        """
        if self._hilo is None:
            return

        if eventos:
            self._frames_sin_eventos = 0
            lineas = [
                self._serializar_evento(numero_frame, evento) for evento in eventos
            ]
        else:
            self._frames_sin_eventos += 1
            cada = max(1, self._cfg.persist_no_detection_every)
            if self._frames_sin_eventos % cada:
                return
            lineas = [self._serializar_latido(numero_frame, activas)]

        for linea in lineas:
            try:
                self._cola.put(linea, timeout=_ESPERA_ENCOLADO)
            except queue.Full:
                logger.warning(
                    "Almacén: cola llena, se descarta el registro del frame %d",
                    numero_frame,
                )

    def detener(self) -> None:
        """Drena la cola pendiente y detiene el hilo. Bloquea hasta el timeout."""
        if self._hilo is None:
            return
        self._parar.set()
        self._despertar()
        self._hilo.join(timeout=_TIMEOUT_CIERRE)
        self._hilo = None
        logger.info("Almacén: detenido")

    # --- internos ---

    @staticmethod
    def _ts() -> str:
        return datetime.now().astimezone().isoformat(timespec="milliseconds")

    def _serializar_evento(self, numero_frame: int, evento: EventoPista) -> str:
        """Arma la línea JSON de un evento. `ensure_ascii=False` para los acentos."""
        registro = {
            "ts": self._ts(),
            "frame": numero_frame,
            "modelo": self._modelo,
            "fuente": self._fuente,
            "tipo": evento.tipo,
            "pista": evento.pista_id,
            "label": evento.label,
            "confianza": round(evento.confianza, 4),
            "bbox": list(evento.bbox),
        }
        return json.dumps(registro, ensure_ascii=False) + "\n"

    def _serializar_latido(self, numero_frame: int, activas: int) -> str:
        """Arma la línea JSON del latido periódico."""
        registro = {
            "ts": self._ts(),
            "frame": numero_frame,
            "modelo": self._modelo,
            "fuente": self._fuente,
            "tipo": _TIPO_LATIDO,
            "activas": activas,
        }
        return json.dumps(registro, ensure_ascii=False) + "\n"

    def _bucle(self) -> None:
        """Hilo escritor: único que abre, escribe, rota y cierra el archivo."""
        try:
            archivo = self._cfg.path.open("a", encoding=_ENCODING, buffering=1)
        except OSError as exc:
            self._anotar_error(f"No se pudo abrir {self._cfg.path}: {exc}")
            logger.error("Almacén: %s", exc)
            return

        try:
            while True:
                try:
                    linea = self._cola.get(timeout=0.2)
                except queue.Empty:
                    if self._parar.is_set() and self._cola.empty():
                        return
                    continue

                if linea is _CENTINELA:
                    return

                assert isinstance(linea, str)
                try:
                    if self._debe_rotar(len(linea.encode(_ENCODING))):
                        archivo.close()
                        self._rotar()
                        archivo = self._cfg.path.open(
                            "a", encoding=_ENCODING, buffering=1
                        )
                    # buffering=1 entrega la línea al sistema operativo sin fsync.
                    archivo.write(linea)
                    self._limpiar_error()
                except OSError as exc:
                    self._anotar_error(f"No se pudo escribir en {self._cfg.path}: {exc}")
        finally:
            try:
                archivo.close()
            except OSError:
                logger.warning("Almacén: fallo al cerrar %s", self._cfg.path)

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
        """Rota el archivo: el actual pasa a `.1`, y se conservan `max_files` en total."""
        ruta = self._cfg.path
        backups = max(0, self._cfg.max_files - 1)
        if backups == 0:
            ruta.unlink(missing_ok=True)
            return

        # El backup más viejo se descarta para no crecer sin límite.
        ruta.with_name(f"{ruta.name}.{backups}").unlink(missing_ok=True)
        for indice in range(backups - 1, 0, -1):
            origen = ruta.with_name(f"{ruta.name}.{indice}")
            if origen.exists():
                origen.replace(ruta.with_name(f"{ruta.name}.{indice + 1}"))
        if ruta.exists():
            ruta.replace(ruta.with_name(f"{ruta.name}.1"))

    def _despertar(self) -> None:
        """Mete el centinela para que el escritor salga aunque la cola esté llena."""
        for _ in range(2):
            try:
                self._cola.put_nowait(_CENTINELA)
                return
            except queue.Full:
                try:
                    self._cola.get_nowait()
                except queue.Empty:
                    return

    def _anotar_error(self, texto: str) -> None:
        with self._lock:
            self._error = texto

    def _limpiar_error(self) -> None:
        with self._lock:
            self._error = ""
