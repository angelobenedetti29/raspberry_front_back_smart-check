"""Ventana principal: shell de navegación con rail lateral y secciones aisladas."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QCloseEvent, QIcon
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from frontend.nucleo.bus import BusApp
from frontend.nucleo.contrato import ContextoApp, SeccionBase
from frontend.nucleo.estado import NivelEstado, SnapshotSistema
from frontend.nucleo.iconos import icono
from frontend.nucleo.proveedor import ProveedorServicio
from frontend.nucleo.registro import SECCIONES, secciones_visibles
from frontend.nucleo.tema import color

if TYPE_CHECKING:
    from backend.config import AppConfig

    from frontend.nucleo.controlador import ControladorStreaming
    from frontend.nucleo.controlador_config import ControladorConfig
    from frontend.nucleo.controlador_dispositivo import (
        ControladorDispositivo,
        TipoDispositivoUI,
    )
    from frontend.nucleo.controlador_lotes import ControladorLotes
    from frontend.nucleo.controlador_monitor import ControladorMonitor

logger = logging.getLogger(__name__)

ANCHO_RAIL = 176
ALTO_ENCABEZADO = 52
ALTO_BARRA_ESTADO = 28
ANCHO_VENTANA = 800
ALTO_VENTANA = 480
_INTERVALO_RELOJ_MS = 1000
_INTERVALO_ESTADO_MS = 1000
_ICONO_NAV_PX = 24


class MainWindow(QMainWindow):
    """Shell: rail de navegación, encabezado, stack de secciones y barra de estado.

    El shell conoce solo la metadata de las secciones (id, título, ícono) y su
    ciclo de vida. Las instancia de forma perezosa la primera vez que se entra.
    """

    def __init__(
        self,
        config: AppConfig,
        controlador: ControladorStreaming,
        controlador_dispositivo: ControladorDispositivo,
        controlador_monitor: ControladorMonitor,
        controlador_config: ControladorConfig,
        controlador_lotes: ControladorLotes,
    ) -> None:
        super().__init__()
        self._config = config
        # Se guarda para leer el tipo de dispositivo en el tick de estado.
        self._controlador_dispositivo = controlador_dispositivo

        self.setWindowTitle("Raspberry Control Panel")
        self.resize(ANCHO_VENTANA, ALTO_VENTANA)

        self._bus = BusApp(self)
        self._proveedor = ProveedorServicio(config, controlador)
        self._ctx = ContextoApp(
            config=config,
            bus=self._bus,
            proveedor=self._proveedor,
            controlador=controlador,
            controlador_dispositivo=controlador_dispositivo,
            controlador_monitor=controlador_monitor,
            controlador_config=controlador_config,
            controlador_lotes=controlador_lotes,
            navegar=self._navegar,
        )

        self._secciones: dict[int, SeccionBase] = {}
        self._paginas: dict[int, QScrollArea] = {}
        # Botón del rail por índice de `SECCIONES` (índices estables).
        self._botones_nav: dict[int, QPushButton] = {}
        self._indice_actual = -1
        # Último tipo con el que se aplicó la visibilidad del rail.
        self._tipo_visibilidad: TipoDispositivoUI | None = None

        self._construir_ui()

        self._bus.navegacion_solicitada.connect(self._navegar)
        self._bus.estado_actualizado.connect(self._al_actualizar_estado)

        # El rail arranca mostrando lo que corresponda al tipo ya registrado.
        self._aplicar_visibilidad(self._tipo_dispositivo())
        self._activar(0)
        self._aplicar_estado(self._proveedor.snapshot())

        # Sin este refresco, nada emite `estado_actualizado` y el chip del
        # encabezado y la barra de estado quedan congelados en el primer snapshot.
        self._temporizador_estado = QTimer(self)
        self._temporizador_estado.setInterval(_INTERVALO_ESTADO_MS)
        self._temporizador_estado.timeout.connect(self._emitir_estado)
        self._temporizador_estado.start()

    # --- construcción del shell ---

    def _construir_ui(self) -> None:
        central = QWidget()
        central.setObjectName("central")
        raiz = QVBoxLayout(central)
        raiz.setContentsMargins(0, 0, 0, 0)
        raiz.setSpacing(0)

        fila = QWidget()
        fila.setObjectName("filaPrincipal")
        fila_layout = QHBoxLayout(fila)
        fila_layout.setContentsMargins(0, 0, 0, 0)
        fila_layout.setSpacing(0)

        fila_layout.addWidget(self._construir_rail())

        columna = QWidget()
        columna.setObjectName("columnaContenido")
        columna_layout = QVBoxLayout(columna)
        columna_layout.setContentsMargins(0, 0, 0, 0)
        columna_layout.setSpacing(0)
        columna_layout.addWidget(self._construir_encabezado())

        self._stack = QStackedWidget()
        self._stack.setObjectName("stack")
        columna_layout.addWidget(self._stack, 1)

        fila_layout.addWidget(columna, 1)
        raiz.addWidget(fila, 1)
        raiz.addWidget(self._construir_barra_estado())

        self.setCentralWidget(central)

    def _construir_rail(self) -> QFrame:
        rail = QFrame()
        rail.setObjectName("rail")
        rail.setFixedWidth(ANCHO_RAIL)

        layout = QVBoxLayout(rail)
        layout.setContentsMargins(12, 16, 12, 16)
        layout.setSpacing(4)

        marca_titulo = QLabel("Raspberry Panel")
        marca_titulo.setObjectName("marcaTitulo")
        marca_subtitulo = QLabel("Control de calidad")
        marca_subtitulo.setObjectName("marcaSubtitulo")
        layout.addWidget(marca_titulo)
        layout.addWidget(marca_subtitulo)
        layout.addSpacing(16)

        self._grupo_nav = QButtonGroup(self)
        self._grupo_nav.setExclusive(True)
        # Se construyen TODOS los botones una sola vez para que los índices
        # del grupo (y de `SECCIONES`) queden estables; la visibilidad se
        # resuelve después con `_aplicar_visibilidad`.
        for indice, clase in enumerate(SECCIONES):
            boton = self._crear_boton_nav(clase)
            self._grupo_nav.addButton(boton, indice)
            self._botones_nav[indice] = boton
            layout.addWidget(boton)

        layout.addStretch(1)
        self._grupo_nav.idClicked.connect(self._activar)
        return rail

    def _crear_boton_nav(self, clase: type[SeccionBase]) -> QPushButton:
        boton = QPushButton(clase.titulo)
        boton.setObjectName("navBoton")
        boton.setCheckable(True)
        boton.setCursor(Qt.CursorShape.PointingHandCursor)
        boton.setIconSize(QSize(_ICONO_NAV_PX, _ICONO_NAV_PX))
        boton.setFixedHeight(56)
        boton.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

        # Mismo SVG en dos colores: apagado en reposo, acento al quedar seleccionado.
        combinado = QIcon()
        combinado.addPixmap(
            icono(clase.icono, color=color("texto_muted"), px=_ICONO_NAV_PX).pixmap(
                _ICONO_NAV_PX, _ICONO_NAV_PX
            ),
            QIcon.Mode.Normal,
            QIcon.State.Off,
        )
        combinado.addPixmap(
            icono(clase.icono, color=color("accent"), px=_ICONO_NAV_PX).pixmap(
                _ICONO_NAV_PX, _ICONO_NAV_PX
            ),
            QIcon.Mode.Normal,
            QIcon.State.On,
        )
        boton.setIcon(combinado)
        return boton

    def _construir_encabezado(self) -> QFrame:
        marco = QFrame()
        marco.setObjectName("encabezadoSeccion")
        marco.setFixedHeight(ALTO_ENCABEZADO)

        layout = QHBoxLayout(marco)
        layout.setContentsMargins(20, 0, 20, 0)
        layout.setSpacing(12)

        self._titulo_seccion = QLabel("")
        self._titulo_seccion.setObjectName("tituloSeccion")
        self._chip_estado = QLabel("")
        self._chip_estado.setObjectName("chipEstado")
        self._chip_estado.setAlignment(Qt.AlignmentFlag.AlignCenter)

        layout.addWidget(self._titulo_seccion)
        layout.addStretch(1)
        layout.addWidget(self._chip_estado)
        return marco

    def _construir_barra_estado(self) -> QFrame:
        marco = QFrame()
        marco.setObjectName("barraEstado")
        marco.setFixedHeight(ALTO_BARRA_ESTADO)

        layout = QHBoxLayout(marco)
        layout.setContentsMargins(16, 0, 16, 0)
        layout.setSpacing(12)

        self._resumen_estado = QLabel("")
        self._resumen_estado.setProperty("rol", "muted")
        self._reloj = QLabel("")
        self._reloj.setProperty("rol", "muted")
        self._reloj.setProperty("mono", "true")

        layout.addWidget(self._resumen_estado)
        layout.addStretch(1)
        layout.addWidget(self._reloj)

        self._temporizador = QTimer(self)
        self._temporizador.setInterval(_INTERVALO_RELOJ_MS)
        self._temporizador.timeout.connect(self._actualizar_reloj)
        self._temporizador.start()
        self._actualizar_reloj()
        return marco

    # --- navegación ---

    def _tipo_dispositivo(self) -> TipoDispositivoUI | None:
        """Tipo de dispositivo registrado, tal como lo ve la UI."""
        return self._controlador_dispositivo.estado().tipo

    def _navegar(self, seccion_id: str) -> None:
        """Cambia de sección por id lógico (lo que usan las secciones).

        Las secciones ocultas para el tipo actual se ignoran: navegar a una
        de ellas dejaría el rail inconsistente.
        """
        visibles = secciones_visibles(self._tipo_visibilidad)
        for indice, clase in enumerate(SECCIONES):
            if clase.id == seccion_id:
                if clase not in visibles:
                    logger.warning("Sección no disponible: %s", seccion_id)
                    return
                self._activar(indice)
                return
        logger.warning("Sección desconocida: %s", seccion_id)

    def _activar(self, indice: int) -> None:
        if indice < 0 or indice >= len(SECCIONES) or indice == self._indice_actual:
            return
        if SECCIONES[indice] not in secciones_visibles(self._tipo_visibilidad):
            return

        actual = self._secciones.get(self._indice_actual)
        if actual is not None:
            actual.al_salir()

        seccion = self._obtener_seccion(indice)
        self._stack.setCurrentWidget(self._paginas[indice])
        seccion.al_entrar()
        self._indice_actual = indice
        self._titulo_seccion.setText(seccion.titulo)

        boton = self._grupo_nav.button(indice)
        if boton is not None:
            boton.setChecked(True)

    def _obtener_seccion(self, indice: int) -> SeccionBase:
        """Instancia la sección la primera vez que se entra y la envuelve en scroll."""
        existente = self._secciones.get(indice)
        if existente is not None:
            return existente

        clase = SECCIONES[indice]
        seccion = clase(self._ctx, parent=self._stack)
        seccion.setObjectName(f"seccion_{clase.id}")

        pagina = QScrollArea()
        pagina.setObjectName("scrollSeccion")
        pagina.setWidgetResizable(True)
        pagina.setFrameShape(QFrame.Shape.NoFrame)
        pagina.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        pagina.setWidget(seccion)

        self._stack.addWidget(pagina)
        self._paginas[indice] = pagina
        self._secciones[indice] = seccion
        logger.debug("Sección instanciada: %s", clase.id)
        return seccion

    def _aplicar_visibilidad(self, tipo: TipoDispositivoUI | None) -> None:
        """Muestra u oculta botones del rail según el tipo de dispositivo.

        Los botones existen siempre y sólo cambia su visibilidad, así los
        índices del `QButtonGroup` no se corren. Si la sección activa deja de
        corresponder, se vuelve a Inicio.
        """
        visibles = secciones_visibles(tipo)
        for indice, clase in enumerate(SECCIONES):
            boton = self._botones_nav.get(indice)
            if boton is not None:
                boton.setVisible(clase in visibles)
        self._tipo_visibilidad = tipo

        if 0 <= self._indice_actual < len(SECCIONES):
            activa = SECCIONES[self._indice_actual]
            if activa not in visibles:
                self._navegar(SECCIONES[0].id)

    # --- estado ---

    def _actualizar_reloj(self) -> None:
        self._reloj.setText(datetime.now().strftime("%H:%M:%S"))

    def _aplicar_estado(self, snapshot: SnapshotSistema) -> None:
        nivel = self._nivel_global(snapshot)
        self._chip_estado.setText(f"● {snapshot.resumen}")
        self._chip_estado.setProperty("nivel", nivel.value)
        self._repolish(self._chip_estado)
        self._resumen_estado.setText(
            f"{snapshot.resumen} · modelo {snapshot.modelo_activo}"
        )

    def _emitir_estado(self) -> None:
        """Publica una instantánea fresca y revalida la visibilidad del rail."""
        self._bus.estado_actualizado.emit(self._proveedor.snapshot())
        # El tipo puede cambiar cuando el operador registra el dispositivo;
        # sólo se recalcula la visibilidad si efectivamente cambió.
        tipo = self._tipo_dispositivo()
        if tipo is not self._tipo_visibilidad:
            self._aplicar_visibilidad(tipo)

    def _al_actualizar_estado(self, snapshot: object) -> None:
        if isinstance(snapshot, SnapshotSistema):
            self._aplicar_estado(snapshot)

    @staticmethod
    def _nivel_global(snapshot: SnapshotSistema) -> NivelEstado:
        """Peor nivel relevante: atención manda, luego cualquier activo cuenta como operativo."""
        niveles = {item.nivel for item in snapshot.items}
        if NivelEstado.ATENCION in niveles:
            return NivelEstado.ATENCION
        if NivelEstado.ACTIVO in niveles:
            return NivelEstado.ACTIVO
        if NivelEstado.INACTIVO in niveles:
            return NivelEstado.INACTIVO
        return NivelEstado.SIN_DATO

    @staticmethod
    def _repolish(widget: QWidget) -> None:
        estilo = widget.style()
        estilo.unpolish(widget)
        estilo.polish(widget)
        widget.update()

    # --- cierre ---

    def closeEvent(self, event: QCloseEvent) -> None:
        """Libera las secciones instanciadas antes de cerrar."""
        for seccion in self._secciones.values():
            try:
                seccion.al_cerrar()
            except Exception:
                logger.exception("Error al cerrar la sección '%s'", seccion.id)
        super().closeEvent(event)
