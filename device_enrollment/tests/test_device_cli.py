"""CLI parsing, code handling, secure cleanup and lightweight-import tests."""

import json
import os
import subprocess
import sys
import types
from pathlib import Path

import pytest

from device_enrollment import cli
from device_enrollment.cli import EXIT_CLEANUP_FAILED, EXIT_OK, EXIT_USAGE, build_parser, main
from device_enrollment.client import EnrollmentClient, EnrollOutcome
from device_enrollment.errors import ConfigurationError
from device_enrollment.urls import normalize_api_base_url, validate_secure_base_url

CODE = "DEVICE_ENROLLMENT_CODE"
REPO_ROOT = Path(__file__).resolve().parents[2]


def fake_outcome(status="enrolled"):
    identity = types.SimpleNamespace(
        phase="enrolled",
        dispositivo_id="22222222-2222-2222-2222-222222222222",
        enrollment_id="enrollment-1",
        fingerprint="fingerprint-1",
    )
    return EnrollOutcome(status, identity, {"keyFingerprint": "fingerprint-1"})


class FakeClient:
    instances = []
    next_outcome = None

    def __init__(self, store, api_base_url, audience):
        self.store = store
        self.api_base_url = api_base_url
        self.audience = audience
        self.code = None
        self.recover_called = False
        FakeClient.instances.append(self)

    def enroll(self, code=None, *, code_provider=None):
        if code is None and code_provider is not None:
            code = code_provider()
        self.code = code
        return FakeClient.next_outcome or fake_outcome()

    def recover(self):
        self.recover_called = True
        return FakeClient.next_outcome or fake_outcome()


