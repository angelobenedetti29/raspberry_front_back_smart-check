"""Sección En vivo: control de la captura, preview local y estado del streaming.

La vista previa es local y se refresca a `stream.preview.fps`; lo que se publica
a MediaMTX sale a `stream.capture.fps`, un ritmo distinto. La sección lo aclara
para que el operador no confunda su pantalla con el stream del servidor.

Toda la interacción pasa por `ControladorStreaming`. Sus métodos lanzan
`RuntimeError` si el servicio no está arrancado: acá se atrapa y se muestra en
un aviso visible, nunca se deja que rompa la sección.
"""

from __future__ import annotations

import math
from typing import Callable

from PySide6.QtCore import QSignalBlocker, Qt, QTimer
from PySide6.QtGui import (
    QColor,
    QImage,
    QPaintEvent,
    QPainter,
    QResizeEvent,
    QStandardItem,
    QStandardItemModel,
)
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from frontend.nucleo.contrato import ContextoApp, SeccionBase
from frontend.nucleo.controlador import (
    EstadoModeloUI,
    EstadoUI,
    ModoUI,
    OpcionFuente,
    OpcionModelo,
)
from frontend.nucleo.estado import NivelEstado
from frontend.nucleo.iconos import icono
from frontend.nucleo.tema import color

_GLIFO_NIVEL: dict[NivelEstado, str] = {
    NivelEstado.ACTIVO: "●",
    NivelEstado.ATENCION: "◐",
    NivelEstado.INACTIVO: "○",
    NivelEstado.SIN_DATO: "—",
}

# Filas de salud: llevan punto de nivel.
_FILAS_ESTADO: tuple[tuple[str, str], ...] = (
    ("publicacion", "Publicación"),
    ("captura", "Captura"),
    ("inferencia", "Inferencia"),
    ("reintento", "Reintento"),
)

# Filas de datos: valor numérico o de texto, sin semáforo.
_FILAS_DATO: tuple[tuple[str, str], ...] = (
    ("modo", "Modo de salida"),
    ("fuente", "Fuente activa"),
    ("rendimiento", "Rendimiento"),
    ("detecciones", "Detecciones"),
    ("retraso", "Retraso de inferencia"),
    ("modelo", "Modelo pedido"),
    ("archivo", "Archivo cargado"),
    ("fase", "Fase del modelo"),
)

_ALTO_FILA_ESTADO = 26
_ALTO_FILA_DATO = 22
_ANCHO_COLUMNA_ESTADO = 240
_ALTO_MINIMO_PREVIEW = 170
_INTERVALO_PANEL_MS = 1000
_MENSAJE_SERVICIO_CAIDO = "El servicio de streaming no está disponible."


def _repolish(widget: QWidget) -> None:
    """Reaplica la hoja de estilos tras cambiar una propiedad que un selector usa."""
    estilo = widget.style()
    estilo.unpolish(widget)
    estilo.polish(widget)
    widget.update()


