"""Tests del adaptador de streaming del frontend (texto de la última detección)."""

from __future__ import annotations

from backend.etiquetas import ResolutorEtiquetas
from backend.inference import ResultadoDeteccion
from frontend.nucleo.adaptador import _texto_deteccion


def test_texto_deteccion_usa_mapa_configurable() -> None:
    resolutor = ResolutorEtiquetas({"chaq": ("fajitas", "quemado")})
    deteccion = ResultadoDeteccion(label="CHAQ", confianza=0.91, bbox=(0, 0, 10, 10))
    assert _texto_deteccion(deteccion, resolutor) == "fajitas · quemado · 0.91"