def _write_config(
    tmp_path,
    monkeypatch,
    *,
    api_base_url="https://api.example.test/api/v1",
    auth_audience="https://api.example.test/api/v1",
):
    """Escribe un ``config.json`` temporal y lo fija vía ``SMARTCHECK_CONFIG``."""
    config_file = tmp_path / "config.json"
    config_file.write_text(
        json.dumps(
            {
                "device": {
                    "api_base_url": api_base_url,
                    "auth_audience": auth_audience,
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("SMARTCHECK_CONFIG", str(config_file))
    return config_file


@pytest.fixture(autouse=True)
def _reset_fakes(monkeypatch):
    FakeClient.instances = []
    FakeClient.next_outcome = None
    monkeypatch.setattr(cli, "EnrollmentClient", FakeClient)
    yield


def test_parser_has_no_plaintext_code_option():
    parser = build_parser()
    option_strings = {
        option
        for action in parser._actions
        for option in action.option_strings
    }
    for subparsers_action in parser._actions:
        choices = getattr(subparsers_action, "choices", None)
        if choices:
            for sub in choices.values():
                option_strings.update(
                    option for action in sub._actions for option in action.option_strings
                )
    assert "--code" not in option_strings

    with pytest.raises(SystemExit):
        parser.parse_args(["enroll", "--code", "nope"])


def test_relative_env_file_is_rejected(tmp_path):
    result = main(
        [
            "enroll",
            "--env-file",
            "relative.env",
            "--api-base-url",
            "https://api.example.test",
            "--audience",
            "https://api.example.test/api/v1",
            "--identity-dir",
            str(tmp_path),
        ]
    )
    assert result == EXIT_USAGE


def test_enroll_normalizes_legacy_base_url_and_persists(tmp_path, monkeypatch):
    monkeypatch.setattr(cli.getpass, "getpass", lambda _prompt: "prompted-code")
    result = main(
        [
            "enroll",
            "--api-base-url",
            "https://api.example.test",
            "--audience",
            "https://api.example.test/api/v1",
            "--identity-dir",
            str(tmp_path),
        ]
    )
    assert result == EXIT_OK
    assert FakeClient.instances[0].api_base_url == "https://api.example.test/api/v1"


def test_enroll_reads_code_from_selected_env_file_and_cleans_it(tmp_path, monkeypatch):
    _write_config(tmp_path, monkeypatch)
    env_file = tmp_path / "device.env"
    env_file.write_text(
        f"{CODE}=from-file-secret\n"
        "HORNO_ID=horno-1\n",
        encoding="utf-8",
    )

    result = main(
        [
            "enroll",
            "--env-file",
            str(env_file),
            "--identity-dir",
            str(tmp_path / "id"),
        ]
    )

    assert result == EXIT_OK
    assert FakeClient.instances[0].code == "from-file-secret"
    content = env_file.read_text(encoding="utf-8")
    assert "from-file-secret" not in content
    assert "HORNO_ID=horno-1" in content
    assert CODE not in content


def test_enroll_uses_no_echo_prompt_when_code_absent(tmp_path, monkeypatch):
    calls = {}

    def fake_getpass(prompt):
        calls["prompt"] = prompt
        return "prompted-code"

    monkeypatch.setattr(cli.getpass, "getpass", fake_getpass)

    result = main(
        [
            "enroll",
            "--api-base-url",
            "https://api.example.test",
            "--audience",
            "https://api.example.test/api/v1",
            "--identity-dir",
            str(tmp_path),
        ]
    )

    assert result == EXIT_OK
    assert FakeClient.instances[0].code == "prompted-code"
    assert CODE in calls["prompt"]


def test_enroll_cleans_process_environment(tmp_path, monkeypatch):
    monkeypatch.setenv(CODE, "process-secret")
    result = main(
        [
            "enroll",
            "--api-base-url",
            "https://api.example.test",
            "--audience",
            "https://api.example.test/api/v1",
            "--identity-dir",
            str(tmp_path),
        ]
    )

    assert result == EXIT_OK
    assert CODE not in os.environ


def test_device_network_config_comes_from_config_json(tmp_path, monkeypatch):
    """La URL base y la audiencia salen de ``config.json``, no del entorno."""
    monkeypatch.delenv("DEVICE_API_BASE_URL", raising=False)
    monkeypatch.delenv("DEVICE_AUTH_AUDIENCE", raising=False)
    monkeypatch.setenv(CODE, "process-code")
    _write_config(
        tmp_path,
        monkeypatch,
        api_base_url="https://cfg.example.test",
        auth_audience="cfg-audience",
    )

    result = main(["enroll", "--identity-dir", str(tmp_path / "id")])

    assert result == EXIT_OK
    assert FakeClient.instances[0].api_base_url == "https://cfg.example.test/api/v1"
    assert FakeClient.instances[0].audience == "cfg-audience"


def test_enroll_flags_override_config_json(tmp_path, monkeypatch):
    """Los flags explícitos siguen teniendo prioridad sobre ``config.json``."""
    monkeypatch.setenv(CODE, "process-code")
    _write_config(
        tmp_path,
        monkeypatch,
        api_base_url="https://cfg.example.test/api/v1",
        auth_audience="cfg-audience",
    )

    result = main(
        [
            "enroll",
            "--api-base-url",
            "https://flag.example.test",
            "--audience",
            "flag-audience",
            "--identity-dir",
            str(tmp_path / "id"),
        ]
    )

    assert result == EXIT_OK
    assert FakeClient.instances[0].api_base_url == "https://flag.example.test/api/v1"
    assert FakeClient.instances[0].audience == "flag-audience"


def test_enroll_secrets_come_from_smartcheck_env_file(tmp_path, monkeypatch):
    """Código e ``identity_dir`` salen del env-file resuelto por ``SMARTCHECK_ENV_FILE``."""
    monkeypatch.delenv(CODE, raising=False)
    monkeypatch.delenv("DEVICE_IDENTITY_DIR", raising=False)
    _write_config(tmp_path, monkeypatch)

    identity_dir = tmp_path / "identidad"
    env_file = tmp_path / "secrets.env"
    env_file.write_text(
        f"{CODE}=file-code\nDEVICE_IDENTITY_DIR={identity_dir}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("SMARTCHECK_ENV_FILE", str(env_file))

    result = main(["enroll"])

    assert result == EXIT_OK
    client = FakeClient.instances[0]
    assert client.code == "file-code"
    assert client.store.identity_dir == identity_dir
    # El código se borra del env-file tras un enrolamiento durable.
    assert CODE not in env_file.read_text(encoding="utf-8")


def test_enroll_cleanup_failure_returns_nonzero_but_keeps_identity(tmp_path, monkeypatch):
    env_file = tmp_path / "device.env"
    env_file.write_text(f"{CODE}=from-file-secret\nHORNO_ID=h\n", encoding="utf-8")

    def boom(path, keys):
        raise OSError("disk on fire")

    monkeypatch.setattr(cli, "remove_assignments", boom)

    result = main(
        [
            "enroll",
            "--env-file",
            str(env_file),
            "--api-base-url",
            "https://api.example.test",
            "--audience",
            "https://api.example.test/api/v1",
            "--identity-dir",
            str(tmp_path / "id"),
        ]
    )

    assert result == EXIT_CLEANUP_FAILED
    # Identity was persisted before cleanup; only the code cleanup failed.
    assert FakeClient.instances[0].code == "from-file-secret"


def test_recover_command_invokes_proof_only_recover(tmp_path):
    result = main(
        [
            "recover",
            "--api-base-url",
            "https://api.example.test",
            "--audience",
            "https://api.example.test/api/v1",
            "--identity-dir",
            str(tmp_path),
        ]
    )
    assert result == EXIT_OK
    assert FakeClient.instances[0].recover_called is True


def test_reset_requires_confirmation(tmp_path, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _prompt: "no")
    result = main(["reset", "--identity-dir", str(tmp_path)])
    assert result == 1


def test_reset_with_yes_removes_identity(tmp_path):
    from device_enrollment.identity import IdentityStore

    store = IdentityStore(tmp_path)
    store.ensure_directory()
    store.initialize_pending("https://api.example.test/api/v1", "aud")
    assert store.private_key_path.exists()

    result = main(["reset", "--yes", "--identity-dir", str(tmp_path)])

    assert result == EXIT_OK
    assert not store.private_key_path.exists()
    assert not store.identity_path.exists()


def test_cli_import_does_not_load_fastapi_gui_or_ai():
    code = (
        "import sys; import device_enrollment.cli; "
        "heavy=[m for m in ('fastapi','starlette','PySide6','cv2','ultralytics','torch') "
        "if m in sys.modules]; "
        "assert not heavy, heavy; "
        "assert 'backend.app' not in sys.modules; "
        "print('ok')"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert "ok" in completed.stdout


# -- API base URL hardening -----------------------------------------------------


def test_normalize_api_base_url_rejects_userinfo_query_and_fragment():
    for value in (
        "https://user:pass@h.example",
        "https://h.example/api/v1?x=1",
        "https://h.example/api/v1#frag",
    ):
        with pytest.raises(ConfigurationError):
            normalize_api_base_url(value)


def test_normalize_api_base_url_suffix_check_is_case_insensitive():
    assert normalize_api_base_url("https://h.example/API/V1") == "https://h.example/API/V1"
    assert normalize_api_base_url("https://h.example") == "https://h.example/api/v1"


def test_validate_secure_base_url_enforces_tls_policy():
    assert validate_secure_base_url("https://h.example/api/v1") == (
        "https://h.example/api/v1"
    )
    assert validate_secure_base_url("http://127.0.0.1:8000/api/v1") == (
        "http://127.0.0.1:8000/api/v1"
    )
    with pytest.raises(ConfigurationError):
        validate_secure_base_url("http://central.example.com/api/v1")
    with pytest.raises(ConfigurationError):
        validate_secure_base_url("ftp://h.example")


def test_validate_secure_base_url_rejects_userinfo_query_and_fragment():
    for value in (
        "https://user:pass@h.example/api/v1",
        "https://h.example/api/v1?x=1",
        "https://h.example/api/v1#frag",
    ):
        with pytest.raises(ConfigurationError):
            validate_secure_base_url(value)


def test_direct_enrollment_client_rejects_userinfo_base_url(tmp_path):
    from device_enrollment.identity import IdentityStore

    store = IdentityStore(tmp_path / "identity")
    with pytest.raises(ConfigurationError):
        EnrollmentClient(store, "https://user:pass@h.example/api/v1", "aud")

