from fastapi import APIRouter, Depends

from backend.app.dependencies import get_detector

router = APIRouter()


@router.get("/api/status")
def get_status(detector=Depends(get_detector)):
    """Estado del servicio.

    Lee metadata del detector sin forzar la carga del modelo: ``loaded`` indica
    si el proxy perezoso ya se inicializó y ``error`` si quedó degradado.
    """
    loaded = bool(getattr(detector, "loaded", True))
    if not loaded:
        return {
            "status": "online",
            "detector": {
                "loaded": False,
                "model_path": None,
                "classes": [],
                "error": "",
            },
        }

    return {
        "status": "online",
        "detector": {
            "loaded": True,
            "model_path": getattr(detector, "model_path", None),
            "classes": list(detector.get_class_names()),
            "error": str(getattr(detector, "error", "") or ""),
        },
    }
