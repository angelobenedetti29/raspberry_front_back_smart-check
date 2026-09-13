"""Env-file code cleanup: surgical removal, preservation and no backups."""

import os
import stat

import pytest

from device_enrollment.envfile import load_env_values, remove_assignments

CODE = "DEVICE_ENROLLMENT_CODE"


def test_load_env_values_ignores_comments_and_blank_lines(tmp_path):
    env_file = tmp_path / "device.env"
    env_file.write_text(
        "# comment\n"
        "\n"
        "DEVICE_API_BASE_URL=https://api.example.test/api/v1\n"
        "export DEVICE_AUTH_AUDIENCE='https://api.example.test/api/v1'\n"
        f"{CODE}=abc123\n",
        encoding="utf-8",
    )

    values = load_env_values(env_file)

    assert values["DEVICE_API_BASE_URL"] == "https://api.example.test/api/v1"
    assert values["DEVICE_AUTH_AUDIENCE"] == "https://api.example.test/api/v1"
    assert values[CODE] == "abc123"


def test_remove_assignments_preserves_unrelated_content(tmp_path):
    env_file = tmp_path / "device.env"
    original = (
        "# device.env\n"
        "DEVICE_API_BASE_URL=https://api.example.test/api/v1\n"
        f"{CODE}=secret-code-value\n"
        "\n"
        "HORNO_ID=horno-1\n"
        f"export {CODE}=second-assignment\n"
        "PING_INTERVAL_SECONDS=10\n"
    )
    env_file.write_text(original, encoding="utf-8")

    removed = remove_assignments(env_file, {CODE})

    assert removed == 2
    content = env_file.read_text(encoding="utf-8")
    assert "secret-code-value" not in content
    assert "second-assignment" not in content
    assert content == (
        "# device.env\n"
        "DEVICE_API_BASE_URL=https://api.example.test/api/v1\n"
        "\n"
        "HORNO_ID=horno-1\n"
        "PING_INTERVAL_SECONDS=10\n"
    )


def test_remove_assignments_does_not_create_backups(tmp_path):
    env_file = tmp_path / "device.env"
    env_file.write_text(f"{CODE}=abc\nHORNO_ID=h\n", encoding="utf-8")

    remove_assignments(env_file, {CODE})

    names = sorted(p.name for p in tmp_path.iterdir())
    assert names == ["device.env"]


def test_remove_assignments_preserves_file_mode(tmp_path):
    env_file = tmp_path / "device.env"
    env_file.write_text(f"{CODE}=abc\n", encoding="utf-8")
    os.chmod(env_file, 0o640)

    remove_assignments(env_file, {CODE})

    assert stat.S_IMODE(os.stat(env_file).st_mode) == 0o640


def test_remove_assignments_returns_zero_when_file_missing(tmp_path):
    assert remove_assignments(tmp_path / "missing.env", {CODE}) == 0


def test_remove_assignments_rejects_symlink(tmp_path):
    target = tmp_path / "real.env"
    target.write_text(f"{CODE}=abc\n", encoding="utf-8")
    link = tmp_path / "link.env"
    link.symlink_to(target)

    with pytest.raises(OSError):
        remove_assignments(link, {CODE})


def test_remove_assignments_only_touches_requested_keys(tmp_path):
    env_file = tmp_path / "device.env"
    env_file.write_text(
        "DEVICE_ENROLLMENT_CODE=abc\n"
        "OTHER_CODE=keep-me\n",
        encoding="utf-8",
    )

    remove_assignments(env_file, {CODE})

    assert env_file.read_text(encoding="utf-8") == "OTHER_CODE=keep-me\n"
