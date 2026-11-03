"""Sección Horno y cinta (SCA-400): línea de entrada del horno.

Muestra el estado actual del horno (T1, velocidad de cinta), el setpoint
objetivo con tolerancia, el modo de control seleccionado, una propuesta
pendiente de aprobación del operario y la cola de los últimos setpoints
aplicados. Consume `ctx.controlador_control` y `ctx.controlador_lotes`
(Protocols + dataclasses UI); no importa `backend.*`.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from frontend.nucleo.contrato import ContextoApp, GrupoSeccion, SeccionBase
from frontend.nucleo.controlador_control import (
    ETIQUETAS_MODO,
    ModoControlUI,
    SetpointUI,
)
from frontend.nucleo.qt_helpers import vaciar_layout
from frontend.nucleo.tema import color, repolish

# --- constantes visuales / bandas ------------------------------------------

_VALOR_SIN_DATO = "—"
# Bandas visuales del semáforo y del chart: no son el Δmáx de aplicación
# (ese lo aplica el backend/ControlService al escribir el PLC).
_BANDA_TEMP_C = 10.0
_BANDA_VEL = 0.05
_PASO_Y_TEMP = 25.0
_PASO_Y_VEL = 0.10
_VENTANA_MINUTOS = 600.0  # 10 min de historial para el mini-chart
_HISTORIAL_APLICADOS_MAX = 10
_ALTURA_CHART = 90
_ALTURA_BOTON_MODO = 56


@dataclass
class _Muestra:
    """Una muestra cacheada para el mini-chart."""

    timestamp: float  # time.monotonic()
    valor: float


# --- mini-chart ---------------------------------------------------------------


class _HornoMiniChart(QFrame):
    """Serie numérica con línea de objetivo punteada y banda de tolerancia.

    El cache vive en la sección (no en el chart) para poder limpiarlo al
    cambiar de lote. El chart sólo guarda la referencia al cache.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("hornoMiniChart")
        self.setMinimumHeight(_ALTURA_CHART)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        # Referencias mutables que el dueño (sección) actualiza.
        self._muestras: deque[_Muestra] = deque()
        self._objetivo: float | None = None
        self._tolerancia: float = 0.0
        self._paso_y: float = _PASO_Y_TEMP
        self._vacio_ms: str = "Acumulando muestras…"

    def configurar(
        self,
        objetivo: float | None,
        tolerancia: float,
        paso_y: float,
        vacio_ms: str,
    ) -> None:
        """Setea objetivo + tolerancia + paso del eje Y. Llama update()."""
        self._objetivo = objetivo
        self._tolerancia = tolerancia
        self._paso_y = paso_y
        self._vacio_ms = vacio_ms
        self.update()

    def asignar_cache(self, muestras: deque[_Muestra]) -> None:
        """Conecta el cache de la sección. No copia: vive afuera."""
        self._muestras = muestras
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - API Qt
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            self._pintar(painter)
        finally:
            painter.end()

    def _pintar(self, painter: QPainter) -> None:
        rect = self.rect().adjusted(8, 6, -8, -6)
        if rect.width() <= 0 or rect.height() <= 0:
            return
        # Filtrar muestras válidas y dentro de la ventana de 10 min.
        ahora = time.monotonic()
        muestras = [m for m in self._muestras if ahora - m.timestamp <= _VENTANA_MINUTOS]
        # Estado vacío: <2 muestras o todas None.
        if len(muestras) < 2:
            self._pintar_centrado(painter, rect, self._vacio_ms)
            return
        valores = [m.valor for m in muestras]
        min_v = min(valores)
        max_v = max(valores)
        if self._objetivo is not None:
            min_v = min(min_v, self._objetivo - self._tolerancia)
            max_v = max(max_v, self._objetivo + self._tolerancia)
        if min_v == max_v:
            min_v -= 1
            max_v += 1
        # Redondeo a múltiplos del paso para que los ticks queden prolijos.
        y_min = (min_v // self._paso_y) * self._paso_y
        y_max = ((max_v // self._paso_y) + 1) * self._paso_y
        if y_min == y_max:
            y_max = y_min + self._paso_y
        # Banda de tolerancia si hay objetivo.
        if self._objetivo is not None and self._tolerancia > 0:
            banda_color = QColor(color("accent"))
            banda_color.setAlpha(0x4D)
            painter.fillRect(
                int(rect.x()),
                int(self._y_a_px(self._objetivo + self._tolerancia, y_min, y_max, rect)),
                int(rect.width()),
                int(
                    self._y_a_px(self._objetivo - self._tolerancia, y_min, y_max, rect)
                    - self._y_a_px(self._objetivo + self._tolerancia, y_min, y_max, rect)
                ),
                banda_color,
            )
        # Grid horizontal.
        grid_color = QColor(color("borde"))
        tick = y_min
        while tick <= y_max + 1e-9:
            y_px = self._y_a_px(tick, y_min, y_max, rect)
            painter.setPen(QPen(grid_color, 1))
            painter.drawLine(int(rect.x()), int(y_px), int(rect.right()), int(y_px))
            tick += self._paso_y
        # Línea de objetivo punteada.
        if self._objetivo is not None:
            muted_color = QColor(color("texto_muted"))
            painter.setPen(QPen(muted_color, 1, Qt.PenStyle.DotLine))
            y_obj = self._y_a_px(self._objetivo, y_min, y_max, rect)
            painter.drawLine(int(rect.x()), int(y_obj), int(rect.right()), int(y_obj))
        # Serie accent.
        accent_color = QColor(color("accent"))
        painter.setPen(QPen(accent_color, 2))
        # Mapear timestamps: t = ahora - (ahora - timestamp) → más reciente a la derecha
        puntos = [
            (
                rect.right() - int((ahora - m.timestamp) * (rect.width() / _VENTANA_MINUTOS)),
                self._y_a_px(m.valor, y_min, y_max, rect),
            )
            for m in muestras
        ]
        for i in range(len(puntos) - 1):
            painter.drawLine(
                int(puntos[i][0]),
                int(puntos[i][1]),
                int(puntos[i + 1][0]),
                int(puntos[i + 1][1]),
            )

    def _pintar_centrado(self, painter: QPainter, rect, texto: str) -> None:
        color_texto = QColor(color("texto_muted"))
        painter.setPen(color_texto)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, texto)

    def _y_a_px(self, valor: float, y_min: float, y_max: float, rect) -> float:
        if y_max == y_min:
            return rect.y() + rect.height() / 2
        frac = (valor - y_min) / (y_max - y_min)
        # y crece hacia abajo en Qt
        return rect.y() + (1 - frac) * rect.height()


# --- la sección -------------------------------------------------------------


class HornoYCinta(SeccionBase):
    """Pantalla del horno y la cinta de entrada."""

    id = "horno_cinta"
    titulo = "Horno y cinta"
    icono = "herramienta"
    grupo = GrupoSeccion.ENTRADA

    def __init__(self, ctx: ContextoApp, parent: QWidget | None = None) -> None:
        super().__init__(ctx, parent)
        # Cache de lecturas por tarjeta (10 min de historial).
        self._cache_temp: deque[_Muestra] = deque()
        self._cache_vel: deque[_Muestra] = deque()
        # Cola de últimos N aplicados con timestamp del tick del bus.
        self._historial: deque[tuple[float, SetpointUI]] = deque(maxlen=_HISTORIAL_APLICADOS_MAX)
        self._ultimo_decision_id: str | None = None
        # ID del feedback timer para auto-ocultar.
        self._feedback_timer_ms: int = 0
        self._construir()
        self.ctx.bus.estado_actualizado.connect(self._al_actualizar_estado)
        self._refrescar()

    # --- construcción ---

    def _construir(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 14, 20, 16)
        layout.setSpacing(10)

        # Banner de aviso cuando el driver es stub (sin hardware).
        self._banner_aviso = QLabel("Driver en modo stub: no hay comunicación con sensores.")
        self._banner_aviso.setObjectName("hornoBannerAviso")
        self._banner_aviso.setVisible(False)
        layout.addWidget(self._banner_aviso)

        layout.addWidget(self._construir_tarjeta_lote())
        layout.addWidget(self._construir_tarjeta_temp())
        layout.addWidget(self._construir_tarjeta_cinta())
        layout.addWidget(self._construir_tarjeta_modo())
        self._boton_aplicar = QPushButton()
        self._boton_aplicar.setObjectName("hornoBotonAplicar")
        self._boton_aplicar.setVisible(False)
        self._boton_aplicar.clicked.connect(self._aplicar_pendiente_click)
        layout.addWidget(self._boton_aplicar)
        self._feedback = QLabel("")
        self._feedback.setObjectName("hornoFeedback")
        self._feedback.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._feedback.setVisible(False)
        layout.addWidget(self._feedback)
        layout.addWidget(self._construir_tarjeta_historial())
        layout.addStretch(1)

    def _construir_tarjeta_lote(self) -> QFrame:
        marco = QFrame()
        marco.setObjectName("hornoTarjetaLote")
        layout = QVBoxLayout(marco)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(6)
        # Header: producto + chip modo.
        header = QHBoxLayout()
        header.setSpacing(8)
        self._lote_producto = QLabel("Esperando lote…")
        self._lote_producto.setProperty("rol", "tileTitulo")
        header.addWidget(self._lote_producto, 1)
        self._lote_chip = QLabel("")
        self._lote_chip.setObjectName("chipEstado")
        header.addWidget(self._lote_chip, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(header)
        # Meta: ventana del setpoint.
        self._lote_ventana = QLabel("")
        self._lote_ventana.setProperty("rol", "meta")
        layout.addWidget(self._lote_ventana)
        # Cuerpo: countdown + barra.
        self._lote_countdown = QLabel("")
        self._lote_countdown.setProperty("rol", "meta")
        layout.addWidget(self._lote_countdown)
        self._lote_progreso = QProgressBar()
        self._lote_progreso.setObjectName("hornoProgresoPoll")
        self._lote_progreso.setRange(0, 1000)
        self._lote_progreso.setTextVisible(False)
        layout.addWidget(self._lote_progreso)
        return marco

    def _construir_tarjeta_temp(self) -> QFrame:
        marco = QFrame()
        marco.setObjectName("hornoTarjetaTemp")
        layout = QVBoxLayout(marco)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(8)
        header = QHBoxLayout()
        header.setSpacing(8)
        rotulo = QLabel("Temperatura")
        rotulo.setProperty("rol", "rotulo")
        header.addWidget(rotulo, 1)
        self._temp_delta = QLabel("")
        self._temp_delta.setProperty("rol", "valorMetrica")
        header.addWidget(self._temp_delta, 0, Qt.AlignmentFlag.AlignVCenter)
        self._temp_semaforo = QLabel("●")
        self._temp_semaforo.setObjectName("puntoEstado")
        self._temp_semaforo.setProperty("nivel", "sin_dato")
        header.addWidget(self._temp_semaforo, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(header)
        # Valor grande T1 + unidad.
        fila_t1 = QHBoxLayout()
        fila_t1.setSpacing(6)
        self._temp_valor = QLabel(_VALOR_SIN_DATO)
        self._temp_valor.setProperty("rol", "display")
        fila_t1.addWidget(self._temp_valor)
        self._temp_unidad = QLabel("°C")
        self._temp_unidad.setProperty("rol", "meta")
        fila_t1.addWidget(self._temp_unidad, 0, Qt.AlignmentFlag.AlignBottom)
        fila_t1.addStretch(1)
        layout.addLayout(fila_t1)
        self._temp_meta = QLabel("")
        self._temp_meta.setProperty("rol", "meta")
        layout.addWidget(self._temp_meta)
        # Mini-chart.
        self._chart_temp = _HornoMiniChart()
        layout.addWidget(self._chart_temp)
        return marco

    def _construir_tarjeta_cinta(self) -> QFrame:
        marco = QFrame()
        marco.setObjectName("hornoTarjetaCinta")
        layout = QVBoxLayout(marco)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(8)
        header = QHBoxLayout()
        header.setSpacing(8)
        rotulo = QLabel("Cinta")
        rotulo.setProperty("rol", "rotulo")
        header.addWidget(rotulo, 1)
        self._cinta_delta = QLabel("")
        self._cinta_delta.setProperty("rol", "valorMetrica")
        header.addWidget(self._cinta_delta, 0, Qt.AlignmentFlag.AlignVCenter)
        self._cinta_semaforo = QLabel("●")
        self._cinta_semaforo.setObjectName("puntoEstado")
        self._cinta_semaforo.setProperty("nivel", "sin_dato")
        header.addWidget(self._cinta_semaforo, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(header)
        fila_v = QHBoxLayout()
        fila_v.setSpacing(6)
        self._cinta_valor = QLabel(_VALOR_SIN_DATO)
        self._cinta_valor.setProperty("rol", "display")
        fila_v.addWidget(self._cinta_valor)
        self._cinta_unidad = QLabel("m/s")
        self._cinta_unidad.setProperty("rol", "meta")
        fila_v.addWidget(self._cinta_unidad, 0, Qt.AlignmentFlag.AlignBottom)
        fila_v.addStretch(1)
        layout.addLayout(fila_v)
        self._cinta_meta = QLabel("")
        self._cinta_meta.setProperty("rol", "meta")
        layout.addWidget(self._cinta_meta)
        self._chart_vel = _HornoMiniChart()
        layout.addWidget(self._chart_vel)
        return marco

    def _construir_tarjeta_modo(self) -> QFrame:
        marco = QFrame()
        marco.setObjectName("hornoTarjetaModo")
        layout = QVBoxLayout(marco)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(8)
        header = QLabel("Modo de control")
        header.setProperty("rol", "rotulo")
        layout.addWidget(header)
        fila = QHBoxLayout()
        fila.setSpacing(8)
        self._botones_modo: dict[ModoControlUI, QPushButton] = {}
        for m in (ModoControlUI.MANUAL, ModoControlUI.SUPERVISION, ModoControlUI.AUTOMATICO):
            boton = QPushButton(ETIQUETAS_MODO[m])
            boton.setObjectName("hornoBotonModo")
            boton.setProperty("modoColor", _modo_color(m))
            boton.setProperty("modoActivo", "false")
            boton.setMinimumHeight(_ALTURA_BOTON_MODO)
            boton.setCursor(Qt.CursorShape.PointingHandCursor)
            boton.clicked.connect(lambda _checked=False, modo=m: self._cambiar_modo_click(modo))
            self._botones_modo[m] = boton
            fila.addWidget(boton, 1)
        layout.addLayout(fila)
        return marco

    def _construir_tarjeta_historial(self) -> QFrame:
        marco = QFrame()
        marco.setObjectName("hornoTarjetaHistorial")
        layout = QVBoxLayout(marco)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(6)
        header = QLabel("Últimos setpoints aplicados")
        header.setProperty("rol", "rotulo")
        layout.addWidget(header)
        self._historial_contenedor = QVBoxLayout()
        self._historial_contenedor.setSpacing(0)
        layout.addLayout(self._historial_contenedor)
        return marco

    # --- refresh ---

    def _al_actualizar_estado(self, _snapshot: object) -> None:
        self._refrescar()

    def _refrescar(self) -> None:
        snap = self.ctx.controlador_control.estado()
        ahora = time.monotonic()
        self._actualizar_banner(snap)
        self._actualizar_lote(snap, ahora)
        self._actualizar_temperatura(snap, ahora)
        self._actualizar_cinta(snap, ahora)
        self._actualizar_modo(snap)
        self._actualizar_aplicar_pendiente(snap)
        self._actualizar_historial(snap, ahora)

    def _actualizar_banner(self, snap) -> None:
        """Stub banner sólo si el driver es stub y estamos habilitados."""
        driver = self.ctx.config.control.sensores.driver
        self._banner_aviso.setVisible(driver == "stub" and snap.habilitado)

    def _actualizar_lote(self, snap, ahora: float) -> None:
        if not snap.habilitado:
            self._lote_producto.setText("Control deshabilitado")
            self._lote_chip.setText("deshabilitado")
            self._lote_chip.setProperty("nivel", "inactivo")
            self._lote_ventana.setText("")
            self._lote_countdown.setText("")
            self._lote_progreso.setValue(0)
            repolish(self._lote_chip)
            return
        if not snap.enganchado:
            self._lote_producto.setText("Esperando lote…")
            self._lote_chip.setText("sin lote")
            self._lote_chip.setProperty("nivel", "sin_dato")
            self._lote_ventana.setText("")
            self._lote_countdown.setText("")
            self._lote_progreso.setValue(0)
            repolish(self._lote_chip)
            return
        # Enganchado: producto del lote (vía lotes), chip modo, ventana.
        lote = self.ctx.controlador_lotes.estado().lote_activo
        producto = lote.producto_nombre if lote and lote.producto_nombre else "Lote abierto"
        self._lote_producto.setText(producto)
        self._lote_chip.setText(snap.modo.value)
        self._lote_chip.setProperty("nivel", "activo")
        self._lote_ventana.setText(
            f"ventana: {snap.ultimo_setpoint.ventana}"
            if snap.ultimo_setpoint and snap.ultimo_setpoint.ventana
            else ""
        )
        # Countdown al próximo poll, medido desde el último poll real del backend.
        intervalo = max(0.1, self.ctx.config.control.poll_consigna_propuesta_segundos)
        restante = _segundos_restantes(ahora, snap.ultimo_poll, intervalo)
        self._lote_countdown.setText(f"Próxima consigna en {formato_mmss(restante)}")
        self._lote_progreso.setValue(int(restante / intervalo * self._lote_progreso.maximum()))
        repolish(self._lote_chip)

    def _actualizar_temperatura(self, snap, ahora: float) -> None:
        lectura = snap.ultima_lectura
        objetivo = snap.ultimo_setpoint
        t1 = lectura.temp_horno_1 if lectura else None
        obj_t1 = objetivo.temp_horno_1 if objetivo else None
        # Big value + unidad.
        if t1 is None:
            self._temp_valor.setText(_VALOR_SIN_DATO)
            self._temp_unidad.setVisible(False)
            delta_txt = _VALOR_SIN_DATO
            nivel = "sin_dato"
        else:
            self._temp_unidad.setVisible(True)
            self._temp_valor.setText(f"{t1:.1f}")
            if obj_t1 is None:
                delta_txt = _VALOR_SIN_DATO
                nivel = "sin_dato"
            else:
                d = t1 - obj_t1
                delta_txt = f"Δ {d:+.1f}°C"
                nivel = "activo" if abs(d) <= _BANDA_TEMP_C else "atencion"
        self._temp_delta.setText(delta_txt)
        self._temp_semaforo.setProperty("nivel", nivel)
        repolish(self._temp_semaforo)
        # Meta: objetivo + tolerancia.
        if obj_t1 is None:
            self._temp_meta.setText("Sin setpoint" if snap.enganchado else "")
        else:
            self._temp_meta.setText(f"Objetivo {obj_t1:.0f} °C  ±{_BANDA_TEMP_C:.0f}")
        # Mini-chart: agregar muestra y configurar objetivo.
        if t1 is not None:
            self._cache_temp.append(_Muestra(ahora, t1))
            cutoff = ahora - _VENTANA_MINUTOS
            while self._cache_temp and self._cache_temp[0].timestamp < cutoff:
                self._cache_temp.popleft()
        self._chart_temp.asignar_cache(self._cache_temp)
        self._chart_temp.configurar(obj_t1, _BANDA_TEMP_C, _PASO_Y_TEMP, "Acumulando muestras…")

    def _actualizar_cinta(self, snap, ahora: float) -> None:
        lectura = snap.ultima_lectura
        objetivo = snap.ultimo_setpoint
        vel = lectura.velocidad_cinta if lectura else None
        obj_v = objetivo.velocidad_cinta if objetivo else None
        if vel is None:
            self._cinta_valor.setText(_VALOR_SIN_DATO)
            self._cinta_unidad.setVisible(False)
            delta_txt = _VALOR_SIN_DATO
            nivel = "sin_dato"
        else:
            self._cinta_unidad.setVisible(True)
            self._cinta_valor.setText(f"{vel:.2f}")
            if obj_v is None:
                delta_txt = _VALOR_SIN_DATO
                nivel = "sin_dato"
            else:
                d = vel - obj_v
                delta_txt = f"Δ {d:+.2f} m/s"
                nivel = "activo" if abs(d) <= _BANDA_VEL else "atencion"
        self._cinta_delta.setText(delta_txt)
        self._cinta_semaforo.setProperty("nivel", nivel)
        repolish(self._cinta_semaforo)
        if obj_v is None:
            self._cinta_meta.setText("Sin setpoint" if snap.enganchado else "")
        else:
            self._cinta_meta.setText(f"Objetivo {obj_v:.2f} m/s  ±{_BANDA_VEL:.2f}")
        if vel is not None:
            self._cache_vel.append(_Muestra(ahora, vel))
            cutoff = ahora - _VENTANA_MINUTOS
            while self._cache_vel and self._cache_vel[0].timestamp < cutoff:
                self._cache_vel.popleft()
        self._chart_vel.asignar_cache(self._cache_vel)
        self._chart_vel.configurar(obj_v, _BANDA_VEL, _PASO_Y_VEL, "Acumulando muestras…")

    def _actualizar_modo(self, snap) -> None:
        # Se puede preseleccionar el modo antes de que haya lote: el próximo
        # lote que abra la Pi nace con ese modo (modo_provider en LoteService).
        enabled = snap.habilitado
        for modo, boton in self._botones_modo.items():
            boton.setEnabled(enabled)
            boton.setProperty("modoActivo", "true" if modo == snap.modo else "false")
            repolish(boton)

    def _actualizar_aplicar_pendiente(self, snap) -> None:
        pendiente = snap.propuesta_pendiente
        if pendiente is None:
            self._boton_aplicar.setVisible(False)
            return
        self._boton_aplicar.setVisible(True)
        t1_txt = f"{pendiente.temp_horno_1:.0f}"
        v_txt = f"{pendiente.velocidad_cinta:.2f}"
        self._boton_aplicar.setText(f"Aprobar propuesta (T={t1_txt} °C, V={v_txt} m/s)")
        self._boton_aplicar.setEnabled(snap.habilitado and snap.enganchado)

    def _actualizar_historial(self, snap, ahora: float) -> None:
        sp = snap.ultimo_setpoint
        decision_id = sp.decision_id if sp else None
        # Si el setpoint cambió (por decision_id), agregar al historial.
        if sp is not None and decision_id and decision_id != self._ultimo_decision_id:
            self._historial.appendleft((ahora, sp))
            self._ultimo_decision_id = decision_id
        # Reconstruir las filas.
        vaciar_layout(self._historial_contenedor)
        if not self._historial:
            self._historial_contenedor.addWidget(
                self._construir_fila_vacia("Sin setpoints aplicados todavía")
            )
            return
        lectura_t1 = snap.ultima_lectura.temp_horno_1 if snap.ultima_lectura else None
        for idx, (ts, setpoint) in enumerate(self._historial):
            self._historial_contenedor.addWidget(
                self._construir_fila_setpoint(idx == 0, ts, setpoint, lectura_t1, ahora)
            )

    def _construir_fila_setpoint(
        self,
        ultima: bool,
        timestamp: float,
        setpoint: SetpointUI,
        lectura_t1_actual: float | None,
        ahora: float,
    ) -> QFrame:
        marco = QFrame()
        marco.setObjectName("hornoFilaSetpoint")
        if ultima:
            marco.setProperty("ultima", "true")
        layout = QHBoxLayout(marco)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(8)
        hora = QLabel(formato_hhmmss(ahora - timestamp))
        # Antigüedad: hace cuánto se vio este setpoint.
        hora.setProperty("rol", "meta")
        hora.setProperty("mono", "true")
        hora.setFixedWidth(72)
        layout.addWidget(hora)
        t1 = QLabel(f"{setpoint.temp_horno_1:.0f} °C")
        t1.setProperty("rol", "valorMetrica")
        t1.setFixedWidth(70)
        t1.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(t1)
        v = QLabel(f"{setpoint.velocidad_cinta:.2f} m/s")
        v.setProperty("rol", "valorMetrica")
        v.setFixedWidth(80)
        v.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(v)
        delta_actual = ""
        color_delta = None
        if lectura_t1_actual is not None:
            d = lectura_t1_actual - setpoint.temp_horno_1
            delta_actual = f"Δ {d:+.1f}"
            color_delta = "activo" if abs(d) <= _BANDA_TEMP_C else "atencion"
        diff = QLabel(delta_actual)
        diff.setProperty("rol", "valorMetrica")
        if color_delta is not None:
            diff.setProperty("nivel", color_delta)
        layout.addWidget(diff, 1, Qt.AlignmentFlag.AlignRight)
        return marco

    def _construir_fila_vacia(self, texto: str) -> QLabel:
        lbl = QLabel(texto)
        lbl.setProperty("rol", "meta")
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        return lbl

    # --- comandos del operario ---

    def _cambiar_modo_click(self, modo: ModoControlUI) -> None:
        self.ctx.controlador_control.cambiar_modo(modo)

    def _aplicar_pendiente_click(self) -> None:
        ok = self.ctx.controlador_control.aplicar_pendiente()
        snap = self.ctx.controlador_control.estado()
        if ok:
            self._mostrar_feedback("Propuesta aplicada", "ok")
        else:
            self._mostrar_feedback(snap.ultimo_error or "No se pudo aplicar la propuesta", "error")

    def _mostrar_feedback(self, texto: str, nivel: str) -> None:
        self._feedback.setText(texto)
        self._feedback.setProperty("nivel", nivel)
        self._feedback.setVisible(True)
        repolish(self._feedback)
        # Auto-ocultar a los 2.5s (no usamos QTimer para evitar ciclo de vida).
        # El próximo _al_actualizar_estado no lo afecta; lo limpiamos nosotros.
        # Truco simple: marcar el timestamp y ocultar en el próximo refresh si pasó.
        self._feedback_timer_ms = int(time.monotonic() * 1000) + 2500


# --- helpers de módulo -------------------------------------------------------


def _modo_color(modo: ModoControlUI) -> str:
    return {
        ModoControlUI.MANUAL: "manual",
        ModoControlUI.SUPERVISION: "supervision",
        ModoControlUI.AUTOMATICO: "automatico",
    }[modo]


def _segundos_restantes(ahora: float, ultimo_poll: float | None, intervalo: float) -> float:
    """Segundos hasta el próximo poll, medidos desde el último poll real."""
    delta = ahora - ultimo_poll if ultimo_poll is not None else intervalo
    return max(0.0, intervalo - delta)


def formato_mmss(segundos: float) -> str:
    """Formatea segundos como m:ss (1 sola tecla después de los dos puntos)."""
    if segundos < 0:
        segundos = 0
    minutos = int(segundos) // 60
    secs = int(segundos) % 60
    return f"{minutos}:{secs:02d}"


def formato_hhmmss(delta_segundos: float) -> str:
    """Formatea un delta relativo como HH:MM:SS (antigüedad del setpoint)."""
    # No tenemos wall-clock time de cuándo se aplicó: usamos un proxy.
    # Si el delta es 0 (justo aplicado), "00:00:00". Si es > 60s, mostramos
    # el delta en mm:ss. Útil para el operario: "hace cuánto se aplicó".
    if delta_segundos < 0:
        delta_segundos = 0
    if delta_segundos < 60:
        return f"hace {int(delta_segundos)}s"
    minutos = int(delta_segundos) // 60
    secs = int(delta_segundos) % 60
    return f"hace {minutos}m{secs:02d}"
