"""Tests for DeviceMetrics.to_ping_payload (camelCase central contract)."""

from backend.domain.entities.device_metrics import DeviceMetrics


def test_minimal_payload_only_required_fields():
    metrics = DeviceMetrics(cpu_pct=12.5, mem_ram_disponible_mb=512.0)

    payload = metrics.to_ping_payload("dev-1")

    assert payload == {
        "dispositivoId": "dev-1",
        "cpuPct": 12.5,
        "memRamDisponibleMb": 512.0,
        "tempChip": 0.0,
        "aiProcessorPct": 0.0,
    }
    assert "memRamTotalMb" not in payload
    assert "almacenamientoDisponibleMb" not in payload
    assert "almacenamientoTotalMb" not in payload


def test_full_payload_includes_optional_fields():
    metrics = DeviceMetrics(
        cpu_pct=50.0,
        mem_ram_disponible_mb=1024.0,
        mem_ram_total_mb=4096.0,
        almacenamiento_disponible_mb=8192.0,
        almacenamiento_total_mb=32768.0,
        temp_chip=45.5,
        ai_processor_pct=32.0,
    )

    payload = metrics.to_ping_payload("dev-2")

    assert payload == {
        "dispositivoId": "dev-2",
        "cpuPct": 50.0,
        "memRamDisponibleMb": 1024.0,
        "memRamTotalMb": 4096.0,
        "almacenamientoDisponibleMb": 8192.0,
        "almacenamientoTotalMb": 32768.0,
        "tempChip": 45.5,
        "aiProcessorPct": 32.0,
    }


def test_storage_pair_omitted_when_only_one_present():
    only_available = DeviceMetrics(
        cpu_pct=1.0,
        mem_ram_disponible_mb=1.0,
        almacenamiento_disponible_mb=10.0,
    )
    only_total = DeviceMetrics(
        cpu_pct=1.0,
        mem_ram_disponible_mb=1.0,
        almacenamiento_total_mb=20.0,
    )

    assert "almacenamientoDisponibleMb" not in only_available.to_ping_payload("d")
    assert "almacenamientoTotalMb" not in only_available.to_ping_payload("d")
    assert "almacenamientoDisponibleMb" not in only_total.to_ping_payload("d")
    assert "almacenamientoTotalMb" not in only_total.to_ping_payload("d")


def test_floats_are_rounded_to_two_decimals():
    metrics = DeviceMetrics(
        cpu_pct=12.3456,
        mem_ram_disponible_mb=1.005,
        mem_ram_total_mb=2.999,
        almacenamiento_disponible_mb=3.14159,
        almacenamiento_total_mb=4.4449,
        temp_chip=36.666,
        ai_processor_pct=0.004,
    )

    payload = metrics.to_ping_payload("dev-3")

    assert payload["cpuPct"] == 12.35
    assert payload["memRamDisponibleMb"] == 1.0
    assert payload["memRamTotalMb"] == 3.0
    assert payload["almacenamientoDisponibleMb"] == 3.14
    assert payload["almacenamientoTotalMb"] == 4.44
    assert payload["tempChip"] == 36.67
    assert payload["aiProcessorPct"] == 0.0
