"""Bus de señales Qt compartido entre el shell, las secciones y los adaptadores."""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal


class BusApp(QObject):
    """Canal único de comunicación desacoplada dentro de la interfaz."""

    estado_actualizado = Signal(object)
    navegacion_solicitada = Signal(str)
    mensaje = Signal(str)
