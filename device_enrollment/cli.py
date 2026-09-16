"""``python -m device_enrollment enroll|recover`` command line entry point."""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from . import DEFAULT_IDENTITY_DIR
from .client import EnrollmentClient, EnrollOutcome
from .envfile import load_env_values, remove_assignments
from .errors import (
    CodeRequiredError,
    ConfigurationError,
    CredentialRevokedError,
    DeviceEnrollmentError,
    NewInvitationRequiredError,
    NotEnrolledError,
)
from .identity import IdentityStore
from .urls import normalize_api_base_url

CODE_ENV_VAR = "DEVICE_ENROLLMENT_CODE"

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_USAGE = 2
EXIT_CLEANUP_FAILED = 3
EXIT_REVOKED = 4
EXIT_NEW_INVITATION = 5
EXIT_NOT_ENROLLED = 6
EXIT_CODE_REQUIRED = 7


@dataclass(frozen=True)
class ResolvedConfig:
    api_base_url: str
    audience: str
    identity_dir: Path
    code: str | None
    env_file: Path | None


def _common_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(
        "--env-file",
        type=Path,
        default=None,
        help="absolute path to the env file to read (and clean on success)",
    )
    parser.add_argument("--api-base-url", default=None, help="device API base URL ending in /api/v1")
    parser.add_argument("--audience", default=None, help="device proof audience (DEVICE_AUTH_AUDIENCE)")
    parser.add_argument("--identity-dir", type=Path, default=None, help="identity storage directory")
    return parser


def build_parser() -> argparse.ArgumentParser:
    common = _common_parser()
    parser = argparse.ArgumentParser(
        prog="python -m device_enrollment",
        description="Smart-Check Raspberry device enrollment CLI.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser(
        "enroll",
        parents=[common],
        help="enroll (or resume) using DEVICE_ENROLLMENT_CODE from env/prompt",
    )
    subparsers.add_parser(
        "recover",
        parents=[common],
        help="proof-only recovery of an existing identity",
    )
    reset = subparsers.add_parser(
        "reset",
        parents=[common],
        help="deliberately discard the local identity (needed after revocation)",
    )
    reset.add_argument("--yes", action="store_true", help="skip the interactive confirmation")
    return parser


def _resolve(args: argparse.Namespace) -> ResolvedConfig:
    if args.env_file is not None and not args.env_file.is_absolute():
        raise ConfigurationError("--env-file must be an absolute path")

    values = load_env_values(args.env_file) if args.env_file is not None else {}
    api_base_url = (
        args.api_base_url
        or values.get("DEVICE_API_BASE_URL")
        or values.get("CENTRAL_BASE_URL")
        or values.get("CENTRAL_LOTE_BASE_URL")
        or os.environ.get("DEVICE_API_BASE_URL")
        or os.environ.get("CENTRAL_BASE_URL")
        or ""
    )
    audience = (
        args.audience
        or values.get("DEVICE_AUTH_AUDIENCE")
        or os.environ.get("DEVICE_AUTH_AUDIENCE")
        or ""
    )
    identity_dir = (
        args.identity_dir
        or values.get("DEVICE_IDENTITY_DIR")
        or os.environ.get("DEVICE_IDENTITY_DIR")
        or DEFAULT_IDENTITY_DIR
    )
    code = values.get(CODE_ENV_VAR) or os.environ.get(CODE_ENV_VAR)
    return ResolvedConfig(
        api_base_url=api_base_url,
        audience=audience,
        identity_dir=Path(identity_dir),
        code=code,
        env_file=args.env_file,
    )


def _require_network_config(config: ResolvedConfig) -> tuple[str, str]:
    api_base_url = normalize_api_base_url(config.api_base_url)
    if not config.audience:
        raise ConfigurationError("DEVICE_AUTH_AUDIENCE (--audience) is not configured")
    return api_base_url, config.audience


def _print_outcome(outcome: EnrollOutcome) -> None:
    identity = outcome.identity
    print(
        f"phase={identity.phase} "
        f"dispositivoId={identity.dispositivo_id} "
        f"enrollmentId={identity.enrollment_id} "
        f"keyFingerprint={identity.fingerprint} "
        f"status={outcome.status}"
    )


def _cleanup_code(env_file: Path | None) -> bool:
    """Remove the code from process env and the selected env-file only."""
    os.environ.pop(CODE_ENV_VAR, None)
    if env_file is None:
        return True
    try:
        remove_assignments(env_file, {CODE_ENV_VAR})
    except OSError:
        return False
    return True


def _cmd_enroll(args: argparse.Namespace) -> int:
    config = _resolve(args)
    api_base_url, audience = _require_network_config(config)
    store = IdentityStore(config.identity_dir)
    client = EnrollmentClient(store, api_base_url, audience)

    def code_provider() -> str:
        return getpass.getpass(f"{CODE_ENV_VAR}: ").strip()

    outcome = client.enroll(code=config.code, code_provider=code_provider)
    _print_outcome(outcome)
    if outcome.status == "enrolled" and not _cleanup_code(config.env_file):
        print(
            "WARNING: enrollment succeeded and the identity is stored, but the "
            f"{CODE_ENV_VAR} assignment could not be removed from the selected env "
            "file. Remove it manually.",
            file=sys.stderr,
        )
        return EXIT_CLEANUP_FAILED
    return EXIT_OK


def _cmd_recover(args: argparse.Namespace) -> int:
    config = _resolve(args)
    api_base_url, audience = _require_network_config(config)
    store = IdentityStore(config.identity_dir)
    client = EnrollmentClient(store, api_base_url, audience)
    outcome = client.recover()
    _print_outcome(outcome)
    return EXIT_OK


def _cmd_reset(args: argparse.Namespace) -> int:
    config = _resolve(args)
    store = IdentityStore(config.identity_dir)
    if not args.yes:
        answer = input(
            "This permanently deletes the local device identity in "
            f"{config.identity_dir}. Type 'reset' to confirm: "
        )
        if answer.strip() != "reset":
            print("aborted", file=sys.stderr)
            return EXIT_FAILURE
    removed = store.reset()
    print(
        f"removed {len(removed)} identity file(s). Run 'enroll' with a NEW "
        "invitation; a fresh key will be generated."
    )
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "enroll":
            return _cmd_enroll(args)
        if args.command == "recover":
            return _cmd_recover(args)
        if args.command == "reset":
            return _cmd_reset(args)
        parser.error(f"unknown command: {args.command}")
        return EXIT_USAGE
    except CredentialRevokedError as exc:
        print(
            f"ERROR: {exc}. This credential is permanently revoked; run "
            "'reset' and enroll with a replacement invitation and a fresh key.",
            file=sys.stderr,
        )
        return EXIT_REVOKED
    except NewInvitationRequiredError as exc:
        print(
            f"ERROR: {exc}. Ask an operator for a new invitation.",
            file=sys.stderr,
        )
        return EXIT_NEW_INVITATION
    except NotEnrolledError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_NOT_ENROLLED
    except CodeRequiredError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_CODE_REQUIRED
    except (ConfigurationError, DeviceEnrollmentError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_USAGE


__all__ = ["build_parser", "main"]
