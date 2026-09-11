"""Central live-video surface with explicit idle/loading/error states."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QLabel, QSizePolicy

from frontend.ui.components import Card, SectionMeta, StatusPill
from frontend.ui.theme import SP_MD, set_dynamic_property


class VideoPanel(Card):
    """Renders detection frames and communicates capture state.

    The render target is :attr:`video_label` so the application can keep
    feeding frames through ``update_image`` while this panel owns how the
    surface looks in every state.
    """

    def __init__(self, parent=None):
        super().__init__(padding=SP_MD, spacing=SP_MD, parent=parent)
        self.setMinimumWidth(380)

        self.set_title("Cámara en vivo")
        self.session_meta = SectionMeta("Sin fuente")
        self.add_header_widget(self.session_meta)
        self.add_header_stretch()

        self.status_pill = StatusPill("Detenida", tone="off")
        self.add_header_widget(self.status_pill)

        self.video_label = QLabel()
        self.video_label.setObjectName("VideoSurface")
        self.video_label.setAlignment(Qt.AlignCenter)
        self.video_label.setMinimumHeight(220)
        self.video_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.video_label.setText("Cámara apagada")
        set_dynamic_property(self.video_label, "state", "off")
        self.body.addWidget(self.video_label, stretch=1)

        self._state = "off"

    # ------------------------------------------------------------------ api
    def show_image(self, qt_image):
        """Scale and display a frame delivered by the detection worker."""
        pixmap = QPixmap.fromImage(qt_image).scaled(
            self.video_label.size(),
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
        self.video_label.setPixmap(pixmap)
        self._apply_state("live")
        self._set_status("on", "En vivo")

    def show_connecting(self, message="Conectando con la fuente de vídeo..."):
        self.video_label.clear()
        self.video_label.setText(message)
        self._apply_state("connecting")
        self._set_status("info", "Conectando")

    def show_off(self, message="Cámara apagada"):
        self.video_label.clear()
        self.video_label.setText(message)
        self._apply_state("off")
        self._set_status("off", "Detenida")

    def show_error(self, message):
        self.video_label.clear()
        self.video_label.setText(message)
        self._apply_state("error")
        self._set_status("danger", "Error")

    def show_recovery(self, message):
        self.video_label.clear()
        self.video_label.setText(message)
        self._apply_state("recovery")
        self._set_status("warning", "Recuperación")

    def set_session_meta(self, text):
        self.session_meta.setText(text)

    # ------------------------------------------------------------- internals
    def _apply_state(self, state):
        if state == self._state:
            return
        self._state = state
        set_dynamic_property(self.video_label, "state", state)

    def _set_status(self, tone, text):
        pill = getattr(self, "status_pill", None)
        if pill is not None:
            pill.set_status(tone, text)