class _EtiquetaElidida(QLabel):
    """Etiqueta de una línea: recorta el texto y lo deja entero en el tooltip."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._texto_completo = ""
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setToolTip("")

    def fijar_texto(self, texto: str) -> None:
        """Guarda el texto completo y muestra la versión que entra en el ancho."""
        self._texto_completo = texto
        self.setToolTip(texto)
        self._recortar()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._recortar()

    def _recortar(self) -> None:
        ancho = self.width()
        if ancho <= 0:
            super().setText(self._texto_completo)
            return
        recortado = self.fontMetrics().elidedText(
            self._texto_completo, Qt.TextElideMode.ElideRight, ancho
        )
        super().setText(recortado)


class _FilaEstado(QFrame):
    """Fila de salud: punto de nivel a la izquierda, detalle a la derecha."""

    def __init__(self, etiqueta: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("filaEstado")
        self.setFixedHeight(_ALTO_FILA_ESTADO)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self._punto = QLabel()
        self._punto.setObjectName("puntoEstado")
        self._punto.setFixedWidth(14)
        self._punto.setAlignment(Qt.AlignmentFlag.AlignCenter)

        etiqueta_lbl = QLabel(etiqueta)
        etiqueta_lbl.setObjectName("etiquetaEstado")

        self._detalle = _EtiquetaElidida()
        self._detalle.setObjectName("detalleEstado")
        self._detalle.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )

        layout.addWidget(self._punto)
        layout.addWidget(etiqueta_lbl)
        layout.addWidget(self._detalle, 1)

        self.fijar("—", NivelEstado.SIN_DATO)

    def fijar(self, detalle: str, nivel: NivelEstado) -> None:
        """Actualiza el punto (con repolish) y el detalle de la fila."""
        self._punto.setText(_GLIFO_NIVEL[nivel])
        self._punto.setProperty("nivel", nivel.value)
        _repolish(self._punto)
        self._detalle.fijar_texto(detalle)


class _FilaDato(QFrame):
    """Fila etiqueta/valor para los números del streaming."""

    def __init__(self, etiqueta: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("filaEstado")
        self.setFixedHeight(_ALTO_FILA_DATO)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        etiqueta_lbl = QLabel(etiqueta)
        etiqueta_lbl.setObjectName("etiquetaEstado")

        self._valor = _EtiquetaElidida()
        self._valor.setObjectName("detalleEstado")
        self._valor.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )

        layout.addWidget(etiqueta_lbl)
        layout.addWidget(self._valor, 1)

        self.fijar("—")

    def fijar(self, valor: str) -> None:
        """Actualiza el valor mostrado."""
        self._valor.fijar_texto(valor)


class _VistaPreview(QWidget):
    """Dibuja el último frame sin deformar; si no hay, muestra un aviso."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._imagen: QImage | None = None
        self._mensaje = "Sin vista previa."
        self._pixmap_vacio = icono(
            "camara", color=color("texto_muted"), px=36
        ).pixmap(36, 36)
        self.setMinimumHeight(_ALTO_MINIMO_PREVIEW)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def fijar(self, imagen: QImage | None, mensaje: str) -> None:
        """Guarda la imagen a mostrar (o el aviso) y pide repintado."""
        self._imagen = imagen
        self._mensaje = mensaje
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        pintor = QPainter(self)
        pintor.fillRect(self.rect(), QColor(color("bg")))

        imagen = self._imagen
        if imagen is None or imagen.isNull():
            medio = self.height() // 2
            if not self._pixmap_vacio.isNull():
                pintor.drawPixmap(
                    (self.width() - self._pixmap_vacio.width()) // 2,
                    max(8, medio - 52),
                    self._pixmap_vacio,
                )
            pintor.setPen(QColor(color("texto_muted")))
            pintor.drawText(
                self.rect().adjusted(16, medio - 6, -16, -8),
                int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
                | int(Qt.TextFlag.TextWordWrap),
                self._mensaje,
            )
            return

        escalada = imagen.scaled(
            self.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        pintor.drawImage(
            (self.width() - escalada.width()) // 2,
            (self.height() - escalada.height()) // 2,
            escalada,
        )


class EnVivo(SeccionBase):
    """Vista de cámara en tiempo real con controles, preview y estado."""

    id = "en_vivo"
    titulo = "En vivo"
    icono = "camara"

    def __init__(self, ctx: ContextoApp, parent: QWidget | None = None) -> None:
        super().__init__(ctx, parent)
        self._error_control = ""
        self._error_estado = ""
        self._construir()
        self._refrescar_controles()
        self._refrescar_panel()
        self._refrescar_preview()

    # --- construcción ---

    def _construir(self) -> None:
        config = self.ctx.config

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 14, 20, 16)
        layout.setSpacing(10)

        subtitulo = QLabel("Supervisión de la cámara en tiempo real.")
        subtitulo.setProperty("rol", "subtitulo")
        layout.addWidget(subtitulo)

        layout.addWidget(self._construir_controles())

        self._banner_error = QLabel()
        self._banner_error.setObjectName("bannerError")
        self._banner_error.setWordWrap(True)
        self._banner_error.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self._banner_error.setStyleSheet(
            "QLabel#bannerError {"
            f" background-color: {color('error')};"
            f" color: {color('bg')};"
            " border-radius: 8px;"
            " padding: 8px 12px;"
            " font-weight: 600;"
            " }"
        )
        self._banner_error.hide()
        layout.addWidget(self._banner_error)

        fila = QHBoxLayout()
        fila.setSpacing(12)
        fila.addWidget(self._construir_preview(), 1)
        fila.addWidget(self._construir_estado())
        layout.addLayout(fila, 1)

        fps_preview = max(1, config.stream.preview.fps)
        # techo del intervalo: nunca refrescar por encima de lo configurado.
        self._timer_preview = QTimer(self)
        self._timer_preview.setInterval(math.ceil(1000 / fps_preview))
        self._timer_preview.timeout.connect(self._refrescar_preview)

        self._timer_panel = QTimer(self)
        self._timer_panel.setInterval(_INTERVALO_PANEL_MS)
        self._timer_panel.timeout.connect(self._refrescar_panel)

    def _construir_controles(self) -> QFrame:
        marco = QFrame()
        marco.setObjectName("panelControles")
        marco.setStyleSheet(self._hoja_controles())

        grid = QGridLayout(marco)
        grid.setContentsMargins(12, 10, 12, 10)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)

        rotulo_fuente = QLabel("Fuente")
        rotulo_fuente.setProperty("rol", "rotulo")
        self._combo_fuente = QComboBox()
        self._combo_fuente.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self._combo_fuente.setToolTip("Cámara o archivo de video a procesar.")
        self._combo_fuente.currentIndexChanged.connect(self._al_cambiar_fuente)

        rotulo_modelo = QLabel("Modelo")
        rotulo_modelo.setProperty("rol", "rotulo")
        self._combo_modelo = QComboBox()
        # Modelo propio de ítems: hace falta para deshabilitar los no disponibles.
        self._modelo_modelos = QStandardItemModel(self._combo_modelo)
        self._combo_modelo.setModel(self._modelo_modelos)
        self._combo_modelo.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self._combo_modelo.setToolTip(
            "Modelo de detección. Los que no están disponibles no se pueden elegir."
        )
        self._combo_modelo.currentIndexChanged.connect(self._al_cambiar_modelo)

        grid.addWidget(rotulo_fuente, 0, 0)
        grid.addWidget(self._combo_fuente, 1, 0)
        grid.addWidget(rotulo_modelo, 0, 1)
        grid.addWidget(self._combo_modelo, 1, 1)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)

        self._grupo_modo = QButtonGroup(self)
        self._grupo_modo.setExclusive(True)
        self._boton_plano = self._boton_modo(ModoUI.PLANO)
        self._boton_inferencia = self._boton_modo(ModoUI.INFERENCIA)
        self._grupo_modo.addButton(self._boton_plano)
        self._grupo_modo.addButton(self._boton_inferencia)

        rotulo_modo = QLabel("Modo")
        rotulo_modo.setProperty("rol", "rotulo")

        self._boton_publicar = QPushButton("Publicar")
        self._boton_publicar.setObjectName("controlPublicar")
        self._boton_publicar.setCursor(Qt.CursorShape.PointingHandCursor)
        self._boton_publicar.clicked.connect(self._alternar_publicacion)

        acciones = QHBoxLayout()
        acciones.setContentsMargins(0, 0, 0, 0)
        acciones.setSpacing(8)
        acciones.addWidget(rotulo_modo)
        acciones.addWidget(self._boton_plano)
        acciones.addWidget(self._boton_inferencia)
        acciones.addStretch(1)
        acciones.addWidget(self._boton_publicar)
        grid.addLayout(acciones, 2, 0, 1, 2)

        return marco

    def _boton_modo(self, modo: ModoUI) -> QPushButton:
        boton = QPushButton(modo.etiqueta)
        boton.setObjectName("controlModo")
        boton.setCheckable(True)
        boton.setCursor(Qt.CursorShape.PointingHandCursor)
        boton.clicked.connect(lambda _=False, m=modo: self._cambiar_modo(m))
        return boton

    @staticmethod
    def _hoja_controles() -> str:
        """Estilo de la tarjeta de controles, con los tokens del tema."""
        return f"""
        QFrame#panelControles {{
            background-color: {color("panel")};
            border: 1px solid {color("borde")};
            border-radius: 10px;
        }}
        QComboBox {{
            background-color: {color("panel_alto")};
            border: 1px solid {color("borde")};
            border-radius: 6px;
            padding: 4px 8px;
            min-height: 24px;
            color: {color("texto")};
        }}
        QComboBox:focus {{
            border-color: {color("accent")};
        }}
        QComboBox::drop-down {{
            border: none;
            width: 22px;
        }}
        QComboBox QAbstractItemView {{
            background-color: {color("panel_alto")};
            border: 1px solid {color("borde")};
            color: {color("texto")};
            selection-background-color: {color("accent_soft")};
            selection-color: {color("texto")};
            outline: none;
        }}
        QPushButton#controlModo {{
            background-color: {color("panel_alto")};
            border: 1px solid {color("borde")};
            border-radius: 6px;
            padding: 4px 12px;
            min-height: 24px;
            color: {color("texto_muted")};
        }}
        QPushButton#controlModo:hover {{
            color: {color("texto")};
        }}
        QPushButton#controlModo:checked {{
            background-color: {color("accent_soft")};
            border-color: {color("accent")};
            color: {color("texto")};
            font-weight: 600;
        }}
        QPushButton#controlPublicar {{
            background-color: {color("accent_soft")};
            border: 1px solid {color("accent")};
            border-radius: 6px;
            padding: 4px 16px;
            min-height: 24px;
            color: {color("accent")};
            font-weight: 600;
        }}
        QPushButton#controlPublicar[publicando="true"] {{
            background-color: {color("error")};
            border-color: {color("error")};
            color: {color("bg")};
        }}
        """

    def _construir_preview(self) -> QFrame:
        marco = QFrame()
        marco.setObjectName("tarjetaEstado")

        layout = QVBoxLayout(marco)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(6)

        self._preview = _VistaPreview()
        layout.addWidget(self._preview, 1)

        config = self.ctx.config
        pie = QLabel(
            f"Vista local a {config.stream.preview.fps} fps, sólo en este panel. "
            f"Lo que se publica va a {config.stream.capture.fps} fps y puede "
            "verse distinto."
        )
        pie.setObjectName("vacioTexto")
        pie.setWordWrap(True)
        layout.addWidget(pie)
        return marco

    def _construir_estado(self) -> QFrame:
        marco = QFrame()
        marco.setObjectName("tarjetaEstado")
        marco.setFixedWidth(_ANCHO_COLUMNA_ESTADO)

        layout = QVBoxLayout(marco)
        layout.setContentsMargins(14, 8, 14, 10)
        layout.setSpacing(0)

        rotulo = QLabel("ESTADO")
        rotulo.setProperty("rol", "rotulo")
        layout.addWidget(rotulo)
        layout.addSpacing(2)

        self._filas_estado: dict[str, _FilaEstado] = {}
        for clave, etiqueta in _FILAS_ESTADO:
            fila = _FilaEstado(etiqueta)
            self._filas_estado[clave] = fila
            layout.addWidget(fila)

        layout.addSpacing(8)
        rotulo_datos = QLabel("ÚLTIMOS DATOS")
        rotulo_datos.setProperty("rol", "rotulo")
        layout.addWidget(rotulo_datos)
        layout.addSpacing(2)

        self._filas_dato: dict[str, _FilaDato] = {}
        for indice, (clave, etiqueta) in enumerate(_FILAS_DATO):
            fila = _FilaDato(etiqueta)
            if indice == len(_FILAS_DATO) - 1:
                fila.setProperty("ultima", "true")
            self._filas_dato[clave] = fila
            layout.addWidget(fila)

        layout.addStretch(1)
        return marco

    # --- controles ---

    def _refrescar_controles(self) -> None:
        """Repuebla los selectores y los alinea con el estado del backend."""
        try:
            fuentes = self.ctx.controlador.fuentes()
            modelos = self.ctx.controlador.modelos()
            estado = self.ctx.controlador.estado()
        except RuntimeError as exc:
            self._reportar_error(exc)
            return

        self._poblar_fuentes(fuentes)
        self._poblar_modelos(modelos)
        self._sincronizar_controles(estado)

    def _poblar_fuentes(self, opciones: tuple[OpcionFuente, ...]) -> None:
        with QSignalBlocker(self._combo_fuente):
            self._combo_fuente.clear()
            for opcion in opciones:
                self._combo_fuente.addItem(opcion.etiqueta, opcion.valor)

    def _poblar_modelos(self, opciones: tuple[OpcionModelo, ...]) -> None:
        with QSignalBlocker(self._combo_modelo):
            self._modelo_modelos.clear()
            for opcion in opciones:
                item = QStandardItem(self._texto_modelo(opcion))
                item.setData(opcion.model_id, Qt.ItemDataRole.UserRole)
                if opcion.disponible:
                    if opcion.motor:
                        item.setToolTip(f"Motor: {opcion.motor}")
                else:
                    # Un modelo no disponible se muestra pero no se puede elegir;
                    # el motivo queda a mano en el tooltip.
                    item.setEnabled(False)
                    item.setToolTip(opcion.motivo or "No disponible.")
                self._modelo_modelos.appendRow(item)

    @staticmethod
    def _texto_modelo(opcion: OpcionModelo) -> str:
        texto = opcion.etiqueta
        if opcion.motor:
            texto = f"{texto} · {opcion.motor}"
        if not opcion.disponible:
            texto = f"{texto} · no disponible"
        return texto

    def _sincronizar_controles(self, estado: EstadoUI) -> None:
        """Refleja el estado real sin disparar comandos de vuelta."""
        self._boton_plano.setChecked(estado.modo is ModoUI.PLANO)
        self._boton_inferencia.setChecked(estado.modo is ModoUI.INFERENCIA)

        publicando = estado.publicando
        self._boton_publicar.setText("Detener" if publicando else "Publicar")
        self._boton_publicar.setProperty(
            "publicando", "true" if publicando else "false"
        )
        _repolish(self._boton_publicar)

        if estado.fuente:
            self._seleccionar(self._combo_fuente, estado.fuente)
        if estado.modelo_id:
            self._seleccionar(self._combo_modelo, estado.modelo_id)

    @staticmethod
    def _seleccionar(combo: QComboBox, valor: str) -> None:
        """Deja seleccionada la opción que corresponde al estado del backend."""
        if combo.view().isVisible():
            return  # no cerrar ni mover lo que el operador está eligiendo
        # La fuente llega como descripción ("Cámara 0") mientras el ítem guarda el
        # valor que consume el servicio ("0"), así que hay que buscar por texto
        # además de por dato.
        indice = combo.findData(valor)
        if indice < 0:
            indice = combo.findText(valor)
        if indice >= 0 and indice != combo.currentIndex():
            with QSignalBlocker(combo):
                combo.setCurrentIndex(indice)

    def _al_cambiar_fuente(self, indice: int) -> None:
        if indice < 0:
            return
        valor = self._combo_fuente.itemData(indice)
        if valor is None:
            return
        self._ejecutar(lambda: self.ctx.controlador.fijar_fuente(str(valor)))

    def _al_cambiar_modelo(self, indice: int) -> None:
        if indice < 0:
            return
        model_id = self._combo_modelo.itemData(indice)
        if not model_id:
            return
        self._ejecutar(lambda: self.ctx.controlador.fijar_modelo(str(model_id)))

    def _cambiar_modo(self, modo: ModoUI) -> None:
        self._ejecutar(lambda: self.ctx.controlador.fijar_modo(modo))

    def _alternar_publicacion(self) -> None:
        def accion() -> None:
            actual = self.ctx.controlador.estado()
            self.ctx.controlador.fijar_publicacion(not actual.publicando)

        if self._ejecutar(accion):
            self._refrescar_panel()

    def _ejecutar(self, accion: Callable[[], None]) -> bool:
        """Corre un comando del controlador y convierte un fallo en aviso."""
        try:
            accion()
        except RuntimeError as exc:
            self._reportar_error(exc)
            return False
        self._error_control = ""
        return True

    # --- estado ---

    def _refrescar_panel(self) -> None:
        try:
            estado = self.ctx.controlador.estado()
        except RuntimeError as exc:
            self._reportar_error(exc)
            return

        # Ojo: NO se limpia el error de la última acción acá. Que `estado()`
        # responda no significa que el comando haya funcionado: si el servicio
        # está detenido, `estado()` contesta igual y el error se borraría al
        # segundo, dejando al operador sin saber por qué no pasó nada. Se limpia
        # recién cuando una acción vuelve a funcionar (`_ejecutar`).
        self._error_estado = estado.error
        self._aplicar_panel(estado)
        self._sincronizar_controles(estado)
        self._actualizar_banner()

    def _aplicar_panel(self, estado: EstadoUI) -> None:
        self._filas_estado["publicacion"].fijar(*self._publicacion(estado))
        self._filas_estado["captura"].fijar(*self._captura(estado))
        self._filas_estado["inferencia"].fijar(*self._inferencia(estado))
        self._filas_estado["reintento"].fijar(*self._reintento(estado))

        datos = self._filas_dato
        datos["modo"].fijar(estado.modo.etiqueta)
        datos["fuente"].fijar(estado.fuente or "—")
        fps = f"{estado.fps:.0f} fps" if estado.fps > 0 else "sin medición"
        datos["rendimiento"].fijar(f"{fps} · {estado.frames} frames")
        ultima = estado.ultima_deteccion or "sin detecciones"
        datos["detecciones"].fijar(f"{estado.detecciones} · {ultima}")
        datos["retraso"].fijar(
            f"{estado.retraso_ms} ms" if estado.retraso_ms else "—"
        )

        modelo = estado.modelo_etiqueta or estado.modelo_id or "sin modelo"
        if estado.motor:
            modelo = f"{modelo} · {estado.motor}"
        datos["modelo"].fijar(modelo)
        datos["archivo"].fijar(estado.modelo_archivo or "—")
        datos["fase"].fijar(estado.modelo_estado.etiqueta)

    @staticmethod
    def _publicacion(estado: EstadoUI) -> tuple[str, NivelEstado]:
        if estado.publicando:
            return "activa", NivelEstado.ACTIVO
        if estado.reintento_en > 0:
            return (
                f"reintentando en {estado.reintento_en:.0f} s",
                NivelEstado.ATENCION,
            )
        return "detenida", NivelEstado.INACTIVO

    @staticmethod
    def _captura(estado: EstadoUI) -> tuple[str, NivelEstado]:
        if estado.capturando:
            return estado.fuente or "activa", NivelEstado.ACTIVO
        if estado.publicando:
            return "publicando sin frames", NivelEstado.ATENCION
        return "detenida", NivelEstado.INACTIVO

    @staticmethod
    def _inferencia(estado: EstadoUI) -> tuple[str, NivelEstado]:
        etiqueta = estado.modelo_etiqueta or estado.modelo_id or "sin modelo"
        if estado.motor:
            etiqueta = f"{etiqueta} · {estado.motor}"
        if estado.modelo_estado is EstadoModeloUI.LISTO:
            return etiqueta, NivelEstado.ACTIVO
        if estado.modelo_estado is EstadoModeloUI.ERROR:
            return f"{etiqueta} · no se pudo cargar", NivelEstado.ATENCION
        return f"{etiqueta} · cargando", NivelEstado.SIN_DATO

    @staticmethod
    def _reintento(estado: EstadoUI) -> tuple[str, NivelEstado]:
        if estado.reintento_en > 0:
            return f"en {estado.reintento_en:.0f} s", NivelEstado.ATENCION
        return "—", NivelEstado.SIN_DATO

    def _reportar_error(self, exc: RuntimeError) -> None:
        """Deja el error a la vista y avisa por el bus; no rompe la sección."""
        mensaje = str(exc).strip() or _MENSAJE_SERVICIO_CAIDO
        # El refresco periódico repite el mismo fallo: avisar una sola vez por bus
        # evita llenar de mensajes a quien escuche.
        if mensaje != self._error_control:
            self.ctx.bus.mensaje.emit(mensaje)
        self._error_control = mensaje
        self._actualizar_banner()

    def _actualizar_banner(self) -> None:
        """El error del servicio manda; si no hay, se muestra el de la última acción."""
        mensaje = self._error_estado or self._error_control
        if mensaje:
            self._banner_error.setText(f"Error: {mensaje}")
            self._banner_error.show()
        else:
            self._banner_error.clear()
            self._banner_error.hide()

    # --- preview ---

    def _refrescar_preview(self) -> None:
        try:
            imagen = self.ctx.controlador.ultimo_frame()
            estado = self.ctx.controlador.estado()
        except RuntimeError as exc:
            self._reportar_error(exc)
            self._preview.fijar(None, _MENSAJE_SERVICIO_CAIDO)
            return

        if imagen is None or imagen.isNull():
            self._preview.fijar(None, self._mensaje_espera(estado))
        else:
            self._preview.fijar(imagen, "")

    @staticmethod
    def _mensaje_espera(estado: EstadoUI) -> str:
        if not estado.publicando:
            return "La publicación está detenida.\nUsá Publicar para ver la cámara."
        if estado.reintento_en > 0:
            return f"Sin señal. Reintentando en {estado.reintento_en:.0f} s."
        if not estado.capturando:
            return "Publicando sin captura. Esperando la fuente de video."
        return "Esperando el primer frame."

    # --- ciclo de vida ---

    def al_entrar(self) -> None:
        """Pide preview, arranca los timers y refresca los selectores."""
        self._refrescar_controles()
        self._refrescar_panel()
        self._refrescar_preview()
        self._ejecutar(lambda: self.ctx.controlador.solicitar_preview(True))
        self._timer_preview.start()
        self._timer_panel.start()

    def al_salir(self) -> None:
        """Suelta el preview y detiene los timers: sin esto se gasta CPU al pedo."""
        self._timer_preview.stop()
        self._timer_panel.stop()
        try:
            self.ctx.controlador.solicitar_preview(False)
        except RuntimeError:
            # Al salir el servicio puede estar caído: no hay a quién avisarle.
            pass

    def al_cerrar(self) -> None:
        self.al_salir()
