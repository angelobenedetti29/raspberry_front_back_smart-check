"""Sección Configuración: edición de un subconjunto operativo del `config.json`.

Los valores se editan sobre una copia y recién se escriben al guardar; el
backend valida antes de tocar el disco. Como los servicios ya están corriendo
con la configuración cargada al arrancar, los cambios **no** se aplican en
caliente: la sección lo avisa y hay que reiniciar la aplicación.

El operador ve los campos agrupados en tarjetas y cuenta con una barra de
acciones fija arriba (guardar y descartar) más un indicador de cambios sin
guardar. Todo pasa por `ControladorConfig`: la sección no importa `backend.*`.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from frontend.nucleo.contrato import ContextoApp, SeccionBase
from frontend.nucleo.controlador_config import (
    CampoConfigUI,
    TipoCampo,
)
from frontend.nucleo.qt_helpers import vaciar_layout
from frontend.nucleo.tema import color

_ALTO_BOTON = 40
_ANCHO_BOTON_GUARDAR = 170
_ANCHO_BOTON_DESCARTAR = 120

# Nombre del objeto de cada editor: acota la hoja scoped y evita pisar el
# QLineEdit interno de los QSpinBox.
_OBJETO_TEXTO = "campoTexto"
_OBJETO_ENTERO = "campoEntero"
_OBJETO_DECIMAL = "campoDecimal"
_OBJETO_OPCION = "campoOpcion"

# Cotas por defecto cuando el campo no fija mínimos o máximos propios.
_MIN_ENTERO = 0
_MAX_ENTERO = 2_000_000_000
_MIN_DECIMAL = 0.0
_MAX_DECIMAL = 1_000_000.0


class _SpinEnteroSinRueda(QSpinBox):
    """Ignora la rueda: el scroll de la sección no cambia el valor."""

    def wheelEvent(self, evento: QWheelEvent) -> None:
        evento.ignore()


class _SpinDecimalSinRueda(QDoubleSpinBox):
    """Ignora la rueda: el scroll de la sección no cambia el valor."""

    def wheelEvent(self, evento: QWheelEvent) -> None:
        evento.ignore()


class _ComboSinRueda(QComboBox):
    """Ignora la rueda: el scroll de la sección no cambia la opción."""

    def wheelEvent(self, evento: QWheelEvent) -> None:
        evento.ignore()


class Configuracion(SeccionBase):
    """Formulario de los campos editables de la configuración de la app."""

    id = "configuracion"
    titulo = "Configuración"
    icono = "configuracion"

    def __init__(self, ctx: ContextoApp, parent: QWidget | None = None) -> None:
        super().__init__(ctx, parent)
        self._campos: dict[str, CampoConfigUI] = {}
        self._editores: dict[str, QWidget] = {}
        self._celdas: dict[str, QWidget] = {}
        self._sucio = False
        self._construir()
        self.setStyleSheet(self._hoja())
        self._cargar_campos()

    # --- construcción ---

    def _construir(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 14, 20, 16)
        layout.setSpacing(10)

        subtitulo = QLabel("Ajustes de captura, publicación, inferencia y backend del equipo.")
        subtitulo.setProperty("rol", "subtitulo")
        layout.addWidget(subtitulo)

        aviso = QLabel(
            "Los cambios se escriben en config.json al guardar y recién se "
            "aplican al reiniciar la aplicación."
        )
        aviso.setProperty("rol", "meta")
        aviso.setWordWrap(True)
        layout.addWidget(aviso)

        layout.addLayout(self._construir_acciones())

        self._banner_error = self._construir_banner("bannerError")
        self._banner_exito = self._construir_banner("bannerExito")
        layout.addWidget(self._banner_error)
        layout.addWidget(self._banner_exito)

        # Contenedor de tarjetas: se vacía y se vuelve a armar en cada recarga,
        # así no hay que diferenciar campo por campo cuando el archivo cambia.
        self._contenedor = QWidget()
        self._layout_campos = QVBoxLayout(self._contenedor)
        self._layout_campos.setContentsMargins(0, 0, 0, 0)
        self._layout_campos.setSpacing(10)
        layout.addWidget(self._contenedor)
        layout.addStretch(1)

    def _construir_acciones(self) -> QHBoxLayout:
        """Barra de acciones, arriba y sin scroll, con el estado de guardado."""
        barra = QHBoxLayout()
        barra.setContentsMargins(0, 0, 0, 0)
        barra.setSpacing(10)

        self._boton_guardar = QPushButton("Guardar cambios")
        self._boton_guardar.setObjectName("botonPrimario")
        self._boton_guardar.setCursor(Qt.CursorShape.PointingHandCursor)
        self._boton_guardar.setFixedHeight(_ALTO_BOTON)
        self._boton_guardar.setFixedWidth(_ANCHO_BOTON_GUARDAR)
        self._boton_guardar.clicked.connect(self._guardar)

        self._boton_descartar = QPushButton("Descartar")
        self._boton_descartar.setObjectName("botonSecundario")
        self._boton_descartar.setCursor(Qt.CursorShape.PointingHandCursor)
        self._boton_descartar.setFixedHeight(_ALTO_BOTON)
        self._boton_descartar.setFixedWidth(_ANCHO_BOTON_DESCARTAR)
        self._boton_descartar.clicked.connect(self._descartar)

        self._indicador = QLabel("Cambios sin guardar")
        self._indicador.setObjectName("indicadorSucio")

        barra.addWidget(self._boton_guardar)
        barra.addWidget(self._boton_descartar)
        barra.addStretch(1)
        barra.addWidget(self._indicador)
        return barra

    def _construir_banner(self, nombre: str) -> QLabel:
        """Aviso de error o de éxito; el estilo vive en la hoja scoped."""
        banner = QLabel()
        banner.setObjectName(nombre)
        banner.setWordWrap(True)
        banner.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        banner.hide()
        return banner

    def _hoja(self) -> str:
        """Estilo local de la sección, con los tokens del tema.

        Se aplica sobre la propia sección, así no toca el QSS global ni otras
        pantallas.
        """
        return f"""
        QLabel#campoEtiqueta {{
            font-size: 13px;
            font-weight: 600;
            color: {color("texto")};
        }}
        QLabel#campoAyuda {{
            font-size: 11px;
            color: {color("texto_muted")};
        }}
        QLineEdit#{_OBJETO_TEXTO},
        QSpinBox#{_OBJETO_ENTERO},
        QDoubleSpinBox#{_OBJETO_DECIMAL},
        QComboBox#{_OBJETO_OPCION} {{
            background-color: {color("panel_alto")};
            border: 1px solid {color("borde")};
            border-radius: 6px;
            padding: 4px 8px;
            min-height: 24px;
            color: {color("texto")};
        }}
        QLineEdit#{_OBJETO_TEXTO}:focus,
        QSpinBox#{_OBJETO_ENTERO}:focus,
        QDoubleSpinBox#{_OBJETO_DECIMAL}:focus,
        QComboBox#{_OBJETO_OPCION}:focus {{
            border-color: {color("accent")};
        }}
        QSpinBox#{_OBJETO_ENTERO} QLineEdit,
        QDoubleSpinBox#{_OBJETO_DECIMAL} QLineEdit {{
            background: transparent;
            border: none;
            padding: 0px;
            color: {color("texto")};
            selection-background-color: {color("accent_soft")};
        }}
        QComboBox#{_OBJETO_OPCION}::drop-down {{
            border: none;
            width: 22px;
        }}
        QComboBox#{_OBJETO_OPCION} QAbstractItemView {{
            background-color: {color("panel_alto")};
            border: 1px solid {color("borde")};
            color: {color("texto")};
            selection-background-color: {color("accent_soft")};
            selection-color: {color("texto")};
            outline: none;
        }}
        QCheckBox {{
            color: {color("texto")};
            spacing: 8px;
        }}
        QCheckBox::indicator {{
            width: 20px;
            height: 20px;
            border: 1px solid {color("borde")};
            border-radius: 4px;
            background-color: {color("panel_alto")};
        }}
        QCheckBox::indicator:checked {{
            background-color: {color("accent_soft")};
            border-color: {color("accent")};
        }}
        QLabel#indicadorSucio {{
            font-size: 13px;
            font-weight: 600;
            color: {color("alerta")};
        }}
        QLabel#bannerError {{
            background-color: {color("error")};
            color: {color("bg")};
            border-radius: 8px;
            padding: 6px 12px;
            font-weight: 600;
        }}
        QLabel#bannerExito {{
            background-color: {color("accent_soft")};
            border: 1px solid {color("ok")};
            color: {color("ok")};
            border-radius: 8px;
            padding: 6px 12px;
            font-weight: 600;
        }}
        QLabel#avisoLotes {{
            color: {color("alerta")};
            border: 1px solid {color("alerta")};
            border-radius: 8px;
            padding: 6px 10px;
            font-size: 13px;
        }}
        """

    # --- recarga y armado de campos ---

    def _cargar_campos(self) -> bool:
        """Repuebla el formulario desde disco. Devuelve False si no se pudo leer."""
        try:
            campos = self.ctx.controlador_config.campos()
        except Exception as exc:  # el backend no debe romper la sección
            self._campos = {}
            self._editores = {}
            self._celdas = {}
            self._limpiar_contenedor()
            self._sucio = False
            self._actualizar_barra()
            self._mostrar_error(str(exc))
            return False

        self._campos = {campo.clave: campo for campo in campos}
        self._reconstruir(campos)
        self._sucio = False
        self._actualizar_barra()
        return True

    def _limpiar_contenedor(self) -> None:
        """Despega y destruye las tarjetas actuales."""
        vaciar_layout(self._layout_campos)

    def _reconstruir(self, campos: tuple[CampoConfigUI, ...]) -> None:
        """Arma una tarjeta por grupo, en el orden de aparición de los campos."""
        self._limpiar_contenedor()
        self._editores = {}
        self._celdas = {}

        grupos = tuple(dict.fromkeys(campo.grupo for campo in campos))
        for grupo in grupos:
            del_grupo = tuple(campo for campo in campos if campo.grupo == grupo)
            self._layout_campos.addWidget(self._tarjeta(grupo, del_grupo))
        self._aplicar_condiciones()

    def _tarjeta(self, grupo: str, campos: tuple[CampoConfigUI, ...]) -> QFrame:
        """Tarjeta de un grupo con sus campos en grilla de dos columnas."""
        tarjeta = QFrame()
        tarjeta.setObjectName("tarjetaEstado")

        layout = QVBoxLayout(tarjeta)
        layout.setContentsMargins(14, 8, 14, 12)
        layout.setSpacing(8)

        rotulo = QLabel(grupo.upper())
        rotulo.setProperty("rol", "rotulo")
        layout.addWidget(rotulo)

        grilla = QGridLayout()
        grilla.setContentsMargins(0, 0, 0, 0)
        grilla.setHorizontalSpacing(18)
        grilla.setVerticalSpacing(8)
        for indice, campo in enumerate(campos):
            grilla.addWidget(self._celda(campo), indice // 2, indice % 2)
        grilla.setColumnStretch(0, 1)
        grilla.setColumnStretch(1, 1)

        layout.addLayout(grilla)
        return tarjeta

    def _celda(self, campo: CampoConfigUI) -> QWidget:
        """Etiqueta, editor y ayuda de un campo, apilados para leer en táctil."""
        celda = QWidget()

        layout = QVBoxLayout(celda)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        etiqueta = QLabel(campo.etiqueta)
        etiqueta.setObjectName("campoEtiqueta")

        editor = self._editor(campo)
        self._editores[campo.clave] = editor

        layout.addWidget(etiqueta)
        layout.addWidget(editor)

        if campo.ayuda:
            ayuda = QLabel(campo.ayuda)
            ayuda.setObjectName("campoAyuda")
            ayuda.setWordWrap(True)
            layout.addWidget(ayuda)

        self._celdas[campo.clave] = celda
        return celda

    def _editor(self, campo: CampoConfigUI) -> QWidget:
        """Crea el editor que corresponde al tipo y conecta el marcado de sucio.

        El valor se fija antes de conectar la señal: así la carga no se cuenta
        como un cambio del operador.
        """
        tipo = campo.tipo

        if tipo is TipoCampo.TEXTO:
            editor = QLineEdit()
            editor.setObjectName(_OBJETO_TEXTO)
            editor.setText(str(campo.valor))
            editor.textEdited.connect(self._marcar_sucio)
            return editor

        if tipo is TipoCampo.ENTERO:
            editor = _SpinEnteroSinRueda()
            editor.setObjectName(_OBJETO_ENTERO)
            editor.setRange(
                self._entero(campo.minimo, _MIN_ENTERO),
                self._entero(campo.maximo, _MAX_ENTERO),
            )
            editor.setValue(self._entero(campo.valor, 0))
            editor.valueChanged.connect(self._marcar_sucio)
            return editor

        if tipo is TipoCampo.DECIMAL:
            editor = _SpinDecimalSinRueda()
            editor.setObjectName(_OBJETO_DECIMAL)
            editor.setDecimals(max(0, campo.decimales))
            editor.setSingleStep(campo.paso if campo.paso > 0 else 1.0)
            editor.setRange(
                self._decimal(campo.minimo, _MIN_DECIMAL),
                self._decimal(campo.maximo, _MAX_DECIMAL),
            )
            editor.setValue(self._decimal(campo.valor, 0.0))
            editor.valueChanged.connect(self._marcar_sucio)
            return editor

        if tipo is TipoCampo.BOOLEANO:
            editor = QCheckBox("Sí")
            editor.setChecked(bool(campo.valor))
            editor.toggled.connect(self._marcar_sucio)
            return editor

        editor = _ComboSinRueda()
        editor.setObjectName(_OBJETO_OPCION)
        for etiqueta, valor in campo.opciones:
            editor.addItem(etiqueta, valor)
        indice = editor.findData(campo.valor)
        if indice >= 0:
            editor.setCurrentIndex(indice)
        editor.currentIndexChanged.connect(self._marcar_sucio)
        editor.currentIndexChanged.connect(self._aplicar_condiciones)
        return editor

    @staticmethod
    def _entero(valor: object, por_defecto: int) -> int:
        """Convierte una cota (o un valor) a int, con respaldo si no hay dato."""
        if isinstance(valor, bool) or not isinstance(valor, (int, float)):
            return por_defecto
        return int(valor)

    @staticmethod
    def _decimal(valor: object, por_defecto: float) -> float:
        """Convierte una cota (o un valor) a float, con respaldo si no hay dato."""
        if isinstance(valor, bool) or not isinstance(valor, (int, float)):
            return por_defecto
        return float(valor)

    # --- estado y acciones ---

    def _marcar_sucio(self, *_args: object) -> None:
        """La primera edición del operador enciende el indicador."""
        if self._sucio:
            return
        self._sucio = True
        self._actualizar_barra()

    def _actualizar_barra(self) -> None:
        """Refleja en la barra si hay cambios pendientes."""
        self._indicador.setVisible(self._sucio)
        self._boton_guardar.setEnabled(self._sucio)
        self._boton_descartar.setEnabled(self._sucio)

    def _leer_valor(self, campo: CampoConfigUI) -> object:
        """Valor actual del editor del campo."""
        editor = self._editores.get(campo.clave)
        if editor is None:
            return campo.valor

        if campo.tipo is TipoCampo.TEXTO:
            return editor.text()  # type: ignore[attr-defined]
        if campo.tipo is TipoCampo.BOOLEANO:
            return editor.isChecked()  # type: ignore[attr-defined]
        if campo.tipo is TipoCampo.OPCION:
            return editor.currentData()  # type: ignore[attr-defined]
        return editor.value()  # type: ignore[attr-defined]

    def _aplicar_condiciones(self, *_args: object) -> None:
        """Muestra u oculta cada celda según la condición de su campo."""
        for clave, campo in self._campos.items():
            if campo.visible_si is None:
                continue
            clave_cond, valor_cond = campo.visible_si
            cond = self._campos.get(clave_cond)
            visible = cond is None or self._leer_valor(cond) == valor_cond
            celda = self._celdas.get(clave)
            if celda is not None:
                celda.setVisible(visible)

    def _guardar(self) -> None:
        """Manda los valores al backend y muestra el resultado."""
        valores = {
            clave: self._leer_valor(campo)
            for clave, campo in self._campos.items()
            # Oculto por su condición: no se envía, para no crear claves
            # que el driver activo no usa (ej. host/port con "simulado").
            if (celda := self._celdas.get(clave)) is None or not celda.isHidden()
        }
        try:
            resultado = self.ctx.controlador_config.guardar(valores)
        except Exception as exc:  # nunca dejar que un fallo rompa la sección
            self._mostrar_error(str(exc))
            return

        if resultado.ok:
            self._sucio = False
            self._actualizar_barra()
            self._mostrar_exito(resultado.mensaje)
        else:
            self._mostrar_error(resultado.mensaje)

    def _descartar(self) -> None:
        """Recarga los valores desde disco, tirando lo editado."""
        if self._cargar_campos():
            self._mostrar_exito("Cambios descartados: se recargó el archivo.")

    def _mostrar_error(self, mensaje: str) -> None:
        texto = mensaje.strip() or "No se pudo completar la operación."
        self._banner_exito.hide()
        self._banner_error.setText(f"Error: {texto}")
        self._banner_error.show()

    def _mostrar_exito(self, mensaje: str) -> None:
        self._banner_error.hide()
        self._banner_exito.setText(mensaje)
        self._banner_exito.show()

    def _ocultar_banners(self) -> None:
        self._banner_error.clear()
        self._banner_error.hide()
        self._banner_exito.clear()
        self._banner_exito.hide()

    # --- ciclo de vida ---

    def al_entrar(self) -> None:
        """Recarga desde disco: el archivo pudo cambiar con la app abierta."""
        self._ocultar_banners()
        self._cargar_campos()
