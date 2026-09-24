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

import threading
from typing import Any, cast

from PySide6.QtCore import Qt, Signal
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
    ModeloConfigUI,
    ProductoConfigUI,
    ResultadoGuardadoUI,
    TipoCampo,
)
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

# Bloque de vínculos modelo→producto: no sale de `campos()` porque el catálogo
# de productos vive en el backend. Acompaña al grupo de modelos del formulario.
_GRUPO_MODELOS = "Modelos"
_SIN_VINCULAR = "Sin vincular"


def _vaciar_layout(layout: QVBoxLayout) -> None:
    """Despega y destruye los widgets de un layout.

    `setParent(None)` los despega ya mismo; `deleteLater()` solo no alcanza
    cuando se reconstruye y se vuelve a medir en el acto.
    """
    while layout.count():
        elemento = layout.takeAt(0)
        if elemento is None:
            continue
        widget = elemento.widget()
        if widget is not None:
            widget.setParent(None)
            widget.deleteLater()


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

    # Señales para volver al hilo de la UI desde los hilos de trabajo. La
    # conexión es *queued* automáticamente porque emisor y receptor viven en
    # hilos distintos; el payload lleva la generación para descartar respuestas
    # viejas (ver `_al_recibir_catalogo`).
    catalogo_listo = Signal(int, object)
    guardado_lotes_listo = Signal(int, object)

    def __init__(self, ctx: ContextoApp, parent: QWidget | None = None) -> None:
        super().__init__(ctx, parent)
        self._campos: dict[str, CampoConfigUI] = {}
        self._editores: dict[str, QWidget] = {}
        self._sucio = False
        # Referencias del bloque de vínculos modelo→producto.
        self._combos_producto: dict[str, QComboBox] = {}
        self._lotes_aviso: QLabel | None = None
        self._lotes_filas: QWidget | None = None
        self._layout_lotes: QVBoxLayout | None = None
        self._boton_guardar_lotes: QPushButton | None = None
        # Control de las consultas en vuelo: cada pedido sube la generación y
        # sólo el último resultado se aplica.
        self._generacion_catalogo = 0
        self._generacion_guardado = 0
        self._consultando_catalogo = False
        self._cerrado = False
        self.catalogo_listo.connect(self._al_recibir_catalogo)
        self.guardado_lotes_listo.connect(self._al_recibir_guardado)
        self._construir()
        self.setStyleSheet(self._hoja())
        self._cargar_campos()

    # --- construcción ---

    def _construir(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 14, 20, 16)
        layout.setSpacing(10)

        subtitulo = QLabel(
            "Ajustes de captura, publicación, inferencia y backend del equipo."
        )
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
            self._limpiar_contenedor()
            self._reset_lotes_refs()
            self._sucio = False
            self._actualizar_barra()
            self._mostrar_error(str(exc))
            return False

        self._campos = {campo.clave: campo for campo in campos}
        self._reconstruir(campos)
        self._cargar_lotes()
        self._sucio = False
        self._actualizar_barra()
        return True

    def _limpiar_contenedor(self) -> None:
        """Despega y destruye las tarjetas actuales."""
        _vaciar_layout(self._layout_campos)

    def _reconstruir(self, campos: tuple[CampoConfigUI, ...]) -> None:
        """Arma una tarjeta por grupo, en el orden de aparición de los campos."""
        self._limpiar_contenedor()
        self._editores = {}
        self._reset_lotes_refs()

        grupos = tuple(dict.fromkeys(campo.grupo for campo in campos))
        for grupo in grupos:
            del_grupo = tuple(campo for campo in campos if campo.grupo == grupo)
            self._layout_campos.addWidget(self._tarjeta(grupo, del_grupo))
            # El bloque de vínculos acompaña al grupo de modelos.
            if grupo == _GRUPO_MODELOS:
                self._layout_campos.addWidget(self._tarjeta_lotes())
        if _GRUPO_MODELOS not in grupos:
            self._layout_campos.addWidget(self._tarjeta_lotes())

    def _tarjeta(
        self, grupo: str, campos: tuple[CampoConfigUI, ...]
    ) -> QFrame:
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

    # --- vínculos modelo → producto ---

    def _reset_lotes_refs(self) -> None:
        """Suelta las referencias del bloque de vínculos al reconstruir."""
        self._combos_producto = {}
        self._lotes_aviso = None
        self._lotes_filas = None
        self._layout_lotes = None
        self._boton_guardar_lotes = None

    def _tarjeta_lotes(self) -> QFrame:
        """Tarjeta de vínculos modelo→producto.

        No sale de `campos()`: el catálogo de productos vive en el backend, así
        que el bloque se arma y se guarda aparte del formulario plano.
        """
        tarjeta = QFrame()
        tarjeta.setObjectName("tarjetaEstado")

        layout = QVBoxLayout(tarjeta)
        layout.setContentsMargins(14, 8, 14, 12)
        layout.setSpacing(8)

        rotulo = QLabel("LOTES")
        rotulo.setProperty("rol", "rotulo")
        layout.addWidget(rotulo)

        ayuda = QLabel(
            "Vinculá cada modelo con el producto que detecta. El vínculo se "
            "guarda por separado de los campos."
        )
        ayuda.setObjectName("campoAyuda")
        ayuda.setWordWrap(True)
        layout.addWidget(ayuda)

        self._lotes_aviso = QLabel()
        self._lotes_aviso.setObjectName("avisoLotes")
        self._lotes_aviso.setWordWrap(True)
        self._lotes_aviso.hide()
        layout.addWidget(self._lotes_aviso)

        self._lotes_filas = QWidget()
        self._layout_lotes = QVBoxLayout(self._lotes_filas)
        self._layout_lotes.setContentsMargins(0, 0, 0, 0)
        self._layout_lotes.setSpacing(6)
        layout.addWidget(self._lotes_filas)

        acciones = QHBoxLayout()
        acciones.setContentsMargins(0, 0, 0, 0)
        acciones.addStretch(1)

        self._boton_guardar_lotes = QPushButton("Guardar vínculos")
        self._boton_guardar_lotes.setObjectName("botonPrimario")
        self._boton_guardar_lotes.setCursor(Qt.CursorShape.PointingHandCursor)
        self._boton_guardar_lotes.setFixedHeight(_ALTO_BOTON)
        self._boton_guardar_lotes.clicked.connect(self._guardar_lotes)
        acciones.addWidget(self._boton_guardar_lotes)
        layout.addLayout(acciones)
        return tarjeta

    def _cargar_lotes(self) -> None:
        """Pide el catálogo en un hilo y deja el bloque en "consultando".

        `productos()` hace HTTP y puede tardar hasta el timeout del cliente, así
        que nunca se llama en el hilo de la UI: el resultado vuelve por la señal
        `catalogo_listo` y recién ahí se tocan los widgets.
        """
        if self._layout_lotes is None or self._lotes_aviso is None:
            return
        # Ya hay una consulta en vuelo (p. ej. la de `__init__` seguida de la de
        # `al_entrar`): no se dispara otra; la que está en curso va a poblar.
        if self._consultando_catalogo:
            return

        self._generacion_catalogo += 1
        generacion = self._generacion_catalogo
        self._consultando_catalogo = True

        self._combos_producto = {}
        _vaciar_layout(self._layout_lotes)
        self._mostrar_aviso_lotes("Consultando el catálogo…")
        self._habilitar_guardado_lotes(False)

        hilo = threading.Thread(
            target=self._consultar_catalogo,
            args=(generacion,),
            name="config-catalogo",
            daemon=True,
        )
        hilo.start()

    def _consultar_catalogo(self, generacion: int) -> None:
        """Hilo de trabajo: junta modelos y productos. No toca widgets."""
        try:
            modelos = self.ctx.controlador_config.modelos()
            productos = self.ctx.controlador_config.productos()
            payload: object = (modelos, productos)
        except Exception as exc:  # el backend no debe romper la sección
            payload = exc
        self._emitir(self.catalogo_listo, generacion, payload)

    def _al_recibir_catalogo(self, generacion: int, payload: object) -> None:
        """Slot en el hilo de la UI: descarta respuestas obsoletas y puebla."""
        # Si la sección ya se cerró, la generación cambió o los widgets se
        # destruyeron, la respuesta llega tarde: se ignora.
        if self._cerrado or generacion != self._generacion_catalogo:
            return
        self._consultando_catalogo = False
        if self._layout_lotes is None or self._lotes_aviso is None:
            return

        if isinstance(payload, Exception):
            self._mostrar_aviso_lotes(
                "No se pudo consultar el catálogo de modelos y productos."
            )
            self._habilitar_guardado_lotes(False)
            return

        modelos, productos = payload  # type: ignore[misc]
        self._poblar_lotes(modelos, productos)

    def _poblar_lotes(
        self,
        modelos: tuple[ModeloConfigUI, ...],
        productos: tuple[ProductoConfigUI, ...],
    ) -> None:
        """Arma las filas de vínculo con el catálogo ya resuelto.

        `productos` vacío significa que no se pudo consultar el backend: los
        combos quedan deshabilitados y se muestra igual el producto guardado.
        """
        if self._layout_lotes is None:
            return
        if not modelos:
            self._mostrar_aviso_lotes("El catálogo local no tiene modelos.")
            self._habilitar_guardado_lotes(False)
            return

        hay_productos = bool(productos)
        if hay_productos:
            self._ocultar_aviso_lotes()
        else:
            self._mostrar_aviso_lotes(
                "No se pudo consultar el backend: el vínculo no se puede "
                "cambiar ahora. Se muestra el producto guardado en cada modelo."
            )

        for modelo in modelos:
            self._layout_lotes.addWidget(
                self._fila_lotes(modelo, productos, hay_productos)
            )
        self._habilitar_guardado_lotes(hay_productos)

    def _mostrar_aviso_lotes(self, texto: str) -> None:
        if self._lotes_aviso is not None:
            self._lotes_aviso.setText(texto)
            self._lotes_aviso.show()

    def _ocultar_aviso_lotes(self) -> None:
        if self._lotes_aviso is not None:
            self._lotes_aviso.hide()

    def _habilitar_guardado_lotes(self, habilitado: bool) -> None:
        if self._boton_guardar_lotes is not None:
            self._boton_guardar_lotes.setEnabled(habilitado)

    def _fila_lotes(
        self,
        modelo: ModeloConfigUI,
        productos: tuple[ProductoConfigUI, ...],
        habilitado: bool,
    ) -> QWidget:
        """Fila con el modelo a la izquierda y su combo de producto a la derecha."""
        fila = QWidget()

        layout = QHBoxLayout(fila)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        etiqueta = QLabel(modelo.label)
        etiqueta.setObjectName("campoEtiqueta")
        # Ancho mínimo para que los combos queden alineados entre filas.
        etiqueta.setMinimumWidth(180)
        etiqueta.setToolTip(modelo.model_id)

        combo = _ComboSinRueda()
        combo.setObjectName(_OBJETO_OPCION)
        combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        combo.setToolTip(
            "Producto que el backend asocia a las detecciones de este modelo."
        )
        combo.addItem(_SIN_VINCULAR, None)
        for producto in productos:
            combo.addItem(producto.nombre, producto.id)
        self._seleccionar_producto(combo, modelo.producto_id)
        combo.setEnabled(habilitado)
        self._combos_producto[modelo.model_id] = combo

        layout.addWidget(etiqueta)
        layout.addWidget(combo, 1)
        return fila

    @staticmethod
    def _seleccionar_producto(combo: QComboBox, producto_id: str | None) -> None:
        """Preselecciona el vínculo actual; si no está en el catálogo, lo agrega.

        Así el operador ve el producto guardado aunque el backend no lo liste
        (p. ej. quedó inactivo), y guardar no lo pierde sin querer.
        """
        if producto_id is None:
            combo.setCurrentIndex(0)
            return
        indice = combo.findData(producto_id)
        if indice < 0:
            combo.addItem(f"{producto_id} (no está en el catálogo)", producto_id)
            indice = combo.count() - 1
        combo.setCurrentIndex(indice)

    def _guardar_lotes(self) -> None:
        """Manda el vínculo de cada modelo en un hilo; no bloquea la UI."""
        if not self._combos_producto:
            return
        por_modelo = {
            model_id: combo.currentData()
            for model_id, combo in self._combos_producto.items()
        }

        self._generacion_guardado += 1
        generacion = self._generacion_guardado
        self._habilitar_guardado_lotes(False)
        self._mostrar_aviso_lotes("Guardando vínculos…")

        hilo = threading.Thread(
            target=self._guardar_lotes_en_hilo,
            args=(generacion, por_modelo),
            name="config-guardar-lotes",
            daemon=True,
        )
        hilo.start()

    def _guardar_lotes_en_hilo(
        self, generacion: int, por_modelo: dict[str, str | None]
    ) -> None:
        """Hilo de trabajo: guarda el vínculo. No toca widgets."""
        try:
            payload: object = self.ctx.controlador_config.guardar_productos(
                por_modelo
            )
        except Exception as exc:  # nunca dejar que un fallo rompa la sección
            payload = exc
        self._emitir(self.guardado_lotes_listo, generacion, payload)

    def _al_recibir_guardado(self, generacion: int, payload: object) -> None:
        """Slot en el hilo de la UI: muestra el resultado del guardado."""
        if self._cerrado or generacion != self._generacion_guardado:
            return

        # Sólo se rehabilita si no hay una consulta de catálogo en curso: esa
        # consulta dejó el botón deshabilitado a propósito.
        self._habilitar_guardado_lotes(not self._consultando_catalogo)
        if not self._consultando_catalogo:
            self._ocultar_aviso_lotes()

        if isinstance(payload, Exception):
            self._mostrar_error(str(payload))
            return
        resultado = cast(ResultadoGuardadoUI, payload)
        if resultado.ok:
            self._mostrar_exito(resultado.mensaje)
        else:
            self._mostrar_error(resultado.mensaje)

    @staticmethod
    def _emitir(senal: Any, generacion: int, payload: object) -> None:
        """Emite desde el hilo de trabajo.

        Si la sección se destruyó con la consulta en vuelo, el objeto C++ ya no
        existe y `emit` levanta `RuntimeError`: se descarta sin más.
        """
        try:
            senal.emit(generacion, payload)
        except RuntimeError:
            pass

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

    def _guardar(self) -> None:
        """Manda los valores al backend y muestra el resultado."""
        valores = {
            clave: self._leer_valor(campo) for clave, campo in self._campos.items()
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

    def al_cerrar(self) -> None:
        """Marca la sección como cerrada.

        No se bloquea esperando hilos: las consultas en vuelo son daemon y su
        resultado se descarta por generación al llegar (ver los slots).
        """
        self._cerrado = True
