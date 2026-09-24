"""Sección Métricas: qué mide la Raspberry y qué logró enviar al backend.

Las métricas que se muestran son las del monitor local del dispositivo: una
lectura que todavía no se pudo enviar se ve igual. Cada campo puede faltar
(`None`) y se muestra como "—", nunca como un cero inventado.

El refresco sigue el patrón de Inicio: el bus avisa cada segundo y la sección
vuelve a leer `ControladorMonitor.estado()`. Si el monitor no responde, el
error queda en un aviso visible y la sección no se rompe.

`estado.recientes` llega del más nuevo al más viejo: así se llena la tira de
últimos envíos, que es lo que el operador espera ver primero.
"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from frontend.nucleo.contrato import ContextoApp, SeccionBase
from frontend.nucleo.controlador_monitor import (
    EnvioMonitorUI,
    EstadoMonitorUI,
    MetricasUI,
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

# Un envío que no llegó no es "atención": va en rojo, como el banner de error.
_NIVEL_ERROR = "error"

_VALOR_SIN_DATO = "—"
_MENSAJE_MONITOR_CAIDO = "El monitor de métricas no está disponible."

_ANCHO_PUNTO = 14
_ANCHO_COLUMNA_ENVIO = 240
_ANCHO_TEXTO_VACIO = 520

# Alturas medidas para que las dos filas de envíos entren en los 400 px del
# viewport junto a las dos tarjetas, sin depender del scroll.
_ALTO_FILA_METRICA = 26
_ALTO_FILA_ENVIO = 24
_ALTO_TILE_ENVIO = 48

# Tira de últimos envíos: dos filas de cinco, de la más reciente a la más vieja.
_ENVIOS_POR_FILA = 5
_FILAS_ENVIOS = 2
_ENVIOS_VISIBLES = _ENVIOS_POR_FILA * _FILAS_ENVIOS

# Umbrales de aviso. La temperatura es la que más importa en la Pi: a 80 °C
# arranca el recorte de frecuencia, así que se avisa bastante antes.
_UMBRAL_CPU = 85.0
_UMBRAL_USO = 90.0
_UMBRAL_TEMP = 70.0

# Filas de la tarjeta de recolección: (clave, etiqueta).
_FILAS_METRICA: tuple[tuple[str, str], ...] = (
    ("cpu", "CPU"),
    ("ram", "RAM libre"),
    ("disco", "Disco libre"),
    ("temp", "Temperatura"),
    ("ia", "Procesador IA"),
)

# Filas de la tarjeta de envío: (clave, etiqueta, lleva semáforo).
_FILAS_ENVIO: tuple[tuple[str, str, bool], ...] = (
    ("registro", "Registro", True),
    ("ultimo", "Último envío", False),
    ("resultado", "Resultado", True),
    ("ok", "Enviados ok", False),
    ("error", "Con error", False),
)


def _repolish(widget: QWidget) -> None:
    """Reaplica la hoja de estilos tras cambiar una propiedad que un selector usa."""
    estilo = widget.style()
    estilo.unpolish(widget)
    estilo.polish(widget)
    widget.update()


def _prop_nivel(nivel: NivelEstado | str) -> str:
    """Valor de la propiedad `nivel` que consumen los selectores del QSS."""
    return nivel.value if isinstance(nivel, NivelEstado) else nivel


def _glifo(nivel: NivelEstado | str) -> str:
    """Glifo del semáforo. Un nivel fuera del enum (error) usa el punto lleno."""
    if isinstance(nivel, NivelEstado):
        return _GLIFO_NIVEL[nivel]
    return "●"


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


class _Fila(QFrame):
    """Fila etiqueta/valor de las tarjetas.

    `punto` agrega el semáforo de nivel; `destacado` usa el valor fuerte (el
    número de una métrica) en vez del secundario (el detalle de un envío). Sin
    semáforo igual se reserva su ancho, así las etiquetas quedan alineadas.
    """

    def __init__(
        self,
        etiqueta: str,
        alto: int,
        *,
        punto: bool = False,
        destacado: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("filaEstado")
        self.setFixedHeight(alto)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self._punto: QLabel | None = None
        if punto:
            self._punto = QLabel()
            self._punto.setObjectName("puntoEstado")
            self._punto.setFixedWidth(_ANCHO_PUNTO)
            self._punto.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(self._punto)
        else:
            layout.addSpacing(_ANCHO_PUNTO)

        etiqueta_lbl = QLabel(etiqueta)
        etiqueta_lbl.setObjectName("etiquetaEstado")

        self._valor = _EtiquetaElidida()
        if destacado:
            # Sin objectName: el estilo lo da el rol, para que no compita con
            # QLabel#detalleEstado (un selector de id gana al de atributo).
            self._valor.setProperty("rol", "valorMetrica")
        else:
            self._valor.setObjectName("detalleEstado")
        self._valor.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )

        layout.addWidget(etiqueta_lbl)
        layout.addWidget(self._valor, 1)

        if self._punto is not None:
            self._punto.setText(_glifo(NivelEstado.SIN_DATO))

    def fijar(
        self, valor: str, nivel: NivelEstado | str = NivelEstado.SIN_DATO
    ) -> None:
        """Actualiza el valor y, sólo si cambió, el semáforo de nivel."""
        if self._punto is not None:
            propiedad = _prop_nivel(nivel)
            # Repolish sólo cuando cambia el nivel: esto se refresca cada segundo.
            if self._punto.property("nivel") != propiedad:
                self._punto.setProperty("nivel", propiedad)
                _repolish(self._punto)
            self._punto.setText(_glifo(nivel))
        self._valor.fijar_texto(valor)


class _TileEnvio(QFrame):
    """Ficha compacta de un envío: hora arriba, resultado con semáforo abajo."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("tileEnvio")
        self.setFixedHeight(_ALTO_TILE_ENVIO)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(1)

        self._hora = QLabel()
        self._hora.setObjectName("detalleEstado")
        self._hora.setAlignment(Qt.AlignmentFlag.AlignCenter)

        fila = QHBoxLayout()
        fila.setContentsMargins(0, 0, 0, 0)
        fila.setSpacing(0)

        self._punto = QLabel()
        self._punto.setObjectName("puntoEstado")
        self._punto.setFixedWidth(_ANCHO_PUNTO)
        self._punto.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._punto.setText("●")

        self._estado = QLabel()
        self._estado.setObjectName("etiquetaEstado")

        fila.addStretch(1)
        fila.addWidget(self._punto)
        fila.addWidget(self._estado)
        fila.addStretch(1)

        layout.addWidget(self._hora)
        layout.addLayout(fila)

    def fijar(self, envio: EnvioMonitorUI) -> None:
        """Refleja un envío: hora, resultado y el detalle completo en el tooltip."""
        self._hora.setText(envio.momento.strftime("%H:%M"))
        nivel = "activo" if envio.ok else _NIVEL_ERROR

        if self._punto.property("nivel") != nivel:
            self._punto.setProperty("nivel", nivel)
            _repolish(self._punto)
        # El borde de la ficha también marca el fallo: la tira se lee de un vistazo.
        if self.property("nivel") != nivel:
            self.setProperty("nivel", nivel)
            _repolish(self)

        self._estado.setText("ok" if envio.ok else "error")
        self.setToolTip(self._detalle(envio))

    @staticmethod
    def _detalle(envio: EnvioMonitorUI) -> str:
        momento = envio.momento.strftime("%d/%m/%Y %H:%M:%S")
        if envio.ok:
            return f"Envío correcto · {momento}"
        return f"Envío fallido · {momento}\n{envio.error or 'sin detalle'}"


