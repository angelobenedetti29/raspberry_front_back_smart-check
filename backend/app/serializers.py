"""Serialización de entidades del dominio para las respuestas de la API.

Vive en la capa API (no en ``domain/``) para que las entidades no conozcan la
forma exacta del JSON que se devuelve por HTTP.
"""


def detection_to_dict(det) -> dict:
    """Convierte una detección en el dict que expone ``/api/detect``.

    Acepta tanto ``DetectionResult`` (sin ``id``/``state``) como ``TrackedToast``
    (que los agrega). Se usa ``hasattr`` porque la capa de detección devuelve
    tostadas trackeadas y los dobles de test pueden ser dataclasses propias.
    """
    item = {
        "label": det.label,
        "confidence": float(det.confidence),
        "bbox": {
            "x": det.bbox[0],
            "y": det.bbox[1],
            "width": det.bbox[2],
            "height": det.bbox[3],
        },
    }

    if hasattr(det, "id"):
        item["id"] = det.id
        item["state"] = det.state

    return item
