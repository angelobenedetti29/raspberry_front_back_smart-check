from datetime import datetime
from typing import Mapping, Sequence

from backend.domain.entities.lote_request import utc_iso
from backend.domain.entities.sensor_readings import SensorReadings

DEFAULT_PRODUCT_ID = "a1b2c3d4-5678-90ab-cdef-1234567890ab"
TOAST_WEIGHT_KG = 0.025


def _average(values: Sequence[float], fallback: float) -> float:
    if not values:
        return fallback
    return round(sum(values) / len(values), 2)


def build_lote_payload(
    inicio_at: datetime,
    fin_at: datetime,
    seen_toasts: Mapping[int, str],
    sensor_samples: Sequence[SensorReadings],
    producto_id: str = DEFAULT_PRODUCT_ID,
) -> dict:
    quemados_count = 0
    correctos_count = 0
    for state in seen_toasts.values():
        if state == "burnt":
            quemados_count += 1
        else:
            correctos_count += 1

    crudas_count = 0

    total_unidades = correctos_count + quemados_count + crudas_count

    weight_per_toast = TOAST_WEIGHT_KG
    correctos_kg = round(correctos_count * weight_per_toast, 2)
    quemados_kg = round(quemados_count * weight_per_toast, 2)
    crudos_kg = round(crudas_count * weight_per_toast, 2)

    temp_h1 = _average([s.tempHorno1 for s in sensor_samples], 220.0)
    temp_c1 = _average([s.tempCombHorno1 for s in sensor_samples], 315.0)
    temp_h2 = _average([s.tempHorno2 for s in sensor_samples], 218.0)
    temp_c2 = _average([s.tempCombHorno2 for s in sensor_samples], 312.0)
    vel_cinta = _average([s.velocidadCinta for s in sensor_samples], 1.10)

    hour = inicio_at.hour
    if 6 <= hour < 14:
        turno = "mañana"
    elif 14 <= hour < 19:
        turno = "tarde"
    else:
        turno = "noche"

    return {
        "productoId": producto_id,
        "turno": turno,
        "inicioAt": utc_iso(inicio_at),
        "finAt": utc_iso(fin_at),
        "totalUnidades": total_unidades,
        "correctos": correctos_count,
        "quemados": quemados_count,
        "crudas": crudas_count,
        "correctosKg": correctos_kg,
        "quemadosKg": quemados_kg,
        "crudosKg": crudos_kg,
        "tempHorno1": temp_h1,
        "tempCombHorno1": temp_c1,
        "tempHorno2": temp_h2,
        "tempCombHorno2": temp_c2,
        "velocidadCinta": vel_cinta,
    }
