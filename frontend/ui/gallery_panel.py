"""Galería de vídeos: lista desplazable de archivos .mp4 locales."""

from PySide6.QtCore import Signal

from frontend.ui.components import Card, ListItem, ScrollableList
from frontend.ui.theme import SP_SM


class GalleryPanel(Card):
    """Lista los vídeos disponibles y emite el nombre del seleccionado.

    Signal
    ------
    video_selected(str): nombre del archivo que el operador pulsó.
    """

    video_selected = Signal(str)

    def __init__(self, parent=None):
        super().__init__(padding=SP_SM + 2, spacing=SP_SM, parent=parent)
        self.setMinimumHeight(150)

        self.set_title("Galería de vídeos")

        self._list = ScrollableList(
            "Sin vídeos",
            "Agrega archivos .mp4 a la carpeta de vídeos",
        )
        self.list_layout = self._list.list_layout
        self.body.addWidget(self._list, stretch=1)

        self._items = []

    # ------------------------------------------------------------------ api
    def set_videos(self, names):
        """Reemplaza el contenido de la galería por *names*."""
        self.clear()
        for name in names:
            self.add_video(name)
        if not names:
            self.show_empty()

    def add_video(self, name):
        """Agrega una entrada que, al pulsarse, emite ``video_selected``."""
        item = ListItem(name)
        item.setToolTip(f"Reproducir {name}")
        item.clicked.connect(
            lambda _checked=False, name=name: self.video_selected.emit(name)
        )
        self._list.add_widget(item)
        self._items.append(item)
        return item

    def clear(self):
        """Elimina todas las entradas y vuelve al estado vacío."""
        for item in self._items:
            self._list.remove_widget(item)
        self._items = []
        self.show_empty()

    def show_empty(self, title="Sin vídeos", hint="Agrega archivos .mp4 a la carpeta de vídeos"):
        """Muestra el estado vacío, con textos nuevos si se indican."""
        self._list.show_empty(title, hint)
