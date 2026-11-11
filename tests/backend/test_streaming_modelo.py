"""Selección del modelo inicial según el tipo de dispositivo."""

from __future__ import annotations

from backend.config import load_config
from backend.device.tipos import TipoDispositivo
from backend.streaming import StreamingService


def test_entrada_carga_modelo_de_entrada() -> None:
    servicio = StreamingService(load_config(), tipo_provider=lambda: TipoDispositivo.ENTRADA_HORNO)
    assert servicio._registro.instantanea().modelo_id == "entrada-horno-v1"


def test_salida_carga_modelo_de_salida() -> None:
    servicio = StreamingService(load_config(), tipo_provider=lambda: TipoDispositivo.SALIDA_HORNO)
    assert servicio._registro.instantanea().modelo_id == "salida-horno-v4"


def test_sin_tipo_usa_fallback() -> None:
    config = load_config()
    servicio = StreamingService(config)
    assert servicio._registro.instantanea().modelo_id == config.models.default_model_id
