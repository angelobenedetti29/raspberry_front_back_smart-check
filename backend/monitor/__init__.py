"""Servicio de monitoreo: recolecta métricas y las envía al backend periódicamente."""

from __future__ import annotations

import logging
import threading
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from backend.config import AppConfig
from backend.device.almacen import AlmacenDispositivo
from backend.monitor.acelerador import FuenteAceleradorIA, detectar_acelerador
from backend.monitor.cliente import ClienteMetricas, ErrorMetricas
from backend.monitor.metricas import MetricasSistema
from backend.monitor.recolector import RecolectorSistema

logger = logging.getLogger(__name__)

# Cuántos envíos recientes se guardan para la vista local.
_HISTORIAL_ENVIOS = 10

# Rechazos de auth (401/403) seguidos necesarios para invalidar la credencial.
# Un 401 aislado puede ser infraestructura (proxy, reinicio del backend); exigir
# varios evita borrar una credencial válida por un hipo transitorio.
_UMBRAL_AUTH = 3


@dataclass(frozen=True, slots=True)
class EnvioMonitor:
    """Resultado de un intento de envío de métricas."""

    momento: datetime
    ok: bool
    error: str | None
    metricas: MetricasSistema


@dataclass(frozen=True, slots=True)
class EstadoMonitor:
    """Foto del monitor para la interfaz (sin tipos del frontend)."""

    registrado: bool
    intervalo_segundos: float
    acelerador: str | None
    recolectadas: MetricasSistema | None
    ultimo_envio: EnvioMonitor | None
    envios_ok: int
    envios_error: int
    recientes: tuple[EnvioMonitor, ...]


class MonitorService:
    """Recolecta métricas del dispositivo cada N segundos y las publica.

    start() no bloquea: lanza un hilo daemon que espera el intervalo, recolecta
    y hace POST al backend. stop() señaliza y espera al hilo.

    El envío sólo ocurre si el dispositivo ya está registrado (hay secret en
    device.json); hasta entonces el ciclo se saltea sin error. Además guarda una
    foto de lo último recolectado/enviado para la sección Métricas de la UI.
    """

    name = "monitor"

    def __init__(
        self,
        config: AppConfig,
        on_credencial_invalida: Callable[[], None] | None = None,
    ) -> None:
        self._intervalo = config.device.ping_interval_seconds
        self._cliente = ClienteMetricas(
            config.api.base_url, config.api.dispositivos_ping_endpoint
        )
        self._on_credencial_invalida = on_credencial_invalida
        self._fuente_ia: FuenteAceleradorIA | None = detectar_acelerador()
        self._recolector = RecolectorSistema(
            fuente_ia=self._fuente_ia,
            ruta_disco=config.paths.root,
        )
        self._almacen = AlmacenDispositivo(Path(config.paths.root) / "device.json")
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

        self._lock = threading.Lock()
        self._registrado = False
        self._recolectadas: MetricasSistema | None = None
        self._ultimo_envio: EnvioMonitor | None = None
        self._recientes: deque[EnvioMonitor] = deque(maxlen=_HISTORIAL_ENVIOS)
        self._envios_ok = 0
        self._envios_error = 0
        self._fallos_auth = 0

    # --- Protocolo Service ---

    def start(self) -> None:
        """Arranca el hilo de monitoreo (idempotente)."""
        if self._thread is not None:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name=self.name, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Señaliza el cese y espera al hilo (bloquea)."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None

    # --- Estado para la UI ---

    def estado(self) -> EstadoMonitor:
        """Foto inmutable de lo último recolectado y enviado."""
        with self._lock:
            return EstadoMonitor(
                registrado=self._registrado,
                intervalo_segundos=self._intervalo,
                acelerador=(
                    self._fuente_ia.nombre if self._fuente_ia is not None else None
                ),
                recolectadas=self._recolectadas,
                ultimo_envio=self._ultimo_envio,
                envios_ok=self._envios_ok,
                envios_error=self._envios_error,
                # El contrato de `recientes` es más nuevo primero: el deque
                # agrega por el final, así que se invierte al exponerlo.
                recientes=tuple(reversed(self._recientes)),
            )

    # --- Hilo de trabajo ---

    def _run(self) -> None:
        # Espera primero el intervalo: así el primer cpuPct promedia una ventana
        # real (el cebado de psutil mide desde la última llamada).
        while not self._stop_event.wait(timeout=self._intervalo):
            try:
                self._ciclo()
            except Exception:  # noqa: BLE001 - el hilo no debe morir
                logger.exception("monitor: error inesperado en el ciclo")

    def _ciclo(self) -> None:
        secret = self._leer_secret()
        metricas = self._recolector.recolectar()
        with self._lock:
            self._registrado = bool(secret)
            self._recolectadas = metricas
        if not secret:
            logger.debug("monitor: dispositivo sin registrar; se omite el envío")
            return
        try:
            self._cliente.enviar(secret, metricas)
        except ErrorMetricas as exc:
            logger.warning("monitor: %s", exc)
            if exc.codigo in (401, 403):
                # Sólo tras varios rechazos seguidos se asume que la credencial
                # fue revocada (ver _UMBRAL_AUTH): así un 401 transitorio de
                # infraestructura no borra device.json ni re-registra de más.
                self._fallos_auth += 1
                if (
                    self._fallos_auth >= _UMBRAL_AUTH
                    and self._on_credencial_invalida is not None
                ):
                    self._on_credencial_invalida()
                    self._fallos_auth = 0
            else:
                self._fallos_auth = 0
            self._registrar_envio(metricas, ok=False, error=str(exc))
            return
        self._fallos_auth = 0
        logger.info("monitor: métricas enviadas (%s)", metricas)
        self._registrar_envio(metricas, ok=True, error=None)

    def _registrar_envio(
        self, metricas: MetricasSistema, *, ok: bool, error: str | None
    ) -> None:
        envio = EnvioMonitor(
            momento=datetime.now(), ok=ok, error=error, metricas=metricas
        )
        with self._lock:
            self._ultimo_envio = envio
            self._recientes.append(envio)
            if ok:
                self._envios_ok += 1
            else:
                self._envios_error += 1

    def _leer_secret(self) -> str | None:
        """Lee el secret persistido por el registro; None si aún no hay alta."""
        estado = self._almacen.cargar()
        if estado is None:
            return None
        return estado.secret
