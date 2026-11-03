"""Timestamps ISO con zona horaria explícita, compartidos por los servicios."""

from __future__ import annotations

from datetime import datetime, timezone


def ahora_iso_utc() -> str:
    """ISO en UTC (offset +00:00), con milisegundos."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def ahora_iso_local() -> str:
    """ISO en la zona local, con su offset explícito y milisegundos."""
    return datetime.now().astimezone().isoformat(timespec="milliseconds")
