"""Lightweight device-enrollment CLI and shared signed-transport library.

This package deliberately depends only on the standard library, ``requests``,
``cryptography`` and ``PyJWT``. It must never import FastAPI, the AI/YOLO or
PySide6 stack, or hardware modules so the ``python -m device_enrollment`` CLI
stays lightweight and can run independently from the application.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "1.0.0"
