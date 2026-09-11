"""Small reusable widgets that make up the Factory Control UI.

Every component here is presentation-only: it owns its look and exposes a
tiny API.  Panels compose these components and the application wires the
signals to behavior.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
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
    """Small uppercase-feeling heading used at the top of a card."""

    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.setObjectName("SectionTitle")


class SectionMeta(QLabel):
    """Muted secondary text next to a section title."""

    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.setObjectName("SectionMeta")


class Card(QFrame):
    """Consistent surface with an optional header row and a body layout.

    ``add_header_widget`` places widgets on the header row; content goes into
    :attr:`body`.  The header row is hidden while empty so simple cards get no
    wasted vertical space.
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
        self.header.addWidget(widget, stretch)
        self._header_widget.setVisible(True)

    def add_header_stretch(self):
        self.header.addStretch(1)

    def set_title(self, text):
        if getattr(self, "_title", None) is None:
            self._title = SectionTitle(text)
            self.header.insertWidget(0, self._title)
        else:
            self._title.setText(text)
        self._header_widget.setVisible(True)
        return self._title


class StatusPill(QLabel):
    """Compact rounded status indicator.

    Tones map to the semantic palette: ``on`` / ``off`` / ``info`` /
    ``warning`` / ``danger``.
    """

    def __init__(self, text="", tone="off", parent=None):
        super().__init__(text, parent)
        self.setObjectName("StatusPill")
        self.setAlignment(Qt.AlignCenter)
        self.set_tone(tone)

    def set_tone(self, tone):
        set_dynamic_property(self, "tone", tone)

    def set_status(self, tone, text=None):
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
    """Push button with a named variant from the design system.

    Variants: ``primary``, ``secondary`` (default), ``danger``, ``nav``.
    """

    def __init__(self, text="", variant="secondary", parent=None):
        super().__init__(text, parent)
        self.setCursor(Qt.PointingHandCursor)
        self.set_variant(variant)

    def set_variant(self, variant):
        set_dynamic_property(self, "variant", variant)


class ListItem(QPushButton):
    """Row button used by the video gallery."""

    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.setObjectName("ListItem")
        self.setCursor(Qt.PointingHandCursor)


class EmptyState(QWidget):
    """Centered empty / informational placeholder for lists and cards."""

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
        self.title_label.setText(title)
        self.hint_label.setText(hint)
        self.hint_label.setVisible(bool(hint))
        refresh_style(self)
