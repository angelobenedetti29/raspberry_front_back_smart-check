"""Parte común de los detectores YOLO que corren sobre este pipeline.

Los dos adaptadores (`yolo_opencv` y `yolo_hailo`) comparten la lectura del
archivo `.names`, la resolución de etiquetas y la de umbrales por clase. Tenerlo
en un solo lugar evita que un motor y otro se desincronicen en el borde del
umbral o en cómo rotulan una clase que el `.names` no cubre.

No importa `cv2` ni `numpy`: los adaptadores los traen por su cuenta.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from backend.inference.detector import ErrorCargaModelo


def leer_nombres(path: Path) -> tuple[str, ...]:
    """Lee un archivo `.names`: una clase por línea, ignorando líneas vacías."""
    contenido = path.read_text(encoding="utf-8")
    return tuple(linea.strip() for linea in contenido.splitlines() if linea.strip())


class DetectorYoloBase:
    """Estado y decisiones comunes a los detectores YOLO.

    Deja resueltos los atributos del puerto `Detector` (`nombres`, `modelo_path`)
    y precomputa el umbral efectivo de cada clase del `.names`. Las subclases
    sólo aportan la inferencia cruda y su decodificación.
    """

    def __init__(
        self,
        modelo_path: str | Path,
        nombres_path: str | Path,
        *,
        image_size: int,
        confidence_threshold: float,
        class_thresholds: Mapping[str, float],
    ) -> None:
        modelo = Path(modelo_path)
        nombres = Path(nombres_path)

        if not modelo.is_file():
            raise ErrorCargaModelo(f"No se encontró el modelo: {modelo}")
        if not nombres.is_file():
            # No se cae a nombres por defecto a propósito: rotular todas las
            # clases con nombres ajenos al modelo es peor que no arrancar.
            raise ErrorCargaModelo(f"No se encontró el archivo de nombres de clase: {nombres}")

        etiquetas = leer_nombres(nombres)
        if not etiquetas:
            raise ErrorCargaModelo(f"El archivo de nombres está vacío: {nombres}")

        self.modelo_path = modelo
        self.nombres = etiquetas
        self.image_size = int(image_size)
        self.confidence_threshold = float(confidence_threshold)
        self.class_thresholds: Mapping[str, float] = dict(class_thresholds)
        # Umbral efectivo por índice de clase: lo declarado para la clase o, si
        # no figura, el global. Se precomputa una vez y no por detección.
        self._umbrales_por_clase: tuple[float, ...] = tuple(
            self.class_thresholds.get(nombre.lower(), self.confidence_threshold)
            for nombre in etiquetas
        )

    def _etiqueta(self, clase_id: int) -> str:
        """Nombre de la clase, con reserva explícita si el `.names` no la cubre."""
        if 0 <= clase_id < len(self.nombres):
            return self.nombres[clase_id]
        return f"class_{clase_id}"

    def _umbral_minimo(self) -> float:
        """Menor umbral efectivo del modelo.

        Se considera también el global porque las clases que no declaran umbral
        lo usan, y puede ser más bajo que todos los declarados. El pre-filtro de
        NMS debe usar este valor: uno más alto descartaría cajas válidas antes
        de que el NMS pueda decidir.
        """
        return min((self.confidence_threshold, *self._umbrales_por_clase))
