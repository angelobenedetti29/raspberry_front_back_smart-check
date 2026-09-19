#!/usr/bin/env python3
"""Levanta el backend (FastAPI) y el frontend (PySide6) juntos.

Uso:
    python run.py

Comportamiento:
  - La configuración (host/puerto del backend, fuente de vídeo, etc.) sale de
    ``config.json`` a través de ``smartcheck_config``.
  - Si ya hay un backend respondiendo en el host/puerto configurados, lo reutiliza.
  - Si no, inicia el backend y lo detiene al cerrar el frontend (o con Ctrl+C).
  - Si el frontend termina con ``RESTART_EXIT_CODE`` (75), se relanza.
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

from smartcheck_config import load_config

ROOT_DIR = Path(__file__).resolve().parent
VENV_PYTHON = ROOT_DIR / ".venv" / "bin" / "python"

# Código de salida con el que el frontend pide un reinicio limpio. Debe coincidir
# con la constante compartida de ``frontend.config``; se define localmente para
# no importar el paquete frontend antes de instalar sus dependencias.
RESTART_EXIT_CODE = 75

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


def _local_check_host(host: str) -> str:
    """Devuelve un host local usable para el chequeo de estado.

    Cuando el backend escucha en ``0.0.0.0`` (todas las interfaces) la petición
    local debe apuntar a loopback, no a la dirección comodín.
    """
    if host in ("", "0.0.0.0", "::"):
        return "127.0.0.1"
    return host


def _status_url(host: str, port: int) -> str:
    """Construye la URL del endpoint de estado del backend local."""
    return f"http://{_local_check_host(host)}:{port}/api/status"


def _child_environment() -> dict[str, str]:
    """Entorno para los procesos hijos con el bootstrap de rutas propagado.

    Se fija ``SMARTCHECK_ROOT`` a la raíz del repo y se reenvían
    ``SMARTCHECK_CONFIG``/``SMARTCHECK_ENV_FILE`` si están definidos, de modo
    que backend y frontend carguen exactamente el mismo ``config.json`` y `.env`.
    """
    env = os.environ.copy()
    env["SMARTCHECK_ROOT"] = str(ROOT_DIR)
    for name in ("SMARTCHECK_CONFIG", "SMARTCHECK_ENV_FILE"):
        value = os.environ.get(name)
        if value:
            env[name] = value
    return env


def backend_is_up(status_url: str) -> bool:
    try:
        with urllib.request.urlopen(status_url, timeout=1) as response:
            data = json.load(response)
    except Exception:
        return False
    return data.get("status") == "online"


def _start_backend(
    host: str, port: int, env: dict[str, str]
) -> subprocess.Popen:
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "backend.app.main:app",
            "--host",
            host,
            "--port",
            str(port),
        ],
        cwd=str(ROOT_DIR),
        env=env,
    )


def _wait_for_backend(
    process: subprocess.Popen, status_url: str, timeout: float = 15.0
) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return False
        if backend_is_up(status_url):
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


def _start_frontend(env: dict[str, str]) -> subprocess.Popen:
    """Lanza el frontend; la fuente de vídeo la manda config.json."""
    return subprocess.Popen(
        [sys.executable, "-m", "frontend.main"],
        cwd=str(ROOT_DIR),
        env=env,
    )


def main() -> int:
    os.chdir(ROOT_DIR)
    _reuse_venv_if_needed()
    _check_dependencies()

    signal.signal(signal.SIGTERM, _raise_keyboard_interrupt)

    loaded = load_config()
    host = loaded.config.api.host
    port = loaded.config.api.port
    status_url = _status_url(host, port)
    child_env = _child_environment()

    print(f"[run] Intérprete: {sys.executable}", flush=True)

    backend_process: subprocess.Popen | None = None
    if backend_is_up(status_url):
        print(
            f"[run] Ya hay un backend activo en {status_url}; se reutiliza.",
            flush=True,
        )
    else:
        print(f"[run] Iniciando backend en http://{host}:{port} ...", flush=True)
        backend_process = _start_backend(host, port, child_env)
        print("[run] Esperando al backend", end="", flush=True)
        if _wait_for_backend(backend_process, status_url):
            print(" listo.", flush=True)
        else:
            print(" falló (revisá si el puerto está ocupado).", flush=True)

    try:
        # El frontend puede pedir un reinicio limpio saliendo con
        # RESTART_EXIT_CODE; en ese caso se relanza sin tocar el backend.
        while True:
            print("[run] Iniciando frontend ...", flush=True)
            frontend_process = _start_frontend(child_env)
            try:
                code = frontend_process.wait()
            except KeyboardInterrupt:
                _stop(frontend_process, "frontend")
                return 130
            _stop(frontend_process, "frontend")
            if code != RESTART_EXIT_CODE:
                return code
            print(
                f"[run] El frontend pidió reinicio (código {RESTART_EXIT_CODE}); "
                "relanzando ...",
                flush=True,
            )
    finally:
        if backend_process is not None:
            _stop(backend_process, "backend")


if __name__ == "__main__":
    raise SystemExit(main())