def _texto_pct(valor: float | None) -> str:
    """Porcentaje del payload. Sin lectura, guion: no se inventa un cero."""
    return f"{valor:.1f} %" if valor is not None else _VALOR_SIN_DATO


def _texto_temp(valor: float | None) -> str:
    return f"{valor:.1f} °C" if valor is not None else _VALOR_SIN_DATO


def _texto_tamano(mb: float | None) -> str:
    """MB del payload en un texto con unidad explícita (GB cuando ya no se lee)."""
    if mb is None:
        return _VALOR_SIN_DATO
    if mb >= 1024.0:
        return f"{mb / 1024.0:.1f} GB"
    return f"{mb:.0f} MB"


def _texto_par(disponible: float | None, total: float | None) -> str:
    """Par disponible/total. El backend exige ambos o ninguno; acá falta cualquiera."""
    if disponible is None and total is None:
        return _VALOR_SIN_DATO
    if total is None:
        return _texto_tamano(disponible)
    if disponible is None:
        return f"{_VALOR_SIN_DATO} / {_texto_tamano(total)}"
    return f"{_texto_tamano(disponible)} / {_texto_tamano(total)}"


def _texto_momento(momento: datetime | None) -> str:
    """Hora del envío; si no es de hoy, se aclara la fecha."""
    if momento is None:
        return _VALOR_SIN_DATO
    if momento.date() == datetime.now().date():
        return momento.strftime("%H:%M:%S")
    return momento.strftime("%d/%m %H:%M")


