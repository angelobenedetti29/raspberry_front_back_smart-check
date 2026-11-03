"""ControlService: publica telemetría del horno y aplica consignas (SCA-400).

Se ejecuta sólo en Pi ENTRADA (TipoDispositivo.ENTRADA_HORNO). El LoteService
sigue manejando la apertura del lote por el primer producto; este servicio
publica los heartbeats de telemetría y aplica los setpoints que el backend
marca como autorizados (modo AUTOMATICO o SUPERVISION ya aprobada por el
operario).

Sigue el Protocol `Service` (start no bloquea, stop bloquea) y arranca
desde run.py como un quinto hilo.

Modelo de hilos:
  - Hilo principal: publica telemetría cada `publicar_telemetria_segundos`.
  - Hilo secundario: polea `consigna-propuesta` cada
    `poll_consigna_propuesta_segundos` y la aplica al PLC/variador.

El service NO decide el setpoint: el backend lo calcula (MPC o approval de
supervisión) y la Pi lo respeta, con un clamp defensivo al Δmáx por ciclo.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from backend.config import AppConfig, ControlConfig
from backend.control.audit import AuditorConsignas
from backend.control.cliente import ClienteControl
from backend.control.sensores import AplicadorConsigna, LectorSensores
from backend.control.tipos import (
    ErrorControl,
    LecturaHorno,
    LimitesAplicacion,
    ModoControl,
    SetpointPropuesto,
)
from backend.device.tipos import TipoDispositivo
from backend.lote.cliente import ClienteLotes
from backend.lote.tipos import ErrorLotes, Lote
from backend.tiempo import ahora_iso_utc

logger = logging.getLogger(__name__)

_TIMEOUT_STOP = 5.0


def _positivo(valor: float | None, default: float) -> float:
    return valor if valor is not None and valor > 0 else default


def _rango(
    minimo: float | None,
    maximo: float | None,
    min_default: float,
    max_default: float,
) -> tuple[float, float]:
    if minimo is None or maximo is None or minimo >= maximo:
        return min_default, max_default
    return minimo, maximo


def _limites_del_producto(producto) -> LimitesAplicacion:
    """Deriva los límites de aplicación del producto, con defaults seguros."""
    temp_min, temp_max = _rango(producto.temp_min, producto.temp_max, 0.0, 500.0)
    vel_min, vel_max = _rango(
        producto.velocidad_cinta_min,
        producto.velocidad_cinta_max,
        0.0,
        5.0,
    )
    return LimitesAplicacion(
        delta_temp_max_c=_positivo(producto.delta_temp1_max_c, 10.0),
        delta_vel_max=_positivo(producto.delta_vel_max, 0.05),
        temp_min=temp_min,
        temp_max=temp_max,
        vel_min=vel_min,
        vel_max=vel_max,
    )


class ControlService:
    """Servicio de control de la Pi ENTRADA (SCA-400).

    Sólo se instancia para dispositivos ENTRADA_HORNO. Otros tipos lo
    ignoran en run.py. Hasta que el LoteService haya abierto un lote
    (self._lote_id es None), el ControlService no publica telemetría ni
    busca propuestas.
    """

    name = "control"

    def __init__(
        self,
        config: AppConfig,
        lector: LectorSensores,
        aplicador: AplicadorConsigna,
        tipo_provider: Callable[[], TipoDispositivo | None],
        secret_provider: Callable[[], str | None],
        on_credencial_invalida: Callable[[], None] | None = None,
    ) -> None:
        self._config = config
        self._ctrl_cfg = self._cargar_config(config)
        self._lector = lector
        self._aplicador = aplicador
        self._tipo_provider = tipo_provider
        self._secret_provider = secret_provider
        self._on_credencial_invalida = on_credencial_invalida
        self._cliente = ClienteControl(config.api.base_url, timeout=5.0)
        self._cliente_lotes = ClienteLotes(config.api, timeout=5.0)
        # F-R10: audit log append-only con decision_id. Vive bajo
        # `paths.data_dir`, rotado por tamaño igual que el log de eventos
        # y el DLQ.
        audit_dir = Path(config.paths.root) / config.paths.data_dir
        self._audit = AuditorConsignas(
            audit_dir / config.control.audit_path,
            max_bytes=config.control.audit_max_bytes,
            max_files=config.control.audit_max_files,
        )

        self._parar = threading.Event()
        self._hilo: threading.Thread | None = None
        self._lock = threading.Lock()
        # El cliente Modbus no es thread-safe: serializamos las lecturas del
        # hilo de telemetría con la foto inicial que pide LoteService.
        self._lector_lock = threading.Lock()
        self._secret: str | None = None
        self._lote_id: str | None = None
        self._modo: ModoControl = ModoControl.MANUAL
        self._limites: LimitesAplicacion | None = None
        # Clave del lote/producto a la que corresponden `_limites`. Evita
        # reconsultar el catálogo entero en cada ciclo de 3 s.
        self._limites_clave: tuple[str, str | None] | None = None
        self._ultimo_setpoint_aplicado: SetpointPropuesto | None = None
        self._ultimo_error: str = ""
        self._backend_ok: bool = False
        self._publicaciones_ok: int = 0
        self._publicaciones_error: int = 0
        self._aplicaciones_ok: int = 0
        self._aplicaciones_error: int = 0
        self._ultima_telemetria: float = 0.0
        # Último poll de propuestas (para el countdown real de la UI).
        self._ultimo_poll: float | None = None
        # Dedup por decision_id del backend: ttl = 3 ciclos de poll para tolerar
        # reintentos del backend sin aplicar dos veces la misma decisión.
        self._decision_vistas: dict[str, float] = {}
        self._decision_ttl_segundos: float = self._ctrl_cfg.poll_consigna_propuesta_segundos * 3
        # Última lectura cruda del lector (cache para la UI). Se actualiza en
        # cada _publicar_telemetria aunque la lectura venga sin datos, así
        # el panel refleja "stale" en vez de "sin inicializar".
        self._ultima_lectura: LecturaHorno | None = None
        # Propuesta buffered para la UI: en SUPERVISION se guarda acá hasta
        # que el operario la aprueba con `aplicar_pendiente`.
        self._propuesta_pendiente: SetpointPropuesto | None = None
        # Acks que no llegaron al backend (decision_id -> (temp, vel)). Se
        # reintentan en cada ciclo sin volver a escribir el PLC.
        self._acks_pendientes: dict[str, tuple[float, float]] = {}

    # --- Protocolo Service ---

    def start(self) -> None:
        """Arranca los hilos de telemetría y aplicación (idempotente)."""
        if not self._ctrl_cfg.habilitado:
            logger.info("control: deshabilitado por configuración")
            return
        if self._hilo is not None:
            return
        self._parar.clear()
        self._hilo = threading.Thread(target=self._bucle, name=self.name, daemon=True)
        self._hilo.start()
        logger.info("control: servicio iniciado")

    def stop(self) -> None:
        """Señaliza el cese y espera al hilo (bloquea)."""
        self._parar.set()
        if self._hilo is not None:
            self._hilo.join(timeout=_TIMEOUT_STOP)
            self._hilo = None
        logger.info("control: servicio detenido")

    # --- comandos del lote (llamados desde run.py / LoteService) ---

    def on_lote_abierto(
        self,
        secret: str,
        lote_id: str,
        modo: ModoControl,
        limites: LimitesAplicacion | None = None,
        iniciado_manualmente: bool = False,
    ) -> None:
        """Conecta el control con un lote recién abierto.

        El LoteService notifica cuando el lote está abierto; hasta entonces
        este servicio está inactivo. `limites` puede llegar como None: el
        LoteService no los calcula, y el control los resolverá de forma
        perezosa en el próximo ciclo contra el catálogo de productos.

        `iniciado_manualmente` ya no fuerza MANUAL: el backend saltea el
        precalentamiento y adapta en el modo del lote. Se conserva el
        parámetro para no romper callers.
        """
        with self._lock:
            self._secret = secret
            self._lote_id = lote_id
            self._modo = modo
            self._limites = limites
            self._limites_clave = None
            self._decision_vistas.clear()
            self._propuesta_pendiente = None
            self._acks_pendientes.clear()
        logger.info(
            "control: enganchado al lote %s modo=%s manual=%s",
            lote_id,
            modo.value,
            iniciado_manualmente,
        )

    def on_lote_cerrado(self) -> None:
        """Desengancha el control cuando el lote se cierra."""
        with self._lock:
            self._secret = None
            self._lote_id = None
            self._modo = ModoControl.MANUAL
            self._limites = None
            self._limites_clave = None
            self._ultimo_setpoint_aplicado = None
            self._decision_vistas.clear()
            self._propuesta_pendiente = None
            self._acks_pendientes.clear()
        logger.info("control: desenganchado del lote")

    def modo_actual(self) -> ModoControl:
        """Modo elegido en la UI, para sembrar el próximo lote que abra la Pi."""
        with self._lock:
            return self._modo

    def estado_inicial_lote(self) -> dict | None:
        """Foto del horno si el operario ya lo puso en marcha manualmente.

        La usa LoteService al abrir el lote: si la cinta ya gira o el horno
        está caliente, el lote nace `iniciado_manualmente=true` con estos
        valores para que el backend omita el precalentamiento y adapte desde
        el estado real.
        """
        try:
            with self._lector_lock:
                lectura = self._lector.leer()
        except Exception:  # noqa: BLE001 - un sensor roto no debe impedir abrir
            logger.exception("control: lectura inicial falló")
            return None
        if lectura.temp_horno_1 is None or lectura.velocidad_cinta is None:
            return None
        en_marcha = (
            lectura.velocidad_cinta > self._ctrl_cfg.umbral_marcha_velocidad
            or lectura.temp_horno_1 > self._ctrl_cfg.umbral_marcha_temp_c
        )
        if not en_marcha:
            return None
        return {
            "temp_horno_1": lectura.temp_horno_1,
            "velocidad_cinta": lectura.velocidad_cinta,
            "temp_objetivo": (
                lectura.temp_objetivo if lectura.temp_objetivo is not None else lectura.temp_horno_1
            ),
            "vel_objetivo": (
                lectura.vel_objetivo
                if lectura.vel_objetivo is not None
                else lectura.velocidad_cinta
            ),
            "confianza_deteccion": lectura.confianza_deteccion,
        }

    def actualizar_modo(self, modo: ModoControl, limites: LimitesAplicacion | None = None) -> None:
        """Cambio de modo en vivo (el operador lo cambia desde el panel)."""
        with self._lock:
            self._modo = modo
            # El LoteService suele mandar None: no pisar límites ya cacheados.
            if limites is not None:
                self._limites = limites
                self._limites_clave = None
        logger.info("control: modo actualizado a %s", modo.value)

    # --- comandos del operario (SUPERVISION) ---

    def aplicar_pendiente(self) -> bool:
        """Aprueba y aplica la propuesta pendiente (sólo SUPERVISION).

        Primero avisa al backend (`aprobar_consigna`) y recién después aplica
        el setpoint al PLC. Devuelve True si el backend aprobó y el PLC recibió
        el setpoint (un fallo de ack queda pendiente de reintento). False si no
        había propuesta, no estamos enganchados, no es SUPERVISION, o falló la
        aprobación/aplicación.
        """
        with self._lock:
            propuesta = self._propuesta_pendiente
            secret = self._secret
            lote_id = self._lote_id
            limites = self._limites
            modo = self._modo
        if modo is not ModoControl.SUPERVISION:
            logger.info("control: aplicar_pendiente sólo aplica en SUPERVISION")
            return False
        if propuesta is None:
            logger.info("control: aplicar_pendiente sin propuesta pendiente")
            return False
        if secret is None or lote_id is None:
            logger.info("control: aplicar_pendiente sin lote enganchado")
            return False
        # Prioridad a los límites que vinieron con la propuesta (tuning vivo
        # del lote); el snapshot del producto es el respaldo.
        limites = propuesta.limites or limites
        if limites is None:
            logger.warning("control: aplicar_pendiente sin límites cargados")
            return False
        try:
            body = (
                {"decision_id": propuesta.decision_id}
                if propuesta.decision_id is not None
                else None
            )
            self._cliente.aprobar_consigna(
                secret,
                lote_id,
                self._ctrl_cfg.aplicar_consigna_endpoint,
                body,
            )
        except ErrorControl as exc:
            # Sin aprobación del backend no tocamos el PLC: la propuesta
            # sigue pendiente para reintentar.
            self._manejar_error(exc, "aprobar consigna")
            return False
        if not self._aplicar_y_confirmar(propuesta, limites, secret, lote_id):
            return False
        with self._lock:
            if (
                self._propuesta_pendiente is not None
                and self._propuesta_pendiente.decision_id == propuesta.decision_id
            ):
                self._propuesta_pendiente = None
        logger.info(
            "control: propuesta pendiente aplicada decision_id=%s",
            propuesta.decision_id,
        )
        # Si el ack quedó pendiente, `_aplicar_y_confirmar` ya auditó el fallo.
        if not self._tiene_ack_pendiente(propuesta.decision_id):
            self._audit.decision_aplicada(
                lote_id,
                propuesta,
                resultado="aplicada",
                detalle="aprobacion local (SUPERVISION)",
            )
        return True

    def cambiar_modo(self, modo: ModoControl) -> None:
        """Cambio de modo desde la UI. Push al backend + actualiza local.

        Si no estamos enganchados a un lote, actualiza local y retorna (el
        backend descubrirá el modo cuando nos enganchemos). Si el push falla
        con 4xx/5xx, log warning y mantiene el cambio local: el backend
        reconcilia en su próximo /lotes/abierto.
        """
        with self._lock:
            secret = self._secret
            lote_id = self._lote_id
        if secret is None or lote_id is None:
            self.actualizar_modo(modo, None)
            return
        try:
            self._cliente.cambiar_modo(
                secret,
                lote_id,
                self._ctrl_cfg.lotes_modo_endpoint,
                modo,
            )
        except ErrorControl as exc:
            self._manejar_error(exc, "push de cambio de modo")
            # Seguimos: actualizamos local aunque el push falló.
        self.actualizar_modo(modo, None)

    # --- lectura para la UI ---

    def estado(self) -> dict:
        """Snapshot inmutable para que la UI lo muestre en horno/cinta."""
        with self._lock:
            return {
                "habilitado": self._ctrl_cfg.habilitado,
                "enganchado": self._lote_id is not None,
                "modo": self._modo.value,
                "ultimo_setpoint": self._ultimo_setpoint_aplicado,
                "ultima_lectura": self._ultima_lectura,
                "propuesta_pendiente": self._propuesta_pendiente,
                "ultimo_error": self._ultimo_error,
                "backend_ok": self._backend_ok,
                "ultimo_poll": self._ultimo_poll,
                "publicaciones_ok": self._publicaciones_ok,
                "publicaciones_error": self._publicaciones_error,
                "aplicaciones_ok": self._aplicaciones_ok,
                "aplicaciones_error": self._aplicaciones_error,
            }

    # --- bucle principal ---

    def _bucle(self) -> None:
        """Corre hasta stop(). Alterna publish telemetría / poll propuesta."""
        while not self._parar.is_set():
            try:
                self._ciclo()
            except Exception:  # noqa: BLE001 - el hilo no debe morir
                logger.exception("control: error inesperado en el ciclo")

    def _ciclo(self) -> None:
        self._detectar_lote()
        with self._lock:
            lote_id = self._lote_id
            secret = self._secret
        if lote_id is None or secret is None:
            # Sin lote enganchado: espera pasiva.
            self._parar.wait(1.0)
            return

        ahora = time.monotonic()
        if ahora - self._ultima_telemetria >= self._ctrl_cfg.publicar_telemetria_segundos:
            self._ultima_telemetria = ahora
            self._publicar_telemetria(secret, lote_id)
        self._reintentar_acks(secret, lote_id)
        self._ultimo_poll = time.monotonic()
        self._buscar_y_aplicar_propuesta(secret, lote_id)

        # Espera hasta el próximo ciclo: usa el menor de los dos intervalos para
        # no perder ni latencia de telemetría ni de propuestas.
        espera = min(
            self._ctrl_cfg.publicar_telemetria_segundos,
            self._ctrl_cfg.poll_consigna_propuesta_segundos,
        )
        self._parar.wait(max(0.2, espera))

    def _detectar_lote(self) -> None:
        """Detecta automáticamente si hay un lote ABIERTO y se engancha.

        Evita acoplar el control al LoteService: la Pi ENTRADA abre lotes
        cuando ve la primera detección, y este servicio los descubre solo
        consultando /api/v1/lotes/abierto. Si el lote cambió (modo,
        limites, etc.) se reengancha con los valores frescos.
        """
        # Gate por tipo de dispositivo: este servicio sólo aplica a ENTRADA.
        tipo = self._tipo_provider()
        if tipo is not TipoDispositivo.ENTRADA_HORNO:
            return

        secret = self._secret_provider()
        if secret is None:
            return
        with self._lock:
            self._secret = secret  # cacheamos para evitar pedirlo en cada ciclo
            ya_enganchado = self._lote_id is not None

        try:
            lote = self._cliente_lotes.lote_abierto(secret)
        except ErrorLotes:
            return

        if lote is None:
            if ya_enganchado:
                logger.info("control: lote cerrado, desenganchando")
                with self._lock:
                    self._lote_id = None
                    self._modo = ModoControl.MANUAL
                    self._limites = None
                    self._limites_clave = None
                    self._ultimo_setpoint_aplicado = None
                    self._propuesta_pendiente = None
                    self._acks_pendientes.clear()
                    self._decision_vistas.clear()
            return

        self._enganchar_o_actualizar(lote)

    def _enganchar_o_actualizar(self, lote: Lote) -> None:
        """Sincroniza el estado del control con el lote activo del backend."""
        clave = (lote.id, lote.producto_id)
        with self._lock:
            # Reusar los límites si el lote/producto no cambió: el catálogo
            # se consultaba entero cada 3 s sin necesidad.
            if self._limites is not None and self._limites_clave == clave:
                limites = self._limites
            else:
                limites = None
        if limites is None:
            limites = self._limites_desde_producto(lote)
        if limites is None:
            # Falló el catálogo: no aplicamos clamp con datos viejos. Reintenta
            # en el próximo ciclo.
            return
        modo = self._modo_desde_lote(lote)
        iniciado_manualmente = bool(getattr(lote, "iniciado_manualmente", False))
        with self._lock:
            enganchado = self._lote_id == lote.id
            self._lote_id = lote.id
            self._modo = modo
            self._limites = limites
            self._limites_clave = clave
            if not enganchado:
                # Lote nuevo: no arrastrar propuestas ni acks del anterior.
                self._propuesta_pendiente = None
                self._acks_pendientes.clear()
                logger.info(
                    "control: enganchado al lote %s modo=%s manual=%s",
                    lote.id,
                    modo.value,
                    iniciado_manualmente,
                )

    @staticmethod
    def _modo_desde_lote(lote: Lote) -> ModoControl:
        modo_str = str(getattr(lote, "modo", "MANUAL"))
        try:
            return ModoControl(modo_str)
        except ValueError:
            return ModoControl.MANUAL

    def _limites_desde_producto(self, lote: Lote) -> LimitesAplicacion | None:
        """Consulta el catálogo y deriva los límites del producto del lote.

        Usa los campos de límites del `Producto` (rango de T/velocidad y los
        Δmáx por ciclo). Si el backend no los informa, cae a defaults seguros:
        el clamp acá es sólo un cinturón por si el MPC propusiera algo fuera.
        """
        with self._lock:
            secret = self._secret
        if secret is None:
            return None
        try:
            productos = self._cliente_lotes.productos(secret)
        except ErrorLotes:
            return None
        producto_id = lote.producto_id
        if producto_id is None:
            return None
        for p in productos:
            if p.id == producto_id:
                return _limites_del_producto(p)
        return None

    # --- publicación de telemetría ---

    def _publicar_telemetria(self, secret: str, lote_id: str) -> None:
        with self._lector_lock:
            lectura = self._lector.leer()
        with self._lock:
            self._ultima_lectura = lectura
        if not self._lectura_tiene_datos(lectura):
            logger.debug("control: sin lecturas para publicar")
            return
        payload = lectura.a_payload()
        payload["received_at"] = ahora_iso_utc()
        try:
            self._cliente.publicar_telemetria(
                secret, lote_id, self._ctrl_cfg.metricas_horno_endpoint, payload
            )
        except ErrorControl as exc:
            self._manejar_error(exc, "publicar telemetría")
            return
        with self._lock:
            self._publicaciones_ok += 1
            self._backend_ok = True
            self._ultimo_error = ""

    @staticmethod
    def _lectura_tiene_datos(lectura: LecturaHorno) -> bool:
        """True si al menos un campo del payload tiene valor."""
        return any(
            getattr(lectura, campo) is not None
            for campo in (
                "temp_horno_1",
                "temp_comb_1",
                "humedad_horno",
                "temp_ambiente",
                "humedad_ambiente",
                "velocidad_cinta",
                "temp_objetivo",
                "vel_objetivo",
                "confianza_deteccion",
            )
        )

    # --- aplicación de consignas ---

    def _buscar_y_aplicar_propuesta(self, secret: str, lote_id: str) -> None:
        # En MANUAL el MPC no propone; el panel puede encolar un override
        # manual ya aprobado, que sí se aplica (ver guard más abajo).
        with self._lock:
            modo = self._modo

        try:
            propuesta = self._cliente.obtener_propuesta(
                secret, lote_id, self._ctrl_cfg.consigna_propuesta_endpoint
            )
        except ErrorControl as exc:
            self._manejar_error(exc, "consultar propuesta")
            return

        if propuesta is None:
            return

        # En MANUAL el MPC no propone, pero el panel puede encolar un override
        # manual ya aprobado; sólo se aplican propuestas autorizadas.
        if modo is ModoControl.MANUAL and not propuesta.autorizada:
            return

        # SUPERVISION pendiente de aprobación: se guarda para la UI y no se
        # aplica ni se marca como vista (el operario puede aprobarla después).
        if not propuesta.autorizada:
            with self._lock:
                ya_pendiente = (
                    self._propuesta_pendiente is not None
                    and self._propuesta_pendiente.decision_id == propuesta.decision_id
                )
                self._propuesta_pendiente = propuesta
            if not ya_pendiente:
                # Sólo auditamos cuando la propuesta entra al buffer: el
                # backend la repite en cada poll hasta que se apruebe.
                logger.info(
                    "control: propuesta pendiente de aprobación decision_id=%s",
                    propuesta.decision_id,
                )
                self._audit.decision_aplicada(
                    lote_id,
                    propuesta,
                    resultado="pendiente_aprobacion",
                )
            return

        # Dedup por decision_id: si el backend reintenta la misma propuesta
        # dentro del TTL, la descartamos sin volver a tocar el PLC.
        if not self._decision_fresca(propuesta.decision_id):
            logger.info(
                "control: propuesta descartada por decision_id duplicado %s",
                propuesta.decision_id,
            )
            # F-R10: registrar el descarte para tener trazabilidad del
            # backend incluso cuando no aplicamos.
            self._audit.decision_aplicada(
                lote_id,
                propuesta,
                resultado="descartada_dup",
                detalle="decision_id dentro del TTL de dedup",
            )
            return

        # Los límites del lote que vienen con la propuesta mandan; si no
        # llegaron, se usa el snapshot del producto cargado al enganchar.
        limites = propuesta.limites or self._limites_snapshot()
        if limites is None:
            logger.warning("control: propuesta recibida sin límites cargados")
            return

        aplicada = self._aplicar_y_confirmar(propuesta, limites, secret, lote_id)
        if not aplicada:
            return
        with self._lock:
            if (
                self._propuesta_pendiente is not None
                and self._propuesta_pendiente.decision_id == propuesta.decision_id
            ):
                self._propuesta_pendiente = None
        logger.info(
            "control: decision_id=%s aplicada T1=%.1f V=%.2f",
            propuesta.decision_id,
            propuesta.temp_horno_1,
            propuesta.velocidad_cinta,
        )
        # F-R10: la entrada "aplicada" cubre el lazo automático. Si el ack
        # quedó pendiente, `_aplicar_y_confirmar` ya auditó "fallo_ack".
        if not self._tiene_ack_pendiente(propuesta.decision_id):
            self._audit.decision_aplicada(
                lote_id,
                propuesta,
                resultado="aplicada",
            )

    def _limites_snapshot(self) -> LimitesAplicacion | None:
        with self._lock:
            return self._limites

    def _decision_fresca(self, decision_id: str | None) -> bool:
        """True si la decisión debe aplicarse (no fue vista o su TTL venció).

        Sin `decision_id` no podemos deduplicar y la aceptamos; el backend
        debería garantizar unicidad cuando el campo esté presente.
        """
        if decision_id is None:
            return True
        ahora = time.monotonic()
        with self._lock:
            vencidas = [
                k
                for k, ts in self._decision_vistas.items()
                if ahora - ts > self._decision_ttl_segundos
            ]
            for k in vencidas:
                self._decision_vistas.pop(k, None)
            return decision_id not in self._decision_vistas

    def _registrar_decision_aplicada(self, decision_id: str | None) -> None:
        """Marca la decisión como aplicada para deduplicarla hasta el TTL."""
        if decision_id is None:
            return
        with self._lock:
            self._decision_vistas[decision_id] = time.monotonic()

    def _tiene_ack_pendiente(self, decision_id: str | None) -> bool:
        """True si el ack de esa decisión quedó pendiente de reintento."""
        if decision_id is None:
            return False
        with self._lock:
            return decision_id in self._acks_pendientes

    def _aplicar_y_confirmar(
        self,
        propuesta: SetpointPropuesto,
        limites: LimitesAplicacion,
        secret: str,
        lote_id: str,
    ) -> bool:
        """Aplica el setpoint clampeado, confirma al backend y registra la decisión.

        Usado por el lazo automático (`_buscar_y_aplicar_propuesta`) y por
        la aplicación manual de una propuesta pendiente (`aplicar_pendiente`).
        Devuelve True si el PLC recibió el setpoint; un fallo del ack al
        backend no invalida la aplicación local (queda pendiente de reintento).
        """
        aplicados = self._clampear_y_aplicar(propuesta, limites)
        if aplicados is None:
            return False
        t1_aplicado, v_aplicado = aplicados
        # El backend debe auditar lo que realmente quedó en el PLC, no lo que
        # el MPC propuso antes del clamp defensivo.
        aplicada_prop = replace(
            propuesta,
            temp_horno_1=t1_aplicado,
            velocidad_cinta=v_aplicado,
        )
        # Registramos la decisión apenas escribimos el PLC: si el ack falla,
        # el reintento no debe volver a escribir el mismo setpoint.
        self._registrar_decision_aplicada(propuesta.decision_id)
        with self._lock:
            self._ultimo_setpoint_aplicado = aplicada_prop
            self._aplicaciones_ok += 1
            self._backend_ok = True
            self._ultimo_error = ""
        if propuesta.decision_id is None:
            return True
        try:
            self._cliente.ack_consigna(
                secret,
                lote_id,
                self._ctrl_cfg.consigna_ack_endpoint,
                aplicada_prop.a_payload_ack(),
            )
        except ErrorControl as exc:
            with self._lock:
                self._acks_pendientes[propuesta.decision_id] = (
                    t1_aplicado,
                    v_aplicado,
                )
            self._manejar_error(exc, "ack de aplicación")
            # F-R10: el setpoint se aplicó al PLC pero el backend no se
            # enteró. Esta es la entrada más importante del audit: sin
            # esto el operador no puede reconciliar el estado del horno
            # con el del backend.
            self._audit.decision_aplicada(
                lote_id,
                propuesta,
                resultado="fallo_ack",
                detalle=str(exc),
            )
            return True
        return True

    def _reintentar_acks(self, secret: str, lote_id: str) -> None:
        """Reenvía los acks que no llegaron al backend en ciclos anteriores.

        Un 4xx que no sea 429 indica que el backend ya no aceptará ese ack
        (p. ej. decisión vencida): se descarta. Ante red/5xx/429 se conserva
        para el próximo ciclo.
        """
        with self._lock:
            pendientes = dict(self._acks_pendientes)
        for decision_id, (temp, vel) in pendientes.items():
            try:
                self._cliente.ack_consigna(
                    secret,
                    lote_id,
                    self._ctrl_cfg.consigna_ack_endpoint,
                    {
                        "decision_id": decision_id,
                        "temp_aplicado": temp,
                        "vel_aplicado": vel,
                    },
                )
            except ErrorControl as exc:
                codigo = exc.codigo
                if codigo is not None and 400 <= codigo < 500 and codigo != 429:
                    with self._lock:
                        self._acks_pendientes.pop(decision_id, None)
                    logger.warning(
                        "control: ack de %s rechazado con %s; se descarta",
                        decision_id,
                        codigo,
                    )
                continue
            with self._lock:
                self._acks_pendientes.pop(decision_id, None)

    def _clampear_y_aplicar(
        self,
        propuesta: SetpointPropuesto,
        limites: LimitesAplicacion,
    ) -> tuple[float, float] | None:
        """Aplica el setpoint respetando los Δmáx por ciclo (cinturón de seguridad).

        Devuelve los valores efectivamente aplicados al PLC, o None si el
        aplicador los rechazó. El ack al backend usa estos valores, no los
        propuestos, para que el audit refleje el clamp real.
        """
        with self._lock:
            lectura = self._ultima_lectura
            ultimo = self._ultimo_setpoint_aplicado
            # Base del clamp: el setpoint activo real del horno en la última
            # lectura (sobrevive a reinicios), luego el último aplicado por
            # este proceso, y por último la propia propuesta.
            actual_t1 = lectura.temp_objetivo if lectura is not None else None
            actual_v = lectura.vel_objetivo if lectura is not None else None
            if actual_t1 is None and ultimo is not None:
                actual_t1 = ultimo.temp_horno_1
            if actual_v is None and ultimo is not None:
                actual_v = ultimo.velocidad_cinta
            if actual_t1 is None:
                actual_t1 = propuesta.temp_horno_1
            if actual_v is None:
                actual_v = propuesta.velocidad_cinta

        if propuesta.manual_override:
            # El valor del operario ya se validó contra el rango del producto:
            # no se escalona por Δmáx.
            t1 = max(limites.temp_min, min(limites.temp_max, propuesta.temp_horno_1))
            v = max(limites.vel_min, min(limites.vel_max, propuesta.velocidad_cinta))
        else:
            t1 = self._clampear_temp(propuesta.temp_horno_1, actual_t1, limites)
            v = self._clampear_vel(propuesta.velocidad_cinta, actual_v, limites)

        try:
            self._aplicador.aplicar(t1, v)
        except Exception as exc:  # noqa: BLE001 - defendemos contra drivers
            with self._lock:
                self._aplicaciones_error += 1
                self._ultimo_error = f"aplicador: {exc}"
            logger.exception("control: aplicador rechazó el setpoint")
            return None
        return (t1, v)

    @staticmethod
    def _clampear_temp(objetivo: float, actual: float, limites: LimitesAplicacion) -> float:
        """Clamp de T al rango del producto + Δmáx por ciclo."""
        delta = objetivo - actual
        if abs(delta) > limites.delta_temp_max_c:
            objetivo = actual + (
                limites.delta_temp_max_c if delta > 0 else -limites.delta_temp_max_c
            )
        return max(limites.temp_min, min(limites.temp_max, objetivo))

    @staticmethod
    def _clampear_vel(objetivo: float, actual: float, limites: LimitesAplicacion) -> float:
        delta = objetivo - actual
        if abs(delta) > limites.delta_vel_max:
            objetivo = actual + (limites.delta_vel_max if delta > 0 else -limites.delta_vel_max)
        return max(limites.vel_min, min(limites.vel_max, objetivo))

    # --- manejo de errores ---

    def _manejar_error(self, exc: ErrorControl, contexto: str) -> None:
        with self._lock:
            self._backend_ok = False
            self._ultimo_error = f"{contexto}: {exc}"
        # 401/403: credencial rechazada, re-registrar vía DeviceService.
        codigo = getattr(exc, "codigo", None)
        if codigo in (401, 403) and self._on_credencial_invalida is not None:
            self._on_credencial_invalida()
        logger.warning("control: %s: %s", contexto, exc)

    # --- carga de config ---

    @staticmethod
    def _cargar_config(config: AppConfig) -> ControlConfig:
        """Devuelve config.control; el loader ya resuelve el default.

        Si la sección no existe en config.json, `load_config()` devuelve un
        ControlConfig deshabilitado con defaults seguros, así que acá nunca
        llega `None`.
        """
        return config.control
