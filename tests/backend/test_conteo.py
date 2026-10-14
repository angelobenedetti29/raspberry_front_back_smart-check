"""Tests del conteo por línea virtual: cruce, estabilización y traducción al lote.

Adaptación a esta rama del test_conteo de d1033ed
(rework/raspberry-halio-nueva-comp): el ``alta`` llega tras 2 frames vistos
consecutivos, una tentativa que se pierde se descarta en silencio (sin
``baja``), el ``cruce`` sólo lo emite una pista confirmada, y ``_traducir``
resuelve producto y estado con la convención ``<producto>_<estado>`` y los
alias transitorios TCQ/TCOK (ver ``backend.etiquetas``).
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from backend.config import load_config
from backend.inference.detector import ResultadoDeteccion
from backend.lote import LoteService
from backend.lote.tipos import EstadoProducto, Producto
from backend.streaming.estabilizador import EstabilizadorDetecciones, EventoPista
from backend.streaming.suscripcion import EventoObservado


class TestEstabilizadorConteo(unittest.TestCase):
    """Cruce de la línea virtual en EstabilizadorDetecciones."""

    def setUp(self) -> None:
        # Línea en Y=540 (frame 720p); cajas de 100×60 → centro_y = top + 30.
        self.linea_y = 540
        self.estabilizador = EstabilizadorDetecciones(linea_conteo_y=self.linea_y)

    def _conteo(self, tipo: str) -> int:
        """Eventos pendientes del tipo (los limpia, como hace el pipeline)."""
        return len([e for e in self.estabilizador.eventos() if e.tipo == tipo])

    def test_cruce_normal(self) -> None:
        """Una tostada que avanza y cruza la línea genera exactamente un cruce."""
        # Frame 1 (centro 430 < 540): nace tentativa, todavía sin alta.
        self.estabilizador.estabilizar(
            [ResultadoDeteccion("TCOK", 0.9, (100, 400, 100, 60))]
        )
        self.assertEqual(self._conteo("alta"), 0)

        # Frame 2 (centro 490): segundo frame visto consecutivo → alta.
        self.estabilizador.estabilizar(
            [ResultadoDeteccion("TCOK", 0.9, (100, 460, 100, 60))]
        )
        self.assertEqual(self._conteo("alta"), 1)

        # Frame 3 (centro 530): todavía arriba de la línea, sin cruce.
        self.estabilizador.estabilizar(
            [ResultadoDeteccion("TCOK", 0.9, (100, 500, 100, 60))]
        )
        self.assertEqual(self._conteo("cruce"), 0)

        # Frame 4 (centro 560 >= 540): cruce, exactamente una vez.
        self.estabilizador.estabilizar(
            [ResultadoDeteccion("TCOK", 0.9, (100, 530, 100, 60))]
        )
        cruces = [e for e in self.estabilizador.eventos() if e.tipo == "cruce"]
        self.assertEqual(len(cruces), 1)
        self.assertEqual(cruces[0].label, "TCOK")

        # Frame 5 (centro 620): sigue avanzando, sin nuevo cruce.
        self.estabilizador.estabilizar(
            [ResultadoDeteccion("TCOK", 0.9, (100, 590, 100, 60))]
        )
        self.assertEqual(self._conteo("cruce"), 0)

    def test_tentativa_perdida_antes_de_la_linea_no_cuenta(self) -> None:
        """Una tentativa que se pierde antes de la línea se descarta en silencio."""
        # Nace en Y=350 (centro 380) y sólo se ve un frame: queda tentativa.
        self.estabilizador.estabilizar(
            [ResultadoDeteccion("TCOK", 0.85, (100, 350, 100, 60))]
        )
        self.estabilizador.eventos()

        # Se pierde más que _FRAMES_MAX_PERDIDA (25): descarte en silencio,
        # sin baja (no era producto) y sin cruce.
        for _ in range(30):
            self.estabilizador.estabilizar([])
        perdidos = self.estabilizador.eventos()
        self.assertEqual(len([e for e in perdidos if e.tipo == "baja"]), 0)
        self.assertEqual(len([e for e in perdidos if e.tipo == "cruce"]), 0)

    def test_reaparicion_antes_de_la_linea_no_duplica(self) -> None:
        """Una pista nueva que cruza la línea suma su cruce, y sólo uno."""
        # Pista confirmada arriba de la línea (2 frames consecutivos).
        self.estabilizador.estabilizar(
            [ResultadoDeteccion("TCOK", 0.85, (100, 450, 100, 60))]
        )
        self.estabilizador.estabilizar(
            [ResultadoDeteccion("TCOK", 0.85, (100, 480, 100, 60))]
        )
        self.estabilizador.eventos()

        # Cruza la línea (centro 560 >= 540): exactamente un cruce.
        self.estabilizador.estabilizar(
            [ResultadoDeteccion("TCOK", 0.85, (100, 530, 100, 60))]
        )
        self.assertEqual(self._conteo("cruce"), 1)

    def test_nacida_despues_de_la_linea_no_cuenta(self) -> None:
        """Una pista que reaparece ya debajo de la línea nace cruzada: no cruza."""
        # 3 frames arriba de la línea (confirmada al 2º).
        for y in (420, 460, 500):
            self.estabilizador.estabilizar(
                [ResultadoDeteccion("TCOK", 0.9, (100, y, 100, 60))]
            )
        # Cruza (centro 560): un cruce.
        self.estabilizador.estabilizar(
            [ResultadoDeteccion("TCOK", 0.9, (100, 530, 100, 60))]
        )
        self.assertEqual(self._conteo("cruce"), 1)

        # Avanza, se pierde por 30 frames (> _FRAMES_MAX_PERDIDA) → baja.
        self.estabilizador.estabilizar(
            [ResultadoDeteccion("TCOK", 0.9, (100, 590, 100, 60))]
        )
        self.estabilizador.eventos()
        for _ in range(30):
            self.estabilizador.estabilizar([])
        self.assertEqual(self._conteo("baja"), 1)

        # Reaparece ya debajo de la línea (centro 650 > 540): nace cruzada,
        # ningún cruce nuevo aunque siga viva.
        self.estabilizador.estabilizar(
            [ResultadoDeteccion("TCOK", 0.9, (100, 620, 100, 60))]
        )
        self.estabilizador.estabilizar(
            [ResultadoDeteccion("TCOK", 0.9, (100, 650, 100, 60))]
        )
        self.assertEqual(self._conteo("cruce"), 0)

    def test_esporadica_no_genera_cruce(self) -> None:
        """Una detección espuria de un frame sobre la línea no genera cruce."""
        # Nace con el centro ya en 570 >= 540: tentativa de un solo frame que
        # además nace cruzada → sin alta y sin cruce.
        self.estabilizador.estabilizar(
            [ResultadoDeteccion("TCOK", 0.7, (100, 540, 100, 60))]
        )
        eventos = self.estabilizador.eventos()
        self.assertEqual(len([e for e in eventos if e.tipo == "cruce"]), 0)
        self.assertEqual(len([e for e in eventos if e.tipo == "alta"]), 0)

    def test_quemada_cruza_con_su_label(self) -> None:
        """Una pista con quemada confirmada emite su cruce con el label latcheado."""
        # 3 frames acumulando quemada (racha acumulativa): latch a TCQ al 3º.
        for y in (420, 460, 500):
            self.estabilizador.estabilizar(
                [ResultadoDeteccion("TCQ", 0.95, (100, y, 100, 60))]
            )
        self.estabilizador.eventos()

        # Cruza la línea (centro 560): cruce con el label latcheado TCQ.
        self.estabilizador.estabilizar(
            [ResultadoDeteccion("TCQ", 0.95, (100, 530, 100, 60))]
        )
        cruces = [e for e in self.estabilizador.eventos() if e.tipo == "cruce"]
        self.assertEqual(len(cruces), 1)
        self.assertEqual(cruces[0].label, "TCQ")


class TestLoteServiceTraduccion(unittest.TestCase):
    """Traducción de eventos de cruce en LoteService (convención de etiquetas)."""

    def setUp(self) -> None:
        # config.json trae la línea habilitada (stream.counting.enabled).
        self.servicio = LoteService(load_config(), MagicMock())
        # Catálogo sembrado: el alias TCQ/TCOK resuelve a tostada_comun
        # (ver _ALIAS_TRANSITORIOS en backend.etiquetas).
        self.servicio._producto_por_nombre = {
            "tostada_comun": Producto(id="p-1", nombre="Tostada común", activo=True)
        }

    def _observado(self, tipo: str, pista_id: int, label: str) -> EventoObservado:
        evento = EventoPista(tipo, pista_id, label, 0.9, (0, 540, 100, 50))
        return EventoObservado(evento=evento, modelo_id="m1", numero_frame=100)

    def test_cruce_ok_no_se_duplica(self) -> None:
        """Un cruce TCOK → EstadoProducto.OK; ni el cruce repetido ni el baja reportan."""
        det = self.servicio._traducir(self._observado("cruce", 10, "TCOK"))
        self.assertIsNotNone(det)
        assert det is not None
        self.assertEqual(det.estado, EstadoProducto.OK)

        # La misma pista otra vez: descartada por ya reportada.
        self.assertIsNone(self.servicio._traducir(self._observado("cruce", 10, "TCOK")))

        # El baja posterior de la misma pista: ya reportada.
        self.assertIsNone(self.servicio._traducir(self._observado("baja", 10, "TCOK")))

    def test_cruce_quemado(self) -> None:
        """Un cruce TCQ → EstadoProducto.QUEMADO."""
        det = self.servicio._traducir(self._observado("cruce", 20, "TCQ"))
        self.assertIsNotNone(det)
        assert det is not None
        self.assertEqual(det.estado, EstadoProducto.QUEMADO)

    def test_cruce_sin_producto_no_reporta(self) -> None:
        """Un cruce con etiqueta que no resuelve producto no ocupa el dedupe."""
        # Sin catálogo sembrado, ningún prefijo resuelve producto.
        self.servicio._producto_por_nombre = {}
        self.assertIsNone(self.servicio._traducir(self._observado("cruce", 30, "TCOK")))
        # El dedupe quedó libre: un cruce posterior de la misma pista reporta.
        self.servicio._producto_por_nombre = {
            "tostada_comun": Producto(id="p-1", nombre="Tostada común", activo=True)
        }
        det = self.servicio._traducir(self._observado("cruce", 30, "TCOK"))
        self.assertIsNotNone(det)
        assert det is not None
        self.assertEqual(det.estado, EstadoProducto.OK)


if __name__ == "__main__":
    unittest.main()
