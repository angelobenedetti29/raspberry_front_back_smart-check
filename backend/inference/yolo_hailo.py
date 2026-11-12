"""Detector YOLO sobre un HEF de Hailo, para la NPU de la Raspberry.

Soporta:
1. Modelos con NMS integrado (post-NMS nativo de Hailo).
2. Modelos YOLO end-to-end con tensor consolidado (1, 4+clases, N).
3. Modelos YOLO11/YOLOv8 con 6 salidas convolucionales crudas podadas vía end_nodes.
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

_ATRIBUTOS_BASE = 4
_COLUMNAS_POST_NMS = 5  # [ymin, xmin, ymax, xmax, confianza]
_FORMATO_ENTRADA = "UINT8"
_FORMATO_SALIDA = "FLOAT32"


def _es_estructura_post_nms(crudas: Any) -> tuple[bool, int]:
    """Determina si la salida corresponde al formato post-NMS de Hailo."""
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
    soltar = getattr(grupo, "release", None)
    if callable(soltar):
        soltar()
    else:
        logger.debug("El network group no expone release(); su liberación depende del VDevice")


class MontajeHailo:
    def __init__(
        self,
        hef: Any,
        grupo: Any,
        entrada: Any,
        salidas: list[Any],
        pipeline: Any,
        pila: ExitStack,
    ) -> None:
        self.hef = hef
        self.grupo = grupo
        self.entrada = entrada
        self.salidas = salidas
        self.pipeline = pipeline
        self._pila = pila

    def cerrar(self) -> None:
        try:
            self._pila.close()
        except Exception:
            logger.warning("Fallo al desmontar el HEF %s", self.hef, exc_info=True)
        _soltar_grupo(self.grupo)


class DispositivoHailo:
    _lock = threading.RLock()
    _instancia: DispositivoHailo | None = None

    def __init__(self, hailo: Any) -> None:
        self._hailo = hailo
        self._pila = ExitStack()
        self._vdevice = self._pila.enter_context(hailo.VDevice())

    @classmethod
    def obtener(cls) -> DispositivoHailo:
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

            return MontajeHailo(hef, grupo, entradas[0], salidas, pipeline, pila)


class DetectorYoloHailo(DetectorYoloBase):
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
        self._tabla_umbrales = (
            np.asarray(self._umbrales_por_clase, dtype=np.float32)
            if self._umbrales_por_clase
            else None
        )

        # Tablas y grillas precalculadas para decodificación DFL rápida (6-ramas)
        self._dfl_conv = np.arange(16, dtype=np.float32)
        self._grids: dict[int, np.ndarray] = {}
        for stride in (8, 16, 32):
            feat_size = self.image_size // stride
            gy, gx = np.meshgrid(np.arange(feat_size), np.arange(feat_size), indexing="ij")
            self._grids[stride] = np.stack([gx, gy], axis=-1).reshape(-1, 2) + 0.5

        self._es_post_nms: bool = False
        self._es_ramas_crudas: bool = False
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

        modo_desc = "post-NMS" if self._es_post_nms else ("6-ramas DFL" if self._es_ramas_crudas else "crudo")
        logger.info(
            "Detector Hailo cargado: %s (%d clases, entrada %dx%d, modo %s)",
            self.modelo_path.name,
            len(self.nombres),
            self.image_size,
            self.image_size,
            modo_desc,
        )

    def detectar_frame(self, frame: np.ndarray) -> list[ResultadoDeteccion]:
        with self._lock:
            if self._montaje is None or frame is None or frame.size == 0:
                return []
            crudas, escala, pad_x, pad_y = self._inferir_crudo(frame)

        if self._es_post_nms:
            return self._decodificar_post_nms(crudas, escala, pad_x, pad_y)

        if self._es_ramas_crudas:
            return self._decodificar_ramas_dfl(crudas, escala, pad_x, pad_y)

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
        with self._lock:
            self._soltar_montaje()

    def _soltar_montaje(self) -> None:
        montaje = self._montaje
        self._montaje = None
        if montaje is not None:
            montaje.cerrar()

    def _inferir_crudo(self, frame: np.ndarray) -> tuple[Any, float, int, int]:
        montaje = self._montaje
        if montaje is None:
            return {}, 1.0, 0, 0
        lienzo, escala, pad_x, pad_y = letterbox(frame, self.image_size)
        rgb = cv2.cvtColor(lienzo, cv2.COLOR_BGR2RGB)
        entrada = np.expand_dims(rgb, axis=0)
        salidas = montaje.pipeline.infer({montaje.entrada.name: entrada})

        if len(montaje.salidas) == 1:
            return salidas[montaje.salidas[0].name], escala, pad_x, pad_y
        return salidas, escala, pad_x, pad_y

    def _umbral_de_indice(self, clase_id: int) -> float:
        if 0 <= clase_id < len(self._umbrales_por_clase):
            return self._umbrales_por_clase[clase_id]
        return self.confidence_threshold

    def _decodificar_ramas_dfl(
        self,
        salidas: dict[str, np.ndarray],
        escala: float,
        pad_x: int,
        pad_y: int,
    ) -> list[ResultadoDeteccion]:
        """Decodifica las 6 salidas convolucionales de YOLO11 podado con end_nodes (optimizado)."""
        tensores = list(salidas.values())
        num_clases = len(self.nombres)

        # Separar ramas de cajas (64 canales) y clases (num_clases canales)
        box_branches = [t for t in tensores if t.shape[-1] == 64]
        cls_branches = [t for t in tensores if t.shape[-1] == num_clases]

        # Ordenar por resolución espacial: 80x80 -> 40x40 -> 20x20
        box_branches.sort(key=lambda t: t.shape[1], reverse=True)
        cls_branches.sort(key=lambda t: t.shape[1], reverse=True)

        cajas_totales = []
        confianzas_totales = []
        clases_totales = []

        strides = (8, 16, 32)
        umbrales_clase = np.asarray(
            [self._umbral_de_indice(c) for c in range(num_clases)], dtype=np.float32
        )

        for stride, box_feat, cls_feat in zip(strides, box_branches, cls_branches):
            cls_flat = cls_feat.reshape(-1, num_clases)
            # Sigmoide sobre los logits de clase (vectorizado y liviano)
            cls_probs = 1.0 / (1.0 + np.exp(-cls_flat))
            best_clase = np.argmax(cls_probs, axis=-1)
            best_score = cls_probs[np.arange(len(cls_probs)), best_clase]

            # Filtro temprano: descartamos anclas de fondo ANTES de calcular el costoso DFL
            mask = best_score >= umbrales_clase[best_clase]
            if not np.any(mask):
                continue

            # Softmax y cálculo de distancias LTRB sólo para las pocas anclas que superan el umbral
            b = box_feat.reshape(-1, 4, 16)[mask]
            b = np.exp(b - np.max(b, axis=-1, keepdims=True))
            b /= np.sum(b, axis=-1, keepdims=True)
            ltrb = np.dot(b, self._dfl_conv)

            # Usamos la grilla precomputada para este stride
            grid = self._grids[stride][mask]
            x1 = (grid[:, 0] - ltrb[:, 0]) * stride
            y1 = (grid[:, 1] - ltrb[:, 1]) * stride
            x2 = (grid[:, 0] + ltrb[:, 2]) * stride
            y2 = (grid[:, 1] + ltrb[:, 3]) * stride

            cajas = np.stack([x1, y1, x2 - x1, y2 - y1], axis=-1)

            cajas_totales.append(cajas)
            confianzas_totales.append(best_score[mask])
            clases_totales.append(best_clase[mask])

        if not cajas_totales:
            return []

        todas_cajas = np.concatenate(cajas_totales, axis=0)
        todas_conf = np.concatenate(confianzas_totales, axis=0)
        todas_clases = np.concatenate(clases_totales, axis=0)

        # NMS con OpenCV en CPU
        indices = cv2.dnn.NMSBoxes(
            bboxes=todas_cajas.tolist(),
            scores=todas_conf.tolist(),
            score_threshold=0.0,
            nms_threshold=self.nms_threshold,
        )

        if len(indices) == 0:
            return []

        indices = np.array(indices).flatten()
        resultados: list[ResultadoDeteccion] = []
        for idx in indices:
            x, y, w, h = todas_cajas[idx]
            left = int(round((x - pad_x) / escala))
            top = int(round((y - pad_y) / escala))
            width = int(round(w / escala))
            height = int(round(h / escala))
            if width <= 0 or height <= 0:
                continue

            clase_id = int(todas_clases[idx])
            resultados.append(
                ResultadoDeteccion(
                    label=self._etiqueta(clase_id),
                    confianza=float(todas_conf[idx]),
                    bbox=(left, top, width, height),
                )
            )
        return resultados

    def _decodificar_post_nms(
        self,
        crudas: Any,
        escala: float,
        pad_x: int,
        pad_y: int,
    ) -> list[ResultadoDeteccion]:
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
                ymin, xmin, ymax, xmax, confianza = (
                    float(v) for v in fila[:_COLUMNAS_POST_NMS]
                )
                if confianza <= umbral:
                    continue

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
        prueba = np.zeros((self.image_size, self.image_size, 3), dtype=np.uint8)
        try:
            crudas, _, _, _ = self._inferir_crudo(prueba)
        except Exception as exc:
            raise ErrorCargaModelo(
                f"Fallo en inferencia de validación para {self.modelo_path.name}: {exc}"
            ) from exc

        # 1. Caso: Diccionario con múltiples ramas (salida podada con end_nodes)
        if isinstance(crudas, dict) and len(crudas) >= 6:
            self._es_ramas_crudas = True
            self._es_post_nms = False
            return

        # 2. Caso: Salida post-NMS de Hailo
        es_post, num_clases = _es_estructura_post_nms(crudas)
        if es_post:
            self._es_post_nms = True
            self._es_ramas_crudas = False
            return

        # 3. Caso: Tensor rectangular consolidado (1, 4+clases, N)
        try:
            preds = np.asarray(crudas)
        except Exception as exc:
            raise ErrorCargaModelo(
                f"Estructura no reconocida en la salida de {self.modelo_path.name}: {exc}"
            ) from exc

        if preds.ndim == 3 and preds.shape[0] == 1 and preds.shape[1] >= _ATRIBUTOS_BASE:
            self._es_post_nms = False
            self._es_ramas_crudas = False
            return

        raise ErrorCargaModelo(
            f"Salida inesperada de {self.modelo_path.name}: se esperaba tensor "
            f"(1, 4+clases, N), diccionario de ramas crudas o estructura post-NMS por clase. "
            f"Forma: {getattr(preds, 'shape', type(crudas))}"
        )