#!/usr/bin/env python3
"""Levanta el backend (FastAPI) y el frontend (PySide6) juntos.

Uso:
    python run.py                # frontend con su fuente por defecto
    python run.py --source 0     # los argumentos extra se pasan al frontend

Comportamiento:
  - Si ya hay un backend respondiendo en http://localhost:8000, lo reutiliza.
  - Si no, inicia el backend y lo detiene al cerrar el frontend (o con Ctrl+C).
  - Si se ejecuta con un Python sin las dependencias y existe `.venv/`, se
    re-ejecuta automáticamente con `.venv/bin/python`.
"""

from __future__ import annotations

import importlib.util
import json
import os
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
VENV_PYTHON = ROOT_DIR / ".venv" / "bin" / "python"
HOST = "0.0.0.0"
PORT = 8000
STATUS_URL = f"http://localhost:{PORT}/api/status"

# Módulos mínimos por componente. Deben reflejar los imports reales del stack:
# el backend importa `jwt` (PyJWT) vía device_enrollment, no solo FastAPI.
REQUIRED = (
    ("backend", ("fastapi", "uvicorn", "jwt"), "backend/requirements.txt"),
    ("frontend", ("PySide6", "cv2"), "requirements.txt"),
)


def _has_module(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _missing_modules(modules: tuple[str, ...]) -> list[str]:
    return [module for module in modules if not _has_module(module)]


def _reuse_venv_if_needed() -> None:
    """Re-ejecuta el script con `.venv/bin/python` si el intérprete actual no sirve."""
    if not VENV_PYTHON.exists():
        return
    if Path(sys.executable).resolve() == VENV_PYTHON.resolve():
        return
    if not any(_missing_modules(modules) for _, modules, _ in REQUIRED):
        return
    os.execv(
        str(VENV_PYTHON),
        [str(VENV_PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]],
    )


def _check_dependencies() -> None:
    missing = False
    for name, modules, req_file in REQUIRED:
        absent = _missing_modules(modules)
        if absent:
            missing = True
            print(
                f"[run] ERROR: faltan dependencias del {name} ({', '.join(absent)}).",
                file=sys.stderr,
            )
            print(
                f"[run] Instalá:  {sys.executable} -m pip install -r {req_file}",
                file=sys.stderr,
            )
    if missing:
        raise SystemExit(1)


def backend_is_up() -> bool:
    try:
        with urllib.request.urlopen(STATUS_URL, timeout=1) as response:
            data = json.load(response)
    except Exception:
        return False
    return data.get("status") == "online"


def _start_backend() -> subprocess.Popen:
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "backend.app.main:app",
            "--host",
            HOST,
            "--port",
            str(PORT),
        ],
        cwd=str(ROOT_DIR),
    )


def _wait_for_backend(process: subprocess.Popen, timeout: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return False
        if backend_is_up():
            return True
        time.sleep(0.5)
    return False


def _stop(process: subprocess.Popen | None, name: str) -> None:
    if process is None or process.poll() is not None:
        return
    print(f"\n[run] Deteniendo {name} (PID {process.pid})...", flush=True)
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def _raise_keyboard_interrupt(_signum, _frame) -> None:
    raise KeyboardInterrupt


def main() -> int:
    os.chdir(ROOT_DIR)
    _reuse_venv_if_needed()
    _check_dependencies()

    signal.signal(signal.SIGTERM, _raise_keyboard_interrupt)

    print(f"[run] Intérprete: {sys.executable}", flush=True)

    backend_process: subprocess.Popen | None = None
    if backend_is_up():
        print(f"[run] Ya hay un backend activo en {STATUS_URL}; se reutiliza.", flush=True)
    else:
        print(f"[run] Iniciando backend en http://{HOST}:{PORT} ...", flush=True)
        backend_process = _start_backend()
        print("[run] Esperando al backend", end="", flush=True)
        if _wait_for_backend(backend_process):
            print(" listo.", flush=True)
        else:
            print(" falló (revisá si el puerto está ocupado).", flush=True)

    print("[run] Iniciando frontend ...", flush=True)
    frontend_process = subprocess.Popen(
        [sys.executable, "-m", "frontend.main", *sys.argv[1:]],
        cwd=str(ROOT_DIR),
    )

    try:
        return frontend_process.wait()
    except KeyboardInterrupt:
        frontend_process.terminate()
        return 130
    finally:
        _stop(frontend_process, "frontend")
        if backend_process is not None:
            _stop(backend_process, "backend")


if __name__ == "__main__":
    raise SystemExit(main())
