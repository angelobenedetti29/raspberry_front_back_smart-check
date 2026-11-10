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
from backend.inference.postprocesado import decodificar_salida
from backend.inference.preprocesado import letterbox

logger = logging.getLogger(__name__)

# El modelo puede tener salida cruda (1, 4 + clases, N) o post-NMS por clase.
_ATRIBUTOS_BASE = 4
_COLUMNAS_POST_NMS = 5  # [ymin, xmin, ymax, xmax, confianza]
_FORMATO_ENTRADA = "UINT8"
_FORMATO_SALIDA = "FLOAT32"


def _es_estructura_post_nms(crudas: Any) -> tuple[bool, int]:
    """Determina si la salida corresponde al formato post-NMS de Hailo.

    Devuelve (es_post_nms, cantidad_clases).
    """
    # Si es un ndarray numérico denso 3D (1, 4+clases, N), es tensor crudo.
    if isinstance(crudas, np.ndarray) and crudas.dtype != object:
        return False, 0

    cand = crudas
    if isinstance(cand, (list, tuple)) and len(cand) == 1:
        cand = cand[0]
    elif (
        isinstance(cand, np.ndarray)
        and cand.ndim >= 1
        and cand.shape[0] == 1
        and cand.dtype == object
    ):
        cand = cand[0]

    if not isinstance(cand, (list, tuple, np.ndarray)) or len(cand) == 0:
        return False, 0

    # Cada elemento de cand corresponde a una clase y contiene filas
    # [ymin, xmin, ymax, xmax, confianza]
    for elem in cand:
        if isinstance(elem, np.ndarray):
            if elem.ndim == 2:
                if elem.shape[0] > 0 and elem.shape[1] != _COLUMNAS_POST_NMS:
                    return False, 0
            elif elem.ndim == 1 and elem.size == 0:
                pass
            else:
                return False, 0
        elif isinstance(elem, (list, tuple)):
            if len(elem) > 0:
                primer_elem = elem[0]
                if (
                    not isinstance(primer_elem, (list, tuple, np.ndarray))
                    or len(primer_elem) != _COLUMNAS_POST_NMS
                ):
                    return False, 0
        else:
            return False, 0

    return True, len(cand)


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
        logger.debug("El network group no expone release(); su liberación depende del VDevice")


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
    _instancia: DispositivoHailo | None = None

    def __init__(self, hailo: Any) -> None:
        self._hailo = hailo
        self._pila = ExitStack()
        self._vdevice = self._pila.enter_context(hailo.VDevice())

    @classmethod
    def obtener(cls) -> DispositivoHailo:
        """Devuelve el dispositivo del proceso, creándolo la primera vez."""
        with cls._lock:
            if cls._instancia is None:
                cls._instancia = cls._crear()
            return cls._instancia

    @classmethod
    def _crear(cls) -> DispositivoHailo:
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

        self._es_post_nms: bool = False
        self._lock = threading.Lock()
        self._montaje: MontajeHailo | None = None

        self._montaje = DispositivoHailo.obtener().montar(self.modelo_path)
        try:
            self._validar_salida()
        except ErrorCargaModelo:
            self.liberar()
            raise
        except Exception as exc:
            self.liberar()
            raise ErrorCargaModelo(
                f"Fallo al validar la salida de {self.modelo_path.name}: {exc}"
            ) from exc

        logger.info(
            "Detector Hailo cargado: %s (%d clases, entrada %dx%d, modo %s)",
            self.modelo_path.name,
            len(self.nombres),
            self.image_size,
            self.image_size,
            "post-NMS" if self._es_post_nms else "crudo",
        )

    def detectar_frame(self, frame: np.ndarray) -> list[ResultadoDeteccion]:
        """Detecta objetos en un frame BGR. Devuelve [] si el detector fue liberado."""
        with self._lock:
            if self._montaje is None or frame is None or frame.size == 0:
                return []
            crudas, escala, pad_x, pad_y = self._inferir_crudo(frame)

        if self._es_post_nms:
            return self._decodificar_post_nms(crudas, escala, pad_x, pad_y)

        return decodificar_salida(
            crudas,
            escala=escala,
            pad_x=pad_x,
            pad_y=pad_y,
            tabla_umbrales=self._tabla_umbrales,
            umbral_global=self.confidence_threshold,
            umbral_minimo=self._umbral_minimo(),
            nms_threshold=self.nms_threshold,
            etiqueta=self._etiqueta,
        )

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

    def _inferir_crudo(self, frame: np.ndarray) -> tuple[Any, float, int, int]:
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

    def _umbral_de_indice(self, clase_id: int) -> float:
        """Devuelve el umbral precalculado para el índice de clase."""
        if 0 <= clase_id < len(self._umbrales_por_clase):
            return self._umbrales_por_clase[clase_id]
        return self.confidence_threshold

    def _decodificar_post_nms(
        self,
        crudas: Any,
        escala: float,
        pad_x: int,
        pad_y: int,
    ) -> list[ResultadoDeteccion]:
        """Decodifica salida post-NMS de Hailo (lista de detecciones por clase)."""
        cand = crudas
        if isinstance(cand, (list, tuple)) and len(cand) == 1:
            cand = cand[0]
        elif (
            isinstance(cand, np.ndarray)
            and cand.ndim >= 1
            and cand.shape[0] == 1
            and cand.dtype == object
        ):
            cand = cand[0]

        resultados: list[ResultadoDeteccion] = []
        for clase_id, filas in enumerate(cand):
            if filas is None or len(filas) == 0:
                continue
            etiqueta = self._etiqueta(clase_id)
            umbral = self._umbral_de_indice(clase_id)
            for fila in filas:
                # Cada fila es [ymin, xmin, ymax, xmax, confianza]
                ymin, xmin, ymax, xmax, confianza = (
                    float(v) for v in fila[:_COLUMNAS_POST_NMS]
                )
                if confianza <= umbral:
                    continue

                # Coordenadas [0, 1] relativas a la entrada letterboxeada
                left = int(round((xmin * self.image_size - pad_x) / escala))
                top = int(round((ymin * self.image_size - pad_y) / escala))
                w = int(round(((xmax - xmin) * self.image_size) / escala))
                h = int(round(((ymax - ymin) * self.image_size) / escala))
                if w <= 0 or h <= 0:
                    continue

                resultados.append(
                    ResultadoDeteccion(
                        label=etiqueta,
                        confianza=confianza,
                        bbox=(left, top, w, h),
                    )
                )
        return resultados

    def _validar_salida(self) -> None:
        """Corre una inferencia en negro y verifica la forma de la salida.

        Soporta tanto HEF con tensor crudo (1, 4+clases, N) como HEF con
        salida post-NMS por clase (NMS embebido en el HEF).
        """
        prueba = np.zeros((self.image_size, self.image_size, 3), dtype=np.uint8)
        try:
            crudas, _, _, _ = self._inferir_crudo(prueba)
        except Exception as exc:
            raise ErrorCargaModelo(
                f"Fallo en inferencia de validación para {self.modelo_path.name}: {exc}"
            ) from exc

        # 1. ¿Es salida post-NMS de Hailo?
        es_post, num_clases = _es_estructura_post_nms(crudas)
        if es_post:
            self._es_post_nms = True
            if num_clases != len(self.nombres):
                logger.warning(
                    "El HEF post-NMS declara %d clases y el archivo de nombres tiene %d: "
                    "las que sobren se rotularán como 'class_N'.",
                    num_clases,
                    len(self.nombres),
                )
            return

        # 2. ¿Es tensor crudo rectangular?
        try:
            preds = np.asarray(crudas)
        except Exception as exc:
            raise ErrorCargaModelo(
                f"Estructura no reconocida en la salida de {self.modelo_path.name}: {exc}"
            ) from exc

        if preds.ndim == 3 and preds.shape[0] == 1 and preds.shape[1] >= _ATRIBUTOS_BASE:
            self._es_post_nms = False
            if preds.shape[1] != _ATRIBUTOS_BASE + len(self.nombres):
                logger.warning(
                    "El HEF declara %d columnas y el archivo de nombres tiene %d "
                    "clases: las detecciones pueden salir mal etiquetadas.",
                    preds.shape[1] - _ATRIBUTOS_BASE,
                    len(self.nombres),
                )
            return

        raise ErrorCargaModelo(
            f"Salida inesperada de {self.modelo_path.name}: se esperaba tensor "
            f"(1, 4+clases, N) o estructura post-NMS por clase. "
            f"Forma: {getattr(preds, 'shape', type(crudas))}"
        )
