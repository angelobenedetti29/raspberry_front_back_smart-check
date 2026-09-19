"""Tests de resolución de rutas de bootstrap."""

from __future__ import annotations

from pathlib import Path

import pytest

from smartcheck_config.paths import (
    ConfigPathError,
    resolve_config_path,
    resolve_env_file,
    resolve_project_path,
    resolve_project_root,
)


def test_resolve_project_root_finds_pyproject(monkeypatch):
    monkeypatch.delenv("SMARTCHECK_ROOT", raising=False)
    root = resolve_project_root()
    assert (root / "pyproject.toml").is_file()


def test_resolve_project_root_env(monkeypatch, tmp_path):
    monkeypatch.setenv("SMARTCHECK_ROOT", str(tmp_path))
    assert resolve_project_root() == tmp_path.resolve()


def test_resolve_project_root_env_invalid(monkeypatch, tmp_path):
    archivo = tmp_path / "no-dir.txt"
    archivo.write_text("x", encoding="utf-8")
    monkeypatch.setenv("SMARTCHECK_ROOT", str(archivo))
    with pytest.raises(ConfigPathError):
        resolve_project_root()


def test_resolve_config_path_explicit(monkeypatch, tmp_path):
    monkeypatch.setenv("SMARTCHECK_CONFIG", str(tmp_path / "env.json"))
    explicit = tmp_path / "explicit.json"
    assert resolve_config_path(explicit) == explicit.resolve()


def test_resolve_config_path_env(monkeypatch, tmp_path):
    monkeypatch.setenv("SMARTCHECK_CONFIG", str(tmp_path / "env.json"))
    assert resolve_config_path() == (tmp_path / "env.json").resolve()


def test_resolve_config_path_default(monkeypatch):
    monkeypatch.delenv("SMARTCHECK_CONFIG", raising=False)
    monkeypatch.delenv("SMARTCHECK_ROOT", raising=False)
    assert resolve_config_path() == resolve_project_root() / "config.json"


def test_resolve_project_path_relative(monkeypatch, tmp_path):
    monkeypatch.setenv("SMARTCHECK_ROOT", str(tmp_path))
    assert resolve_project_path("multimedia/videos") == str(
        tmp_path / "multimedia" / "videos"
    )


def test_resolve_project_path_empty_and_absolute(tmp_path):
    assert resolve_project_path("") == ""
    absolute = str(tmp_path / "abs.onnx")
    assert resolve_project_path(absolute) == absolute


def test_resolve_env_file_explicit_wins_even_if_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("SMARTCHECK_ENV_FILE", str(tmp_path / "env-var"))
    explicit = tmp_path / "explicit.env"
    assert resolve_env_file(explicit) == explicit.resolve()
    assert not explicit.exists()


def test_resolve_env_file_env_var(monkeypatch, tmp_path):
    monkeypatch.setenv("SMARTCHECK_ENV_FILE", str(tmp_path / "desde-env"))
    assert resolve_env_file() == (tmp_path / "desde-env").resolve()


def test_resolve_env_file_missing_returns_none(monkeypatch, tmp_path):
    monkeypatch.delenv("SMARTCHECK_ENV_FILE", raising=False)
    monkeypatch.setenv("SMARTCHECK_ROOT", str(tmp_path))
    assert resolve_env_file() is None


def test_resolve_env_file_default_exists(monkeypatch, tmp_path):
    monkeypatch.delenv("SMARTCHECK_ENV_FILE", raising=False)
    monkeypatch.setenv("SMARTCHECK_ROOT", str(tmp_path))
    env_file = tmp_path / ".env"
    env_file.write_text("DEVICE_ENROLLMENT_CODE=x\n", encoding="utf-8")
    assert resolve_env_file() == Path(env_file)
