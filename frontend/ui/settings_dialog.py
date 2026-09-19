"""Editor modal de la configuración global (``config.json``).

``SettingsDialog`` presenta la configuración vigente agrupada en tarjetas y
permite editarla con la aplicación en marcha. El diálogo es de presentación y
validación: recibe un ``AppConfig`` (snapshot fresco) y, al aceptar,
:meth:`result_config` devuelve una copia modificada. No lee ni escribe
``config.json``; de eso se encarga ``frontend.app``.

``validate_settings`` es una función pura (sin Qt, sin E/S) y es la misma que
usa ``accept()`` para impedir el cierre con datos inválidos. Al ser pura, se
puede probar sin instanciar el diálogo.

Los valores por defecto y los textos de ayuda salen del esquema
``smartcheck_config``; el aspecto usa los tokens y componentes de
``frontend.ui``.
"""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from smartcheck_config import (
    AppConfig,
    CaptureSettings,
    InferenceSettings,
    ModelEntry,
    PublisherSettings,
    ReconnectSettings,
    StorageSettings,
    StreamSettings,
)
from frontend.ui.components import ActionButton, Card
from frontend.ui.theme import SP_MD, SP_SM, SP_XS

__all__ = ["SettingsDialog", "validate_settings"]

# Opciones admitidas por el esquema; se ofrecen como listas cerradas para que
# los valores válidos sean los únicos seleccionables.
FPS_OPTIONS = (20, 30)
GOP_OPTIONS = (1, 2)
OUTPUT_URL_SCHEMES = ("rtsp", "http", "https")


# ---------------------------------------------------------------------------
# Validación pura
# ---------------------------------------------------------------------------
def validate_settings(config: AppConfig) -> list[str]:
    """Valida una configuración antes de guardarla.

    Es pura: no usa Qt ni toca el disco, así que se puede llamar desde los
    tests sin instanciar el diálogo. Devuelve una lista de mensajes de error en
    español (vacía si todo está bien).

    No comprueba que los archivos existan en disco: eso sería E/S y haría la
    validación dependiente del entorno. En su lugar verifica que las rutas del
    catálogo estén presentes, que los identificadores sean únicos y que las
    referencias entre entradas existan.
    """
    errors: list[str] = []
    capture = config.stream.capture
    publisher = config.stream.publisher
    inference = config.stream.inference
    reconnect = config.stream.reconnect

    if capture.width % 2 != 0 or capture.height % 2 != 0:
        errors.append("El ancho y el alto deben ser pares (compatibilidad yuv420p).")
    if capture.fps not in FPS_OPTIONS:
        errors.append("Los FPS deben ser 20 o 30.")
    if not _has_output_scheme(publisher.output_url):
        errors.append("La URL de salida debe comenzar con rtsp:// o http://.")
    if not 1 <= config.api.port <= 65535:
        errors.append("El puerto de la API debe estar entre 1 y 65535.")
    if not 0.0 <= inference.confidence_threshold <= 1.0:
        errors.append("El umbral de confianza debe estar entre 0 y 1.")
    if reconnect.max_seconds < reconnect.initial_seconds:
        errors.append(
            "La espera máxima de reconexión no puede ser menor que la inicial."
        )

    catalog = config.models.catalog
    if not catalog:
        errors.append("El catálogo de modelos no puede estar vacío.")

    seen_ids: set[str] = set()
    for index, entry in enumerate(catalog, start=1):
        if not entry.model_id.strip():
            errors.append(f"La entrada {index} del catálogo no tiene identificador.")
        elif entry.model_id in seen_ids:
            errors.append(
                f"El identificador '{entry.model_id}' está repetido en el catálogo."
            )
        seen_ids.add(entry.model_id)
        if not entry.label.strip():
            errors.append(f"La entrada {index} del catálogo no tiene etiqueta.")
        if not entry.model_path.strip():
            errors.append(f"La entrada {index} del catálogo no tiene ruta de modelo.")
        if not entry.names_path.strip():
            errors.append(f"La entrada {index} del catálogo no tiene ruta de etiquetas.")

    if config.models.default_model_id not in seen_ids:
        errors.append("El modelo por defecto debe existir en el catálogo.")
    if config.models.npu_model_id not in seen_ids:
        errors.append("El modelo de NPU debe existir en el catálogo.")

    if inference.model_path is not None and not str(inference.model_path).strip():
        errors.append("La ruta de modelo de inferencia no puede estar vacía.")
    if inference.labels_path is not None and not str(inference.labels_path).strip():
        errors.append("La ruta de etiquetas de inferencia no puede estar vacía.")

    return errors


