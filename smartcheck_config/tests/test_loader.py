"""Tests de carga y guardado de ``config.json``."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from smartcheck_config.loader import (
    ConfigError,
    RevisionConflictError,
    load_config,
    save_config,
)
from smartcheck_config.schema import AppConfig, DeviceSettings


def _config_path(tmp_path):
    return tmp_path / "config.json"


def test_load_config_missing_returns_defaults(tmp_path):
    path = _config_path(tmp_path)
    loaded = load_config(path)
    assert loaded.path == path
    # Sin archivo la revisión base es 0 (consistente con ``_read_revision``).
    assert loaded.config == replace(AppConfig(), revision=0)
    assert loaded.config.revision == 0


def test_save_config_tras_archivo_ausente(tmp_path):
    """La revisión cargada de un archivo ausente se puede usar como expected."""
    path = _config_path(tmp_path)
    loaded = load_config(path)
    saved = save_config(
        AppConfig(),
        config_path=path,
        expected_revision=loaded.config.revision,
    )
    assert saved.revision == 1
    assert load_config(path).config.revision == 1


def test_load_config_strict_missing_raises(tmp_path):
    with pytest.raises(ConfigError):
        load_config(_config_path(tmp_path), strict=True)


def test_load_config_invalid_json_raises(tmp_path):
    path = _config_path(tmp_path)
    path.write_text("{ no es json", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(path)


def test_load_config_reads_values(tmp_path):
    path = _config_path(tmp_path)
    path.write_text(
        json.dumps({"device": {"horno_id": "h1"}}), encoding="utf-8"
    )
    loaded = load_config(path)
    assert loaded.config.device.horno_id == "h1"
    assert loaded.path == path


def test_save_config_creates_file_with_indent_and_newline(tmp_path):
    path = _config_path(tmp_path)
    saved = save_config(AppConfig(), config_path=path)
    assert saved.revision == 1
    raw = path.read_text(encoding="utf-8")
    assert raw.endswith("\n")
    assert json.loads(raw)["revision"] == 1
    assert "\n  " in raw  # indent=2


def test_save_config_increments_revision(tmp_path):
    path = _config_path(tmp_path)
    first = save_config(AppConfig(), config_path=path)
    second = save_config(AppConfig(), config_path=path)
    third = save_config(AppConfig(device=DeviceSettings(horno_id="h")), config_path=path)
    assert (first.revision, second.revision, third.revision) == (1, 2, 3)
    assert load_config(path).config.revision == 3
    assert load_config(path).config.device.horno_id == "h"


def test_save_config_accepts_dict(tmp_path):
    path = _config_path(tmp_path)
    saved = save_config({"api": {"port": 9000}}, config_path=path)
    assert saved.revision == 1
    assert load_config(path).config.api.port == 9000


def test_save_config_revision_conflict(tmp_path):
    path = _config_path(tmp_path)
    save_config(AppConfig(), config_path=path)  # revision 1
    save_config(AppConfig(), config_path=path)  # revision 2
    with pytest.raises(RevisionConflictError):
        save_config(AppConfig(), config_path=path, expected_revision=1)


def test_save_config_expected_revision_matches(tmp_path):
    path = _config_path(tmp_path)
    first = save_config(AppConfig(), config_path=path)
    second = save_config(
        AppConfig(), config_path=path, expected_revision=first.revision
    )
    assert second.revision == 2


def test_save_config_does_not_touch_other_config(tmp_path):
    real = tmp_path / "real" / "config.json"
    real.parent.mkdir()
    save_config(AppConfig(), config_path=real)
    assert real.exists()
    assert not (tmp_path / "otro").exists()
