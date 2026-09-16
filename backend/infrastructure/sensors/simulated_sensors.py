import random

from backend.domain.entities.sensor_readings import SensorReadings


class SimulatedSensorProvider:
    """Genera lecturas simuladas con los rangos nominales del horno.

    Es el proveedor de sensores por defecto del worker del frontend cuando no
    hay hardware real conectado.
    """

    def __init__(self, rng: random.Random | None = None):
        """Inicializa el generador aleatorio.

        ``rng`` permite inyectar una semilla para obtener lecturas reproducibles;
        si es ``None`` se usa una instancia nueva de ``random.Random``.
        """
        self._rng = rng if rng is not None else random.Random()

    def read(self) -> SensorReadings:
        """Devuelve una lectura simulada con ruido leve alrededor del valor nominal.

        Las temperaturas oscilan ±1.5 °C (hornos) o ±2.0 °C (combustión) y la
        velocidad de cinta ±0.05, de modo que los valores se mantienen verosímiles.
        """
        return SensorReadings(
            tempHorno1=220.0 + self._rng.uniform(-1.5, 1.5),
            tempCombHorno1=315.0 + self._rng.uniform(-2.0, 2.0),
            tempHorno2=218.0 + self._rng.uniform(-1.5, 1.5),
            tempCombHorno2=312.0 + self._rng.uniform(-2.0, 2.0),
            velocidadCinta=1.10 + self._rng.uniform(-0.05, 0.05),
        )
