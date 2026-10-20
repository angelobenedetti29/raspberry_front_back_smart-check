"""Tests de la decodificación de salida YOLO compartida por los detectores.

No requieren modelo ni pesos: el tensor se arma a mano con la forma que exporta
Ultralytics, `(1, 4 + clases, N)`.
"""

from __future__ import annotations

import numpy as np
import pytest

from backend.inference.postprocesado import decodificar_salida


def _preds(*filas: tuple[float, ...]) -> np.ndarray:
    """Tensor `(1, 4 + clases, N)` con una columna por fila de `filas`."""
    return np.array([filas], dtype=np.float32).transpose(0, 2, 1)


def _decodificar(preds: np.ndarray, **cambios: object) -> list:
    """Llama a `decodificar_salida` con los parámetros del caso base."""
    parametros = dict(
        escala=1.0,
        pad_x=0,
        pad_y=0,
        tabla_umbrales=None,
        umbral_global=0.5,
        umbral_minimo=0.5,
        nms_threshold=0.4,
        etiqueta=lambda i: f"c{i}",
    )
    parametros.update(cambios)
    return decodificar_salida(preds, **parametros)  # type: ignore[arg-type]


def test_filtra_por_umbral_global_y_desescala_cajas() -> None:
    """La fila bajo el umbral se descarta y la caja vuelve a píxeles del frame."""
    preds = _preds(
        (100, 200, 40, 20, 0.9, 0.0),
        (500, 600, 40, 20, 0.1, 0.8),
        (300, 300, 40, 20, 0.1, 0.1),
    )

    resultados = _decodificar(preds)

    assert len(resultados) == 2
    assert [r.label for r in resultados] == ["c0", "c1"]
    assert resultados[0].bbox == (80, 190, 40, 20)
    assert resultados[0].confianza == pytest.approx(0.9)


def test_tabla_de_umbrales_por_clase() -> None:
    """La tabla por clase filtra una fila que el umbral global dejaría pasar."""
    preds = _preds(
        (100, 200, 40, 20, 0.9, 0.0),
        (500, 600, 40, 20, 0.1, 0.8),
    )

    resultados = _decodificar(preds, tabla_umbrales=np.array([0.95, 0.5], dtype=np.float32))

    assert [r.label for r in resultados] == ["c1"]


def test_desproyecta_el_letterbox() -> None:
    """Quita el relleno y divide por la escala uniforme del letterbox."""
    preds = _preds((100, 200, 40, 20, 0.9, 0.0))

    resultados = _decodificar(preds, escala=0.5, pad_x=10, pad_y=20)

    assert len(resultados) == 1
    assert resultados[0].bbox == (140, 340, 80, 40)


def test_nms_suprime_cajas_solapadas_de_la_misma_clase() -> None:
    """Dos cajas casi iguales quedan en una sola detección."""
    preds = _preds(
        (100, 200, 40, 20, 0.9, 0.0),
        (102, 202, 40, 20, 0.8, 0.0),
    )

    resultados = _decodificar(preds)

    assert len(resultados) == 1
    assert resultados[0].bbox == (80, 190, 40, 20)
