"""Tests de carga de secretos desde ``.env`` y entorno."""

from __future__ import annotations

from device_enrollment import DEFAULT_IDENTITY_DIR

from smartcheck_config.secrets import Secrets, load_secrets


def test_defaults(monkeypatch, tmp_path):
    monkeypatch.setenv("SMARTCHECK_ENV_FILE", str(tmp_path / "sin.env"))
    secrets = load_secrets()
    assert secrets == Secrets(
        enrollment_code="", identity_dir=DEFAULT_IDENTITY_DIR
    )


def test_reads_from_env_file(monkeypatch, tmp_path):
    env_file = tmp_path / "cfg.env"
    env_file.write_text(
        "DEVICE_ENROLLMENT_CODE=abc123\n"
        "DEVICE_IDENTITY_DIR=/tmp/identidad\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("DEVICE_ENROLLMENT_CODE", raising=False)
    monkeypatch.delenv("DEVICE_IDENTITY_DIR", raising=False)
    secrets = load_secrets(env_file=env_file)
    assert secrets.enrollment_code == "abc123"
    assert secrets.identity_dir == "/tmp/identidad"


def test_injected_env_wins_over_file(monkeypatch, tmp_path):
    env_file = tmp_path / "cfg.env"
    env_file.write_text(
        "DEVICE_ENROLLMENT_CODE=archivo\n"
        "DEVICE_IDENTITY_DIR=/archivo\n",
        encoding="utf-8",
    )
    secrets = load_secrets(
        env_file=env_file,
        env={
            "DEVICE_ENROLLMENT_CODE": "entorno",
            "DEVICE_IDENTITY_DIR": "/entorno",
        },
    )
    assert secrets.enrollment_code == "entorno"
    assert secrets.identity_dir == "/entorno"


def test_empty_identity_dir_falls_back(monkeypatch, tmp_path):
    env_file = tmp_path / "cfg.env"
    env_file.write_text("DEVICE_IDENTITY_DIR=\n", encoding="utf-8")
    secrets = load_secrets(env_file=env_file, env={})
    assert secrets.identity_dir == DEFAULT_IDENTITY_DIR


def test_missing_env_file_is_ignored(monkeypatch, tmp_path):
    secrets = load_secrets(
        env_file=tmp_path / "no-existe.env",
        env={"DEVICE_ENROLLMENT_CODE": "solo-entorno"},
    )
    assert secrets.enrollment_code == "solo-entorno"
    assert secrets.identity_dir == DEFAULT_IDENTITY_DIR
