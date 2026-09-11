import random

from backend.infrastructure.sensors.simulated_sensors import SimulatedSensorProvider


def test_reads_stay_within_documented_ranges():
    provider = SimulatedSensorProvider(random.Random(1))
    for _ in range(2000):
        r = provider.read()
        assert 218.5 <= r.tempHorno1 <= 221.5
        assert 313.0 <= r.tempCombHorno1 <= 317.0
        assert 216.5 <= r.tempHorno2 <= 219.5
        assert 310.0 <= r.tempCombHorno2 <= 314.0
        assert 1.05 <= r.velocidadCinta <= 1.15


def test_injected_seeded_random_is_deterministic():
    first = SimulatedSensorProvider(random.Random(7))
    second = SimulatedSensorProvider(random.Random(7))
    for _ in range(50):
        assert first.read() == second.read()


def test_default_provider_returns_sensor_readings():
    provider = SimulatedSensorProvider()
    reading = provider.read()
    assert isinstance(reading.tempHorno1, float)
    assert isinstance(reading.tempCombHorno1, float)
    assert isinstance(reading.tempHorno2, float)
    assert isinstance(reading.tempCombHorno2, float)
    assert isinstance(reading.velocidadCinta, float)
