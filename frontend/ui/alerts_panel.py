"""Panel de historial de alertas con estado vacío explícito."""

import time

from PySide6.QtWidgets import QLabel

from frontend.ui.components import Card, ScrollableList
from frontend.ui.theme import SP_SM, set_dynamic_property

# Máximo de avisos conservados en pantalla. Al superarlo se descarta el más
# antiguo, para que el historial no crezca sin límite en sesiones largas. Solo
# afecta a lo mostrado: no hay nada persistido que se pierda.
MAX_ALERTS = 100


class AlertsPanel(Card):
    """Lista de alertas en orden cronológico inverso.

    :meth:`add_alert` antepone una entrada y oculta el estado vacío; el estado
    vacío reaparece cuando la lista se queda sin avisos. Se conservan como mucho
    ``MAX_ALERTS`` entradas.
    """

    def __init__(self, parent=None):
        super().__init__(padding=SP_SM + 2, spacing=SP_SM, parent=parent)
        self.setMinimumHeight(180)

        self.set_title("Historial de alertas")

        self._list = ScrollableList(
            "Sin alertas",
            "No se han detectado tostadas quemadas. Esperando...",
        )
        self.list_layout = self._list.list_layout
        self.body.addWidget(self._list, stretch=1)

        # Referencias a las entradas, de más antigua a más reciente.
        self._entries = []

    # ------------------------------------------------------------------ api
    def add_alert(self, message, tone="danger"):
        """Antepone una alerta con marca de tiempo al historial.

        ``tone`` admite ``danger`` (por defecto), ``info`` y ``warning``; el
        aspecto de cada uno está definido en ``frontend.ui.theme``.
        """
        entry = QLabel(f"[{_timestamp()}] {message}")
        entry.setObjectName("AlertItem")
        entry.setWordWrap(True)
        set_dynamic_property(entry, "tone", tone)
        self._list.add_widget(entry, prepend=True)
        self._entries.append(entry)
        self._trim_history()
        return entry

    # ------------------------------------------------------------- internals
    def _trim_history(self):
        """Descarta las alertas más antiguas mientras se supere MAX_ALERTS."""
        while len(self._entries) > MAX_ALERTS:
            self._list.remove_widget(self._entries.pop(0))


def _timestamp():
    """Hora actual en formato HH:MM:SS para la marca de cada aviso."""
    return time.strftime("%H:%M:%S")
