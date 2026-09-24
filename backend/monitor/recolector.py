"""Recolector de métricas de hardware, independiente del tipo de dispositivo.

Usa psutil para CPU, RAM, disco y temperatura. Cada lectura es defensiva: si
una métrica no está disponible en la plataforma (p. ej. temperatura en
Windows), devuelve None en vez de interrumpir la recolección. La utilización
del acelerador de IA se delega a `acelerador.py`.
"""

from __future__ import annotations

import logging
from pathlib import Path

import psutil

from backend.monitor.acelerador import FuenteAceleradorIA
from backend.monitor.metricas import MetricasSistema

logger = logging.getLogger(__name__)

_BYTES_A_MB = 1024 * 1024

# Nombres de sensor típicos de CPU según la plataforma (Linux/FreeBSD).
_SENSORES_CPU = ("coretemp", "cpu_thermal", "cpu-thermal", "k10temp", "acpitz")


class RecolectorSistema:
    """Obtiene una foto de las métricas del dispositivo anfitrión.

    `fuente_ia` es opcional: si es None (no hay acelerador compatible) el
    porcentaje de IA queda en None y el resto se recolecta igual.
    """

    def __init__(
        self,
        fuente_ia: FuenteAceleradorIA | None = None,
        ruta_disco: Path | str = "/",
    ) -> None:
        self._fuente_ia = fuente_ia
        self._ruta_disco = str(ruta_disco)
        # psutil.cpu_percent(interval=None) mide desde la llamada anterior; la
        # primera devuelve 0.0. Esta llamada "ceba" la medición para que el
        # primer ciclo real ya traiga un valor.
        self._cebar_cpu()

    def recolectar(self) -> MetricasSistema:
        """Devuelve la foto actual. Nunca lanza."""
        mem_disp, mem_total = self._leer_ram()
        alm_disp, alm_total = self._leer_disco()
        return MetricasSistema(
            cpu_pct=self._leer_cpu_pct(),
            mem_ram_disponible_mb=mem_disp,
            mem_ram_total_mb=mem_total,
            almacenamiento_disponible_mb=alm_disp,
            almacenamiento_total_mb=alm_total,
            temp_chip=self._leer_temp(),
            ai_processor_pct=self._leer_ia(),
        )

    def _cebar_cpu(self) -> None:
        try:
            psutil.cpu_percent(interval=None)
        except Exception:  # noqa: BLE001
            logger.debug("monitor: no se pudo cebar cpu_percent", exc_info=True)

    def _leer_cpu_pct(self) -> float | None:
        try:
            return round(float(psutil.cpu_percent(interval=None)), 1)
        except Exception:  # noqa: BLE001
            logger.debug("monitor: falló la lectura de CPU", exc_info=True)
            return None

    def _leer_ram(self) -> tuple[float | None, float | None]:
        try:
            vm = psutil.virtual_memory()
            return _a_mb(vm.available), _a_mb(vm.total)
        except Exception:  # noqa: BLE001
            logger.debug("monitor: falló la lectura de RAM", exc_info=True)
            return None, None

    def _leer_disco(self) -> tuple[float | None, float | None]:
        try:
            du = psutil.disk_usage(self._ruta_disco)
            return _a_mb(du.free), _a_mb(du.total)
        except Exception:  # noqa: BLE001
            logger.debug("monitor: falló la lectura de disco", exc_info=True)
            return None, None

    def _leer_temp(self) -> float | None:
        valor = _leer_temp_psutil()
        if valor is not None:
            return valor
        return _leer_temp_sysfs()

    def _leer_ia(self) -> float | None:
        if self._fuente_ia is None:
            return None
        try:
            return self._fuente_ia.porcentaje_utilizacion()
        except Exception:  # noqa: BLE001
            logger.debug("monitor: falló la lectura del acelerador de IA", exc_info=True)
            return None


def _a_mb(cantidad_bytes: int) -> float:
    """Convierte bytes a MB redondeados a 1 decimal."""
    return round(cantidad_bytes / _BYTES_A_MB, 1)


def _leer_temp_psutil() -> float | None:
    """Temperatura vía psutil; sólo existe en Linux/FreeBSD."""
    getter = getattr(psutil, "sensors_temperatures", None)
    if getter is None:
        return None
    try:
        sensores = getter()
    except Exception:  # noqa: BLE001
        return None
    for nombre in _SENSORES_CPU:
        for entrada in sensores.get(nombre) or ():
            if entrada.current is not None:
                return round(float(entrada.current), 1)
    # Sin un nombre conocido, usar el primer sensor con valor.
    for entradas in sensores.values():
        for entrada in entradas:
            if entrada.current is not None:
                return round(float(entrada.current), 1)
    return None


def _leer_temp_sysfs() -> float | None:
    """Fallback Linux: /sys/class/thermal/thermal_zone*/temp (milésimas de °C)."""
    for zona in sorted(Path("/sys/class/thermal").glob("thermal_zone*/temp")):
        try:
            return round(int(zona.read_text(encoding="utf-8").strip()) / 1000, 1)
        except (OSError, ValueError):
            continue
    return None
