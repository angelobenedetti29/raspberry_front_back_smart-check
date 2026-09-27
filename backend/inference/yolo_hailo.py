"""Detector YOLO sobre un HEF de Hailo, para la NPU de la Raspberry.

Sólo funciona donde exista `hailo_platform`, así que el import es perezoso y este
módulo se carga recién cuando el motor resuelto es NPU.

El NMS y la normalización vienen **compilados dentro del HEF** (ver el `.alls`
que genera `backend/ai_training/compilar_hailo.py`): acá no se re-implementan y
no se divide la entrada por 255. Dividirla —como hacen muchos ejemplos de la comunidad que
usan `FormatType.FLOAT32`— dejaría un modelo que no detecta nada.

El NMS del HEF es **por clase**: no suprime entre clases. Para que un mismo objeto
no salga a la vez con dos etiquetas contradictorias (p. ej. TCQ y TCOK) se aplica
además un NMS class-agnostic aguas arriba, igual que hace el detector de CPU. La
nota sobre no dividir por 255 sigue vigente.
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

from backend.inference.detector import (
    ErrorCargaModelo,
    MotorInferencia,
    ResultadoDeteccion,
)

logger = logging.getLogger(__name__)

# Cada fila de la salida post-NMS es [ymin, xmin, ymax, xmax, confianza].
_COLUMNAS_POST_NMS = 5
_FORMATO_ENTRADA = "UINT8"
_FORMATO_SALIDA = "FLOAT32"


def _leer_nombres(path: Path) -> tuple[str, ...]:
    """Lee un archivo `.names`: una clase por línea, ignorando líneas vacías."""
    contenido = path.read_text(encoding="utf-8")
    return tuple(linea.strip() for linea in contenido.splitlines() if linea.strip())


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


class DetectorYoloHailo:
    """Detector YOLO que corre en la NPU Hailo.

    Recibe `nms_threshold` para el NMS class-agnostic que se aplica aguas arriba
    del NMS por clase que ya trae el HEF, y evitar que un mismo objeto aparezca
    dos veces con clases contradictorias (p. ej. TCQ y TCOK).

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
        modelo = Path(modelo_path)
        nombres = Path(nombres_path)

        if not modelo.is_file():
            raise ErrorCargaModelo(f"No se encontró el modelo: {modelo}")
        if not nombres.is_file():
            raise ErrorCargaModelo(
                f"No se encontró el archivo de nombres de clase: {nombres}"
            )

        etiquetas = _leer_nombres(nombres)
        if not etiquetas:
            raise ErrorCargaModelo(f"El archivo de nombres está vacío: {nombres}")

        self.modelo_path = modelo
        self.nombres = etiquetas
        self.image_size = int(image_size)
        self.confidence_threshold = float(confidence_threshold)
        self.nms_threshold = float(nms_threshold)
        self.class_thresholds: Mapping[str, float] = dict(class_thresholds)
        self._lock = threading.Lock()
        self._montaje: MontajeHailo | None = None

        self._montaje = DispositivoHailo.obtener().montar(modelo)
        try:
            self._validar_salida()
        except ErrorCargaModelo:
            self.liberar()
            raise

        logger.info(
            "Detector Hailo cargado: %s (%d clases, entrada %dx%d)",
            modelo.name,
            len(etiquetas),
            self.image_size,
            self.image_size,
        )

    def detectar_frame(self, frame: np.ndarray) -> list[ResultadoDeteccion]:
        """Detecta objetos en un frame BGR. Devuelve [] si el detector fue liberado.

        El HEF ya aplica su NMS por clase; acá se agrega un NMS class-agnostic para
        que un mismo objeto no aparezca dos veces con clases contradictorias (p.
        ej. TCQ y TCOK), coherente con el detector de CPU.
        """
        with self._lock:
            if self._montaje is None or frame is None or frame.size == 0:
                return []
            alto, ancho = frame.shape[:2]
            crudas = self._inferir_crudo(frame)

        cajas: list[list[int]] = []
        confianzas: list[float] = []
        etiquetas: list[str] = []
        for clase_id, filas in enumerate(crudas):
            etiqueta = self._etiqueta(clase_id)
            umbral = self._umbral(etiqueta)
            for fila in filas:
                ymin, xmin, ymax, xmax, confianza = (
                    float(valor) for valor in fila[:_COLUMNAS_POST_NMS]
                )
                if confianza < umbral:
                    continue
                # Las coordenadas vienen normalizadas a [0, 1] sobre la entrada
                # del modelo, no en píxeles del frame.
                cajas.append(
                    [
                        int(xmin * ancho),
                        int(ymin * alto),
                        int((xmax - xmin) * ancho),
                        int((ymax - ymin) * alto),
                    ]
                )
                confianzas.append(confianza)
                etiquetas.append(etiqueta)

        if not cajas:
            return []

        indices = cv2.dnn.NMSBoxes(
            cajas, confianzas, self._umbral_minimo(), self.nms_threshold
        )
        resultados: list[ResultadoDeteccion] = []
        for indice in indices:  # type: ignore[union-attr]
            # OpenCV 4.x devolvía índices anidados ([[i], [j]]) y 5.x planos.
            valor = indice[0] if isinstance(indice, (list, np.ndarray)) else indice
            i = int(valor)
            left, top, caja_ancho, caja_alto = cajas[i]
            resultados.append(
                ResultadoDeteccion(
                    label=etiquetas[i],
                    confianza=confianzas[i],
                    bbox=(left, top, caja_ancho, caja_alto),
                )
            )
        return resultados

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

    def _inferir_crudo(self, frame: np.ndarray) -> Any:
        """Corre la NPU y devuelve el tensor crudo de salida."""
        montaje = self._montaje
        if montaje is None:
            return []
        redimensionado = cv2.resize(
            frame,
            (self.image_size, self.image_size),
            interpolation=cv2.INTER_NEAREST,
        )
        rgb = cv2.cvtColor(redimensionado, cv2.COLOR_BGR2RGB)
        entrada = np.expand_dims(rgb, axis=0)
        salidas = montaje.pipeline.infer({montaje.entrada.name: entrada})
        # La salida de un HEF con NMS viene envuelta por batch: el [0] es la
        # lista de detecciones por clase. Sin desenvolverla, `_validar_salida` la
        # rechaza y `detectar_frame` leería el batch como si fuera la clase 0.
        return salidas[montaje.salida.name][0]

    def _validar_salida(self) -> None:
        """Corre una inferencia en negro y verifica la forma de la salida.

        Un HEF con otro orden de tensores devolvería "cero detecciones" en
        silencio. Es mejor fallar en la carga, donde el error se ve en el panel.
        Esto importa porque `yolov8s-hailo` viene del model zoo y no de la receta
        propia.
        """
        prueba = np.zeros((self.image_size, self.image_size, 3), dtype=np.uint8)
        crudas = self._inferir_crudo(prueba)

        if not isinstance(crudas, (list, tuple)) or not crudas:
            raise ErrorCargaModelo(
                f"Salida inesperada de {self.modelo_path.name}: se esperaba una "
                "lista de detecciones por clase"
            )
        por_clase = crudas[0]
        if getattr(por_clase, "ndim", 0) != 2 or por_clase.shape[1] != _COLUMNAS_POST_NMS:
            raise ErrorCargaModelo(
                f"Salida inesperada de {self.modelo_path.name}: se esperaban filas "
                "[ymin, xmin, ymax, xmax, confianza]"
            )
        if len(crudas) != len(self.nombres):
            logger.warning(
                "El HEF declara %d clases y el archivo de nombres tiene %d: "
                "las que sobren se rotularán como 'class_N'.",
                len(crudas),
                len(self.nombres),
            )

    def _etiqueta(self, clase_id: int) -> str:
        """Nombre de la clase, con reserva explícita si el `.names` no la cubre."""
        if 0 <= clase_id < len(self.nombres):
            return self.nombres[clase_id]
        return f"class_{clase_id}"

    def _umbral(self, etiqueta: str) -> float:
        """Umbral de la clase; las claves de config están en minúscula."""
        return self.class_thresholds.get(etiqueta.lower(), self.confidence_threshold)

    def _umbral_minimo(self) -> float:
        """Menor umbral por clase.

        El pre-filtro del NMS usa este valor y no el global: filtrar con un umbral
        más alto descartaría cajas válidas antes de que el NMS pueda decidir.
        """
        if not self.class_thresholds:
            return self.confidence_threshold
        return min(self.class_thresholds.values())
