"""Left navigation sidebar: brand, camera toggle, model selector and status."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from frontend.config import MODEL_CATALOG
from frontend.ui.components import ActionButton, StatusPill
from frontend.ui.theme import (
    SIDEBAR_MAX_WIDTH,
    SIDEBAR_MIN_WIDTH,
    SP_SM,
    SP_XS,
)


class Sidebar(QFrame):
    """Functional navigation for the live camera plus the model selector.

    Signals
    -------
    camera_toggled(bool): emitted when the camera navigation button changes.
    model_changed(int): emitted with the new catalog index.
    """

    camera_toggled = Signal(bool)
    model_changed = Signal(int)

    def __init__(self, model_labels=None, current_index=0, parent=None):
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self.setMinimumWidth(SIDEBAR_MIN_WIDTH)
        self.setMaximumWidth(SIDEBAR_MAX_WIDTH)

        labels = list(model_labels) if model_labels is not None else [
            entry[0] for entry in MODEL_CATALOG
        ]

        layout = QVBoxLayout(self)
        layout.setContentsMargins(SP_SM + 4, SP_SM + 4, SP_SM + 4, SP_SM + 4)
        layout.setSpacing(SP_SM)

        layout.addWidget(self._build_brand())
        layout.addSpacing(SP_XS)

        layout.addWidget(self._group_label("OPERACIÓN"))
        self.camera_btn = ActionButton("Cámara en vivo", variant="nav")
        self.camera_btn.setCheckable(True)
        self.camera_btn.setToolTip("Encender o apagar la cámara en vivo")
        self.camera_btn.clicked.connect(self.camera_toggled)
        self.nav_buttons = [self.camera_btn]
        layout.addWidget(self.camera_btn)

        layout.addSpacing(SP_XS)
        layout.addWidget(self._group_label("MODELO ACTIVO"))

        self.model_selector = QComboBox()
        for label in labels:
            self.model_selector.addItem(label)
        self.model_selector.setCurrentIndex(max(0, min(current_index, len(labels) - 1)))
        self.model_selector.setToolTip("Selecciona el modelo de detección")
        self.model_selector.currentIndexChanged.connect(self.model_changed)
        layout.addWidget(self.model_selector)

        layout.addStretch(1)

        footer = QWidget(self)
        footer_layout = QVBoxLayout(footer)
        footer_layout.setContentsMargins(0, 0, 0, 0)
        footer_layout.setSpacing(SP_XS)
        footer_layout.addWidget(self._group_label("ESTADO DEL DETECTOR"))

        self.detector_pill = StatusPill("Inicializando", tone="info")
        self.detector_pill.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        footer_layout.addWidget(self.detector_pill)
        layout.addWidget(footer)

    # ------------------------------------------------------------------ api
    def set_camera_checked(self, checked):
        """Reflect the camera state without re-emitting ``camera_toggled``."""
        self.camera_btn.setChecked(checked)

    def set_detector_pill(self, text, tone="info"):
        self.detector_pill.setText(text)
        self.detector_pill.set_tone(tone)

    # ------------------------------------------------------------- internals
    def _build_brand(self):
        brand = QWidget(self)
        box = QVBoxLayout(brand)
        box.setContentsMargins(2, 0, 2, 0)
        box.setSpacing(SP_XS)

        title = QLabel("FACTORY CONTROL")
        title.setObjectName("Brand")
        title.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        subtitle = QLabel("SISTEMA DE INSPECCIÓN")
        subtitle.setObjectName("BrandSub")

        box.addWidget(title)
        box.addWidget(subtitle)
        return brand

    def _group_label(self, text):
        label = QLabel(text)
        label.setObjectName("SidebarGroup")
        return label
