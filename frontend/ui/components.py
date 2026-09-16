"""Widgets reutilizables que componen la interfaz de Factory Control.

Todos los componentes de este módulo son de presentación pura: cada uno es dueño
de su aspecto y expone una API mínima. Los paneles de ``frontend.ui`` los
componen y ``frontend/app.py`` cablea sus señales con el comportamiento.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from frontend.ui.theme import (
    SP_MD,
    SP_SM,
    refresh_style,
    set_dynamic_property,
)


class SectionTitle(QLabel):
    """Encabezado corto que corona una tarjeta."""

    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.setObjectName("SectionTitle")


class SectionMeta(QLabel):
    """Texto secundario y atenuado que acompaña a un ``SectionTitle``."""

    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.setObjectName("SectionMeta")


class Card(QFrame):
    """Superficie consistente con cabecera opcional y un layout de cuerpo.

    La cabecera se rellena con :meth:`add_header_widget` y el contenido va en
    :attr:`body`. La fila de cabecera permanece oculta mientras esté vacía, para
    que las tarjetas simples no desperdicien altura.
    """

    def __init__(self, padding=SP_MD, spacing=SP_MD, parent=None):
        super().__init__(parent)
        self.setObjectName("Card")

        self._root = QVBoxLayout(self)
        self._root.setContentsMargins(padding, padding, padding, padding)
        self._root.setSpacing(spacing)

        self.header = QHBoxLayout()
        self.header.setContentsMargins(0, 0, 0, 0)
        self.header.setSpacing(SP_SM)
        self._header_widget = QWidget(self)
        self._header_widget.setLayout(self.header)
        self._header_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._root.addWidget(self._header_widget)

        self.body = QVBoxLayout()
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(spacing)
        self._root.addLayout(self.body)

    def add_header_widget(self, widget, stretch=0):
        """Agrega un widget a la fila de cabecera y la hace visible."""
        self.header.addWidget(widget, stretch)
        self._header_widget.setVisible(True)

    def add_header_stretch(self):
        """Empuja hacia la izquierda lo ya agregado a la cabecera."""
        self.header.addStretch(1)

    def set_title(self, text):
        """Crea o actualiza el ``SectionTitle`` de la cabecera y lo devuelve."""
        if getattr(self, "_title", None) is None:
            self._title = SectionTitle(text)
            self.header.insertWidget(0, self._title)
        else:
            self._title.setText(text)
        self._header_widget.setVisible(True)
        return self._title


class StatusPill(QLabel):
    """Indicador de estado compacto y redondeado.

    Los tonos válidos son ``on`` / ``off`` / ``info`` / ``warning`` / ``danger``
    y mapean a la paleta semántica definida en ``frontend.ui.theme``.
    """

    def __init__(self, text="", tone="off", parent=None):
        super().__init__(text, parent)
        self.setObjectName("StatusPill")
        self.setAlignment(Qt.AlignCenter)
        self.set_tone(tone)

    def set_tone(self, tone):
        """Cambia el tono semántico del indicador."""
        set_dynamic_property(self, "tone", tone)

    def set_status(self, tone, text=None):
        """Actualiza tono y texto, repintando solo si algo cambió de verdad."""
        tone_changed = self.property("tone") != tone
        if text is None:
            if tone_changed:
                self.set_tone(tone)
            return
        text_changed = self.text() != text
        if not text_changed and not tone_changed:
            return
        if text_changed:
            self.setText(text)
        if tone_changed:
            self.set_tone(tone)


class ActionButton(QPushButton):
    """Botón de acción con la variante de navegación del sistema de diseño."""

    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.setCursor(Qt.PointingHandCursor)
        set_dynamic_property(self, "variant", "nav")


class ListItem(QPushButton):
    """Fila-botón que usa la galería de vídeos."""

    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.setObjectName("ListItem")
        self.setCursor(Qt.PointingHandCursor)


class EmptyState(QWidget):
    """Marcador centrado para listas y tarjetas sin contenido."""

    def __init__(self, title="", hint="", parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(SP_SM, SP_MD, SP_SM, SP_MD)
        layout.setSpacing(4)
        layout.addStretch(1)

        self.title_label = QLabel(title)
        self.title_label.setObjectName("EmptyTitle")
        self.title_label.setAlignment(Qt.AlignCenter)
        self.title_label.setWordWrap(True)

        self.hint_label = QLabel(hint)
        self.hint_label.setObjectName("EmptyHint")
        self.hint_label.setAlignment(Qt.AlignCenter)
        self.hint_label.setWordWrap(True)
        self.hint_label.setVisible(bool(hint))

        layout.addWidget(self.title_label)
        layout.addWidget(self.hint_label)
        layout.addStretch(1)

    def set_text(self, title, hint=""):
        """Reemplaza el texto; oculta la pista si viene vacía."""
        self.title_label.setText(title)
        self.hint_label.setText(hint)
        self.hint_label.setVisible(bool(hint))
        refresh_style(self)


class ScrollableList(QWidget):
    """Lista vertical desplazable con estado vacío incorporado.

    Reúne el patrón que comparten el historial de alertas y la galería de
    vídeos: un ``QScrollArea`` sobre un contenedor con un ``QVBoxLayout``
    alineado arriba, más un ``EmptyState`` que se ve mientras no haya elementos.

    Las entradas se agregan con :meth:`add_widget` (al final o anteponiendo) y se
    quitan con :meth:`remove_widget`. ``list_layout`` queda expuesto para quien
    necesite recorrer los widgets ya insertados.
    """

    def __init__(self, empty_title="", empty_hint="", spacing=SP_SM, parent=None):
        super().__init__(parent)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QScrollArea.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self._content = QWidget()
        self.list_layout = QVBoxLayout(self._content)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(spacing)
        self.list_layout.setAlignment(Qt.AlignTop)

        self.empty = EmptyState(empty_title, empty_hint)
        self.list_layout.addWidget(self.empty)

        self._scroll.setWidget(self._content)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self._scroll)

    # ------------------------------------------------------------------ api
    def add_widget(self, widget, prepend=False):
        """Inserta un widget en la lista y oculta el estado vacío."""
        self.hide_empty()
        if prepend:
            self.list_layout.insertWidget(0, widget)
        else:
            self.list_layout.addWidget(widget)
        return widget

    def remove_widget(self, widget):
        """Saca un widget de la lista y lo marca para destruir."""
        self.list_layout.removeWidget(widget)
        widget.deleteLater()

    def show_empty(self, title=None, hint=""):
        """Muestra el estado vacío, con textos nuevos si se indican."""
        if title is not None:
            self.empty.set_text(title, hint)
        self.empty.setVisible(True)

    def hide_empty(self):
        """Oculta el estado vacío porque ya hay elementos que mostrar."""
        self.empty.setVisible(False)
