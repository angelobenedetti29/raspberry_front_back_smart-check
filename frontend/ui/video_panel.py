"""Superficie central de vídeo en vivo con estados explícitos."""

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QLabel, QSizePolicy

from frontend.ui.components import Card, SectionMeta, StatusPill
from frontend.ui.theme import SP_MD, set_dynamic_property


class VideoSurface(QLabel):
    """Destino de render cuyo tamaño no depende del pixmap mostrado.

    Un ``QLabel`` normal deriva ``sizeHint()``/``minimumSizeHint()`` del pixmap
    actual. Como :meth:`VideoPanel.show_image` escala el frame al tamaño del
    propio label, cada frame cambiaba la pista de tamaño, forzaba un relayout y
    volvía a escalar el siguiente frame al nuevo tamaño: un bucle que hacía
    vibrar la ventana durante la reproducción. Devolver pistas neutras (16:9)
    rompe el bucle sin dejar de ser un ``QLabel`` (``setPixmap``/``pixmap``
    siguen funcionando) ni tocar el diseño visual.
    """

    _NEUTRAL_HINT = QSize(640, 360)  # 16:9, por encima del suelo de 220 px.

    def sizeHint(self):
        return QSize(self._NEUTRAL_HINT)

    def minimumSizeHint(self):
        return QSize(0, 0)


class VideoPanel(Card):
    """Dibuja los frames de detección y comunica el estado de la captura.

    El destino de render es :attr:`video_label`, de modo que la aplicación sigue
    enviando frames con ``update_image`` mientras este panel decide cómo se ve la
    superficie. La máquina de estados es ``off`` → ``connecting`` → ``live``, con
    ``error`` y ``recovery`` como estados de fallo:

    - ``off``: no hay fuente activa ("Cámara apagada").
    - ``connecting``: se lanzó una fuente y se espera el primer frame.
    - ``live``: llegan frames de forma continua.
    - ``error``: falló la configuración de vídeo o la fuente.
    - ``recovery``: no se pudo detener el worker anterior y hace falta que el
      operador intervenga.

    Cada estado se refleja en el QSS con la propiedad dinámica ``state`` (ver
    ``QLabel#VideoSurface[state=...]`` en ``frontend.ui.theme``).
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

        self.video_label = VideoSurface()
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
        """Escala y muestra un frame entregado por el worker de detección."""
        pixmap = QPixmap.fromImage(qt_image).scaled(
            self.video_label.size(),
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
        self.video_label.setPixmap(pixmap)
        self._apply_state("live")
        self._set_status("on", "En vivo")

    def show_connecting(self, message="Conectando con la fuente de vídeo..."):
        """Pasa al estado ``connecting`` mientras se espera el primer frame."""
        self.video_label.clear()
        self.video_label.setText(message)
        self._apply_state("connecting")
        self._set_status("info", "Conectando")

    def show_off(self, message="Cámara apagada"):
        """Vuelve al estado ``off`` (sin fuente activa)."""
        self.video_label.clear()
        self.video_label.setText(message)
        self._apply_state("off")
        self._set_status("off", "Detenida")

    def show_error(self, message):
        """Muestra un fallo de vídeo o de fuente en el estado ``error``."""
        self.video_label.clear()
        self.video_label.setText(message)
        self._apply_state("error")
        self._set_status("danger", "Error")

    def show_recovery(self, message):
        """Muestra que hace falta intervención del operador (``recovery``)."""
        self.video_label.clear()
        self.video_label.setText(message)
        self._apply_state("recovery")
        self._set_status("warning", "Recuperación")

    def set_session_meta(self, text):
        """Actualiza el texto de contexto de la cabecera (fuente activa)."""
        self.session_meta.setText(text)

    # ------------------------------------------------------------- internals
    def _apply_state(self, state):
        """Aplica el estado, sin repolish si no cambió respecto al actual."""
        if state == self._state:
            return
        self._state = state
        set_dynamic_property(self.video_label, "state", state)

    def _set_status(self, tone, text):
        """Actualiza la píldora de estado de la cabecera si ya existe."""
        pill = getattr(self, "status_pill", None)
        if pill is not None:
            pill.set_status(tone, text)