def _has_output_scheme(url: str) -> bool:
    """Indica si ``url`` usa un esquema admitido para ``output_url``."""
    if not url or "://" not in url:
        return False
    return url.split("://", 1)[0].lower() in OUTPUT_URL_SCHEMES


# ---------------------------------------------------------------------------
# Constructores de widgets
# ---------------------------------------------------------------------------
def _hint(text: str) -> QLabel:
    """Texto de ayuda breve y atenuado."""
    label = QLabel(text)
    label.setObjectName("FieldHint")
    label.setWordWrap(True)
    return label


def _line_edit(value="", placeholder="") -> QLineEdit:
    edit = QLineEdit(str(value or ""))
    if placeholder:
        edit.setPlaceholderText(placeholder)
    return edit


def _spin(value, minimum, maximum, step=1) -> QSpinBox:
    box = QSpinBox()
    box.setRange(int(minimum), int(maximum))
    box.setSingleStep(int(step))
    box.setValue(int(value))
    # Sin flechas nativas: el tema oscuro no define su aspecto y quedan fuera
    # de lugar. El valor se edita escribiendo o con la rueda del mouse.
    box.setButtonSymbols(QAbstractSpinBox.NoButtons)
    return box


def _double_spin(value, minimum, maximum, step=1.0, decimals=2) -> QDoubleSpinBox:
    box = QDoubleSpinBox()
    box.setRange(float(minimum), float(maximum))
    box.setSingleStep(float(step))
    box.setDecimals(int(decimals))
    box.setValue(float(value))
    box.setButtonSymbols(QAbstractSpinBox.NoButtons)
    return box


def _combo(options, value) -> QComboBox:
    combo = QComboBox()
    for option in options:
        combo.addItem(str(option), option)
    index = combo.findData(value)
    combo.setCurrentIndex(index if index >= 0 else 0)
    return combo


def _checkbox(text, checked) -> QCheckBox:
    box = QCheckBox(text)
    box.setChecked(bool(checked))
    return box


def _make_card(title, hint=None):
    """Crea una ``Card`` con su título, ayuda y un ``QFormLayout`` listo."""
    card = Card()
    card.set_title(title)
    if hint:
        card.body.addWidget(_hint(hint))

    form = QFormLayout()
    form.setContentsMargins(0, 0, 0, 0)
    form.setHorizontalSpacing(SP_MD)
    form.setVerticalSpacing(SP_SM)
    form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
    card.body.addLayout(form)
    return card, form


def _row(form, label, widget, help_text=None):
    """Agrega una fila etiqueta/widget y adjunta la ayuda como tooltip."""
    if help_text:
        widget.setToolTip(help_text)
    form.addRow(label, widget)
    return widget


