"""Detector YOLO sobre un HEF de Hailo, para la NPU de la Raspberry.

Sólo funciona donde exista `hailo_platform`, así que el import es perezoso y este
módulo se carga recién cuando el motor resuelto es NPU.

La normalización (1/255) viene **compilada dentro del HEF** (ver el `.alls` que
genera `backend/ai_training/compilar_hailo.py`): por eso acá no se divide la
entrada por 255. Dividirla —como hacen muchos ejemplos de la comunidad que
usan `FormatType.FLOAT32`— dejaría un modelo que no detecta nada.

A partir del flujo YOLO26 end-to-end NMS-free, el HEF devuelve el tensor crudo
`(1, 4 + clases, N)`. El NMS se aplica en CPU con `cv2.dnn.NMSBoxes`, igual que
en `yolo_opencv.py`.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Mapping
from contextlib import ExitStack
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from backend.inference.base import DetectorYoloBase
from backend.inference.detector import (
    ErrorCargaModelo,
    MotorInferencia,
    ResultadoDeteccion,
)
from backend.inference.preprocesado import letterbox

logger = logging.getLogger(__name__)

# El modelo es una exportación de Ultralytics, con salida (1, 4 + clases, N).
_ATRIBUTOS_BASE = 4
_FORMATO_ENTRADA = "UINT8"
_FORMATO_SALIDA = "FLOAT32"


def _soltar_grupo(grupo: Any) -> None:
    """Suelta el network group si esta versión de HailoRT lo permite.

    HailoRT no expone `release()` en todas las versiones: en algunas el grupo
    queda atado al `VDevice` y se libera con él. Se intenta y, si no existe, se
    registra para que la prueba de estrés en la Pi lo detecte.
    """
    soltar = getattr(grupo, "release", None)
    if callable(soltar):
        soltar()
    else:
        logger.debug(
            "El network group no expone release(); su liberación depende del VDevice"
        )


class MontajeHailo:
    """Un HEF cargado y listo para inferir, con su pila de contextos.

    La pila se cierra en orden inverso al montaje: primero `InferVStreams`,
    después la activación del grupo.
    """

    def __init__(
        self,
        hef: Any,
        grupo: Any,
        entrada: Any,
        salida: Any,
        pipeline: Any,
        pila: ExitStack,
    ) -> None:
        self.hef = hef
        self.grupo = grupo
        self.entrada = entrada
        self.salida = salida
        self.pipeline = pipeline
        self._pila = pila

    def cerrar(self) -> None:
        """Desmonta el HEF. Idempotente y tolerante a fallos parciales."""
        try:
            self._pila.close()
        except Exception:
            # Un fallo al desmontar no debe impedir liberar el grupo ni ocultar
            # el resto de la limpieza.
            logger.warning("Fallo al desmontar el HEF %s", self.hef, exc_info=True)
        _soltar_grupo(self.grupo)


class DispositivoHailo:
    """Dueño único del `VDevice` de la NPU.

    En HailoRT puede existir **un solo `VDevice` a la vez**, y crearlo y
    liberarlo en cada cambio de modelo es una causa conocida de que la NPU quede
    tomada. Por eso el dispositivo vive lo que vive el proceso y lo único que se
    desmonta al cambiar de modelo es lo que depende del HEF.

    Nadie más construye ni libera la NPU: pasa por acá.
    """

    _lock = threading.RLock()
    _instancia: "DispositivoHailo | None" = None

    def __init__(self, hailo: Any) -> None:
        self._hailo = hailo
        self._pila = ExitStack()
        self._vdevice = self._pila.enter_context(hailo.VDevice())

    @classmethod
    def obtener(cls) -> "DispositivoHailo":
        """Devuelve el dispositivo del proceso, creándolo la primera vez."""
        with cls._lock:
            if cls._instancia is None:
                cls._instancia = cls._crear()
            return cls._instancia

    @classmethod
    def _crear(cls) -> "DispositivoHailo":
        try:
            import hailo_platform
        except ImportError as exc:
            raise ErrorCargaModelo(
                "La NPU no está disponible: no se pudo importar 'hailo_platform'"
            ) from exc
        try:
            return cls(hailo_platform)
        except Exception as exc:
            raise ErrorCargaModelo(f"No se pudo inicializar la NPU: {exc}") from exc

    def montar(self, hef_path: Path) -> MontajeHailo:
        """Carga un HEF en la NPU y devuelve el montaje listo para inferir."""
        with self._lock:
            hailo = self._hailo
            pila = ExitStack()
            try:
                hef = hailo.HEF(str(hef_path))
                configure_params = hailo.ConfigureParams.create_from_hef(
                    hef, interface=hailo.HailoStreamInterface.PCIe
                )
                grupo = self._vdevice.configure(hef, configure_params)[0]

                entradas = hef.get_input_vstream_infos()
                salidas = hef.get_output_vstream_infos()
                if not entradas or not salidas:
                    raise ErrorCargaModelo(
                        f"El HEF no declara streams de entrada/salida: {hef_path.name}"
                    )

                params_entrada = hailo.InputVStreamParams.make(
                    grupo, format_type=getattr(hailo.FormatType, _FORMATO_ENTRADA)
                )
                params_salida = hailo.OutputVStreamParams.make(
                    grupo, format_type=getattr(hailo.FormatType, _FORMATO_SALIDA)
                )
                pila.enter_context(grupo.activate())
                pipeline = pila.enter_context(
                    hailo.InferVStreams(grupo, params_entrada, params_salida)
                )
            except ErrorCargaModelo:
                pila.close()
                raise
            except Exception as exc:
                pila.close()
                raise ErrorCargaModelo(
                    f"No se pudo montar {hef_path.name} en la NPU: {exc}"
                ) from exc

            return MontajeHailo(hef, grupo, entradas[0], salidas[0], pipeline, pila)


class DetectorYoloHailo(DetectorYoloBase):
    """Detector YOLO que corre en la NPU Hailo.

    El HEF devuelve el tensor crudo `(1, 4 + clases, N)` y el NMS se corre en
    CPU con `cv2.dnn.NMSBoxes` (mismo criterio que la rama OpenCV). Por eso acá
    sí se recibe `nms_threshold`, a diferencia del esquema anterior en el que
    el NMS venía horneado en el HEF.

    No es seguro para uso concurrente; el dueño (el hilo de inferencia del
    pipeline) serializa las llamadas y también la liberación.
    """

    motor = MotorInferencia.HAILO

    def __init__(
        self,
        modelo_path: str | Path,
        nombres_path: str | Path,
        *,
        image_size: int,
        confidence_threshold: float,
        nms_threshold: float,
        class_thresholds: Mapping[str, float],
    ) -> None:
        super().__init__(
            modelo_path,
            nombres_path,
            image_size=image_size,
            confidence_threshold=confidence_threshold,
            class_thresholds=class_thresholds,
        )

        self.nms_threshold = float(nms_threshold)
        # Tabla de umbrales por índice de clase, para resolver el filtro de todo
        # el tensor de una vez. Sin clases declaradas no hay tabla y se usa el
        # global para todas.
        self._tabla_umbrales = (
            np.asarray(self._umbrales_por_clase, dtype=np.float32)
            if self._umbrales_por_clase
            else None
        )

        self._lock = threading.Lock()
        self._montaje: MontajeHailo | None = None

        self._montaje = DispositivoHailo.obtener().montar(self.modelo_path)
        try:
            self._validar_salida()
        except ErrorCargaModelo:
            self.liberar()
            raise

        logger.info(
            "Detector Hailo cargado: %s (%d clases, entrada %dx%d)",
            self.modelo_path.name,
            len(self.nombres),
            self.image_size,
            self.image_size,
        )

    def detectar_frame(self, frame: np.ndarray) -> list[ResultadoDeteccion]:
        """Detecta objetos en un frame BGR. Devuelve [] si el detector fue liberado."""
        with self._lock:
            if self._montaje is None or frame is None or frame.size == 0:
                return []
            preds, escala, pad_x, pad_y = self._inferir_crudo(frame)

        # (1, 4 + clases, N) -> (N, 4 + clases)
        preds = np.asarray(preds)[0].transpose()
        xywh = preds[:, :_ATRIBUTOS_BASE]
        scores = preds[:, _ATRIBUTOS_BASE:]
        clases = scores.argmax(axis=1)
        confianzas = scores[np.arange(scores.shape[0]), clases]

        seleccion = confianzas > self._umbrales(clases)
        if not seleccion.any():
            return []

        xywh = xywh[seleccion]
        confianzas = confianzas[seleccion]
        clases = clases[seleccion]

        # La salida trae (centro_x, centro_y, ancho, alto) en el espacio del
        # modelo letterboxeado: se quita el relleno y se divide por la escala
        # uniforme para volver a píxeles del frame original.
        cajas = np.column_stack(
            (
                (xywh[:, 0] - 0.5 * xywh[:, 2] - pad_x) / escala,
                (xywh[:, 1] - 0.5 * xywh[:, 3] - pad_y) / escala,
                xywh[:, 2] / escala,
                xywh[:, 3] / escala,
            )
        ).astype(int).tolist()

        indices = cv2.dnn.NMSBoxes(
            cajas, confianzas.tolist(), self._umbral_minimo(), self.nms_threshold
        )
        return self._armar(indices, cajas, confianzas.tolist(), clases.tolist())

    def liberar(self) -> None:
        """Suelta el montaje del HEF. Idempotente.

        El `VDevice` no se libera acá: es del proceso, ver `DispositivoHailo`.
        """
        with self._lock:
            self._soltar_montaje()

    # --- internos ---

    def _soltar_montaje(self) -> None:
        montaje = self._montaje
        self._montaje = None
        if montaje is not None:
            montaje.cerrar()

    def _inferir_crudo(
        self, frame: np.ndarray
    ) -> tuple[Any, float, int, int]:
        """Corre la NPU y devuelve el tensor crudo y los parámetros del letterbox."""
        montaje = self._montaje
        if montaje is None:
            return np.empty((0, _ATRIBUTOS_BASE), dtype=np.float32), 1.0, 0, 0
        lienzo, escala, pad_x, pad_y = letterbox(frame, self.image_size)
        rgb = cv2.cvtColor(lienzo, cv2.COLOR_BGR2RGB)
        entrada = np.expand_dims(rgb, axis=0)
        salidas = montaje.pipeline.infer({montaje.entrada.name: entrada})
        return (
            salidas[montaje.salida.name],
            escala,
            pad_x,
            pad_y,
        )

    def _validar_salida(self) -> None:
        """Corre una inferencia en negro y verifica la forma de la salida.

        Un HEF con otro orden de tensores devolvería "cero detecciones" en
        silencio. Es mejor fallar en la carga, donde el error se ve en el panel.
        """
        prueba = np.zeros((self.image_size, self.image_size, 3), dtype=np.uint8)
        crudas, _, _, _ = self._inferir_crudo(prueba)
        preds = np.asarray(crudas)
        if preds.ndim != 3 or preds.shape[0] != 1:
            raise ErrorCargaModelo(
                f"Salida inesperada de {self.modelo_path.name}: se esperaba "
                f"tensor (1, 4+clases, N), se obtuvo {preds.shape}"
            )
        if preds.shape[1] != _ATRIBUTOS_BASE + len(self.nombres):
            logger.warning(
                "El HEF declara %d columnas y el archivo de nombres tiene %d "
                "clases: las detecciones pueden salir mal etiquetadas.",
                preds.shape[1] - _ATRIBUTOS_BASE,
                len(self.nombres),
            )

    def _umbrales(self, clases: np.ndarray) -> np.ndarray:
        """Umbral efectivo de cada fila según su clase (vectorizado)."""
        umbrales = np.full(clases.shape, self.confidence_threshold, dtype=np.float32)
        tabla = self._tabla_umbrales
        if tabla is not None:
            dentro = clases < tabla.size
            umbrales[dentro] = tabla[clases[dentro]]
        return umbrales

    def _armar(
        self,
        indices: object,
        cajas: list[list[int]],
        confianzas: list[float],
        clases: list[int],
    ) -> list[ResultadoDeteccion]:
        """Traduce los índices que devuelve el NMS a resultados."""
        resultados: list[ResultadoDeteccion] = []
        for indice in indices:  # type: ignore[union-attr]
            # OpenCV 4.x devolvía índices anidados ([[i], [j]]) y 5.x planos.
            valor = indice[0] if isinstance(indice, (list, np.ndarray)) else indice
            i = int(valor)
            left, top, caja_ancho, caja_alto = cajas[i]
            resultados.append(
                ResultadoDeteccion(
                    label=self._etiqueta(clases[i]),
                    confianza=confianzas[i],
                    bbox=(left, top, caja_ancho, caja_alto),
                )
            )
        return resultados