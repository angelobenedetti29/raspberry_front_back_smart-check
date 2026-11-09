"""Evita cajas fantasma sin perder la continuidad del seguimiento y conteo."""

from __future__ import annotations

import unittest

from backend.inference.detector import ResultadoDeteccion
from backend.streaming.estabilizador import EstabilizadorDetecciones


class TestEstabilizadorVisualizacion(unittest.TestCase):
    def test_caja_perdida_no_duplica_una_deteccion_actual_solapada(self) -> None:
        estabilizador = EstabilizadorDetecciones()
        anteriores = [
            ResultadoDeteccion("Fajita", 0.9, (0, 0, 100, 100)),
            ResultadoDeteccion("Fajita", 0.9, (0, 70, 100, 100)),
        ]
        estabilizador.estabilizar(anteriores)
        self.assertEqual(len(estabilizador.estabilizar(anteriores)), 2)

        # Ambas pistas compiten por una caja. La que pierde la asociación
        # conserva su estado interno, pero no debe dibujarse sobre la actual.
        actual = ResultadoDeteccion("Fajita", 0.9, (0, 35, 100, 100))
        self.assertEqual(estabilizador.estabilizar([actual]), [actual])

    def test_pista_oculta_se_recupera_y_cuenta_sin_otra_alta(self) -> None:
        estabilizador = EstabilizadorDetecciones(linea_conteo_y=540)
        for y in (450, 480):
            estabilizador.estabilizar([ResultadoDeteccion("Fajita", 0.9, (100, y, 100, 60))])
        altas = [e for e in estabilizador.eventos() if e.tipo == "alta"]
        self.assertEqual(len(altas), 1)

        self.assertEqual(estabilizador.estabilizar([]), [])
        self.assertEqual(estabilizador.eventos(), [])

        actual = ResultadoDeteccion("Fajita", 0.9, (100, 530, 100, 60))
        self.assertEqual(estabilizador.estabilizar([actual]), [actual])
        eventos = estabilizador.eventos()
        self.assertEqual([e.tipo for e in eventos], ["cruce"])
        self.assertEqual(eventos[0].pista_id, altas[0].pista_id)

    def test_ocultar_no_adelanta_la_baja_de_la_pista(self) -> None:
        estabilizador = EstabilizadorDetecciones()
        deteccion = ResultadoDeteccion("Fajita", 0.9, (100, 200, 100, 60))
        estabilizador.estabilizar([deteccion])
        estabilizador.estabilizar([deteccion])
        alta = estabilizador.eventos()[0]

        for _ in range(25):
            self.assertEqual(estabilizador.estabilizar([]), [])
            self.assertEqual(estabilizador.eventos(), [])

        self.assertEqual(estabilizador.estabilizar([]), [])
        eventos = estabilizador.eventos()
        self.assertEqual([e.tipo for e in eventos], ["baja"])
        self.assertEqual(eventos[0].pista_id, alta.pista_id)
        self.assertEqual(eventos[0].bbox, deteccion.bbox)


if __name__ == "__main__":
    unittest.main()
