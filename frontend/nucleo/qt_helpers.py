"""Helpers de Qt compartidos por las secciones."""

from __future__ import annotations

from PySide6.QtWidgets import QLayout


def vaciar_layout(layout: QLayout) -> None:
    """Despega y destruye widgets y sub-layouts de un layout.

    `setParent(None)` los despega ya mismo; `deleteLater()` solo no alcanza
    cuando se reconstruye y se vuelve a medir en el acto.
    """
    while layout.count():
        elemento = layout.takeAt(0)
        if elemento is None:
            continue
        widget = elemento.widget()
        if widget is not None:
            widget.setParent(None)
            widget.deleteLater()
            continue
        sub = elemento.layout()
        if sub is not None:
            vaciar_layout(sub)
