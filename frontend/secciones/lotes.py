"""Sección Lotes: lote en curso del sector, reporte e historial de cierres.

Es una sección de grupo COMÚN: la ven tanto la entrada como la salida del
horno, pero el contenido cambia según el rol del dispositivo. Si la función
está deshabilitada, el dispositivo no está registrado o el backend todavía no
le asignó un sector, se muestra un panel vacío que explica el motivo en vez de
inventar conteos.

Toda la lectura pasa por `ctx.controlador_lotes` (nunca por `backend.*`). El
refresco sigue el patrón de Métricas: el bus avisa y la sección vuelve a leer
`estado()`. Como acá no hay preview ni timers propios, la conexión se suelta al
salir de la sección y se retoma al volver a entrar.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from frontend.nucleo.contrato import ContextoApp, SeccionBase
from frontend.nucleo.controlador_dispositivo import TipoDispositivoUI
from frontend.nucleo.controlador_lotes import EstadoLotesUI, LoteUI
from frontend.nucleo.iconos import icono
from frontend.nucleo.tema import color

_VALOR_SIN_DATO = "—"
_MENSAJE_SERVICIO_CAIDO = "El servicio de lotes no está disponible."

# El texto del vacío va sin wrap y con ancho fijo: así el alto no depende del
# ancho de la ventana (mismo criterio que Métricas).
_ANCHO_TEXTO_VACIO = 520

_ALTO_FILA_DATO = 26
_ALTO_FILA_HISTORIAL = 30

# Columnas del historial. El motivo se queda con el ancho sobrante.
_ANCHO_HIST_LOTE = 120
_ANCHO_HIST_CIERRE = 110
_ANCHO_HIST_TOTAL = 70
_ANCHO_HIST_QUEMADAS = 90

# Motivos de cierre del backend, traducidos para la interfaz.
_MOTIVOS_CIERRE: dict[str, str] = {
    "sin_detecciones": "Sin detecciones",
    "manual": "Manual",
    "apagado": "Apagado",
}

# Conteos del lote: (clave del campo, rótulo, tono del número).
_CONTEOS: tuple[tuple[str, str, str | None], ...] = (
    ("ok", "Ok", "ok"),
    ("crudo", "Crudo", "alerta"),
    ("quemado", "Quemado", "error"),
    ("total", "Total", None),
)


def _repolish(widget: QWidget) -> None:
    """Reaplica la hoja de estilos tras cambiar una propiedad que un selector usa."""
    estilo = widget.style()
    estilo.unpolish(widget)
    estilo.polish(widget)
    widget.update()


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


def _texto_motivo(motivo: str) -> str:
    """Motivo de cierre legible; si es desconocido se muestra tal cual."""
    if not motivo:
        return _VALOR_SIN_DATO
    return _MOTIVOS_CIERRE.get(motivo, motivo)


def _texto_numero(valor: int | None) -> str:
    """Conteo. `None` significa que el modelo no produce ese estado."""
    return _VALOR_SIN_DATO if valor is None else str(valor)


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


class _FilaDato(QFrame):
    """Fila etiqueta/valor de las tarjetas."""

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

    def fijar(self, valor: str) -> None:
        """Actualiza el valor mostrado."""
        self._valor.fijar_texto(valor)


class _Conteo(QFrame):
    """Ficha de un conteo: número grande arriba, rótulo abajo.

    El tono del número es fijo por ficha (ok/crudo/quemado/total), así que se
    fija una sola vez al construir y no hace falta repolish en cada refresco.
    """

    def __init__(
        self, rotulo: str, tono: str | None = None, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setObjectName("conteoTile")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 8)
        layout.setSpacing(0)

        self._valor = QLabel(_VALOR_SIN_DATO)
        self._valor.setObjectName("conteoValor")
        self._valor.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if tono is not None:
            self._valor.setProperty("tono", tono)

        rotulo_lbl = QLabel(rotulo)
        rotulo_lbl.setObjectName("conteoRotulo")
        rotulo_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

        layout.addWidget(self._valor)
        layout.addWidget(rotulo_lbl)

    def fijar(self, valor: int | None) -> None:
        """Refleja un conteo; sin dato se muestra un guion."""
        self._valor.setText(_texto_numero(valor))


class _FilaHistorial(QFrame):
    """Fila de un lote cerrado, con las columnas alineadas con la cabecera."""

    def __init__(self, lote: LoteUI, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("filaEstado")
        self.setFixedHeight(_ALTO_FILA_HISTORIAL)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        identificador = _EtiquetaElidida()
        identificador.setObjectName("etiquetaEstado")
        identificador.setProperty("mono", "true")
        identificador.setFixedWidth(_ANCHO_HIST_LOTE)
        identificador.fijar_texto(lote.id or _VALOR_SIN_DATO)

        cierre = QLabel(lote.cerrado_en or _VALOR_SIN_DATO)
        cierre.setObjectName("detalleEstado")
        cierre.setFixedWidth(_ANCHO_HIST_CIERRE)

        motivo = _EtiquetaElidida()
        motivo.setObjectName("etiquetaEstado")
        motivo.fijar_texto(_texto_motivo(lote.motivo_cierre))

        total = QLabel(str(lote.conteos.total))
        total.setProperty("rol", "valorMetrica")
        total.setFixedWidth(_ANCHO_HIST_TOTAL)
        total.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )

        quemadas = QLabel(_texto_numero(lote.conteos.quemado))
        quemadas.setProperty("rol", "valorMetrica")
        quemadas.setFixedWidth(_ANCHO_HIST_QUEMADAS)
        quemadas.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )

        layout.addWidget(identificador)
        layout.addWidget(cierre)
        layout.addWidget(motivo, 1)
        layout.addWidget(total)
        layout.addWidget(quemadas)


class Lotes(SeccionBase):
    """Pantalla del lote en curso, el reporte y el historial del sector."""

    id = "lotes"
    titulo = "Lotes"
    icono = "carpeta"

    def __init__(self, ctx: ContextoApp, parent: QWidget | None = None) -> None:
        super().__init__(ctx, parent)
        self._conectado = False
        self._firma_historial: tuple[object, ...] | None = None
        self._construir()
        self.setStyleSheet(self._hoja())
        self._refrescar()

    # --- construcción ---

    def _construir(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 14, 20, 16)
        layout.setSpacing(10)

        subtitulo = QLabel("Lote en curso del sector e historial de cierres.")
        subtitulo.setProperty("rol", "subtitulo")
        layout.addWidget(subtitulo)

        # Vacío y contenido son excluyentes. Se alternan por visibilidad (no en
        # una pila) para que el panel vacío no herede el alto del contenido.
        self._vacio = self._construir_vacio()
        self._contenido = self._construir_contenido()
        layout.addWidget(self._vacio, 1)
        layout.addWidget(self._contenido, 1)
        self._contenido.hide()

    def _construir_vacio(self) -> QFrame:
        """Panel vacío reutilizado por los tres motivos por los que no hay datos."""
        marco = QFrame()
        marco.setObjectName("estadoVacio")

        layout = QVBoxLayout(marco)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(6)

        glifo = QLabel()
        glifo.setPixmap(
            icono("carpeta", color=color("texto_muted"), px=36).pixmap(36, 36)
        )
        glifo.setFixedSize(36, 36)

        self._vacio_titulo = QLabel()
        self._vacio_titulo.setObjectName("vacioTitulo")
        self._vacio_titulo.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._vacio_texto = QLabel()
        self._vacio_texto.setObjectName("vacioTexto")
        self._vacio_texto.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._vacio_texto.setFixedWidth(_ANCHO_TEXTO_VACIO)

        layout.addStretch(1)
        layout.addWidget(glifo, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(self._vacio_titulo)
        layout.addWidget(self._vacio_texto, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addStretch(1)
        return marco

    def _construir_contenido(self) -> QWidget:
        contenido = QWidget()

        layout = QVBoxLayout(contenido)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        layout.addWidget(self._construir_encabezado())
        layout.addWidget(self._construir_lote())

        self._banner_alerta = QLabel()
        self._banner_alerta.setObjectName("avisoAlerta")
        self._banner_alerta.setWordWrap(True)
        self._banner_alerta.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self._banner_alerta.hide()
        layout.addWidget(self._banner_alerta)

        layout.addWidget(self._construir_reporte())
        layout.addWidget(self._construir_historial())
        layout.addStretch(1)
        return contenido

    def _construir_encabezado(self) -> QFrame:
        """Sector, rol, compañeros y el estado del backend."""
        marco = QFrame()
        marco.setObjectName("tarjetaEstado")

        layout = QVBoxLayout(marco)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(6)

        fila = QHBoxLayout()
        fila.setContentsMargins(0, 0, 0, 0)
        fila.setSpacing(12)

        textos = QVBoxLayout()
        textos.setContentsMargins(0, 0, 0, 0)
        textos.setSpacing(2)

        self._sector_nombre = QLabel()
        self._sector_nombre.setObjectName("sectorNombre")

        self._sector_meta = QLabel()
        self._sector_meta.setProperty("rol", "meta")
        self._sector_meta.setWordWrap(True)

        textos.addWidget(self._sector_nombre)
        textos.addWidget(self._sector_meta)

        self._chip_backend = QLabel()
        self._chip_backend.setObjectName("chipEstado")

        fila.addLayout(textos, 1)
        fila.addWidget(
            self._chip_backend, 0, Qt.AlignmentFlag.AlignTop
        )
        layout.addLayout(fila)

        # El mensaje del backend caído se muestra sólo cuando hay algo que decir.
        self._aviso_backend = QLabel()
        self._aviso_backend.setObjectName("avisoLote")
        self._aviso_backend.setWordWrap(True)
        self._aviso_backend.hide()
        layout.addWidget(self._aviso_backend)
        return marco

    def _construir_lote(self) -> QFrame:
        """Lote en curso: identificación, conteos y la acción del rol."""
        marco = QFrame()
        marco.setObjectName("tarjetaEstado")

        layout = QVBoxLayout(marco)
        layout.setContentsMargins(16, 10, 16, 12)
        layout.setSpacing(8)

        cabecera = QHBoxLayout()
        cabecera.setContentsMargins(0, 0, 0, 0)
        cabecera.setSpacing(8)

        rotulo = QLabel("LOTE EN CURSO")
        rotulo.setProperty("rol", "rotulo")

        self._lote_id = QLabel()
        self._lote_id.setProperty("rol", "meta")

        cabecera.addWidget(rotulo)
        cabecera.addStretch(1)
        cabecera.addWidget(self._lote_id)
        layout.addLayout(cabecera)

        self._lote_detalle = self._construir_lote_detalle()
        layout.addWidget(self._lote_detalle)

        self._lote_ausente = QLabel("Sin lote en curso.")
        self._lote_ausente.setObjectName("vacioTexto")
        self._lote_ausente.hide()
        layout.addWidget(self._lote_ausente)
        return marco

    def _construir_lote_detalle(self) -> QWidget:
        bloque = QWidget()

        layout = QVBoxLayout(bloque)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self._fila_producto = _FilaDato("Producto")
        self._fila_abierto = _FilaDato("Abierto")
        self._fila_abrio = _FilaDato("Abierto por")
        layout.addWidget(self._fila_producto)
        layout.addWidget(self._fila_abierto)
        layout.addWidget(self._fila_abrio)

        conteos = QHBoxLayout()
        conteos.setContentsMargins(0, 4, 0, 4)
        conteos.setSpacing(8)
        self._conteos: dict[str, _Conteo] = {}
        for clave, rotulo, tono in _CONTEOS:
            ficha = _Conteo(rotulo, tono)
            self._conteos[clave] = ficha
            conteos.addWidget(ficha, 1)
        layout.addLayout(conteos)

        self._aviso_lote = QLabel()
        self._aviso_lote.setObjectName("avisoLote")
        self._aviso_lote.setWordWrap(True)
        self._aviso_lote.hide()
        layout.addWidget(self._aviso_lote)

        acciones = QHBoxLayout()
        acciones.setContentsMargins(0, 0, 0, 0)
        acciones.setSpacing(10)

        self._lote_cierre = QLabel()
        self._lote_cierre.setProperty("rol", "meta")
        self._lote_cierre.hide()

        self._boton_finalizar = QPushButton("Finalizar lote")
        self._boton_finalizar.setObjectName("botonPrimario")
        self._boton_finalizar.setCursor(Qt.CursorShape.PointingHandCursor)
        self._boton_finalizar.clicked.connect(self._finalizar)

        acciones.addWidget(self._lote_cierre)
        acciones.addStretch(1)
        acciones.addWidget(self._boton_finalizar)
        layout.addLayout(acciones)
        return bloque

    def _construir_reporte(self) -> QFrame:
        """Pendientes de envío y descartes que pueden dejar el conteo incompleto."""
        marco = QFrame()
        marco.setObjectName("tarjetaEstado")

        layout = QVBoxLayout(marco)
        layout.setContentsMargins(16, 10, 16, 12)
        layout.setSpacing(4)

        rotulo = QLabel("REPORTE")
        rotulo.setProperty("rol", "rotulo")
        layout.addWidget(rotulo)

        self._fila_pendientes = _FilaDato("Pendientes de envío")
        self._fila_descartados = _FilaDato("Descartados")
        self._fila_rechazados = _FilaDato("Rechazados")
        for fila in (self._fila_pendientes, self._fila_descartados, self._fila_rechazados):
            layout.addWidget(fila)

        self._reporte_limpio = QLabel("Sin pendientes ni descartes.")
        self._reporte_limpio.setObjectName("vacioTexto")
        layout.addWidget(self._reporte_limpio)
        return marco

    def _construir_historial(self) -> QWidget:
        """Lotes cerrados, del más reciente al más viejo."""
        bloque = QWidget()

        layout = QVBoxLayout(bloque)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        cabecera = QHBoxLayout()
        cabecera.setContentsMargins(0, 0, 0, 0)
        cabecera.setSpacing(8)

        rotulo = QLabel("HISTORIAL")
        rotulo.setProperty("rol", "rotulo")

        self._historial_meta = QLabel()
        self._historial_meta.setProperty("rol", "meta")

        cabecera.addWidget(rotulo)
        cabecera.addStretch(1)
        cabecera.addWidget(self._historial_meta)
        layout.addLayout(cabecera)

        self._historial_vacio = QLabel("Sin lotes cerrados todavía.")
        self._historial_vacio.setObjectName("vacioTexto")
        layout.addWidget(self._historial_vacio)

        self._historial_lista = QWidget()
        self._layout_historial = QVBoxLayout(self._historial_lista)
        self._layout_historial.setContentsMargins(0, 0, 0, 0)
        self._layout_historial.setSpacing(0)
        layout.addWidget(self._historial_lista)
        return bloque

    def _hoja(self) -> str:
        """Estilo local de la sección, con los tokens del tema.

        Se aplica sobre la propia sección, así no toca el QSS global ni otras
        pantallas.
        """
        return f"""
        QLabel#sectorNombre {{
            font-size: 24px;
            font-weight: 600;
            color: {color("texto")};
        }}
        QLabel#avisoLote {{
            color: {color("alerta")};
            border: 1px solid {color("alerta")};
            border-radius: 8px;
            padding: 6px 10px;
            font-size: 13px;
        }}
        QLabel#avisoAlerta {{
            background-color: {color("alerta")};
            color: {color("bg")};
            border-radius: 8px;
            padding: 6px 12px;
            font-weight: 600;
        }}
        QFrame#conteoTile {{
            background-color: {color("panel_alto")};
            border: 1px solid {color("borde")};
            border-radius: 8px;
        }}
        QLabel#conteoValor {{
            font-size: 22px;
            font-weight: 600;
            color: {color("texto")};
            font-family: "JetBrains Mono", "DejaVu Sans Mono", monospace;
        }}
        QLabel#conteoValor[tono="ok"] {{
            color: {color("ok")};
        }}
        QLabel#conteoValor[tono="alerta"] {{
            color: {color("alerta")};
        }}
        QLabel#conteoValor[tono="error"] {{
            color: {color("error")};
        }}
        QLabel#conteoRotulo {{
            font-size: 12px;
            color: {color("texto_muted")};
        }}
        """

    # --- estado ---

    def _refrescar(self) -> None:
        """Relee el servicio de lotes y vuelca la instantánea."""
        try:
            estado = self.ctx.controlador_lotes.estado()
        except RuntimeError as exc:
            # `estado()` no debería fallar, pero si el servicio no arrancó no se
            # deja que rompa la sección: se avisa y se conserva lo último pintado.
            self.ctx.bus.mensaje.emit(str(exc).strip() or _MENSAJE_SERVICIO_CAIDO)
            return

        self._aplicar(estado)

    def _aplicar(self, estado: EstadoLotesUI) -> None:
        if not estado.habilitado:
            self._mostrar_vacio(
                "Función de lotes deshabilitada",
                "El servicio de lotes está apagado en la configuración.",
            )
            return

        if not estado.registrado:
            self._mostrar_vacio(
                "Sin registrar",
                "Registrá el dispositivo en Inicio.",
            )
            return

        if not estado.sector_nombre:
            self._mostrar_vacio(
                "Sin sector asignado",
                "El backend no asignó un sector a este dispositivo.",
            )
            return

        self._mostrar_contenido()
        self._poblar_encabezado(estado)
        self._poblar_lote(estado)
        self._poblar_reporte(estado)
        self._poblar_historial(estado.historial)

    def _mostrar_vacio(self, titulo: str, texto: str) -> None:
        self._vacio_titulo.setText(titulo)
        self._vacio_texto.setText(texto)
        self._contenido.hide()
        self._vacio.show()

    def _mostrar_contenido(self) -> None:
        self._vacio.hide()
        self._contenido.show()

    def _poblar_encabezado(self, estado: EstadoLotesUI) -> None:
        self._sector_nombre.setText(estado.sector_nombre)

        rol = estado.rol.etiqueta if estado.rol is not None else "Rol sin definir"
        companeros = (
            ", ".join(estado.companeros)
            if estado.companeros
            else "sin compañeros en el sector"
        )
        self._sector_meta.setText(f"{rol} · Compañeros: {companeros}")

        ok = estado.backend_ok
        self._chip_backend.setText("Backend ok" if ok else "Backend caído")
        nivel = "activo" if ok else "atencion"
        if self._chip_backend.property("nivel") != nivel:
            self._chip_backend.setProperty("nivel", nivel)
            _repolish(self._chip_backend)

        mensaje = estado.mensaje.strip() if not ok else ""
        if not ok and not mensaje:
            mensaje = "Sin conexión con el backend."
        self._aviso_backend.setText(mensaje)
        self._aviso_backend.setVisible(bool(mensaje))

    def _poblar_lote(self, estado: EstadoLotesUI) -> None:
        lote = estado.lote_activo
        self._lote_detalle.setVisible(lote is not None)
        self._lote_ausente.setVisible(lote is None)
        if lote is None:
            self._lote_id.clear()
            return

        self._lote_id.setText(lote.id)
        self._fila_producto.fijar(lote.producto_nombre or _VALOR_SIN_DATO)
        self._fila_abierto.fijar(lote.abierto_en or _VALOR_SIN_DATO)
        self._fila_abrio.fijar(lote.abierto_por or _VALOR_SIN_DATO)

        conteos = lote.conteos
        self._conteos["ok"].fijar(conteos.ok)
        self._conteos["crudo"].fijar(conteos.crudo)
        self._conteos["quemado"].fijar(conteos.quemado)
        self._conteos["total"].fijar(conteos.total)

        self._poblar_aviso_lote(estado, lote)
        self._poblar_accion_lote(estado)

    def _poblar_aviso_lote(self, estado: EstadoLotesUI, lote: LoteUI) -> None:
        """Junta en un solo aviso las advertencias que apliquen al lote abierto."""
        avisos: list[str] = []
        if lote.degradado:
            avisos.append(
                "Este lote lo abrió la salida: no hay entrada registrada."
            )
        if estado.rol is TipoDispositivoUI.ENTRADA_HORNO and not self._hay_salida(
            estado.companeros
        ):
            avisos.append(
                "No hay una salida registrada en el sector: el lote no se cerrará solo."
            )

        self._aviso_lote.setText("\n".join(avisos))
        self._aviso_lote.setVisible(bool(avisos))

    @staticmethod
    def _hay_salida(companeros: tuple[str, ...]) -> bool:
        """Detecta un compañero de salida.

        Los compañeros llegan formateados como "hostname (rol)"; el único dato
        disponible para distinguirlos es la etiqueta del rol.
        """
        etiqueta = TipoDispositivoUI.SALIDA_HORNO.etiqueta
        return any(etiqueta in companero for companero in companeros)

    def _poblar_accion_lote(self, estado: EstadoLotesUI) -> None:
        """Ajusta el texto de cierre y la acción según el rol del dispositivo."""
        es_salida = estado.rol is TipoDispositivoUI.SALIDA_HORNO

        self._boton_finalizar.setText(
            "Finalizar ahora" if es_salida else "Finalizar lote"
        )

        if not es_salida:
            self._lote_cierre.hide()
            return

        segundos = estado.segundos_para_cierre
        if segundos is None:
            texto = "Sin cierre automático programado."
        else:
            texto = f"Sin detecciones, cierra solo en {segundos:.0f} s."
        self._lote_cierre.setText(texto)
        self._lote_cierre.show()

    def _poblar_reporte(self, estado: EstadoLotesUI) -> None:
        self._fila_pendientes.setVisible(estado.pendientes > 0)
        self._fila_descartados.setVisible(estado.descartados > 0)
        self._fila_rechazados.setVisible(estado.rechazados > 0)
        self._reporte_limpio.setVisible(
            estado.pendientes <= 0
            and estado.descartados <= 0
            and estado.rechazados <= 0
        )

        self._fila_pendientes.fijar(str(estado.pendientes))
        self._fila_descartados.fijar(str(estado.descartados))
        self._fila_rechazados.fijar(str(estado.rechazados))

        if estado.descartados > 0 or estado.rechazados > 0:
            self._banner_alerta.setText(
                f"{estado.descartados} descartados · "
                f"{estado.rechazados} rechazados · "
                "el conteo puede estar incompleto."
            )
            self._banner_alerta.show()
        else:
            self._banner_alerta.clear()
            self._banner_alerta.hide()

    def _poblar_historial(self, historial: tuple[LoteUI, ...]) -> None:
        """Reconstruye la lista sólo si cambió: los lotes cerrados son inmutables."""
        firma = tuple(
            (
                lote.id,
                lote.cerrado_en,
                lote.motivo_cierre,
                lote.conteos.total,
                lote.conteos.quemado,
            )
            for lote in historial
        )
        if firma == self._firma_historial:
            return
        self._firma_historial = firma

        _vaciar_layout(self._layout_historial)

        cantidad = len(historial)
        if cantidad == 0:
            etiqueta = ""
        elif cantidad == 1:
            etiqueta = "1 lote"
        else:
            etiqueta = f"{cantidad} lotes"
        self._historial_meta.setText(etiqueta)
        self._historial_vacio.setVisible(not historial)
        self._historial_lista.setVisible(bool(historial))
        if not historial:
            return

        self._layout_historial.addWidget(self._cabecera_historial())
        for lote in historial:
            self._layout_historial.addWidget(_FilaHistorial(lote))

    @staticmethod
    def _cabecera_historial() -> QWidget:
        """Rótulos de columna, con los mismos anchos que las filas."""
        fila = QFrame()
        fila.setObjectName("filaEstado")

        layout = QHBoxLayout(fila)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        def rotulo(texto: str, ancho: int | None = None) -> QLabel:
            etiqueta = QLabel(texto)
            etiqueta.setProperty("rol", "rotulo")
            if ancho is not None:
                etiqueta.setFixedWidth(ancho)
            return etiqueta

        motivo = rotulo("Motivo")
        total = rotulo("Total", _ANCHO_HIST_TOTAL)
        total.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        quemadas = rotulo("Quemadas", _ANCHO_HIST_QUEMADAS)
        quemadas.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )

        layout.addWidget(rotulo("Lote", _ANCHO_HIST_LOTE))
        layout.addWidget(rotulo("Cierre", _ANCHO_HIST_CIERRE))
        layout.addWidget(motivo, 1)
        layout.addWidget(total)
        layout.addWidget(quemadas)
        return fila

    # --- acciones ---

    def _finalizar(self) -> None:
        """Pide cerrar el lote abierto, previa confirmación del operador."""
        if not self._confirmar_finalizar():
            return
        try:
            self.ctx.controlador_lotes.finalizar_lote()
        except RuntimeError as exc:
            self.ctx.bus.mensaje.emit(str(exc).strip() or _MENSAJE_SERVICIO_CAIDO)
            return
        # El comando no bloquea: releer da feedback inmediato y el bus corrige
        # después si el backend tarda en reflejarlo.
        self._refrescar()

    def _confirmar_finalizar(self) -> bool:
        """Confirmación modal. Devuelve True sólo si el operador acepta."""
        caja = QMessageBox(self)
        caja.setIcon(QMessageBox.Icon.Question)
        caja.setWindowTitle("Finalizar lote")
        caja.setText(
            "¿Cerrar el lote en curso?\n"
            "El conteo queda cerrado con lo detectado hasta ahora."
        )
        aceptar = caja.addButton("Finalizar", QMessageBox.ButtonRole.AcceptRole)
        caja.addButton("Cancelar", QMessageBox.ButtonRole.RejectRole)
        caja.exec()
        return caja.clickedButton() is aceptar

    # --- ciclo de vida ---

    def _al_actualizar_estado(self, _snapshot: object) -> None:
        """El bus avisa cada segundo; la fuente es el servicio de lotes."""
        self._refrescar()

    def _conectar(self) -> None:
        if not self._conectado:
            self.ctx.bus.estado_actualizado.connect(self._al_actualizar_estado)
            self._conectado = True

    def _desconectar(self) -> None:
        if self._conectado:
            self.ctx.bus.estado_actualizado.disconnect(self._al_actualizar_estado)
            self._conectado = False

    def al_entrar(self) -> None:
        """Pide releer catálogo, sector y lote, y escucha el bus."""
        self._conectar()
        self.ctx.controlador_lotes.refrescar()
        self._refrescar()

    def al_salir(self) -> None:
        """Deja de escuchar mientras la sección no se ve."""
        self._desconectar()

    def al_cerrar(self) -> None:
        self._desconectar()
