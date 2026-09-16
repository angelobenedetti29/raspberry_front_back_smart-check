import logging
import os
import shutil
import time

from backend.domain.entities.device_metrics import DeviceMetrics
from backend.domain.interfaces.system_metrics import ISystemMetricsProvider

logger = logging.getLogger(__name__)

_CPU_FIELDS = ("user", "nice", "system", "idle", "iowait", "irq", "softirq", "steal")
_CPU_STAT_PATH = "/proc/stat"
_MEMINFO_PATH = "/proc/meminfo"
_THERMAL_ZONE_PATH = "/sys/class/thermal/thermal_zone0/temp"
_HWMON_TEMP_PATH = "/sys/class/hwmon/hwmon0/temp1_input"


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _parse_cpu_stat(line: str) -> dict:
    """Convierte la línea ``cpu ...`` de /proc/stat en un dict de contadores."""
    parts = line.split()
    if not parts or parts[0] != "cpu":
        return {}

    result = {}
    for index, field in enumerate(_CPU_FIELDS):
        if index + 1 >= len(parts):
            break
        try:
            result[field] = int(parts[index + 1])
        except ValueError:
            return {}
    return result


def _cpu_pct_from_stats(prev: dict, curr: dict) -> float:
    """Calcula el % de CPU entre dos muestras; 0.0 si no hay delta válido."""
    if not prev or not curr:
        return 0.0

    prev_total = sum(prev.get(field, 0) for field in _CPU_FIELDS)
    curr_total = sum(curr.get(field, 0) for field in _CPU_FIELDS)
    delta_total = curr_total - prev_total
    if delta_total <= 0:
        return 0.0

    prev_idle = prev.get("idle", 0) + prev.get("iowait", 0)
    curr_idle = curr.get("idle", 0) + curr.get("iowait", 0)
    delta_idle = curr_idle - prev_idle
    pct = (1.0 - (delta_idle / delta_total)) * 100.0
    return _clamp(pct, 0.0, 100.0)


def _parse_meminfo(text: str) -> dict:
    """Convierte /proc/meminfo en un dict de valores en kB."""
    result = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, rest = line.split(":", 1)
        parts = rest.split()
        if not parts:
            continue
        try:
            result[key.strip()] = float(parts[0])
        except ValueError:
            continue
    return result


def _ram_mb_from_meminfo(info: dict) -> tuple[float | None, float | None]:
    """Devuelve (disponible, total) en MB a partir de /proc/meminfo."""
    available_kb = info.get("MemAvailable")
    total_kb = info.get("MemTotal")
    if available_kb is None or total_kb is None:
        return None, None

    total_mb = max(0.0, total_kb / 1024.0)
    available_mb = _clamp(available_kb / 1024.0, 0.0, total_mb)
    return available_mb, total_mb


def _parse_temperature(raw: str) -> float | None:
    """Normaliza lecturas en milli-°C o °C y las limita a [-40, 120]."""
    try:
        value = float(raw.strip())
    except (TypeError, ValueError):
        return None

    if abs(value) > 1000:
        value = value / 1000.0
    return _clamp(value, -40.0, 120.0)


class LinuxSystemMetricsProvider(ISystemMetricsProvider):
    """Métricas reales de Linux/Raspberry. Nunca lanza: usa fallbacks a 0/None."""

    def sample(self) -> DeviceMetrics:
        try:
            mem_available, mem_total = self._read_ram_mb()
            disk_available, disk_total = self._read_disk_mb()
            return DeviceMetrics(
                cpu_pct=self._read_cpu_pct(),
                mem_ram_disponible_mb=(
                    mem_available if mem_available is not None else 0.0
                ),
                mem_ram_total_mb=mem_total,
                almacenamiento_disponible_mb=disk_available,
                almacenamiento_total_mb=disk_total,
                temp_chip=self._read_temp_chip(),
                ai_processor_pct=self._read_ai_processor_pct(),
            )
        except Exception as exc:  # pragma: no cover - salvaguarda defensiva
            logger.warning("Error al muestrear métricas: %s", exc)
            return DeviceMetrics(cpu_pct=0.0, mem_ram_disponible_mb=0.0)

    def _read_cpu_stats(self) -> dict:
        try:
            with open(_CPU_STAT_PATH, "r", encoding="utf-8") as handle:
                first_line = handle.readline()
            return _parse_cpu_stat(first_line)
        except OSError:
            return {}

    def _read_cpu_pct(self) -> float:
        try:
            prev = self._read_cpu_stats()
            time.sleep(0.2)
            curr = self._read_cpu_stats()
            pct = _cpu_pct_from_stats(prev, curr)
            if pct > 0.0:
                return pct
            return self._load_avg_pct()
        except Exception:
            return self._load_avg_pct()

    def _load_avg_pct(self) -> float:
        try:
            load_1min = os.getloadavg()[0]
            cpus = os.cpu_count() or 1
            return _clamp((load_1min / cpus) * 100.0, 0.0, 100.0)
        except Exception:
            return 0.0

    def _read_ram_mb(self) -> tuple[float | None, float | None]:
        try:
            with open(_MEMINFO_PATH, "r", encoding="utf-8") as handle:
                return _ram_mb_from_meminfo(_parse_meminfo(handle.read()))
        except OSError:
            return None, None

    def _read_disk_mb(self) -> tuple[float | None, float | None]:
        try:
            usage = shutil.disk_usage("/")
            return usage.free / (1024 * 1024), usage.total / (1024 * 1024)
        except OSError:
            return None, None

    def _read_temp_chip(self) -> float:
        for path in (_THERMAL_ZONE_PATH, _HWMON_TEMP_PATH):
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    parsed = _parse_temperature(handle.read())
                if parsed is not None:
                    return parsed
            except OSError:
                continue
        return 0.0

    def _read_ai_processor_pct(self) -> float:
        # TODO(Hailo): integrar el uso real del NPU Hailo cuando esté disponible.
        return 0.0
