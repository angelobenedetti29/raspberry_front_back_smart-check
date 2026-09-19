"""Endpoints de configuración: consulta del snapshot y recarga en caliente.

Son defs sincrónicos a propósito (FastAPI los corre en su threadpool) y nunca
exponen identidad, códigos de enrolamiento ni secretos.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request

from backend.app.dependencies import Runtime, get_runtime

logger = logging.getLogger(__name__)

router = APIRouter()


def _settings_view(runtime: Runtime) -> dict:
    state = runtime.state
    settings = state.settings
    return {
        "device": {
            "api_base_url": settings.device_api_base_url,
            "auth_audience": settings.device_auth_audience,
            "ping_interval_seconds": settings.ping_interval_seconds,
        },
        "business": {
            "horno_id": settings.horno_id,
            "producto_id": settings.default_producto_id,
        },
        "telemetry": {
            "enabled": bool(state.telemetry.enabled),
            "interval_seconds": settings.ping_interval_seconds,
        },
        "inference": {
            "enabled": settings.inference_enabled,
            "require_hailo": settings.require_hailo,
            "model_path": settings.inference_model_path,
            "labels_path": settings.inference_labels_path,
            "confidence_threshold": settings.inference_confidence_threshold,
        },
    }


@router.get("/api/config")
def get_config(runtime: Runtime = Depends(get_runtime)) -> dict:
    """Devuelve la configuración efectiva del runtime (sin secretos)."""
    state = runtime.state
    return {
        "revision": state.settings.config_revision,
        "enrolled": state.identity is not None,
        "last_error": runtime.store.last_error,
        "path": str(runtime.store.path),
        "settings": _settings_view(runtime),
    }


@router.post("/api/config/reload")
def reload_config(
    request: Request,
    runtime: Runtime = Depends(get_runtime),
) -> dict:
    """Fuerza la recarga de ``config.json`` y aplica los cambios.

    Devuelve 200 incluso si no hubo cambios o si el JSON es inválido: en ese
    caso ``ok`` es ``False`` y el último valor bueno sigue activo. Si la
    dependencia de app ``refresh_runtime`` ya aplicó el cambio en esta petición,
    se reporta igualmente ``changed: True``.
    """
    try:
        changed, applied = runtime.reload()
    except Exception as exc:  # primer arranque sin valor bueno
        logger.warning("Recarga de configuración falló: %s", exc)
        return {
            "ok": False,
            "revision": runtime.store.revision,
            "changed": False,
            "applied": [],
            "requires_restart": False,
            "error": str(exc),
        }

    if not changed:
        changed = bool(getattr(request.state, "config_changed", False))
        applied = list(getattr(request.state, "config_applied", []) or [])

    return {
        "ok": runtime.store.last_ok,
        "revision": runtime.state.settings.config_revision,
        "changed": changed,
        "applied": applied,
        "requires_restart": False,
        "error": runtime.store.last_error,
    }
