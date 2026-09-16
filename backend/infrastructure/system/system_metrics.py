import logging
import os
import shutil

from backend.domain.entities.device_metrics import DeviceMetrics

logger = logging.getLogger(__name__)

_CPU_FIELDS = ("user", "nice", "system", "idle", "iowait", "irq", "softirq", "steal")
_CPU_STAT_PATH = "/proc/stat"
_MEMINFO_PATH = "/proc/meminfo"
_THERMAL_ZONE_PATH = "/sys/class/thermal/thermal_zone0/temp"
_HWMON_TEMP_PATH = "/sys/class/hwmon/hwmon0/temp1_input"


def _clamp(value: float, low: float, high: float) -> float:
    """Limita ``value`` al intervalo cerrado ``[low, high]``."""
    return max(low, min(high, value))


def _parse_cpu_stat(line: str) -> dict:
    """Convierte la línea ``cpu ...`` de /proc/stat en un dict de contadores.

    La línea tiene la forma ``cpu user nice system idle iowait irq softirq steal ...``.
    Se leen solo los campos de ``_CPU_FIELDS`` (uno por columna tras ``cpu``) y se
    ignoran los restantes. Devuelve ``{}`` si la línea no es de CPU o si algún campo
    no es un entero, para que el llamador lo trate como "sin datos".
    """
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


def _cpu_pct_from_stats(prev: dict, curr: dict) -> float | None:
    """Calcula el % de CPU entre dos muestras, o ``None`` si no hay datos.

    Devuelve ``None`` cuando falta alguna muestra o no hay avance de tiempo de
    CPU (``delta_total <= 0``), para que el llamador pueda distinguir "sin
    datos" de un 0.0 realmente ocioso. Con un delta válido, el porcentaje se
    limita a [0, 100].
    """
    if not prev or not curr:
        return None

    prev_total = sum(prev.get(field, 0) for field in _CPU_FIELDS)
    curr_total = sum(curr.get(field, 0) for field in _CPU_FIELDS)
    delta_total = curr_total - prev_total
    # Los contadores de /proc/stat son monótonos; un delta <= 0 delata una muestra
    # inválida (o un reinicio del contador) y evita además la división por cero.
    if delta_total <= 0:
        return None

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


class LinuxSystemMetricsProvider:
    """Métricas reales de Linux/Raspberry. Nunca lanza: usa fallbacks a 0/None."""

    def __init__(self):
        """Inicializa el proveedor sin muestra previa de CPU.

        ``_last_cpu_stats`` guarda la lectura anterior de /proc/stat para poder
        calcular el porcentaje por diferencia entre dos muestras.
        """
        self._last_cpu_stats: dict | None = None

    def sample(self) -> DeviceMetrics:
        """Toma una muestra completa de CPU, RAM, disco y temperatura.

        Nunca propaga excepciones: ante cualquier error devuelve un
        ``DeviceMetrics`` con ceros. Los campos opcionales (RAM, disco) pueden
        quedar en ``None`` si su fuente no está disponible.
        """
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
        """Lee solo la primera línea ``cpu ...`` de /proc/stat.

        La primera línea contiene los contadores globales agregados de todos los
        núcleos. Devuelve ``{}`` si el archivo no se puede abrir.
        """
        try:
            with open(_CPU_STAT_PATH, "r", encoding="utf-8") as handle:
                first_line = handle.readline()
            return _parse_cpu_stat(first_line)
        except OSError:
            return {}

    def _read_cpu_pct(self) -> float:
        """Calcula el porcentaje de CPU entre la muestra actual y la anterior.

        Actualiza ``_last_cpu_stats`` con la lectura actual. Si no hay contadores
        o no existe una muestra previa, degrada al promedio de carga del sistema.
        """
        try:
            curr = self._read_cpu_stats()
            if not curr:
                return self._load_avg_pct()
            pct = _cpu_pct_from_stats(self._last_cpu_stats or {}, curr)
            self._last_cpu_stats = curr
            if pct is None:
                return self._load_avg_pct()
            return pct
        except Exception:
            return self._load_avg_pct()

    def _load_avg_pct(self) -> float:
        """Estima el uso de CPU como ``load_avg / núcleos``, limitado a [0, 100].

        Es el valor de respaldo cuando no hay deltas válidos de /proc/stat. Usa al
        menos un núcleo para evitar la división por cero.
        """
        try:
            load_1min = os.getloadavg()[0]
            cpus = os.cpu_count() or 1
            return _clamp((load_1min / cpus) * 100.0, 0.0, 100.0)
        except Exception:
            return 0.0

    def _read_ram_mb(self) -> tuple[float | None, float | None]:
        """Devuelve (disponible, total) de RAM en MB leyendo /proc/meminfo."""
        try:
            with open(_MEMINFO_PATH, "r", encoding="utf-8") as handle:
                return _ram_mb_from_meminfo(_parse_meminfo(handle.read()))
        except OSError:
            return None, None

    def _read_disk_mb(self) -> tuple[float | None, float | None]:
        """Devuelve (libre, total) del disco raíz en MB usando ``shutil``."""
        try:
            usage = shutil.disk_usage("/")
            return usage.free / (1024 * 1024), usage.total / (1024 * 1024)
        except OSError:
            return None, None

    def _read_temp_chip(self) -> float:
        """Lee la temperatura del chip, probando la zona térmica y luego hwmon.

        Devuelve 0.0 si ninguna de las rutas devuelve un valor válido.
        """
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
        """Devuelve el uso del acelerador de IA; por ahora siempre 0.0."""
        # TODO(Hailo): integrar el uso real del NPU Hailo cuando esté disponible.
        return 0.0
