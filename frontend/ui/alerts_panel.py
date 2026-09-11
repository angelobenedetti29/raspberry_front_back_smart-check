"""Alerts history panel with an explicit empty state."""

import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QScrollArea, QVBoxLayout, QWidget

from frontend.ui.components import Card, EmptyState
from frontend.ui.theme import SP_SM, set_dynamic_property


class AlertsPanel(Card):
    """Reverse-chronological list of alert messages.

    :meth:`add_entry` prepends an entry and hides the empty state; the empty
    state returns whenever the list is cleared.
    """

    def __init__(self, parent=None):
        super().__init__(padding=SP_SM + 2, spacing=SP_SM, parent=parent)
        self.setMinimumHeight(180)

        self.set_title("Historial de alertas")

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QScrollArea.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self._content = QWidget()
        self.list_layout = QVBoxLayout(self._content)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(SP_SM)
        self.list_layout.setAlignment(Qt.AlignTop)

        self._empty = EmptyState(
            "Sin alertas",
            "No se han detectado tostadas quemadas. Esperando...",
        )
        self.list_layout.addWidget(self._empty)

        self._scroll.setWidget(self._content)
        self.body.addWidget(self._scroll, stretch=1)

        self._entries = []

    # ------------------------------------------------------------------ api
    def add_alert(self, message, tone="danger"):
        """Prepend an alert with a timestamp to the history."""
        self._empty.setVisible(False)
        entry = QLabel(f"[{time.strftime('%H:%M:%S')}] {message}")
        entry.setObjectName("AlertItem")
        entry.setWordWrap(True)
        set_dynamic_property(entry, "tone", tone)
        self.list_layout.insertWidget(0, entry)
        self._entries.append(entry)
        return entry
