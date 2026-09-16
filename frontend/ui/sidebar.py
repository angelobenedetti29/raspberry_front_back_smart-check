"""Barra lateral de navegación: marca, cámara, selector de modelo y estado."""

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
    """Navegación funcional de la cámara en vivo más el selector de modelo.

    Si no se le pasan etiquetas, las toma del catálogo ``MODEL_CATALOG``. Los
    botones de navegación quedan expuestos en :attr:`nav_buttons` para que la
    aplicación pueda consultarlos o reflejar el estado activo.

    Signals
    -------
    camera_toggled(bool): cambió el botón de cámara (encendido o apagado).
    model_changed(int): nueva posición del catálogo elegida en el selector.
    """

    camera_toggled = Signal(bool)
    model_changed = Signal(int)

    def __init__(self, model_labels=None, current_index=0, parent=None):
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self.setMinimumWidth(SIDEBAR_MIN_WIDTH)
        self.setMaximumWidth(SIDEBAR_MAX_WIDTH)

        labels = list(model_labels) if model_labels is not None else [
            entry.label for entry in MODEL_CATALOG
        ]

        layout = QVBoxLayout(self)
        layout.setContentsMargins(SP_SM + 4, SP_SM + 4, SP_SM + 4, SP_SM + 4)
        layout.setSpacing(SP_SM)

        layout.addWidget(self._build_brand())
        layout.addSpacing(SP_XS)

        layout.addWidget(self._group_label("OPERACIÓN"))
        self.camera_btn = ActionButton("Cámara en vivo")
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
        """Refleja el estado de la cámara sin reemitir ``camera_toggled``."""
        self.camera_btn.setChecked(checked)

    def set_detector_pill(self, text, tone="info"):
        """Actualiza texto y tono de la píldora del estado del detector."""
        self.detector_pill.setText(text)
        self.detector_pill.set_tone(tone)

    # ------------------------------------------------------------- internals
    def _build_brand(self):
        """Construye el bloque de marca: título grande y subtítulo."""
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
        """Crea una etiqueta de grupo (cabecera de sección de la barra)."""
        label = QLabel(text)
        label.setObjectName("SidebarGroup")
        return label
