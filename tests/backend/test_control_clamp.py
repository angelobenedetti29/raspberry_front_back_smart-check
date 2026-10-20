"""Tests del clamp defensivo de setpoints del ControlService.

`_clampear_temp` y `_clampear_vel` son estáticos: no hace falta instanciar el
servicio ni levantar hilos para verificar el cinturón de seguridad.
"""

from __future__ import annotations

from backend.control import ControlService
from backend.control.tipos import LimitesAplicacion

_LIMITES = LimitesAplicacion(
    delta_temp_max_c=10.0,
    delta_vel_max=0.05,
    temp_min=0.0,
    temp_max=500.0,
    vel_min=0.0,
    vel_max=5.0,
)


def test_temp_dentro_del_delta_pasa_igual() -> None:
    assert ControlService._clampear_temp(105.0, 100.0, _LIMITES) == 105.0


def test_temp_salto_arriba_se_clampea_al_actual_mas_delta() -> None:
    assert ControlService._clampear_temp(150.0, 100.0, _LIMITES) == 110.0


def test_temp_salto_abajo_se_clampea_al_actual_menos_delta() -> None:
    assert ControlService._clampear_temp(50.0, 100.0, _LIMITES) == 90.0


def test_temp_final_se_clampea_al_maximo_del_producto() -> None:
    # 498 + 10 = 508, pero el rango seguro del producto termina en 500.
    assert ControlService._clampear_temp(600.0, 498.0, _LIMITES) == 500.0


def test_temp_final_se_clampea_al_minimo_del_producto() -> None:
    assert ControlService._clampear_temp(-50.0, 3.0, _LIMITES) == 0.0


def test_vel_dentro_del_delta_pasa_igual() -> None:
    assert ControlService._clampear_vel(1.02, 1.0, _LIMITES) == 1.02


def test_vel_salto_arriba_se_clampea_al_actual_mas_delta() -> None:
    assert ControlService._clampear_vel(2.0, 1.0, _LIMITES) == 1.05


def test_vel_salto_abajo_se_clampea_al_actual_menos_delta() -> None:
    assert ControlService._clampear_vel(0.0, 1.0, _LIMITES) == 0.95


def test_vel_final_se_clampea_al_maximo_del_producto() -> None:
    assert ControlService._clampear_vel(10.0, 4.99, _LIMITES) == 5.0
