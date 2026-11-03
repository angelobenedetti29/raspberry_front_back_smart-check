"""Tests del lector Modbus: lee las consignas de las direcciones configuradas."""

from __future__ import annotations

from backend.config import ModbusDriverConfig
from backend.control.sensores import (
    LectorSensoresModbus,
    crear_lector,
)


class _ClienteFalso:
    """Registra las direcciones leídas y devuelve ceros (float 42.0)."""

    def __init__(self) -> None:
        self.lecturas: list[tuple[int, int]] = []

    def leer_bloque(self, direccion: int, cantidad: int) -> list[int]:
        self.lecturas.append((direccion, cantidad))
        return [0] * cantidad

    def convertir_float32(self, regs: list[int]) -> float:
        return 42.0


def test_lector_usa_las_direcciones_de_consigna_pasadas() -> None:
    cliente = _ClienteFalso()
    lector = LectorSensoresModbus(cliente, consigna_reg_temp1=110, consigna_reg_vel=114)

    lectura = lector.leer()

    assert (110, 2) in cliente.lecturas
    assert (114, 2) in cliente.lecturas
    assert lectura.temp_objetivo == 42.0
    assert lectura.vel_objetivo == 42.0


def test_lector_default_usa_100_y_104() -> None:
    cliente = _ClienteFalso()

    LectorSensoresModbus(cliente).leer()

    assert (100, 2) in cliente.lecturas
    assert (104, 2) in cliente.lecturas


def test_crear_lector_inyecta_las_direcciones(monkeypatch) -> None:
    creados: list[ModbusDriverConfig] = []

    class _ClienteFalsoConstructor:
        def __init__(self, cfg: ModbusDriverConfig) -> None:
            creados.append(cfg)

    monkeypatch.setattr("backend.control.sensores.ClienteModbus", _ClienteFalsoConstructor)
    cfg = ModbusDriverConfig(driver="modbus_tcp", host="127.0.0.1", port=5020)

    lector = crear_lector(cfg, 110, 114)

    assert creados == [cfg]
    assert lector._consigna_reg_temp1 == 110
    assert lector._consigna_reg_vel == 114
