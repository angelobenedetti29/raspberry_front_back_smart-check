"""Puerto de configuración hacia las secciones de la interfaz.

La sección Configuración no conoce el archivo `config.json` ni el backend: pide
una lista de campos editables ya resueltos (`CampoConfigUI`) y devuelve valores
para guardar. El adaptador (`adaptador_config.py`) es quien traduce eso al
backend real.

El orden de los grupos se deriva de `campos()` preservando la primera aparición:
`tuple(dict.fromkeys(c.grupo for c in campos))`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Protocol, runtime_checkable


class TipoCampo(str, Enum):
    """Tipo de dato de un campo de configuración, para elegir el editor."""

    TEXTO = "texto"
    ENTERO = "entero"
    DECIMAL = "decimal"
    BOOLEANO = "booleano"
    OPCION = "opcion"


@dataclass(frozen=True, slots=True)
class CampoConfigUI:
    """Un campo editable de la configuración, ya resuelto para la interfaz.

    `clave` es la ruta punteada dentro del JSON (ej. `"stream.capture.fps"`).
    `grupo` agrupa campos en la pantalla. `opciones` sólo aplica a `OPCION` y
    trae pares `(etiqueta, valor)`. Los límites son orientativos: el backend
    vuelve a validar al guardar.
    """

    clave: str
    grupo: str
    etiqueta: str
    tipo: TipoCampo
    valor: object
    opciones: tuple[tuple[str, object], ...] = ()
    ayuda: str = ""
    minimo: float | None = None
    maximo: float | None = None
    decimales: int = 0
    paso: float = 1.0
    # Condición `(clave, valor)` que otro campo debe cumplir para mostrarse;
    # None = siempre visible.
    visible_si: tuple[str, object] | None = None


@dataclass(frozen=True, slots=True)
class ResultadoGuardadoUI:
    """Resultado de un intento de guardado, listo para mostrar al operador."""

    ok: bool
    mensaje: str


@runtime_checkable
class ControladorConfig(Protocol):
    """Lo que la sección Configuración le pide al archivo de configuración."""

    def campos(self) -> tuple[CampoConfigUI, ...]:
        """Devuelve los campos editables con su valor actual en disco."""
        ...

    def guardar(self, valores: Mapping[str, object]) -> ResultadoGuardadoUI:
        """Valida y guarda los valores; nunca levanta, informa el resultado."""
        ...
