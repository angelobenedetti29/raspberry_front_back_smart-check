"""Regresiones de geometría entre captura, inferencia y video visible."""

from __future__ import annotations

import unittest
from dataclasses import replace
from unittest.mock import Mock

import numpy as np

from backend.config import load_config
from backend.inference.detector import ResultadoDeteccion
from backend.streaming.capture import CapturaOpenCV
from backend.streaming.pipeline import Pipeline


class TestGeometriaStreaming(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_config()
        self.captura = CapturaOpenCV(
            replace(self.config.stream.capture, width=1280, height=720),
            self.config.stream.reconnect,
            self.config.paths.videos_dir,
        )

    def test_video_cuadrado_conserva_original_y_agrega_bandas(self) -> None:
        original = np.full((640, 640, 3), 200, dtype=np.uint8)
        capturado = self.captura._normalizar(original)

        self.assertIs(capturado.original, original)
        self.assertEqual(capturado.visualizacion.shape, (720, 1280, 3))
        np.testing.assert_array_equal(capturado.visualizacion[:, :280], 114)
        np.testing.assert_array_equal(capturado.visualizacion[:, 280:1000], 200)
        np.testing.assert_array_equal(capturado.visualizacion[:, 1000:], 114)
        self.assertEqual(capturado.proyectar_bbox((0, 0, 640, 640)), (280, 0, 720, 720))
        self.assertEqual(capturado.proyectar_bbox((80, 160, 160, 80)), (370, 180, 180, 90))

    def test_video_ancho_agrega_bandas_horizontales(self) -> None:
        capturado = self.captura._normalizar(np.zeros((320, 1280, 3), dtype=np.uint8))

        self.assertEqual(capturado.proyectar_bbox((0, 0, 1280, 320)), (0, 200, 1280, 320))
        np.testing.assert_array_equal(capturado.visualizacion[:200], 114)
        np.testing.assert_array_equal(capturado.visualizacion[200:520], 0)
        np.testing.assert_array_equal(capturado.visualizacion[520:], 114)

    def test_reduccion_con_medidas_impares_proyecta_hasta_el_borde(self) -> None:
        capturado = self.captura._normalizar(np.zeros((1001, 2001, 3), dtype=np.uint8))

        self.assertEqual(capturado.proyectar_bbox((0, 0, 2001, 1001)), (0, 40, 1280, 640))

    def test_tamano_de_salida_coincidente_no_copia_ni_desplaza(self) -> None:
        original = np.zeros((720, 1280, 3), dtype=np.uint8)
        capturado = self.captura._normalizar(original)

        self.assertIs(capturado.original, original)
        self.assertIs(capturado.visualizacion, original)
        self.assertEqual(capturado.proyectar_bbox((10, 20, 30, 40)), (10, 20, 30, 40))

    def test_pipeline_infiere_original_y_estabiliza_cajas_proyectadas(self) -> None:
        original = np.zeros((640, 640, 3), dtype=np.uint8)
        capturado = self.captura._normalizar(original)
        deteccion = ResultadoDeteccion("Fajita", 0.8, (80, 160, 160, 80))
        pipeline = Pipeline(self.config, Mock(), Mock(), lambda: None)
        pipeline._captura = Mock()
        pipeline._captura.leer.return_value = capturado
        pipeline._asegurar_captura = Mock()
        pipeline._desea_preview = True
        pipeline._detector = Mock()
        pipeline._detector.detectar_frame.return_value = [deteccion]
        pipeline._estabilizador = Mock()
        pipeline._estabilizador.estabilizar.side_effect = lambda detecciones: detecciones
        pipeline._estabilizador.eventos.return_value = []

        pipeline._producir_frame()
        pipeline._inferir()

        self.assertIs(pipeline._detector.detectar_frame.call_args.args[0], original)
        proyectada = replace(deteccion, bbox=(370, 180, 180, 90))
        pipeline._estabilizador.estabilizar.assert_called_once_with([proyectada])
        self.assertEqual(pipeline._slot_detecciones[0], [proyectada])
        self.assertEqual(pipeline.ultimo_frame().shape, (720, 1280, 3))

        # La siguiente anotación usa cajas previas sin contaminar el original.
        pipeline._producir_frame()
        np.testing.assert_array_equal(original, 0)


if __name__ == "__main__":
    unittest.main()
