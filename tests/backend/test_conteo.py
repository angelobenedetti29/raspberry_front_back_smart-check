"""Tests unitarios para la lógica de conteo, cruce de línea y estabilización."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from backend.inference.detector import ResultadoDeteccion
from backend.lote import LoteService
from backend.lote.estado import clasificar_estado
from backend.lote.tipos import EstadoProducto
from backend.streaming.estabilizador import EstabilizadorDetecciones, EventoPista
from backend.streaming.suscripcion import EventoObservado


class TestEstabilizadorConteo(unittest.TestCase):
    """Pruebas del detector de cruce de línea en EstabilizadorDetecciones."""

    def setUp(self) -> None:
        # Línea de conteo en Y = 540 (frame 720p)
        self.linea_y = 540
        self.estabilizador = EstabilizadorDetecciones(linea_conteo_y=self.linea_y)

    def test_cruce_normal(self) -> None:
        """Una tostada que avanza y cruza la línea debe generar exactamente un cruce."""
        # Frame 1: nace en Y = 400 (centro = 400 + 30 = 430 < 540)
        det1 = [ResultadoDeteccion("TCOK", 0.9, (100, 400, 100, 60))]
        self.estabilizador.estabilizar(det1)
        eventos = self.estabilizador.eventos()
        self.assertEqual(len([e for e in eventos if e.tipo == "alta"]), 1)
        self.assertEqual(len([e for e in eventos if e.tipo == "cruce"]), 0)

        # Frame 2: avanza a Y = 460 (centro = 490 < 540)
        det2 = [ResultadoDeteccion("TCOK", 0.9, (100, 460, 100, 60))]
        self.estabilizador.estabilizar(det2)
        eventos = self.estabilizador.eventos()
        self.assertEqual(len([e for e in eventos if e.tipo == "cruce"]), 0)

        # Frame 3: avanza a Y = 500 (centro = 530 < 540)
        det3 = [ResultadoDeteccion("TCOK", 0.9, (100, 500, 100, 60))]
        self.estabilizador.estabilizar(det3)
        eventos = self.estabilizador.eventos()
        self.assertEqual(len([e for e in eventos if e.tipo == "cruce"]), 0)

        # Frame 4: cruza la línea a Y = 530 (centro = 530 + 30 = 560 >= 540)
        det4 = [ResultadoDeteccion("TCOK", 0.9, (100, 530, 100, 60))]
        self.estabilizador.estabilizar(det4)
        eventos = self.estabilizador.eventos()
        cruces = [e for e in eventos if e.tipo == "cruce"]
        self.assertEqual(len(cruces), 1)
        self.assertEqual(cruces[0].label, "TCOK")

        # Frame 5: continúa avanzando a Y = 590 (centro = 620)
        det5 = [ResultadoDeteccion("TCOK", 0.9, (100, 590, 100, 60))]
        self.estabilizador.estabilizar(det5)
        eventos = self.estabilizador.eventos()
        self.assertEqual(len([e for e in eventos if e.tipo == "cruce"]), 0)

    def test_antiparapadeo_antes_de_la_linea(self) -> None:
        """Si la tostada parpadea y muere antes de la línea, no suma; suma al cruzar luego."""
        # Nace en Y = 350
        det1 = [ResultadoDeteccion("TCOK", 0.85, (100, 350, 100, 60))]
        self.estabilizador.estabilizar(det1)
        self.estabilizador.eventos()

        # Desaparece durante 30 frames (supera _FRAMES_MAX_PERDIDA = 25)
        for _ in range(30):
            self.estabilizador.estabilizar([])
        eventos_perdida = self.estabilizador.eventos()
        bajas = [e for e in eventos_perdida if e.tipo == "baja"]
        self.assertEqual(len(bajas), 1)
        self.assertEqual(len([e for e in eventos_perdida if e.tipo == "cruce"]), 0)

        # Reaparece en Y = 450 con nueva pista
        det_nueva = [ResultadoDeteccion("TCOK", 0.85, (100, 450, 100, 60))]
        self.estabilizador.estabilizar(det_nueva)
        # 2 frames más para madurar antes de cruzar
        self.estabilizador.estabilizar([ResultadoDeteccion("TCOK", 0.85, (100, 480, 100, 60))])
        self.estabilizador.estabilizar([ResultadoDeteccion("TCOK", 0.85, (100, 500, 100, 60))])

        # Cruza la línea (centro = 530 + 30 = 560 >= 540)
        det_cruza = [ResultadoDeteccion("TCOK", 0.85, (100, 530, 100, 60))]
        self.estabilizador.estabilizar(det_cruza)
        eventos = self.estabilizador.eventos()
        cruces = [e for e in eventos if e.tipo == "cruce"]
        # En total sólo hubo 1 evento cruce
        self.assertEqual(len(cruces), 1)

    def test_antiparapadeo_despues_de_la_linea(self) -> None:
        """Si la tostada cruza y luego parpadea post-línea, la nueva pista no vuelve a contar."""
        # 3 frames previos antes de la línea
        for y in (420, 460, 500):
            self.estabilizador.estabilizar([ResultadoDeteccion("TCOK", 0.9, (100, y, 100, 60))])
            self.estabilizador.eventos()

        # Cruza la línea a Y = 530 (centro = 560 >= 540)
        self.estabilizador.estabilizar([ResultadoDeteccion("TCOK", 0.9, (100, 530, 100, 60))])
        eventos_cruce = self.estabilizador.eventos()
        self.assertEqual(len([e for e in eventos_cruce if e.tipo == "cruce"]), 1)

        # Avanza a Y = 590 y luego se pierde por 30 frames
        self.estabilizador.estabilizar([ResultadoDeteccion("TCOK", 0.9, (100, 590, 100, 60))])
        self.estabilizador.eventos()
        for _ in range(30):
            self.estabilizador.estabilizar([])
        self.estabilizador.eventos()

        # Reaparece más adelante en Y = 620 (centro = 650 > 540)
        # Como nace ya pasada la línea, debe nacer con cruzada = True
        self.estabilizador.estabilizar([ResultadoDeteccion("TCOK", 0.9, (100, 620, 100, 60))])
        self.estabilizador.estabilizar([ResultadoDeteccion("TCOK", 0.9, (100, 650, 100, 60))])
        eventos_post = self.estabilizador.eventos()
        # No debe haber ningún evento cruce nuevo
        self.assertEqual(len([e for e in eventos_post if e.tipo == "cruce"]), 0)

    def test_filtro_madurez_antiruido(self) -> None:
        """Una detección espuria de 1 solo cuadro sobre la línea no genera cruce."""
        # Aparece directamente cruzando en el frame 1 (frames_vistos = 1 < 3)
        det = [ResultadoDeteccion("TCOK", 0.7, (100, 540, 100, 60))]
        self.estabilizador.estabilizar(det)
        eventos = self.estabilizador.eventos()
        self.assertEqual(len([e for e in eventos if e.tipo == "cruce"]), 0)

    def test_clasificacion_quemada_en_cruce(self) -> None:
        """Una tostada con etiqueta quemada confirmada debe emitir cruce con TCQ."""
        # 3 frames acumulando etiqueta quemada
        for y in (420, 460, 500):
            self.estabilizador.estabilizar([ResultadoDeteccion("TCQ", 0.95, (100, y, 100, 60))])
            self.estabilizador.eventos()

        # Cruza la línea
        self.estabilizador.estabilizar([ResultadoDeteccion("TCQ", 0.95, (100, 530, 100, 60))])
        eventos = self.estabilizador.eventos()
        cruces = [e for e in eventos if e.tipo == "cruce"]
        self.assertEqual(len(cruces), 1)
        self.assertEqual(cruces[0].label, "TCQ")


class TestLoteServiceTraduccion(unittest.TestCase):
    """Pruebas de traducción de eventos de cruce en LoteService."""

    def setUp(self) -> None:
        from backend.config import load_config
        config = load_config()
        streaming = MagicMock()
        self.servicio = LoteService(config, streaming)

    def test_traduccion_cruce_ok(self) -> None:
        """Un evento cruce con TCOK se traduce a EstadoProducto.OK y no se duplica."""
        ev = EventoPista("cruce", pista_id=10, label="TCOK", confianza=0.9, bbox=(0, 540, 100, 50))
        observado = EventoObservado(evento=ev, modelo_id="m1", numero_frame=100)

        # Primera traducción: debe dar OK
        det = self.servicio._traducir(observado)
        self.assertIsNotNone(det)
        assert det is not None
        self.assertEqual(det.estado, EstadoProducto.OK)

        # Segunda llamada con la misma pista: descartada por ya reportada
        det_duplicado = self.servicio._traducir(observado)
        self.assertIsNone(det_duplicado)

        # Evento baja posterior: descartado porque ya fue reportada
        ev_baja = EventoPista("baja", pista_id=10, label="TCOK", confianza=0.9, bbox=(0, 680, 100, 50))
        observado_baja = EventoObservado(evento=ev_baja, modelo_id="m1", numero_frame=150)
        self.assertIsNone(self.servicio._traducir(observado_baja))

    def test_traduccion_cruce_quemado(self) -> None:
        """Un evento cruce con TCQ se traduce a EstadoProducto.QUEMADO."""
        ev = EventoPista("cruce", pista_id=20, label="TCQ", confianza=0.95, bbox=(0, 540, 100, 50))
        observado = EventoObservado(evento=ev, modelo_id="m1", numero_frame=100)

        det = self.servicio._traducir(observado)
        self.assertIsNotNone(det)
        assert det is not None
        self.assertEqual(det.estado, EstadoProducto.QUEMADO)


if __name__ == "__main__":
    unittest.main()
