from dataclasses import dataclass
from datetime import datetime
from typing import Mapping

from backend.domain.entities.lote_request import utc_iso
from backend.domain.entities.sensor_readings import SensorReadings

# Producto usado cuando no se configura uno real para el lote.
DEFAULT_PRODUCT_ID = "a1b2c3d4-5678-90ab-cdef-1234567890ab"
# Peso de referencia de una tostada, usado para estimar los kg del lote.
TOAST_WEIGHT_KG = 0.025


@dataclass(frozen=True)
class SensorAverages:
    """Promedios del lote completo para cada sensor."""

    tempHorno1: float
    tempCombHorno1: float
    tempHorno2: float
    tempCombHorno2: float
    velocidadCinta: float


class SensorAccumulator:
    """Acumula suma y conteo por campo: memoria constante, promedio del lote completo."""

    def __init__(self) -> None:
        self.reset()

    def add(self, reading: SensorReadings) -> None:
        """Suma una lectura al acumulador sin conservar la muestra."""
        self._temp_horno1 += reading.tempHorno1
        self._temp_comb_horno1 += reading.tempCombHorno1
        self._temp_horno2 += reading.tempHorno2
        self._temp_comb_horno2 += reading.tempCombHorno2
        self._velocidad_cinta += reading.velocidadCinta
        self._count += 1

    @property
    def count(self) -> int:
        """Cantidad de muestras acumuladas."""
        return self._count

    def averages(self) -> SensorAverages:
        """Promedios del lote con los mismos respaldos que la versión con muestras."""
        if self._count == 0:
            return SensorAverages(
                tempHorno1=220.0,
                tempCombHorno1=315.0,
                tempHorno2=218.0,
                tempCombHorno2=312.0,
                velocidadCinta=1.10,
            )
        count = self._count
        return SensorAverages(
            tempHorno1=round(self._temp_horno1 / count, 2),
            tempCombHorno1=round(self._temp_comb_horno1 / count, 2),
            tempHorno2=round(self._temp_horno2 / count, 2),
            tempCombHorno2=round(self._temp_comb_horno2 / count, 2),
            velocidadCinta=round(self._velocidad_cinta / count, 2),
        )

    def reset(self) -> None:
        """Reinicia sumas y conteo para empezar un lote nuevo."""
        self._temp_horno1 = 0.0
        self._temp_comb_horno1 = 0.0
        self._temp_horno2 = 0.0
        self._temp_comb_horno2 = 0.0
        self._velocidad_cinta = 0.0
        self._count = 0


def build_lote_payload(
    inicio_at: datetime,
    fin_at: datetime,
    seen_toasts: Mapping[int, str],
    sensor_averages: SensorAverages,
    producto_id: str = DEFAULT_PRODUCT_ID,
) -> dict:
    """Arma el payload camelCase de cierre de lote para el servidor central.

    Cuenta las tostadas por estado (todo estado distinto de ``"burnt"`` cuenta
    como correcta), estima los kg con ``TOAST_WEIGHT_KG``, toma los promedios de
    sensores ya calculados y deriva el turno desde la hora de ``inicio_at``.
    """
    quemados_count = 0
    correctos_count = 0
    for state in seen_toasts.values():
        if state == "burnt":
            quemados_count += 1
        else:
            correctos_count += 1

    # Las crudas todavía no se detectan: siempre quedan en cero.
    crudas_count = 0

    total_unidades = correctos_count + quemados_count + crudas_count

    weight_per_toast = TOAST_WEIGHT_KG
    correctos_kg = round(correctos_count * weight_per_toast, 2)
    quemados_kg = round(quemados_count * weight_per_toast, 2)
    crudos_kg = round(crudas_count * weight_per_toast, 2)

    # Promedios del lote completo; sus respaldos ya vienen resueltos.
    temp_h1 = sensor_averages.tempHorno1
    temp_c1 = sensor_averages.tempCombHorno1
    temp_h2 = sensor_averages.tempHorno2
    temp_c2 = sensor_averages.tempCombHorno2
    vel_cinta = sensor_averages.velocidadCinta

    # Límites de turno: mañana [6, 14), tarde [14, 19) y noche el resto.
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
