"""IoT device controls: toaster relay and alarm buzzer."""

from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

from PySide6.QtCore import Signal

from frontend.ui.components import ActionButton, Card, StatusPill
from frontend.ui.theme import SP_XS


class IoTPanel(Card):
    """Two device rows, each with a state pill and a toggle button.

    Signals
    -------
    toaster_toggled: the toaster relay button was clicked.
    alarm_toggled: the alarm buzzer button was clicked.
    """

    toaster_toggled = Signal()
    alarm_toggled = Signal()

    def __init__(self, parent=None):
        super().__init__(spacing=SP_XS + 2, parent=parent)

        self.set_title("Dispositivos IoT")

        self.toaster_lbl, self.toaster_pill, self.toaster_btn, toaster_row = self._build_row(
            "Relé tostadora"
        )
        self.toaster_btn.clicked.connect(lambda: self.toaster_toggled.emit())
        self.body.addWidget(toaster_row)

        self.alarm_lbl, self.alarm_pill, self.alarm_btn, alarm_row = self._build_row(
            "Buzzer alarma"
        )
        self.alarm_btn.clicked.connect(lambda: self.alarm_toggled.emit())
        self.body.addWidget(alarm_row)

    # ------------------------------------------------------------------ api
    def update_state(self, toaster_on, alarm_on):
        """Reflect the current device states."""
        self._sync_row(self.toaster_pill, self.toaster_btn, toaster_on)
        self._sync_row(self.alarm_pill, self.alarm_btn, alarm_on)

    # ------------------------------------------------------------- internals
    def _build_row(self, title):
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SP_XS + 2)

        label = QLabel(title)
        layout.addWidget(label)
        layout.addStretch(1)

        pill = StatusPill("Apagado", tone="off")
        layout.addWidget(pill)

        button = ActionButton("Encender", variant="primary")
        layout.addWidget(button)
        return label, pill, button, row

    def _sync_row(self, pill, button, is_on):
        if is_on:
            pill.set_status("on", "Encendido")
            button.setText("Apagar")
            button.set_variant("danger")
        else:
            pill.set_status("off", "Apagado")
            button.setText("Encender")
            button.set_variant("primary")
