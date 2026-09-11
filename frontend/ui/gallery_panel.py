"""Video gallery: scrollable list of local ``.mp4`` files."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QScrollArea, QVBoxLayout, QWidget

from frontend.ui.components import Card, EmptyState, ListItem
from frontend.ui.theme import SP_SM


class GalleryPanel(Card):
    """Lists available videos and emits the selected file name.

    Signal
    ------
    video_selected(str): the file name the operator clicked.
    """

    video_selected = Signal(str)

    def __init__(self, parent=None):
        super().__init__(padding=SP_SM + 2, spacing=SP_SM, parent=parent)
        self.setMinimumHeight(150)

        self.set_title("Galería de vídeos")

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
            "Sin vídeos",
            "Agrega archivos .mp4 a la carpeta de vídeos",
        )
        self.list_layout.addWidget(self._empty)

        self._scroll.setWidget(self._content)
        self.body.addWidget(self._scroll, stretch=1)

        self._items = []

    # ------------------------------------------------------------------ api
    def set_videos(self, names):
        """Replace the gallery contents with *names*."""
        self.clear()
        for name in names:
            self.add_video(name)
        if not names:
            self.show_empty()

    def add_video(self, name):
        self._hide_empty()
        item = ListItem(name)
        item.setToolTip(f"Reproducir {name}")
        item.clicked.connect(
            lambda _checked=False, name=name: self.video_selected.emit(name)
        )
        self.list_layout.addWidget(item)
        self._items.append(item)
        return item

    def clear(self):
        for item in self._items:
            item.setParent(None)
            item.deleteLater()
        self._items = []
        self.show_empty()

    def show_empty(self, title="Sin vídeos", hint="Agrega archivos .mp4 a la carpeta de vídeos"):
        self._empty.set_text(title, hint)
        self._empty.setVisible(True)

    # ------------------------------------------------------------- internals
    def _hide_empty(self):
        self._empty.setVisible(False)
