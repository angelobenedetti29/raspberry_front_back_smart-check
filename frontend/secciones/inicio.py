"""Sección de Inicio: saludo, accesos directos y estado del sistema."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import (
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
from frontend.nucleo.controlador_dispositivo import (
    EstadoRegistroDispositivoUI,
    EstadoRegistroUI,
    TipoDispositivoUI,
)
from frontend.nucleo.estado import EstadoItem, NivelEstado, SnapshotSistema
from frontend.nucleo.iconos import icono
from frontend.nucleo.tema import color

_GLIFO_NIVEL: dict[NivelEstado, str] = {
    NivelEstado.ACTIVO: "●",
    NivelEstado.ATENCION: "◐",
    NivelEstado.INACTIVO: "○",
    NivelEstado.SIN_DATO: "—",
}

_ALTO_FILA_ESTADO = 34

# El ancho fijo del botón de registro evita que la tarjeta salte de tamaño al
# cambiar de etiqueta entre estados ("Registrar dispositivo" es la más larga).
_ANCHO_BOTON_DISPOSITIVO = 200
_ALTO_BOTON_DISPOSITIVO = 42


def _repolish(widget: QWidget) -> None:
    """Reaplica la hoja de estilos tras cambiar una propiedad que un selector usa."""
    estilo = widget.style()
    estilo.unpolish(widget)
    estilo.polish(widget)
    widget.update()


class _TarjetaAcceso(QFrame):
    """Tarjeta táctil grande que navega a otra sección."""

    clicked = Signal()

    def __init__(
        self,
        clave_icono: str,
        titulo: str,
        subtitulo: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("tarjetaAcceso")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(76)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(14)

        icono_lbl = QLabel()
        icono_lbl.setPixmap(
            icono(clave_icono, color=color("accent"), px=32).pixmap(32, 32)
        )
        icono_lbl.setFixedSize(32, 32)

        textos = QVBoxLayout()
        textos.setContentsMargins(0, 0, 0, 0)
        textos.setSpacing(2)

        titulo_lbl = QLabel(titulo)
        titulo_lbl.setProperty("rol", "tileTitulo")
        subtitulo_lbl = QLabel(subtitulo)
        subtitulo_lbl.setProperty("rol", "tileSub")

        textos.addWidget(titulo_lbl)
        textos.addWidget(subtitulo_lbl)

        layout.addWidget(icono_lbl)
        layout.addLayout(textos, 1)

        # Los hijos no deben comerse el hover ni el click: la tarjeta es el target.
        for hijo in (icono_lbl, titulo_lbl, subtitulo_lbl):
            hijo.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        """Emite `clicked` si el toque terminó dentro de la tarjeta."""
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(
            event.position().toPoint()
        ):
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class Inicio(SeccionBase):
    """Pantalla de guardia: qué puedo hacer y cómo está la máquina."""

    id = "inicio"
    titulo = "Inicio"
    icono = "inicio"

    def __init__(self, ctx: ContextoApp, parent: QWidget | None = None) -> None:
        super().__init__(ctx, parent)
        self._estado_dispositivo: EstadoRegistroDispositivoUI | None = None
        self._construir()
        self._aplicar(self.ctx.proveedor.snapshot())
        self.ctx.bus.estado_actualizado.connect(self._al_actualizar_estado)

    # --- construcción ---

    def _construir(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(0)

        saludo = QLabel("Buen turno.")
        saludo.setProperty("rol", "display")
        listo = QLabel("Raspberry está listo para operar.")
        listo.setProperty("rol", "subtitulo")
        self._meta = QLabel()
        self._meta.setProperty("rol", "meta")

        layout.addWidget(saludo)
        layout.addSpacing(2)
        layout.addWidget(listo)
        layout.addSpacing(4)
        layout.addWidget(self._meta)
        layout.addSpacing(14)

        layout.addLayout(self._construir_accesos())
        layout.addSpacing(14)

        layout.addWidget(self._construir_dispositivo())
        layout.addSpacing(14)

        rotulo = QLabel("ESTADO DEL SISTEMA")
        rotulo.setProperty("rol", "rotulo")
        layout.addWidget(rotulo)
        layout.addSpacing(6)

        self._tarjeta_estado = QFrame()
        self._tarjeta_estado.setObjectName("tarjetaEstado")
        self._layout_estado = QVBoxLayout(self._tarjeta_estado)
        self._layout_estado.setContentsMargins(16, 6, 16, 6)
        self._layout_estado.setSpacing(0)
        layout.addWidget(self._tarjeta_estado)
        layout.addStretch(1)

    def _construir_accesos(self) -> QGridLayout:
        """Accesos directos a las secciones: En vivo y Métricas arriba, Configuración abajo.

        La ventana deja ~584 px útiles: tres tarjetas en una fila no darían
        ancho para el subtítulo, así que van en dos columnas y la tercera ocupa
        la fila completa.
        """
        grilla = QGridLayout()
        grilla.setContentsMargins(0, 0, 0, 0)
        grilla.setHorizontalSpacing(12)
        grilla.setVerticalSpacing(12)

        en_vivo = _TarjetaAcceso("camara", "En vivo", "Supervisión con cámara")
        en_vivo.clicked.connect(lambda: self.ctx.navegar("en_vivo"))

        metricas = _TarjetaAcceso(
            "metricas", "Métricas", "Hardware y envíos al backend"
        )
        metricas.clicked.connect(lambda: self.ctx.navegar("metricas"))

        configuracion = _TarjetaAcceso(
            "configuracion", "Configuración", "Ajustes del equipo"
        )
        configuracion.clicked.connect(lambda: self.ctx.navegar("configuracion"))

        # 2+1: las dos pantallas de supervisión comparten la fila de arriba y
        # los ajustes quedan en una fila propia.
        grilla.addWidget(en_vivo, 0, 0)
        grilla.addWidget(metricas, 0, 1)
        grilla.addWidget(configuracion, 1, 0, 1, 2)
        grilla.setColumnStretch(0, 1)
        grilla.setColumnStretch(1, 1)
        return grilla

    def _construir_dispositivo(self) -> QFrame:
        """Tarjeta de registro: hostname, semáforo de estado y acción."""
        tarjeta = QFrame()
        tarjeta.setObjectName("tarjetaDispositivo")

        layout = QVBoxLayout(tarjeta)
        layout.setContentsMargins(14, 8, 14, 10)
        layout.setSpacing(4)

        rotulo = QLabel("DISPOSITIVO")
        rotulo.setProperty("rol", "rotulo")
        layout.addWidget(rotulo)

        fila = QHBoxLayout()
        fila.setContentsMargins(0, 0, 0, 0)
        fila.setSpacing(12)

        icono_lbl = QLabel()
        icono_lbl.setPixmap(
            icono("herramienta", color=color("accent"), px=28).pixmap(28, 28)
        )
        icono_lbl.setFixedSize(28, 28)

        textos = QVBoxLayout()
        textos.setContentsMargins(0, 0, 0, 0)
        textos.setSpacing(2)

        self._host_dispositivo = QLabel()
        self._host_dispositivo.setObjectName("hostDispositivo")

        estado_fila = QHBoxLayout()
        estado_fila.setContentsMargins(0, 0, 0, 0)
        estado_fila.setSpacing(6)

        self._punto_dispositivo = QLabel()
        self._punto_dispositivo.setObjectName("puntoEstado")
        self._punto_dispositivo.setFixedWidth(16)
        self._punto_dispositivo.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._detalle_dispositivo = QLabel()
        self._detalle_dispositivo.setObjectName("detalleEstado")

        estado_fila.addWidget(self._punto_dispositivo)
        estado_fila.addWidget(self._detalle_dispositivo)
        estado_fila.addStretch(1)

        textos.addWidget(self._host_dispositivo)
        textos.addLayout(estado_fila)

        self._boton_dispositivo = QPushButton()
        self._boton_dispositivo.setObjectName("botonPrimario")
        self._boton_dispositivo.setCursor(Qt.CursorShape.PointingHandCursor)
        self._boton_dispositivo.setFixedSize(
            _ANCHO_BOTON_DISPOSITIVO, _ALTO_BOTON_DISPOSITIVO
        )
        self._boton_dispositivo.clicked.connect(self._accion_dispositivo)

        fila.addWidget(icono_lbl)
        fila.addLayout(textos, 1)
        fila.addWidget(self._boton_dispositivo)
        layout.addLayout(fila)
        layout.addLayout(self._construir_selector())

        return tarjeta

    def _construir_selector(self) -> QHBoxLayout:
        """Fila del tipo de dispositivo: selector al registrar, etiqueta si ya está.

        Va debajo de la fila principal porque el ancho útil de la tarjeta
        (~584 px) no da para sumarla al icono, los textos y el botón.
        """
        fila = QHBoxLayout()
        fila.setContentsMargins(0, 0, 0, 0)
        fila.setSpacing(8)

        etiqueta = QLabel("Tipo")
        etiqueta.setProperty("rol", "tileSub")

        self._combo_tipo = QComboBox()
        self._combo_tipo.setObjectName("tipoDispositivo")
        self._combo_tipo.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self._combo_tipo.setToolTip(
            "Tipo con el que la Raspberry se registra en el backend."
        )
        # La primera opción no tiene valor: sin tipo elegido no se puede registrar.
        self._combo_tipo.addItem("Seleccionar tipo", None)
        for tipo in (TipoDispositivoUI.ENTRADA_HORNO, TipoDispositivoUI.SALIDA_HORNO):
            self._combo_tipo.addItem(tipo.etiqueta, tipo)
        self._combo_tipo.currentIndexChanged.connect(self._al_cambiar_tipo)
        self._combo_tipo.setStyleSheet(self._hoja_combo())

        # Etiqueta que reemplaza al selector cuando el tipo ya está definido.
        self._valor_tipo = QLabel()
        self._valor_tipo.setObjectName("etiquetaEstado")
        self._valor_tipo.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )

        fila.addWidget(etiqueta)
        fila.addWidget(self._combo_tipo, 1)
        fila.addWidget(self._valor_tipo, 1)
        return fila

    @staticmethod
    def _hoja_combo() -> str:
        """Estilo del selector de tipo, con los tokens del tema.

        El QSS global sólo estiliza el popup del QComboBox, no el cerrado, así
        que acá se lo alinea con los campos de Configuración y En vivo.
        """
        return f"""
        QComboBox#tipoDispositivo {{
            background-color: {color("panel_alto")};
            border: 1px solid {color("borde")};
            border-radius: 6px;
            padding: 4px 8px;
            min-height: 24px;
            color: {color("texto")};
        }}
        QComboBox#tipoDispositivo:focus {{
            border-color: {color("accent")};
        }}
        QComboBox#tipoDispositivo::drop-down {{
            border: none;
            width: 22px;
        }}
        """

    # --- estado ---

    def _aplicar(self, snapshot: SnapshotSistema) -> None:
        self._meta.setText(self._texto_meta(snapshot))
        self._poblar_estado(snapshot)
        self._poblar_dispositivo()

    def _texto_meta(self, snapshot: SnapshotSistema) -> str:
        fecha = datetime.now().strftime("%d/%m/%Y %H:%M")
        return f"{fecha} · Modelo: {snapshot.modelo_activo}"

    def _poblar_estado(self, snapshot: SnapshotSistema) -> None:
        while self._layout_estado.count():
            elemento = self._layout_estado.takeAt(0)
            if elemento is None:
                continue
            widget = elemento.widget()
            if widget is not None:
                # setParent(None) despega la fila ya mismo: deleteLater() por sí
                # solo no se procesa hasta volver al loop y dejaría filas viejas
                # pintadas encima de las nuevas.
                widget.setParent(None)
                widget.deleteLater()

        total = len(snapshot.items)
        for indice, item in enumerate(snapshot.items):
            self._layout_estado.addWidget(
                self._fila(item, es_ultima=indice == total - 1)
            )

    def _fila(self, item: EstadoItem, es_ultima: bool) -> QWidget:
        fila = QFrame()
        fila.setObjectName("filaEstado")
        fila.setProperty("ultima", "true" if es_ultima else "false")
        fila.setFixedHeight(_ALTO_FILA_ESTADO)

        layout = QHBoxLayout(fila)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        punto = QLabel(_GLIFO_NIVEL[item.nivel])
        punto.setObjectName("puntoEstado")
        punto.setProperty("nivel", item.nivel.value)
        punto.setFixedWidth(16)
        punto.setAlignment(Qt.AlignmentFlag.AlignCenter)

        etiqueta = QLabel(item.etiqueta)
        etiqueta.setObjectName("etiquetaEstado")

        detalle = QLabel(item.detalle)
        detalle.setObjectName("detalleEstado")
        detalle.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )

        layout.addWidget(punto)
        layout.addWidget(etiqueta)
        layout.addStretch(1)
        layout.addWidget(detalle)
        return fila

    # --- dispositivo ---

    def _poblar_dispositivo(self) -> None:
        """Refleja el registro en la tarjeta ya construida, sin recrearla."""
        estado = self.ctx.controlador_dispositivo.estado()
        self._estado_dispositivo = estado

        self._host_dispositivo.setText(estado.hostname or "equipo local")

        nivel, detalle, texto, variante = self._apariencia_dispositivo(estado)

        self._punto_dispositivo.setText(_GLIFO_NIVEL[nivel])
        self._punto_dispositivo.setProperty("nivel", nivel.value)
        _repolish(self._punto_dispositivo)

        self._detalle_dispositivo.setText(detalle)

        self._boton_dispositivo.setText(texto)
        # Cambiar el objectName dispara otro estilo (primario/secundario); sin
        # repolish el botón conservaría el aspecto anterior.
        if self._boton_dispositivo.objectName() != variante:
            self._boton_dispositivo.setObjectName(variante)
            _repolish(self._boton_dispositivo)

        self._reflejar_selector(estado)

    def _reflejar_selector(self, estado: EstadoRegistroDispositivoUI) -> None:
        """Muestra el selector sólo cuando hay que registrar o reintentar.

        Con el alta pendiente o aprobada el tipo ya está decidido: en su lugar se
        muestra la etiqueta del tipo registrado. El botón queda habilitado recién
        cuando hay un tipo elegido, para no pedir el alta sin tipo.
        """
        requiere_tipo = estado.estado in (
            EstadoRegistroUI.NO_REGISTRADO,
            EstadoRegistroUI.ERROR,
            EstadoRegistroUI.REVOCADO,
        )

        self._combo_tipo.setVisible(requiere_tipo)
        self._valor_tipo.setVisible(not requiere_tipo)

        if requiere_tipo:
            self._boton_dispositivo.setEnabled(self._tipo_seleccionado() is not None)
        else:
            self._valor_tipo.setText(self._texto_tipo(estado.tipo))
            self._boton_dispositivo.setEnabled(True)

    @staticmethod
    def _texto_tipo(tipo: TipoDispositivoUI | None) -> str:
        """Etiqueta del tipo registrado, o un texto neutro si el backend no lo expone."""
        return tipo.etiqueta if tipo is not None else "Sin especificar"

    def _tipo_seleccionado(self) -> TipoDispositivoUI | None:
        """Tipo elegido en el selector; `None` mientras siga la opción sin valor.

        Qt guarda el dato como `str` (el enum hereda de `str`), así que se lo
        vuelve a convertir por valor.
        """
        dato = self._combo_tipo.currentData()
        if isinstance(dato, TipoDispositivoUI):
            return dato
        if isinstance(dato, str):
            try:
                return TipoDispositivoUI(dato)
            except ValueError:
                return None
        return None

    def _al_cambiar_tipo(self, _indice: int) -> None:
        """Habilita el botón apenas hay un tipo elegido."""
        if self._estado_dispositivo is None:
            return
        if self._estado_dispositivo.estado in (
            EstadoRegistroUI.NO_REGISTRADO,
            EstadoRegistroUI.ERROR,
            EstadoRegistroUI.REVOCADO,
        ):
            self._boton_dispositivo.setEnabled(self._tipo_seleccionado() is not None)

    @staticmethod
    def _apariencia_dispositivo(
        estado: EstadoRegistroDispositivoUI,
    ) -> tuple[NivelEstado, str, str, str]:
        """Traduce el registro a (nivel, detalle, texto del botón, variante)."""
        if estado.estado is EstadoRegistroUI.PENDIENTE:
            detalle = "Esperando aprobación"
            if estado.mensaje:
                detalle += " · último intento falló"
            return NivelEstado.ATENCION, detalle, "Cancelar", "botonSecundario"

        if estado.estado is EstadoRegistroUI.APROBADO:
            detalle = estado.device_id or estado.estado.etiqueta
            return NivelEstado.ACTIVO, detalle, "Olvidar", "botonSecundario"

        if estado.estado is EstadoRegistroUI.ERROR:
            detalle = estado.mensaje or estado.estado.etiqueta
            return NivelEstado.ATENCION, detalle, "Reintentar", "botonPrimario"

        if estado.estado is EstadoRegistroUI.REVOCADO:
            detalle = "Credencial revocada por el panel"
            return NivelEstado.ATENCION, detalle, "Registrar", "botonPrimario"

        return (
            NivelEstado.INACTIVO,
            "Sin registrar",
            "Registrar dispositivo",
            "botonPrimario",
        )

    def _accion_dispositivo(self) -> None:
        """Ejecuta el comando que corresponde al botón según el estado actual."""
        estado = self._estado_dispositivo
        if estado is None:
            return

        if estado.estado in (
            EstadoRegistroUI.NO_REGISTRADO,
            EstadoRegistroUI.ERROR,
            EstadoRegistroUI.REVOCADO,
        ):
            tipo = self._tipo_seleccionado()
            if tipo is None:
                return
            self.ctx.controlador_dispositivo.solicitar_registro(tipo)
        else:
            self.ctx.controlador_dispositivo.olvidar()
        # Los comandos no bloquean: releer el estado da feedback inmediato y el
        # bus corrige después si el backend tarda en reflejarlo.
        self._poblar_dispositivo()

    def _al_actualizar_estado(self, snapshot: object) -> None:
        if isinstance(snapshot, SnapshotSistema):
            self._aplicar(snapshot)

    # --- ciclo de vida ---

    def al_entrar(self) -> None:
        """Refresca el estado cada vez que se entra (hora incluida)."""
        self._aplicar(self.ctx.proveedor.snapshot())
