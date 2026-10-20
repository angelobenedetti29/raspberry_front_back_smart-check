"""Tests del adaptador de configuración: opciones de los combos de driver."""

from __future__ import annotations

from frontend.nucleo.adaptador_config import AdaptadorConfig


def test_opciones_de_driver_incluyen_simulado() -> None:
    """El combo ofrece el driver `simulado` que el loader y config.json usan.

    Sin esta opción, ``findData("simulado")`` devuelve -1 y guardar desde la
    UI pisa el driver con ``stub`` sin que el operador lo haya cambiado.
    """
    for clave in (
        "control.sensores.driver",
        "control.actuadores.driver",
    ):
        valores = {valor for _etiqueta, valor in AdaptadorConfig._opciones(clave, {})}
        assert valores == {"stub", "simulado", "modbus_rtu", "modbus_tcp"}
