from dataclasses import dataclass


@dataclass(frozen=True)
class SensorReadings:
    """Muestra inmutable de los sensores de un horno en un instante.

    Cada registro corresponde a una lectura; el payload de cierre promedia las
    muestras recolectadas durante el lote. Es ``frozen`` para que las muestras
    no muten una vez tomadas.
    """

    tempHorno1: float
    tempCombHorno1: float
    tempHorno2: float
    tempCombHorno2: float
    velocidadCinta: float