# ---------------------------------------------------------------------------
# Diálogo
# ---------------------------------------------------------------------------
class SettingsDialog(QDialog):
    """Diálogo modal para ver y editar la configuración vigente.

    Se abre con un snapshot ``AppConfig`` (leído por el llamador) y nunca hace
    E/S: al aceptar, :meth:`result_config` devuelve una copia modificada con
    ``dataclasses.replace``. Si :func:`validate_settings` reporta errores,
    :meth:`accept` los muestra y no cierra.
    """

    def __init__(self, config: AppConfig, parent=None):
        super().__init__(parent)
        self.setObjectName("SettingsDialog")
        self.setWindowTitle("Configuración del sistema")
        self.setModal(True)
        self.resize(720, 620)
        self.setMinimumSize(520, 420)

        self._snapshot = config
        self._catalog: list[ModelEntry] = list(config.models.catalog)

        self._build_ui()
        self._load_catalog_list()
        self._sync_inference_model_state()

    # ------------------------------------------------------------------ ui
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(SP_MD, SP_MD, SP_MD, SP_MD)
        root.setSpacing(SP_MD)

        title = QLabel("Configuración del sistema")
        title.setObjectName("SettingsTitle")
        root.addWidget(title)
        root.addWidget(
            _hint(
                "Los cambios se guardan en config.json. Algunos se aplican en "
                "caliente y otros requieren reiniciar la aplicación."
            )
        )

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, SP_XS, 0)
        content_layout.setSpacing(SP_MD)

        content_layout.addWidget(self._build_capture_card())
        content_layout.addWidget(self._build_publisher_card())
        content_layout.addWidget(self._build_inference_card())
        content_layout.addWidget(self._build_storage_card())
        content_layout.addWidget(self._build_reconnect_card())
        content_layout.addWidget(self._build_device_card())
        content_layout.addWidget(self._build_api_card())
        content_layout.addWidget(self._build_catalog_card())
        content_layout.addStretch(1)

        scroll.setWidget(content)
        root.addWidget(scroll, stretch=1)

        footer = QHBoxLayout()
        footer.setSpacing(SP_SM)
        footer.addStretch(1)
        self.cancel_btn = ActionButton("Cancelar", variant="default")
        self.cancel_btn.clicked.connect(self.reject)
        self.save_btn = ActionButton("Guardar cambios", variant="primary")
        self.save_btn.clicked.connect(self.accept)
        footer.addWidget(self.cancel_btn)
        footer.addWidget(self.save_btn)
        root.addLayout(footer)

    def _build_capture_card(self):
        capture = self._snapshot.stream.capture
        card, form = _make_card(
            "Captura y vídeo",
            "Fuente, resolución y cadencia del pipeline de vídeo.",
        )
        self.source_edit = _row(
            form,
            "Fuente",
            _line_edit(capture.source, "0 o nombre de archivo"),
            "Cámara: 0. Vídeo: nombre dentro de la carpeta de vídeos o ruta.",
        )
        self.width_spin = _row(
            form, "Ancho (px)", _spin(capture.width, 2, 7680, 2),
            "Debe ser par para publicar en yuv420p.",
        )
        self.height_spin = _row(
            form, "Alto (px)", _spin(capture.height, 2, 7680, 2),
            "Debe ser par para publicar en yuv420p.",
        )
        self.fps_combo = _row(form, "FPS", _combo(FPS_OPTIONS, capture.fps))
        self.loop_check = _row(
            form,
            "Repetir vídeo",
            _checkbox("Reiniciar al terminar", capture.loop_video),
            "Solo aplica a fuentes de archivo.",
        )
        self.buffer_spin = _row(
            form, "Búfer de captura", _spin(capture.buffer_size, 1, 256),
        )
        self.stable_spin = _row(
            form, "Frames estables", _spin(capture.stable_frames, 1, 100000),
        )
        self.read_timeout_spin = _row(
            form,
            "Timeout de lectura (s)",
            _double_spin(capture.read_timeout_seconds, 0.1, 600, 0.5, 1),
        )
        return card

    def _build_publisher_card(self):
        publisher = self._snapshot.stream.publisher
        card, form = _make_card(
            "Publicación",
            "Encoder y destino del flujo de salida.",
        )
        self.output_url_edit = _row(
            form,
            "URL de salida",
            _line_edit(publisher.output_url, "rtsp://host:puerto/ruta"),
            "Destino del flujo: rtsp:// o http://.",
        )
        self.encoder_edit = _row(
            form,
            "Encoder",
            _line_edit(publisher.encoder, "libx264"),
            "Codificador de FFmpeg, por ejemplo libx264.",
        )
        self.bitrate_edit = _row(
            form, "Bitrate", _line_edit(publisher.bitrate, "2M"),
            "Objetivo de bitrate, por ejemplo 2M o 4000k.",
        )
        self.ffmpeg_edit = _row(
            form, "Ejecutable ffmpeg", _line_edit(publisher.ffmpeg_executable),
        )
        pixel_format = _row(
            form, "Formato de píxel", _line_edit(publisher.pixel_format),
            "Fijado en yuv420p por compatibilidad con WebRTC.",
        )
        pixel_format.setEnabled(False)
        self.gop_combo = _row(form, "GOP (s)", _combo(GOP_OPTIONS, publisher.gop_seconds))
        self.bframes_spin = _row(form, "B-frames", _spin(publisher.b_frames, 0, 16))
        self.pub_queue_spin = _row(
            form, "Cola del publicador", _spin(publisher.queue_size, 1, 64),
        )
        self.pub_write_spin = _row(
            form,
            "Timeout de escritura (s)",
            _double_spin(publisher.write_timeout, 0.01, 10, 0.05, 2),
        )
        self.pub_stable_spin = _row(
            form,
            "Ventana estable (s)",
            _double_spin(publisher.stable_seconds, 0, 600, 0.5, 1),
        )
        return card

    def _build_inference_card(self):
        inference = self._snapshot.stream.inference
        card, form = _make_card(
            "Inferencia",
            "Modelo de detección y sensibilidad de la detección.",
        )
        self.default_model_combo = _row(
            form,
            "Modelo por defecto",
            QComboBox(),
            "Modelo que la aplicación elige al arrancar.",
        )
        self.confidence_spin = _row(
            form,
            "Umbral de confianza",
            _double_spin(inference.confidence_threshold, 0.0, 1.0, 0.05, 2),
            "Entre 0 y 1. Solo cuenta en el modelo activo.",
        )
        self.inference_check = _row(
            form,
            "Inferencia activada",
            _checkbox("Detectar objetos en el vídeo", inference.enabled),
            "Controla el pipeline de streaming standalone; en esta aplicación "
            "la inferencia corre mientras la cámara está encendida.",
        )
        self.require_hailo_check = _row(
            form,
            "Requiere Hailo",
            _checkbox("Exigir la NPU Hailo", inference.require_hailo),
            "Requiere que la inferencia esté activada.",
        )
        self.inference_check.toggled.connect(self._sync_inference_model_state)
        return card

    def _build_storage_card(self):
        storage = self._snapshot.stream.storage
        card, form = _make_card(
            "Almacenamiento",
            "Registro de detecciones y rotación de archivos.",
        )
        self.storage_path_edit = _row(
            form, "Ruta", _line_edit(storage.path),
        )
        self.storage_queue_spin = _row(
            form, "Cola", _spin(storage.queue_size, 1, 4096),
        )
        self.storage_max_bytes_spin = _row(
            form,
            "Tamaño máximo (bytes)",
            _spin(storage.max_bytes, 1, 2_000_000_000, 1048576),
            "Se rota el archivo al superar este tamaño.",
        )
        self.storage_max_files_spin = _row(
            form, "Archivos máximos", _spin(storage.max_files, 1, 100),
        )
        self.persist_spin = _row(
            form,
            "Persistir sin detección cada",
            _spin(storage.persist_no_detection_every, 0, 100000),
            "0 desactiva el registro de frames sin detecciones.",
        )
        return card

    def _build_reconnect_card(self):
        reconnect = self._snapshot.stream.reconnect
        card, form = _make_card(
            "Reconexión",
            "Espera entre reintentos cuando se cae la fuente o el destino.",
        )
        self.reconnect_initial_spin = _row(
            form,
            "Espera inicial (s)",
            _double_spin(reconnect.initial_seconds, 0, 600, 0.5, 1),
        )
        self.reconnect_max_spin = _row(
            form,
            "Espera máxima (s)",
            _double_spin(reconnect.max_seconds, 0, 3600, 1.0, 1),
        )
        return card

    def _build_device_card(self):
        device = self._snapshot.device
        card, form = _make_card(
            "Dispositivo",
            "Identidad del horno y enlace con el servidor central.",
        )
        self.horno_edit = _row(
            form, "Horno", _line_edit(device.horno_id, "ID del horno"),
        )
        self.producto_edit = _row(
            form, "Producto", _line_edit(device.producto_id, "ID del producto"),
            "Se aplica al próximo lote; el lote en curso conserva el anterior.",
        )
        self.ping_spin = _row(
            form,
            "Ping (s)",
            _double_spin(device.ping_interval_seconds, 0.1, 3600, 1.0, 1),
        )
        self.api_base_edit = _row(
            form, "API base", _line_edit(device.api_base_url, "https://central"),
        )
        self.auth_audience_edit = _row(
            form, "Audience de auth", _line_edit(device.auth_audience),
        )
        return card

    def _build_api_card(self):
        api = self._snapshot.api
        card, form = _make_card(
            "API local",
            "Backend HTTP que corre en el propio dispositivo.",
        )
        self.api_host_edit = _row(form, "Host", _line_edit(api.host))
        self.api_port_spin = _row(form, "Puerto", _spin(api.port, 1, 65535))
        self.lote_endpoint_edit = _row(
            form,
            "Endpoint de lotes",
            _line_edit(api.lote_endpoint, "http://localhost:8000/api/lotes/finalizar"),
        )
        return card

    def _build_catalog_card(self):
        card, form = _make_card(
            "Catálogo de modelos",
            "Modelos disponibles para elegir. Los identificadores deben ser únicos.",
        )
        self.npu_model_combo = _row(
            form,
            "Modelo de NPU",
            QComboBox(),
            "Modelo que se usa en Raspberry Pi con Hailo.",
        )
        self._catalog_editor = self._build_catalog_editor()
        card.body.addWidget(self._catalog_editor)
        return card

    def _build_catalog_editor(self):
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, SP_SM, 0, 0)
        layout.setSpacing(SP_SM)

        layout.addWidget(
            _hint("Seleccioná una entrada para editarla. Usá Agregar para crear una.")
        )

        self.catalog_list = QListWidget()
        self.catalog_list.setMinimumHeight(96)
        self.catalog_list.setMaximumHeight(150)
        self.catalog_list.currentRowChanged.connect(self._on_catalog_row_changed)
        layout.addWidget(self.catalog_list)

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(SP_MD)
        form.setVerticalSpacing(SP_SM)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)

        self.catalog_label_edit = _line_edit()
        self.catalog_id_edit = _line_edit()
        self.catalog_model_edit = _line_edit()
        self.catalog_names_edit = _line_edit()
        form.addRow("Etiqueta", self.catalog_label_edit)
        form.addRow("Identificador", self.catalog_id_edit)
        form.addRow("Ruta del modelo", self.catalog_model_edit)
        form.addRow("Ruta de etiquetas", self.catalog_names_edit)
        layout.addLayout(form)

        buttons = QHBoxLayout()
        buttons.setSpacing(SP_SM)
        self.catalog_add_btn = ActionButton("Agregar", variant="primary")
        self.catalog_add_btn.clicked.connect(self._on_catalog_add)
        self.catalog_update_btn = ActionButton("Actualizar", variant="default")
        self.catalog_update_btn.clicked.connect(self._on_catalog_update)
        self.catalog_remove_btn = ActionButton("Quitar", variant="danger")
        self.catalog_remove_btn.clicked.connect(self._on_catalog_remove)
        for button in (
            self.catalog_add_btn,
            self.catalog_update_btn,
            self.catalog_remove_btn,
        ):
            buttons.addWidget(button)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        return box

    # ------------------------------------------------------- catálogo de modelos
    def _load_catalog_list(self):
        """Vuelca ``self._catalog`` en la lista y en los combos de selección."""
        self.catalog_list.blockSignals(True)
        self.catalog_list.clear()
        for entry in self._catalog:
            self.catalog_list.addItem(
                QListWidgetItem(f"{entry.label}  ·  {entry.model_id}")
            )
        self.catalog_list.blockSignals(False)

        self._sync_model_combos()
        if self._catalog:
            self.catalog_list.setCurrentRow(0)
        else:
            self._clear_catalog_form()

    def _sync_model_combos(self):
        """Refresca los combos conservando el modelo elegido si sigue existiendo."""
        self._fill_model_combo(
            self.default_model_combo, self._snapshot.models.default_model_id
        )
        self._fill_model_combo(
            self.npu_model_combo, self._snapshot.models.npu_model_id
        )

    def _fill_model_combo(self, combo, preferred_id):
        current_id = self._combo_id(combo) or preferred_id
        combo.blockSignals(True)
        combo.clear()
        for entry in self._catalog:
            combo.addItem(entry.label, entry.model_id)
        index = combo.findData(current_id)
        if index < 0 and self._catalog:
            index = 0
        combo.setCurrentIndex(index)
        combo.blockSignals(False)

    @staticmethod
    def _combo_id(combo):
        index = combo.currentIndex()
        return combo.itemData(index) if index >= 0 else None

    def _on_catalog_row_changed(self, row):
        if not 0 <= row < len(self._catalog):
            return
        entry = self._catalog[row]
        self.catalog_label_edit.setText(entry.label)
        self.catalog_id_edit.setText(entry.model_id)
        self.catalog_model_edit.setText(entry.model_path)
        self.catalog_names_edit.setText(entry.names_path)

    def _clear_catalog_form(self):
        for widget in (
            self.catalog_label_edit,
            self.catalog_id_edit,
            self.catalog_model_edit,
            self.catalog_names_edit,
        ):
            widget.clear()

    def _catalog_entry_from_form(self) -> ModelEntry:
        return ModelEntry(
            label=self.catalog_label_edit.text().strip(),
            model_id=self.catalog_id_edit.text().strip(),
            model_path=self.catalog_model_edit.text().strip(),
            names_path=self.catalog_names_edit.text().strip(),
        )

    def _validate_catalog_entry(self, entry: ModelEntry, exclude_index=None):
        """Devuelve el motivo del rechazo o ``None`` si la entrada es válida."""
        if not entry.model_id:
            return "El identificador no puede estar vacío."
        if not entry.label:
            return "La etiqueta no puede estar vacía."
        if not entry.model_path:
            return "La ruta del modelo no puede estar vacía."
        if not entry.names_path:
            return "La ruta de etiquetas no puede estar vacía."
        for index, existing in enumerate(self._catalog):
            if index != exclude_index and existing.model_id == entry.model_id:
                return (
                    f"Ya existe una entrada con el identificador '{entry.model_id}'."
                )
        return None

    def _on_catalog_add(self):
        entry = self._catalog_entry_from_form()
        error = self._validate_catalog_entry(entry)
        if error:
            QMessageBox.warning(self, "Catálogo de modelos", error)
            return
        self._catalog.append(entry)
        self._load_catalog_list()
        self.catalog_list.setCurrentRow(len(self._catalog) - 1)

    def _on_catalog_update(self):
        row = self.catalog_list.currentRow()
        if not 0 <= row < len(self._catalog):
            QMessageBox.warning(
                self, "Catálogo de modelos", "Seleccioná una entrada para actualizar."
            )
            return
        entry = self._catalog_entry_from_form()
        error = self._validate_catalog_entry(entry, exclude_index=row)
        if error:
            QMessageBox.warning(self, "Catálogo de modelos", error)
            return
        self._catalog[row] = entry
        self._load_catalog_list()
        self.catalog_list.setCurrentRow(row)

    def _on_catalog_remove(self):
        row = self.catalog_list.currentRow()
        if not 0 <= row < len(self._catalog):
            QMessageBox.warning(
                self, "Catálogo de modelos", "Seleccioná una entrada para quitar."
            )
            return
        self._catalog.pop(row)
        self._load_catalog_list()

    # ------------------------------------------------------------ inferencia
    def _sync_inference_model_state(self):
        """Habilita ``require_hailo`` solo si la inferencia está activada."""
        enabled = self.inference_check.isChecked()
        self.require_hailo_check.setEnabled(enabled)
        if not enabled:
            self.require_hailo_check.setChecked(False)

    # ------------------------------------------------------------------ api
    def result_config(self) -> AppConfig:
        """Devuelve una copia modificada del snapshot, sin tocar el disco.

        Puede lanzar ``SchemaError`` si algún valor aislado no pasa la
        validación del esquema (por ejemplo, una URL de salida vacía); el
        llamador lo maneja como parte de :meth:`accept`.
        """
        snapshot = self._snapshot
        capture = replace(
            snapshot.stream.capture,
            source=self.source_edit.text().strip(),
            width=self.width_spin.value(),
            height=self.height_spin.value(),
            fps=self.fps_combo.currentData(),
            loop_video=self.loop_check.isChecked(),
            buffer_size=self.buffer_spin.value(),
            stable_frames=self.stable_spin.value(),
            read_timeout_seconds=self.read_timeout_spin.value(),
        )
        publisher = replace(
            snapshot.stream.publisher,
            output_url=self.output_url_edit.text().strip(),
            bitrate=self.bitrate_edit.text().strip(),
            ffmpeg_executable=self.ffmpeg_edit.text().strip(),
            encoder=self.encoder_edit.text().strip(),
            gop_seconds=self.gop_combo.currentData(),
            b_frames=self.bframes_spin.value(),
            queue_size=self.pub_queue_spin.value(),
            write_timeout=self.pub_write_spin.value(),
            stable_seconds=self.pub_stable_spin.value(),
        )
        inference = replace(
            snapshot.stream.inference,
            enabled=self.inference_check.isChecked(),
            require_hailo=self.require_hailo_check.isChecked(),
            confidence_threshold=self.confidence_spin.value(),
        )
        storage = replace(
            snapshot.stream.storage,
            path=self.storage_path_edit.text().strip(),
            queue_size=self.storage_queue_spin.value(),
            max_bytes=self.storage_max_bytes_spin.value(),
            max_files=self.storage_max_files_spin.value(),
            persist_no_detection_every=self.persist_spin.value(),
        )
        reconnect = replace(
            snapshot.stream.reconnect,
            initial_seconds=self.reconnect_initial_spin.value(),
            max_seconds=self.reconnect_max_spin.value(),
        )
        stream = StreamSettings(
            capture=capture,
            publisher=publisher,
            inference=inference,
            storage=storage,
            reconnect=reconnect,
        )
        device = replace(
            snapshot.device,
            horno_id=self.horno_edit.text().strip(),
            producto_id=self.producto_edit.text().strip(),
            ping_interval_seconds=self.ping_spin.value(),
            api_base_url=self.api_base_edit.text().strip(),
            auth_audience=self.auth_audience_edit.text().strip(),
        )
        api = replace(
            snapshot.api,
            host=self.api_host_edit.text().strip(),
            port=self.api_port_spin.value(),
            lote_endpoint=self.lote_endpoint_edit.text().strip(),
        )
        models = replace(
            snapshot.models,
            default_model_id=self._combo_id(self.default_model_combo),
            npu_model_id=self._combo_id(self.npu_model_combo),
            catalog=tuple(self._catalog),
        )
        return replace(snapshot, stream=stream, device=device, api=api, models=models)

    def accept(self):
        """Cierra solo si la configuración resultante es válida."""
        try:
            config = self.result_config()
        except Exception as exc:  # noqa: BLE001 - se muestra al operador
            QMessageBox.warning(
                self,
                "Configuración inválida",
                f"No se pudo construir la configuración:\n{exc}",
            )
            return

        errors = validate_settings(config)
        if errors:
            QMessageBox.warning(
                self,
                "Configuración inválida",
                "\n".join(f"- {error}" for error in errors),
            )
            return
        super().accept()

    # ------------------------------------------------------------- pantalla
    def showEvent(self, event):
        """Ajusta el diálogo al alto/ancho disponible de la pantalla."""
        super().showEvent(event)
        screen = self.screen()
        if screen is None:
            return
        available = screen.availableGeometry()
        max_width = int(available.width() * 0.92)
        max_height = int(available.height() * 0.92)
        self.resize(
            min(self.width(), max_width),
            min(self.height(), max_height),
        )
