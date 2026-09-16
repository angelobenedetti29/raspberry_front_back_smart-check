"""Lightweight device-enrollment CLI and shared signed-transport library.

This package deliberately depends only on the standard library, ``requests``,
``cryptography`` and ``PyJWT``. It must never import FastAPI, the AI/YOLO or
PySide6 stack, or hardware modules so the ``python -m device_enrollment`` CLI
stays lightweight and can run independently from the application.
"""

from __future__ import annotations

__all__ = ["DEFAULT_IDENTITY_DIR", "__version__"]

# Directorio por defecto de la identidad del dispositivo. Se define una sola vez
# aquí para que la CLI (``device_enrollment.cli``) y el backend
# (``backend.app.config``) compartan exactamente el mismo valor.
DEFAULT_IDENTITY_DIR = "/var/lib/smart-check/device"

__version__ = "1.0.0"
