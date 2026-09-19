"""Tests del router de configuración con un runtime aislado por ``tmp_path``."""

from __future__ import annotations

import json
import os

from fastapi.testclient import TestClient

from backend.app.config_store import ConfigStore
from backend.app.dependencies import Runtime, get_runtime
from backend.app.main import app

_BASE_NS = 1_700_000_000_000_000_000
_STEP_NS = 1_000_000_000


def _write_config(path, revision: int, horno: str = "horno-1") -> None:
    path.write_text(
        json.dumps(
            {
                "revision": revision,
                "device": {
                    "api_base_url": "https://central.example.com",
                    "auth_audience": "https://central.example.com/api/v1",
                    "horno_id": horno,
                    "producto_id": "prod-1",
                    "ping_interval_seconds": 10.0,
                },
                "stream": {"inference": {"enabled": True}},
            }
        ),
        encoding="utf-8",
    )


def _set_mtime(path, ns: int) -> None:
    os.utime(path, ns=(ns, ns))


def _make_runtime(tmp_path, path) -> Runtime:
    store = ConfigStore(config_path=path, env_file=tmp_path / "missing.env")
    return Runtime(store)


def test_get_config_devuelve_forma_sin_secretos(tmp_path, monkeypatch):
    monkeypatch.delenv("DEVICE_IDENTITY_DIR", raising=False)
    monkeypatch.delenv("DEVICE_ENROLLMENT_CODE", raising=False)
    path = tmp_path / "config.json"
    _write_config(path, 3)
    _set_mtime(path, _BASE_NS)
    runtime = _make_runtime(tmp_path, path)
    app.dependency_overrides[get_runtime] = lambda: runtime
    try:
        with TestClient(app) as client:
            response = client.get("/api/config")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["revision"] == 3
    assert body["enrolled"] is False
    assert body["last_error"] is None
    assert body["path"] == str(runtime.store.path)

    settings = body["settings"]
    assert set(settings) == {"device", "business", "telemetry", "inference"}
    assert settings["device"]["api_base_url"] == "https://central.example.com/api/v1"
    assert settings["device"]["auth_audience"] == "https://central.example.com/api/v1"
    assert settings["business"] == {"horno_id": "horno-1", "producto_id": "prod-1"}
    assert settings["telemetry"]["enabled"] is False
    assert settings["inference"]["enabled"] is True

    # Nunca se filtran secretos ni la ruta de identidad.
    lowered = response.text.lower()
    assert "identity_dir" not in response.text
    assert "enrollment" not in lowered


def test_post_reload_aplica_archivo_cambiado(tmp_path, monkeypatch):
    monkeypatch.delenv("DEVICE_IDENTITY_DIR", raising=False)
    path = tmp_path / "config.json"
    _write_config(path, 1)
    _set_mtime(path, _BASE_NS)
    runtime = _make_runtime(tmp_path, path)
    app.dependency_overrides[get_runtime] = lambda: runtime
    try:
        with TestClient(app) as client:
            assert client.get("/api/config").json()["revision"] == 1

            _write_config(path, 2, horno="horno-2")
            _set_mtime(path, _BASE_NS + _STEP_NS)

            response = client.post("/api/config/reload")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["changed"] is True
    assert body["revision"] == 2
    assert "horno_id" in body["applied"]
    assert body["requires_restart"] is False
    assert body["error"] is None
    assert runtime.state.settings.horno_id == "horno-2"


def test_post_reload_sin_cambios(tmp_path, monkeypatch):
    monkeypatch.delenv("DEVICE_IDENTITY_DIR", raising=False)
    path = tmp_path / "config.json"
    _write_config(path, 1)
    _set_mtime(path, _BASE_NS)
    runtime = _make_runtime(tmp_path, path)
    app.dependency_overrides[get_runtime] = lambda: runtime
    try:
        with TestClient(app) as client:
            response = client.post("/api/config/reload")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["changed"] is False
    assert body["applied"] == []
    assert body["revision"] == 1


def test_post_reload_con_json_invalido_mantiene_ultimo_bueno(tmp_path, monkeypatch):
    monkeypatch.delenv("DEVICE_IDENTITY_DIR", raising=False)
    path = tmp_path / "config.json"
    _write_config(path, 1)
    _set_mtime(path, _BASE_NS)
    runtime = _make_runtime(tmp_path, path)
    app.dependency_overrides[get_runtime] = lambda: runtime
    try:
        with TestClient(app) as client:
            path.write_text("{ roto", encoding="utf-8")
            _set_mtime(path, _BASE_NS + _STEP_NS)
            response = client.post("/api/config/reload")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["changed"] is False
    assert body["error"]
    # El último valor bueno sigue activo.
    assert body["revision"] == 1
    assert runtime.state.settings.horno_id == "horno-1"
