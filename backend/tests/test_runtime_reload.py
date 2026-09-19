"""Regresiones de concurrencia y recarga en caliente del ``Runtime``.

Cubre:
- H1: dos refresh concurrentes no pierden la aplicación del cambio.
- M5: el ``stop()`` de telemetría ocurre fuera del lock principal y no se
  arrancan dos loops.
- L3: un candidato construido y descartado cierra su transporte.
- L4: un ``refresh(force=True)`` adopta una identidad recién enrolada.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import replace
from types import SimpleNamespace

from backend.app import dependencies as dependencies_module
from backend.app.config_store import ConfigStore
from backend.app.dependencies import Runtime

_BASE_NS = 1_700_000_000_000_000_000
_STEP_NS = 1_000_000_000


def _write_config(path, revision: int, horno: str = "horno-1") -> None:
    path.write_text(
        json.dumps({"revision": revision, "device": {"horno_id": horno}}),
        encoding="utf-8",
    )


def _set_mtime(path, ns: int) -> None:
    os.utime(path, ns=(ns, ns))


def _make_store(tmp_path, path) -> ConfigStore:
    return ConfigStore(config_path=path, env_file=tmp_path / "missing.env")


def test_refresh_concurrente_aplica_el_cambio(tmp_path, monkeypatch):
    """H1: el flag de cambio se entrega atómico y nadie pierde el apply."""
    monkeypatch.delenv("DEVICE_IDENTITY_DIR", raising=False)
    path = tmp_path / "config.json"
    _write_config(path, 1)
    _set_mtime(path, _BASE_NS)
    runtime = Runtime(_make_store(tmp_path, path))

    _write_config(path, 2, horno="horno-2")
    _set_mtime(path, _BASE_NS + _STEP_NS)

    barrier = threading.Barrier(4)
    errors: list[Exception] = []

    def worker():
        try:
            barrier.wait()
            runtime.refresh()
        except Exception as exc:  # pragma: no cover - solo diagnóstico
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)

    assert not errors
    assert runtime.state.settings.config_revision == 2
    assert runtime.state.settings.horno_id == "horno-2"
    assert runtime.store.revision == 2


def test_telemetria_stop_fuera_del_lock_y_sin_doble_start(tmp_path, monkeypatch):
    """M5: stop/join fuera del lock principal; nunca dos loops activos."""
    monkeypatch.delenv("DEVICE_IDENTITY_DIR", raising=False)
    path = tmp_path / "config.json"
    _write_config(path, 1)
    _set_mtime(path, _BASE_NS)

    created: list = []

    class FakeTelemetry:
        runtime = None

        def __init__(
            self,
            ping_use_case,
            metrics_provider,
            dispositivo_id,
            interval_seconds,
            enabled=True,
        ):
            self.interval = interval_seconds
            self.enabled = enabled
            self.started = False
            self.stopped = False
            self.lock_free = False
            created.append(self)

        def start(self):
            self.started = True

        def stop(self, timeout=5.0):
            holder = FakeTelemetry.runtime
            acquired = holder._lock.acquire(timeout=0.5)
            if acquired:
                holder._lock.release()
            self.lock_free = acquired
            self.stopped = True

    monkeypatch.setattr(dependencies_module, "TelemetryLoop", FakeTelemetry)
    runtime = Runtime(_make_store(tmp_path, path))
    FakeTelemetry.runtime = runtime
    runtime.start_telemetry()

    first = created[-1]
    assert first.started

    runtime.apply(
        replace(runtime.state.settings, ping_interval_seconds=3.0, config_revision=2)
    )
    second = created[-1]
    assert second is not first
    assert first.stopped and first.lock_free
    assert second.started and not second.stopped

    runtime.apply(
        replace(runtime.state.settings, ping_interval_seconds=4.0, config_revision=3)
    )
    third = created[-1]
    assert third is not second
    assert second.stopped
    # Solo el loop vigente queda arrancado.
    running = [loop for loop in created if loop.started and not loop.stopped]
    assert running == [third]


class _SpyTransport:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def test_apply_cierra_candidato_descartado(tmp_path, monkeypatch):
    """L3: si otro hilo publica primero, el candidato tentativo cierra su sesión."""
    monkeypatch.delenv("DEVICE_IDENTITY_DIR", raising=False)
    path = tmp_path / "config.json"
    _write_config(path, 1)
    _set_mtime(path, _BASE_NS)
    runtime = Runtime(_make_store(tmp_path, path))
    base = runtime.state

    spy = _SpyTransport()
    real_build = runtime._build_candidate
    calls = {"n": 0}

    def wrapper(settings, current_base):
        state, rebuild, restart, detector_changed = real_build(settings, current_base)
        calls["n"] += 1
        if calls["n"] == 1:
            # Simula que otro hilo publica antes de que este apply tome el lock.
            concurrent, *_ = real_build(settings, base)
            runtime._state = concurrent
            return replace(state, transport=spy), rebuild, restart, detector_changed
        return state, rebuild, restart, detector_changed

    monkeypatch.setattr(runtime, "_build_candidate", wrapper)

    runtime.apply(
        replace(base.settings, horno_id="horno-x", config_revision=2)
    )

    assert spy.closed is True


def test_apply_retira_transporte_y_shutdown_lo_cierra(tmp_path, monkeypatch):
    """L3: el transporte reemplazado se retira y ``shutdown`` lo cierra."""
    monkeypatch.delenv("DEVICE_IDENTITY_DIR", raising=False)
    path = tmp_path / "config.json"
    _write_config(path, 1)
    _set_mtime(path, _BASE_NS)
    runtime = Runtime(_make_store(tmp_path, path))
    old_transport = runtime.state.transport

    closed = {"n": 0}
    original_close = old_transport.close

    def counting_close():
        closed["n"] += 1
        original_close()

    old_transport.close = counting_close

    runtime.apply(
        replace(
            runtime.state.settings,
            device_api_base_url="https://b.example/api/v1",
            config_revision=2,
        )
    )

    assert runtime.state.transport is not old_transport
    assert runtime._retired == [old_transport]

    runtime.shutdown()
    assert closed["n"] == 1
    assert runtime._retired == []


def test_force_reload_adopta_identidad_nueva(tmp_path, monkeypatch):
    """L4: POST reload (force) adopta una identidad recién enrolada."""
    monkeypatch.delenv("DEVICE_IDENTITY_DIR", raising=False)
    path = tmp_path / "config.json"
    _write_config(path, 1)
    _set_mtime(path, _BASE_NS)

    class FakeIdentityStore:
        result = None

        def __init__(self, identity_dir):
            self.identity_dir = identity_dir

        def try_load_enrolled(self):
            return FakeIdentityStore.result

    monkeypatch.setattr(dependencies_module, "IdentityStore", FakeIdentityStore)
    runtime = Runtime(_make_store(tmp_path, path))
    assert runtime.state.identity is None

    FakeIdentityStore.result = SimpleNamespace(
        fingerprint="fp-1", dispositivo_id="dev-1"
    )
    changed, applied = runtime.refresh(force=True)
    assert changed is True
    assert "identity" in applied
    assert runtime.state.identity is not None
    assert runtime.state.identity.dispositivo_id == "dev-1"

    # Sin cambios de identidad ni de config, un force no reconstruye.
    FakeIdentityStore.result = SimpleNamespace(
        fingerprint="fp-1", dispositivo_id="dev-1"
    )
    changed_again, applied_again = runtime.refresh(force=True)
    assert changed_again is False
    assert applied_again == []
