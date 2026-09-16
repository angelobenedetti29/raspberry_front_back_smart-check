"""Tests for TelemetryLoop (fakes, no network)."""

import threading
import time
from types import SimpleNamespace

from backend.app import telemetry as telemetry_module
from backend.app.telemetry import TelemetryLoop
from backend.domain.entities.device_metrics import DeviceMetrics
from device_enrollment.transport import SendResult


class FakeMetricsProvider:
    def __init__(self, raise_error=False):
        self.raise_error = raise_error
        self.calls = 0

    def sample(self):
        self.calls += 1
        if self.raise_error:
            raise RuntimeError("metrics boom")
        return DeviceMetrics(cpu_pct=10.0, mem_ram_disponible_mb=256.0)


class FakePingUseCase:
    def __init__(self, result=True, raise_error=False):
        self.result = SendResult(ok=bool(result), error=None if result else "last error")
        self.raise_error = raise_error
        self.payloads = []

    def execute(self, payload):
        self.payloads.append(payload)
        if self.raise_error:
            raise RuntimeError("ping boom")
        return self.result


def wait_until(predicate, timeout=2.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def _make_loop(provider=None, ping=None, enabled=True, interval=0.02):
    return TelemetryLoop(
        ping or FakePingUseCase(),
        provider or FakeMetricsProvider(),
        "device-1",
        interval,
        enabled=enabled,
    )


def test_start_sends_at_least_once_then_stop_stops():
    ping = FakePingUseCase()
    loop = _make_loop(ping=ping)

    loop.start()
    assert wait_until(lambda: len(ping.payloads) >= 1)
    assert loop.running is True

    loop.stop()
    assert loop.running is False
    sent = len(ping.payloads)
    time.sleep(0.15)
    assert len(ping.payloads) == sent


def test_payload_uses_dispositivo_id():
    ping = FakePingUseCase()
    loop = _make_loop(ping=ping)

    loop.start()
    assert wait_until(lambda: len(ping.payloads) >= 1)
    loop.stop()

    assert ping.payloads[0]["dispositivoId"] == "device-1"


def test_disabled_loop_does_not_start():
    ping = FakePingUseCase()
    provider = FakeMetricsProvider()
    loop = _make_loop(ping=ping, provider=provider, enabled=False)

    loop.start()

    assert loop.running is False
    assert ping.payloads == []
    assert provider.calls == 0


def test_provider_exception_does_not_break_loop():
    provider = FakeMetricsProvider(raise_error=True)
    loop = _make_loop(provider=provider)

    loop.start()
    assert wait_until(lambda: provider.calls >= 2)
    loop.stop()

    assert loop.running is False


def test_use_case_exception_does_not_break_loop():
    ping = FakePingUseCase(raise_error=True)
    loop = _make_loop(ping=ping)

    loop.start()
    assert wait_until(lambda: len(ping.payloads) >= 2)
    loop.stop()

    assert loop.running is False


def test_start_is_idempotent_under_concurrent_calls(monkeypatch):
    """``start`` debe crear un solo hilo aunque se llame en paralelo."""
    loop = _make_loop()
    created = []

    class FakeThread:
        def __init__(self, target=None, daemon=None):
            self.target = target
            created.append(self)

        def start(self):
            pass

        def is_alive(self):
            return True

    barrier = threading.Barrier(8)

    def worker():
        barrier.wait()
        loop.start()

    # Los hilos trabajadores se crean con el ``threading`` real; el parche solo
    # reemplaza el ``threading`` que ve ``TelemetryLoop.start``.
    workers = [threading.Thread(target=worker) for _ in range(8)]
    monkeypatch.setattr(
        telemetry_module, "threading", SimpleNamespace(Thread=FakeThread)
    )
    for thread in workers:
        thread.start()
    for thread in workers:
        thread.join()

    assert len(created) == 1
