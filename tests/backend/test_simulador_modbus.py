"""El mapa de registros del simulador debe espejar el del driver real."""

from __future__ import annotations

from backend.control import modbus
from tools import simulador_modbus


def test_mapa_modbus_coincide_con_el_simulador() -> None:
    assert modbus.DIRECCION_BASE_SENSORES == simulador_modbus.DIRECCION_BASE_SENSORES
    assert modbus.CANTIDAD_REGS_SENSORES == simulador_modbus.CANTIDAD_REGS_SENSORES
    assert modbus._OFFSET_SENSORES == simulador_modbus.OFFSET_SENSORES
    assert modbus.DIRECCION_TEMP1_OBJETIVO == simulador_modbus.DIRECCION_TEMP1_OBJETIVO
    assert modbus.DIRECCION_VEL_OBJETIVO == simulador_modbus.DIRECCION_VEL_OBJETIVO
