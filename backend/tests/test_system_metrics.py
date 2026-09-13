"""Tests for LinuxSystemMetricsProvider and its pure parsing helpers."""

from backend.infrastructure.system.system_metrics import (
    LinuxSystemMetricsProvider,
    _cpu_pct_from_stats,
    _parse_cpu_stat,
    _parse_meminfo,
    _parse_temperature,
    _ram_mb_from_meminfo,
)


def _cpu_stat(user=0, nice=0, system=0, idle=100, iowait=0, irq=0, softirq=0, steal=0):
    return {
        "user": user,
        "nice": nice,
        "system": system,
        "idle": idle,
        "iowait": iowait,
        "irq": irq,
        "softirq": softirq,
        "steal": steal,
    }


def test_parse_cpu_stat_reads_counters():
    parsed = _parse_cpu_stat("cpu  10 20 30 40 50 60 70 80 90 0")
    assert parsed["user"] == 10
    assert parsed["idle"] == 40
    assert parsed["steal"] == 80


def test_parse_cpu_stat_ignores_non_cpu_lines():
    assert _parse_cpu_stat("intr 123") == {}
    assert _parse_cpu_stat("") == {}


def test_cpu_pct_from_stats_computes_delta():
    prev = _cpu_stat(idle=100)
    curr = _cpu_stat(idle=150, system=50)
    # delta total = 100, delta idle = 50 -> 50% de CPU.
    assert _cpu_pct_from_stats(prev, curr) == 50.0


def test_cpu_pct_from_stats_returns_zero_without_delta():
    stats = _cpu_stat(idle=100)
    assert _cpu_pct_from_stats(stats, stats) == 0.0
    assert _cpu_pct_from_stats({}, stats) == 0.0
    assert _cpu_pct_from_stats(stats, {}) == 0.0


def test_cpu_pct_from_stats_is_clamped():
    # idle decrece (dato inválido) no debe superar 0..100.
    prev = _cpu_stat(idle=200)
    curr = _cpu_stat(idle=100)
    assert _cpu_pct_from_stats(prev, curr) == 0.0


def test_parse_meminfo_and_ram_conversion():
    text = "MemTotal:       4096000 kB\nMemAvailable:   1024000 kB\nCached: 1 kB\n"
    info = _parse_meminfo(text)
    assert info["MemTotal"] == 4096000

    available, total = _ram_mb_from_meminfo(info)
    assert available == 1000.0
    assert total == 4000.0


def test_ram_conversion_handles_missing_fields():
    assert _ram_mb_from_meminfo({}) == (None, None)


def test_ram_available_never_exceeds_total():
    info = {"MemTotal": 100.0, "MemAvailable": 999.0}
    available, total = _ram_mb_from_meminfo(info)
    assert available == total


def test_parse_temperature_handles_milli_and_degrees():
    assert _parse_temperature("45000") == 45.0
    assert _parse_temperature("45.5") == 45.5
    assert _parse_temperature("999999") == 120.0
    assert _parse_temperature("-999999") == -40.0
    assert _parse_temperature("no-number") is None


def test_provider_sample_returns_valid_ranges():
    provider = LinuxSystemMetricsProvider()
    metrics = provider.sample()

    assert 0.0 <= metrics.cpu_pct <= 100.0
    assert metrics.mem_ram_disponible_mb >= 0.0
    assert metrics.mem_ram_total_mb is None or metrics.mem_ram_total_mb >= 0.0
    assert -40.0 <= metrics.temp_chip <= 120.0
    assert 0.0 <= metrics.ai_processor_pct <= 100.0

    storage = [
        metrics.almacenamiento_disponible_mb,
        metrics.almacenamiento_total_mb,
    ]
    assert all(value is None for value in storage) or all(
        value is not None for value in storage
    )


def test_provider_never_raises_even_if_readers_fail(monkeypatch):
    provider = LinuxSystemMetricsProvider()

    def boom(*_args, **_kwargs):
        raise RuntimeError("hardware failure")

    monkeypatch.setattr(provider, "_read_ram_mb", boom)
    metrics = provider.sample()

    assert metrics.cpu_pct >= 0.0
    assert metrics.mem_ram_disponible_mb == 0.0


def test_provider_ai_processor_defaults_to_zero():
    provider = LinuxSystemMetricsProvider()
    assert provider._read_ai_processor_pct() == 0.0
