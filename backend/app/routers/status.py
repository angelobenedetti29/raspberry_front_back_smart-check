from fastapi import APIRouter, Depends

from backend.app.dependencies import get_detector

router = APIRouter()


@router.get("/api/status")
def get_status(detector=Depends(get_detector)):
    return {
        "status": "online",
        "detector": {
            "model_path": detector.model_path,
            "classes": detector.get_class_names(),
        },
    }
