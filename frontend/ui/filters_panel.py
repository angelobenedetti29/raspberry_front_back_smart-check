"""Filtros de visibilidad de las detecciones (tostadas OK / quemadas)."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QPushButton

from frontend.ui.components import Card, SectionMeta
from frontend.ui.theme import SP_XS, set_dynamic_property


class FiltersPanel(Card):
    """Dos chips con estado que controlan qué etiquetas se dibujan.

    Las señales no llevan argumento: el panel solo avisa del clic, y es
    ``frontend/app.py`` quien alterna su estado y vuelve a llamar a
    :meth:`update_state`. Así el panel no guarda estado propio de filtros.

    Signals
    -------
    filter_ok_toggled: se pulsó el chip de visibilidad de tostadas OK.
    filter_burnt_toggled: se pulsó el chip de visibilidad de tostadas quemadas.
    """

    filter_ok_toggled = Signal()
    filter_burnt_toggled = Signal()

    def __init__(self, parent=None):
        super().__init__(spacing=SP_XS + 2, parent=parent)

        self.set_title("Filtro de detecciones")
        self.hint = SectionMeta(
            "Toca una opción para mostrar u ocultar etiquetas en el vídeo"
        )
        self.hint.setWordWrap(True)
        self.body.addWidget(self.hint)

        self.btn_filter_ok = QPushButton()
        self.btn_filter_ok.setObjectName("FilterChip")
        self.btn_filter_ok.setCursor(Qt.PointingHandCursor)
        self.btn_filter_ok.clicked.connect(lambda: self.filter_ok_toggled.emit())
        self.body.addWidget(self.btn_filter_ok)

        self.btn_filter_burnt = QPushButton()
        self.btn_filter_burnt.setObjectName("FilterChip")
        self.btn_filter_burnt.setCursor(Qt.PointingHandCursor)
        self.btn_filter_burnt.clicked.connect(
            lambda: self.filter_burnt_toggled.emit()
        )
        self.body.addWidget(self.btn_filter_burnt)

    # ------------------------------------------------------------------ api
    def update_state(self, show_ok, show_burnt):
        """Refleja en ambos chips el estado de filtros de la aplicación."""
        self._render_chip(self.btn_filter_ok, "Tostadas OK", show_ok)
        self._render_chip(self.btn_filter_burnt, "Tostadas quemadas", show_burnt)

    # ------------------------------------------------------------- internals
    def _render_chip(self, button, label, active):
        """Pinta el chip como visible u oculto según ``active``."""
        if active:
            button.setText(f"{label}: visibles")
            button.setToolTip(
                f"Clic para ocultar las etiquetas de {label.lower()} en el vídeo"
            )
        else:
            button.setText(f"{label}: ocultas")
            button.setToolTip(
                f"Clic para mostrar las etiquetas de {label.lower()} en el vídeo"
            )
        set_dynamic_property(button, "active", bool(active))
