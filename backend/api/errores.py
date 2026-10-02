"""Errores del transporte HTTP compartido contra el backend Go.

No conocen dominio: cada cliente (`device`, `monitor`, `lote`) los traduce a su
propio tipo de error conservando `codigo` cuando corresponde.
"""

from __future__ import annotations


class ErrorApi(Exception):
    """Base de los fallos al hablar con el backend."""

    def __init__(self, detalle: str) -> None:
        super().__init__(detalle)
        self.detalle = detalle


class ErrorConexion(ErrorApi):
    """No hubo respuesta HTTP: red caída, DNS o timeout."""

    def __init__(self, detalle: str, *, metodo: str, url: str) -> None:
        super().__init__(detalle)
        self.metodo = metodo
        self.url = url


class ErrorHttp(ErrorApi):
    """El backend respondió con un status HTTP de error (>= 400)."""

    def __init__(self, codigo: int, detalle: str, *, metodo: str, url: str) -> None:
        super().__init__(detalle)
        self.codigo = codigo
        self.metodo = metodo
        self.url = url


class ErrorFormato(ErrorApi):
    """La respuesta no es JSON válido o no tiene la forma esperada."""

    def __init__(self, detalle: str, *, metodo: str = "", url: str = "") -> None:
        super().__init__(detalle)
        self.metodo = metodo
        self.url = url
