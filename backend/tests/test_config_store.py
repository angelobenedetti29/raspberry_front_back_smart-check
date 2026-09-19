"""Tests de ``ConfigStore``: recarga por mtime, último valor bueno e inmutabilidad.

No se usa ``sleep``: los stamps de ``os.utime`` se fijan de forma determinista.
"""

from __future__ import annotations

import dataclasses
import json
import os

import pytest

from smartcheck_config import ConfigError

from backend.app import config_store as config_store_module
from backend.app.config_store import ConfigStore

_BASE_NS = 1_700_000_000_000_000_000
_STEP_NS = 1_000_000_000


def _write_config(path, revision: int, horno: str = "horno-1") -> None:
    path.write_text(
        json.dumps(
            {
                "revision": revision,
                "device": {"horno_id": horno},
            }
        ),
        encoding="utf-8",
    )


def _set_mtime(path, ns: int) -> None:
    os.utime(path, ns=(ns, ns))


def _make_store(tmp_path, path):
    return ConfigStore(config_path=path, env_file=tmp_path / "missing.env")


def test_recarga_por_mtime(tmp_path):
    path = tmp_path / "config.json"
    _write_config(path, 1)
    _set_mtime(path, _BASE_NS)
    store = _make_store(tmp_path, path)

    first = store.get()
    assert first.config_revision == 1
    assert store.changed is True  # primera carga

    # Sin cambios en disco no se relee ni se reporta cambio.
    assert store.get() is first
    assert store.changed is False

    _write_config(path, 2, horno="horno-2")
    _set_mtime(path, _BASE_NS + _STEP_NS)

    second = store.get()
    assert second.config_revision == 2
    assert second.horno_id == "horno-2"
    assert store.changed is True

    # Estable tras la recarga.
    assert store.get() is second
    assert store.changed is False


def test_force_relee_aunque_no_cambie_el_mtime(tmp_path):
    path = tmp_path / "config.json"
    _write_config(path, 1)
    _set_mtime(path, _BASE_NS)
    store = _make_store(tmp_path, path)
    store.get()

    settings = store.refresh(force=True)
    assert settings.config_revision == 1
    assert store.last_ok is True
    assert store.last_error is None


def test_json_invalido_conserva_ultimo_valor_bueno(tmp_path):
    path = tmp_path / "config.json"
    _write_config(path, 1)
    _set_mtime(path, _BASE_NS)
    store = _make_store(tmp_path, path)
    good = store.get()

    path.write_text("{ no es json", encoding="utf-8")
    _set_mtime(path, _BASE_NS + _STEP_NS)

    kept = store.get()
    assert kept == good
    assert kept.config_revision == 1
    assert store.last_ok is False
    assert store.last_error is not None
    # La revisión reportada sigue siendo la última buena.
    assert store.revision == 1

    # Al reparar el archivo se recupera sin intervención.
    _write_config(path, 2)
    _set_mtime(path, _BASE_NS + 2 * _STEP_NS)
    recovered = store.get()
    assert recovered.config_revision == 2
    assert store.last_ok is True
    assert store.last_error is None


def test_schema_invalido_conserva_ultimo_valor_bueno(tmp_path):
    path = tmp_path / "config.json"
    _write_config(path, 1)
    _set_mtime(path, _BASE_NS)
    store = _make_store(tmp_path, path)
    good = store.get()

    # fps fuera de (20, 30) => SchemaError del esquema unificado.
    path.write_text(
        json.dumps({"revision": 2, "stream": {"capture": {"fps": 25}}}),
        encoding="utf-8",
    )
    _set_mtime(path, _BASE_NS + _STEP_NS)

    kept = store.get()
    assert kept == good
    assert kept.config_revision == 1
    assert store.last_ok is False
    assert store.last_error is not None


def test_error_cacheado_no_reintenta_si_el_stamp_no_cambia(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    _write_config(path, 1)
    _set_mtime(path, _BASE_NS)
    store = _make_store(tmp_path, path)
    store.get()

    calls = {"n": 0}
    real_load = config_store_module._load_config

    def counting_load(config_path):
        calls["n"] += 1
        return real_load(config_path)

    monkeypatch.setattr(config_store_module, "_load_config", counting_load)

    path.write_text("{ roto", encoding="utf-8")
    _set_mtime(path, _BASE_NS + _STEP_NS)

    store.get()  # primer intento con el stamp nuevo
    assert calls["n"] == 1
    assert store.last_error is not None

    # Mismo stamp: el error queda cacheado y no se relee ni parsea de nuevo.
    store.get()
    store.get()
    assert calls["n"] == 1

    # Al cambiar el archivo se reintenta y se recupera.
    _write_config(path, 2)
    _set_mtime(path, _BASE_NS + 2 * _STEP_NS)
    recovered = store.get()
    assert calls["n"] == 2
    assert recovered.config_revision == 2
    assert store.last_error is None


def test_error_en_primer_arranque_se_propaga(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("eso no es json", encoding="utf-8")
    _set_mtime(path, _BASE_NS)
    store = _make_store(tmp_path, path)

    with pytest.raises(ConfigError):
        store.get()
    assert store.last_ok is False
    assert store.last_error is not None


def test_snapshots_inmutables(tmp_path):
    path = tmp_path / "config.json"
    _write_config(path, 1)
    _set_mtime(path, _BASE_NS)
    store = _make_store(tmp_path, path)

    first = store.get()
    snapshot = store.snapshot()
    assert snapshot.config.revision == 1

    _write_config(path, 2)
    _set_mtime(path, _BASE_NS + _STEP_NS)
    second = store.get()

    # El snapshot anterior no se muta: se reemplaza por uno nuevo.
    assert first is not second
    assert first.config_revision == 1
    assert second.config_revision == 2
    assert snapshot.config.revision == 1

    with pytest.raises(dataclasses.FrozenInstanceError):
        first.horno_id = "mutado"  # type: ignore[misc]