def _uso_pct(disponible: float | None, total: float | None) -> float | None:
    """Porcentaje usado derivado del par. None si falta alguno de los dos."""
    if disponible is None or total is None or total <= 0:
        return None
    return (total - disponible) / total * 100.0


def _nivel_uso(uso: float | None) -> NivelEstado:
    if uso is None:
        return NivelEstado.SIN_DATO
    return NivelEstado.ATENCION if uso >= _UMBRAL_USO else NivelEstado.ACTIVO


def _nivel_cpu(cpu: float | None) -> NivelEstado:
    if cpu is None:
        return NivelEstado.SIN_DATO
    return NivelEstado.ATENCION if cpu >= _UMBRAL_CPU else NivelEstado.ACTIVO


def _nivel_temp(temp: float | None) -> NivelEstado:
    if temp is None:
        return NivelEstado.SIN_DATO
    return NivelEstado.ATENCION if temp >= _UMBRAL_TEMP else NivelEstado.ACTIVO


def _nivel_ia(ia: float | None) -> NivelEstado:
    # Sin umbral: acá el dato es que el acelerador esté leyéndose.
    return NivelEstado.SIN_DATO if ia is None else NivelEstado.ACTIVO


class Metricas(SeccionBase):
    """Panel del monitor: qué mide la máquina y qué logró enviar."""

    id = "metricas"
    titulo = "Métricas"
    icono = "metricas"

    def __init__(self, ctx: ContextoApp, parent: QWidget | None = None) -> None:
        super().__init__(ctx, parent)
        self._error_monitor = ""
        self._error_envio = ""
        self._construir()
        self._refrescar()
        self.ctx.bus.estado_actualizado.connect(self._al_actualizar_estado)

    # --- construcción ---

    def _construir(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 14, 20, 16)
        layout.setSpacing(10)

        subtitulo = QLabel("Lo que la Raspberry mide y lo que logra enviar.")
        subtitulo.setProperty("rol", "subtitulo")
        layout.addWidget(subtitulo)

        self._meta = QLabel()
        self._meta.setProperty("rol", "meta")
        layout.addWidget(self._meta)

        self._banner_error = self._construir_banner()
        layout.addWidget(self._banner_error)

        # Vacío y contenido son excluyentes: en una pila cada uno se queda con
        # toda la altura disponible. Es lo que hace que el vacío sea un panel
        # entero y que el contenido quede arriba, sin repartir espacio entre dos.
        self._vacio = self._construir_vacio()
        self._contenido = self._construir_contenido()

        self._pila = QStackedWidget()
        self._pila.addWidget(self._vacio)
        self._pila.addWidget(self._contenido)
        layout.addWidget(self._pila, 1)

    def _construir_banner(self) -> QLabel:
        """Aviso de error: mismo tratamiento que el de En vivo."""
        banner = QLabel()
        banner.setObjectName("bannerError")
        banner.setWordWrap(True)
        banner.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        banner.setStyleSheet(
            "QLabel#bannerError {"
            f" background-color: {color('error')};"
            f" color: {color('bg')};"
            " border-radius: 8px;"
            " padding: 6px 12px;"
            " font-weight: 600;"
            " }"
        )
        banner.hide()
        return banner

    def _construir_vacio(self) -> QFrame:
        """Estado vacío: aparece sólo mientras no haya nada recolectado ni enviado."""
        marco = QFrame()
        marco.setObjectName("estadoVacio")

        layout = QVBoxLayout(marco)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(6)

        glifo = QLabel()
        glifo.setPixmap(
            icono("metricas", color=color("texto_muted"), px=36).pixmap(36, 36)
        )
        glifo.setFixedSize(36, 36)

        self._vacio_titulo = QLabel()
        self._vacio_titulo.setObjectName("vacioTitulo")
        self._vacio_titulo.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._vacio_texto = QLabel()
        self._vacio_texto.setObjectName("vacioTexto")
        self._vacio_texto.setTextFormat(Qt.TextFormat.PlainText)
        self._vacio_texto.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # Sin wordWrap y con saltos explícitos: el alto de un QLabel con wrap no
        # se recalcula al cambiar el ancho de la ventana (Qt usaba el alto de una
        # línea y el texto se solapaba). Con líneas fijas el alto es estable.
        self._vacio_texto.setFixedWidth(_ANCHO_TEXTO_VACIO)

        # Los dos tramos hacen que el bloque quede centrado en la altura del panel.
        layout.addStretch(1)
        layout.addWidget(glifo, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(self._vacio_titulo)
        layout.addWidget(self._vacio_texto, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addStretch(1)
        return marco

    def _construir_contenido(self) -> QWidget:
        """Tarjetas de recolección y envío más la tira de últimos envíos."""
        contenido = QWidget()

        layout = QVBoxLayout(contenido)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        fila = QHBoxLayout()
        fila.setContentsMargins(0, 0, 0, 0)
        fila.setSpacing(12)
        fila.addWidget(self._construir_metricas(), 1)
        fila.addWidget(self._construir_envio())
        layout.addLayout(fila)

        layout.addWidget(self._construir_envios())
        # El contenido arranca arriba: el sobrante queda abajo, no estirado.
        layout.addStretch(1)
        return contenido

    def _construir_metricas(self) -> QFrame:
        """Tarjeta con la última recolección."""
        marco = QFrame()
        marco.setObjectName("tarjetaEstado")

        layout = QVBoxLayout(marco)
        layout.setContentsMargins(14, 8, 14, 10)
        layout.setSpacing(0)

        rotulo = QLabel("ÚLTIMA RECOLECCIÓN")
        rotulo.setProperty("rol", "rotulo")
        layout.addWidget(rotulo)
        layout.addSpacing(2)

        self._filas_metrica: dict[str, _Fila] = {}
        for indice, (clave, etiqueta) in enumerate(_FILAS_METRICA):
            fila = _Fila(etiqueta, _ALTO_FILA_METRICA, punto=True, destacado=True)
            if indice == len(_FILAS_METRICA) - 1:
                fila.setProperty("ultima", "true")
            self._filas_metrica[clave] = fila
            layout.addWidget(fila)

        layout.addStretch(1)
        return marco

    def _construir_envio(self) -> QFrame:
        """Tarjeta con el estado del envío al backend."""
        marco = QFrame()
        marco.setObjectName("tarjetaEstado")
        marco.setFixedWidth(_ANCHO_COLUMNA_ENVIO)

        layout = QVBoxLayout(marco)
        layout.setContentsMargins(14, 8, 14, 10)
        layout.setSpacing(0)

        rotulo = QLabel("ENVÍO AL BACKEND")
        rotulo.setProperty("rol", "rotulo")
        layout.addWidget(rotulo)
        layout.addSpacing(2)

        self._filas_envio: dict[str, _Fila] = {}
        for indice, (clave, etiqueta, con_punto) in enumerate(_FILAS_ENVIO):
            fila = _Fila(etiqueta, _ALTO_FILA_ENVIO, punto=con_punto)
            if indice == len(_FILAS_ENVIO) - 1:
                fila.setProperty("ultima", "true")
            self._filas_envio[clave] = fila
            layout.addWidget(fila)

        layout.addStretch(1)
        return marco

    def _construir_envios(self) -> QWidget:
        """Dos filas de fichas con los últimos envíos. Se oculta si no hay ninguno."""
        bloque = QWidget()

        layout = QVBoxLayout(bloque)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        encabezado = QHBoxLayout()
        encabezado.setContentsMargins(0, 0, 0, 0)
        encabezado.setSpacing(8)

        rotulo = QLabel("ÚLTIMOS ENVÍOS")
        rotulo.setProperty("rol", "rotulo")
        # Sin esta aclaración la tira se puede leer al revés (como un log viejo).
        orden = QLabel("primero el más reciente")
        orden.setProperty("rol", "meta")

        encabezado.addWidget(rotulo)
        encabezado.addStretch(1)
        encabezado.addWidget(orden)
        layout.addLayout(encabezado)

        self._tiles: list[_TileEnvio] = []
        for _ in range(_FILAS_ENVIOS):
            fila = QHBoxLayout()
            fila.setContentsMargins(0, 0, 0, 0)
            fila.setSpacing(8)
            for _ in range(_ENVIOS_POR_FILA):
                tile = _TileEnvio()
                self._tiles.append(tile)
                fila.addWidget(tile, 1)
            layout.addLayout(fila)

        self._bloque_envios = bloque
        return bloque

    # --- estado ---

    def _refrescar(self) -> None:
        """Relee el monitor y vuelca la instantánea. Un fallo se muestra, no rompe."""
        try:
            estado = self.ctx.controlador_monitor.estado()
        except RuntimeError as exc:
            self._reportar_monitor_caido(exc)
            return

        self._error_monitor = ""
        self._aplicar(estado)
        self._actualizar_banner()

    def _aplicar(self, estado: EstadoMonitorUI) -> None:
        self._actualizar_meta(estado)
        self._error_envio = self._detalle_error(estado.ultimo_envio)

        if estado.recolectadas is None and estado.ultimo_envio is None:
            self._mostrar_vacio(estado)
            return

        # setCurrentWidget no hace nada si ya está: no hay parpadeo por segundo.
        self._pila.setCurrentWidget(self._contenido)
        self._poblar_metricas(estado.recolectadas)
        self._poblar_envio(estado)
        self._poblar_envios(estado.recientes)

    def _actualizar_meta(self, estado: EstadoMonitorUI) -> None:
        cadencia = (
            f"cada {estado.intervalo_segundos:.0f} s"
            if estado.intervalo_segundos > 0
            else "sin intervalo configurado"
        )
        acelerador = estado.acelerador or "no detectado"
        self._meta.setText(f"Envío {cadencia} · Acelerador IA: {acelerador}")

    @staticmethod
    def _detalle_error(envio: EnvioMonitorUI | None) -> str:
        """Motivo del último fallo, para el banner. Vacío si el envío salió bien."""
        if envio is None or envio.ok:
            return ""
        return envio.error or "el backend rechazó el envío"

    def _mostrar_vacio(self, estado: EstadoMonitorUI) -> None:
        self._pila.setCurrentWidget(self._vacio)

        if estado.registrado:
            self._vacio_titulo.setText("Todavía sin recolección")
            self._vacio_texto.setText(
                "El dispositivo está registrado. Las métricas aparecen\n"
                "apenas el monitor haga la primera lectura."
            )
        else:
            self._vacio_titulo.setText("Sin registrar")
            self._vacio_texto.setText(
                "Las métricas se envían recién cuando el backend aprueba\n"
                "el alta. El registro se pide desde Inicio."
            )

    def _poblar_metricas(self, metricas: MetricasUI | None) -> None:
        """Vuelca la última recolección. Sin recolección, todas las filas en guion."""
        cpu = metricas.cpu_pct if metricas is not None else None
        ram_disp = metricas.mem_ram_disponible_mb if metricas is not None else None
        ram_total = metricas.mem_ram_total_mb if metricas is not None else None
        disco_disp = (
            metricas.almacenamiento_disponible_mb if metricas is not None else None
        )
        disco_total = (
            metricas.almacenamiento_total_mb if metricas is not None else None
        )
        temp = metricas.temp_chip if metricas is not None else None
        ia = metricas.ai_processor_pct if metricas is not None else None

        filas = self._filas_metrica
        filas["cpu"].fijar(_texto_pct(cpu), _nivel_cpu(cpu))
        filas["ram"].fijar(
            _texto_par(ram_disp, ram_total), _nivel_uso(_uso_pct(ram_disp, ram_total))
        )
        filas["disco"].fijar(
            _texto_par(disco_disp, disco_total),
            _nivel_uso(_uso_pct(disco_disp, disco_total)),
        )
        filas["temp"].fijar(_texto_temp(temp), _nivel_temp(temp))
        filas["ia"].fijar(_texto_pct(ia), _nivel_ia(ia))

    def _poblar_envio(self, estado: EstadoMonitorUI) -> None:
        filas = self._filas_envio

        registrado = estado.registrado
        filas["registro"].fijar(
            "Registrado" if registrado else "Sin registrar",
            NivelEstado.ACTIVO if registrado else NivelEstado.INACTIVO,
        )

        ultimo = estado.ultimo_envio
        filas["ultimo"].fijar(_texto_momento(ultimo.momento if ultimo else None))
        if ultimo is None:
            filas["resultado"].fijar(_VALOR_SIN_DATO, NivelEstado.SIN_DATO)
        else:
            filas["resultado"].fijar(
                "ok" if ultimo.ok else "error",
                NivelEstado.ACTIVO if ultimo.ok else _NIVEL_ERROR,
            )

        filas["ok"].fijar(str(estado.envios_ok))
        filas["error"].fijar(str(estado.envios_error))

    def _poblar_envios(self, recientes: tuple[EnvioMonitorUI, ...]) -> None:
        """Llena las fichas de más nuevo a más viejo, fila por fila, y esconde las que sobran.

        Se asume el contrato de `recientes`: el índice 0 es el envío más reciente.
        Las fichas se crean en orden (fila 1 de izquierda a derecha, después la
        fila 2), así el índice de la lista es el índice de la ficha.
        """
        visibles = recientes[:_ENVIOS_VISIBLES]
        self._bloque_envios.setVisible(bool(visibles))

        for indice, tile in enumerate(self._tiles):
            if indice < len(visibles):
                tile.fijar(visibles[indice])
                tile.setVisible(True)
            else:
                tile.setVisible(False)

    def _reportar_monitor_caido(self, exc: RuntimeError) -> None:
        """Deja el fallo a la vista y avisa por el bus; no rompe la sección."""
        mensaje = str(exc).strip() or _MENSAJE_MONITOR_CAIDO
        # El refresco repite el mismo fallo cada segundo: avisar una sola vez.
        if mensaje != self._error_monitor:
            self.ctx.bus.mensaje.emit(mensaje)
        self._error_monitor = mensaje
        self._actualizar_banner()

    def _actualizar_banner(self) -> None:
        """Manda el error del monitor; si no hay, el del último envío."""
        mensaje = self._error_monitor or self._error_envio
        if mensaje:
            self._banner_error.setText(f"Error: {mensaje}")
            self._banner_error.show()
        else:
            self._banner_error.clear()
            self._banner_error.hide()

    # --- ciclo de vida ---

    def _al_actualizar_estado(self, _snapshot: object) -> None:
        """El bus avisa cada segundo; la fuente es el monitor, no el snapshot."""
        self._refrescar()

    def al_entrar(self) -> None:
        self._refrescar()
