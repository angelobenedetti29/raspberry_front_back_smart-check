"""ControlService: publica telemetría del horno y aplica consignas (SCA-400).

Se ejecuta sólo en Pi ENTRADA (TipoDispositivo.ENTRADA_HORNO). El LoteService
sigue manejando la apertura del lote por el primer producto; este servicio
publica los heartbeats de telemetría y aplica los setpoints que el backend
propone (modo AUTOMATICO) o aprobó (modo SUPERVISION).

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
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

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

logger = logging.getLogger(__name__)

_ESPERA_REINTENTO_INI = 1.0
_ESPERA_REINTENTO_MAX = 30.0
_TIMEOUT_STOP = 5.0


def _ahora_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


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
        self._secret: str | None = None
        self._lote_id: str | None = None
        self._modo: ModoControl = ModoControl.MANUAL
        self._horno2_habilitado: bool = False
        self._limites: LimitesAplicacion | None = None
        self._ultimo_setpoint_aplicado: SetpointPropuesto | None = None
        self._ultimo_error: str = ""
        self._backend_ok: bool = False
        self._publicaciones_ok: int = 0
        self._publicaciones_error: int = 0
        self._aplicaciones_ok: int = 0
        self._aplicaciones_error: int = 0
        self._backoff: float = _ESPERA_REINTENTO_INI
        # Dedup por decision_id del backend: ttl = 3 ciclos de poll para tolerar
        # reintentos del backend sin aplicar dos veces la misma decisión.
        self._decision_vistas: dict[str, float] = {}
        self._decision_ttl_segundos: float = (
            self._ctrl_cfg.poll_consigna_propuesta_segundos * 3
        )
        # Última lectura cruda del lector (cache para la UI). Se actualiza en
        # cada _publicar_telemetria aunque la lectura venga sin datos, así
        # el panel refleja "stale" en vez de "sin inicializar".
        self._ultima_lectura: LecturaHorno | None = None
        # Buffer para SUPERVISION híbrido: si el operario clickea "aplazar la
        # siguiente", la próxima propuesta del backend se guarda acá en vez
        # de aplicarse. La UI la muestra y la aplica con `aplicar_pendiente`.
        self._propuesta_pendiente: SetpointPropuesto | None = None
        self._aplazar_siguiente: bool = False

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
        horno2_habilitado: bool,
        limites: LimitesAplicacion | None = None,
        iniciado_manualmente: bool = False,
    ) -> None:
        """Conecta el control con un lote recién abierto.

        El LoteService notifica cuando el lote está abierto; hasta entonces
        este servicio está inactivo. `limites` puede llegar como None: el
        LoteService no los calcula, y el control los resolverá de forma
        perezosa en el próximo ciclo contra el catálogo de productos.

        F-R11: si `iniciado_manualmente` es True, el lote lo abrió el
        operador (no la Pi). En ese caso default a MANUAL: la Raspberry
        no debe arrancar el horno/cinta, sólo monitorear. El operario
        puede cambiar a SUPERVISION/AUTOMATICO desde la UI después.
        """
        if iniciado_manualmente and modo is not ModoControl.MANUAL:
            logger.info(
                "control: lote %s iniciado manualmente; default a MANUAL",
                lote_id,
            )
            modo = ModoControl.MANUAL
        with self._lock:
            self._secret = secret
            self._lote_id = lote_id
            self._modo = modo
            self._horno2_habilitado = horno2_habilitado
            self._limites = limites
            self._decision_vistas.clear()
            self._propuesta_pendiente = None
            self._aplazar_siguiente = False
        logger.info(
            "control: enganchado al lote %s modo=%s horno2=%s manual=%s",
            lote_id,
            modo.value,
            horno2_habilitado,
            iniciado_manualmente,
        )

    def on_lote_cerrado(self) -> None:
        """Desengancha el control cuando el lote se cierra."""
        with self._lock:
            self._secret = None
            self._lote_id = None
            self._modo = ModoControl.MANUAL
            self._horno2_habilitado = False
            self._limites = None
            self._ultimo_setpoint_aplicado = None
            self._decision_vistas.clear()
            self._propuesta_pendiente = None
            self._aplazar_siguiente = False
        logger.info("control: desenganchado del lote")

    def actualizar_modo(
        self, modo: ModoControl, limites: LimitesAplicacion | None = None
    ) -> None:
        """Cambio de modo en vivo (el operador lo cambia desde el panel)."""
        with self._lock:
            self._modo = modo
            self._limites = limites
        logger.info("control: modo actualizado a %s", modo.value)

    # --- comandos del operario (SUPERVISION híbrido) ---

    def aplazar_siguiente(self) -> None:
        """El operario quiere revisar la próxima propuesta antes de aplicarla.

        La siguiente propuesta que llegue en modo SUPERVISION se guardará en
        `_propuesta_pendiente` en lugar de aplicarse. Se consume con
        `aplicar_pendiente()`. Sólo aplica a SUPERVISION; en AUTOMATICO/MANUAL
        el flag se ignora (manual nunca aplica; auto siempre auto).
        """
        with self._lock:
            self._aplazar_siguiente = True
        logger.info("control: siguiente propuesta será pendiente")

    def aplicar_pendiente(self) -> bool:
        """Aplica la propuesta pendiente guardada y limpia el flag.

        Devuelve True si la confirmación al backend fue exitosa. False si no
        había propuesta pendiente, si no estamos enganchados a un lote, o si
        la confirmación falló.
        """
        with self._lock:
            propuesta = self._propuesta_pendiente
            secret = self._secret
            lote_id = self._lote_id
            limites = self._limites
        if propuesta is None:
            logger.info("control: aplicar_pendiente sin propuesta pendiente")
            return False
        if secret is None or lote_id is None:
            logger.info("control: aplicar_pendiente sin lote enganchado")
            return False
        if limites is None:
            logger.warning("control: aplicar_pendiente sin límites cargados")
            return False
        if not self._aplicar_y_confirmar(propuesta, limites, secret, lote_id):
            return False
        with self._lock:
            self._propuesta_pendiente = None
            self._aplazar_siguiente = False
        logger.info(
            "control: propuesta pendiente aplicada decision_id=%s",
            propuesta.decision_id,
        )
        # F-R10: la entrada "aplicada" también cubre `aplicar_pendiente`
        # (el operario aprobó manualmente una decisión buffered).
        self._audit.decision_aplicada(
            lote_id, propuesta, resultado="aplicada",
            detalle="aplicar_pendiente (SUPERVISION)",
        )
        self._marcar_ok()
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
                "horno2_habilitado": self._horno2_habilitado,
                "ultimo_setpoint": self._ultimo_setpoint_aplicado,
                "ultima_lectura": self._ultima_lectura,
                "propuesta_pendiente": self._propuesta_pendiente,
                "aplazar_siguiente": self._aplazar_siguiente,
                "ultimo_error": self._ultimo_error,
                "backend_ok": self._backend_ok,
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

        self._publicar_telemetria(secret, lote_id)
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
                    self._horno2_habilitado = False
                    self._limites = None
                    self._ultimo_setpoint_aplicado = None
            return

        self._enganchar_o_actualizar(lote)

    def _enganchar_o_actualizar(self, lote: Lote) -> None:
        """Sincroniza el estado del control con el lote activo del backend."""
        limites = self._limites_desde_producto(lote)
        if limites is None:
            # Falló el catálogo: no aplicamos clamp con datos viejos. Reintenta
            # en el próximo ciclo.
            return
        modo = self._modo_desde_lote(lote)
        horno2 = bool(getattr(lote, "horno2_habilitado", False))
        # F-R11: si el lote es manual y el modo que trae el backend no es
        # MANUAL, respetamos el flag y default a MANUAL (sólo monitorear).
        iniciado_manualmente = bool(
            getattr(lote, "iniciado_manualmente", False)
        )
        if iniciado_manualmente and modo is not ModoControl.MANUAL:
            modo = ModoControl.MANUAL
        with self._lock:
            enganchado = self._lote_id == lote.id
            self._lote_id = lote.id
            self._modo = modo
            self._horno2_habilitado = horno2
            self._limites = limites
            if not enganchado:
                logger.info(
                    "control: enganchado al lote %s modo=%s horno2=%s manual=%s",
                    lote.id, modo.value, horno2, iniciado_manualmente,
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

        El Producto expone temp_min/max, velocidad_cinta_min/max y los
        delta_temp1/2_max_c y delta_vel_max. Para v1 usamos el delta_temp1
        como tope de cambio de T (T2 hereda el mismo si horno2=true).
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
                # El Producto del backend expone temp_min/max pero no deltas
                # (es el espejo del schema sin horno2). Para v1 usamos defaults
                # seguros: el MPC ya respeta los rangos. El clamp aquí es sólo
                # un cinturón por si el backend propusiera algo fuera.
                return LimitesAplicacion(
                    delta_temp_max_c=10.0,
                    delta_vel_max=0.05,
                    temp_min=0.0,
                    temp_max=500.0,
                    vel_min=0.0,
                    vel_max=5.0,
                )
        return None

    # --- publicación de telemetría ---

    def _publicar_telemetria(self, secret: str, lote_id: str) -> None:
        lectura = self._lector.leer()
        with self._lock:
            self._ultima_lectura = lectura
        if not self._lectura_tiene_datos(lectura):
            logger.debug("control: sin lecturas para publicar")
            return
        payload = lectura.a_payload()
        payload["received_at"] = _ahora_iso()
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
        self._marcar_ok()

    @staticmethod
    def _lectura_tiene_datos(lectura: LecturaHorno) -> bool:
        """True si al menos un campo del payload tiene valor."""
        return any(
            getattr(lectura, campo) is not None
            for campo in (
                "temp_horno_1",
                "temp_horno_2",
                "temp_comb_1",
                "temp_comb_2",
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
        with self._lock:
            modo = self._modo
        if modo is ModoControl.MANUAL:
            return  # el sistema nunca propone ni aplica en MANUAL

        try:
            propuesta = self._cliente.obtener_propuesta(
                secret, lote_id, self._ctrl_cfg.consigna_propuesta_endpoint
            )
        except ErrorControl as exc:
            self._manejar_error(exc, "consultar propuesta")
            return

        if propuesta is None:
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
                lote_id, propuesta, resultado="descartada_dup",
                detalle="decision_id dentro del TTL de dedup",
            )
            return

        limites = self._limites_snapshot()
        if limites is None:
            logger.warning("control: propuesta recibida sin límites cargados")
            return

        # SUPERVISION híbrido: si el operario pidió aplazar la siguiente,
        # guardamos la propuesta en el buffer en vez de aplicarla. La UI
        # la muestra y se aplica con `aplicar_pendiente`.
        if modo is ModoControl.SUPERVISION:
            with self._lock:
                aplazar = self._aplazar_siguiente
            if aplazar:
                with self._lock:
                    self._propuesta_pendiente = propuesta
                logger.info(
                    "control: propuesta buffered decision_id=%s "
                    "(operario pidió aplazar)",
                    propuesta.decision_id,
                )
                # F-R10: queda pendiente de `aplicar_pendiente`.
                self._audit.decision_aplicada(
                    lote_id, propuesta, resultado="buffered",
                    detalle="SUPERVISION: aplazar_siguiente",
                )
                return

        aplicada = self._aplicar_y_confirmar(propuesta, limites, secret, lote_id)
        if not aplicada:
            return
        logger.info(
            "control: decision_id=%s aplicada T1=%.1f V=%.2f",
            propuesta.decision_id,
            propuesta.temp_horno_1,
            propuesta.velocidad_cinta,
        )
        # F-R10: la entrada "aplicada" cubre el lazo automático.
        self._audit.decision_aplicada(
            lote_id, propuesta, resultado="aplicada",
        )
        self._marcar_ok()

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
        Devuelve True sólo si la confirmación al backend fue exitosa.
        """
        aplicada = self._clampear_y_aplicar(propuesta, limites)
        if not aplicada:
            return False
        try:
            self._cliente.aplicar_consigna(
                secret,
                lote_id,
                self._ctrl_cfg.aplicar_consigna_endpoint,
                propuesta.a_payload_aplicacion(),
            )
        except ErrorControl as exc:
            self._manejar_error(exc, "ack de aplicación")
            # F-R10: el setpoint se aplicó al PLC pero el backend no se
            # enteró. Esta es la entrada más importante del audit: sin
            # esto el operador no puede reconciliar el estado del horno
            # con el del backend.
            self._audit.decision_aplicada(
                lote_id, propuesta, resultado="fallo_ack",
                detalle=str(exc),
            )
            return False
        # Recién ahora registramos la decisión como vista: si el ack falla,
        # permitimos reintento en el próximo ciclo.
        self._registrar_decision_aplicada(propuesta.decision_id)
        with self._lock:
            self._ultimo_setpoint_aplicado = propuesta
            self._aplicaciones_ok += 1
            self._backend_ok = True
            self._ultimo_error = ""
        return True

    def _clampear_y_aplicar(
        self,
        propuesta: SetpointPropuesto,
        limites: LimitesAplicacion,
    ) -> bool:
        """Aplica el setpoint respetando los Δmáx por ciclo (cinturón de seguridad).

        Devuelve True si la aplicación tuvo éxito, False si falló o si el
        setpoint fue rechazado por el aplicador.
        """
        with self._lock:
            actual_t1 = (
                self._ultimo_setpoint_aplicado.temp_horno_1
                if self._ultimo_setpoint_aplicado is not None
                else propuesta.temp_horno_1
            )
            actual_v = (
                self._ultimo_setpoint_aplicado.velocidad_cinta
                if self._ultimo_setpoint_aplicado is not None
                else propuesta.velocidad_cinta
            )

        t1 = self._clampear_temp(
            propuesta.temp_horno_1, actual_t1, limites
        )
        v = self._clampear_vel(propuesta.velocidad_cinta, actual_v, limites)

        t2: float | None = None
        if self._horno2_habilitado and propuesta.temp_horno_2 is not None:
            actual_t2 = (
                self._ultimo_setpoint_aplicado.temp_horno_2
                if (
                    self._ultimo_setpoint_aplicado is not None
                    and self._ultimo_setpoint_aplicado.temp_horno_2 is not None
                )
                else propuesta.temp_horno_2
            )
            t2 = self._clampear_temp(
                propuesta.temp_horno_2, actual_t2, limites
            )

        try:
            self._aplicador.aplicar(t1, t2, v)
        except Exception as exc:  # noqa: BLE001 - defendemos contra drivers
            with self._lock:
                self._aplicaciones_error += 1
                self._ultimo_error = f"aplicador: {exc}"
            logger.exception("control: aplicador rechazó el setpoint")
            return False
        return True

    @staticmethod
    def _clampear_temp(
        objetivo: float, actual: float, limites: LimitesAplicacion
    ) -> float:
        """Clamp de T al rango del producto + Δmáx por ciclo."""
        delta = objetivo - actual
        if abs(delta) > limites.delta_temp_max_c:
            objetivo = actual + (
                limites.delta_temp_max_c if delta > 0 else -limites.delta_temp_max_c
            )
        return max(limites.temp_min, min(limites.temp_max, objetivo))

    @staticmethod
    def _clampear_vel(
        objetivo: float, actual: float, limites: LimitesAplicacion
    ) -> float:
        delta = objetivo - actual
        if abs(delta) > limites.delta_vel_max:
            objetivo = actual + (
                limites.delta_vel_max if delta > 0 else -limites.delta_vel_max
            )
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

    def _marcar_ok(self) -> None:
        with self._lock:
            self._backoff = _ESPERA_REINTENTO_INI

    # --- carga de config ---

    @staticmethod
    def _cargar_config(config: AppConfig) -> ControlConfig:
        """Lee config.control. Si falta la sección, devuelve defaults seguros.

        Mantener compat con configs viejos: si la sección no existe, se
        deshabilita el servicio (sin telemetría ni aplicación). Los nuevos
        campos (sensores/actuadores/lotes_modo_endpoint) caen a sus defaults
        del dataclass, así que un config viejo sigue funcionando con stubs.
        """
        cfg = getattr(config, "control", None)
        if cfg is None:
            return ControlConfig(
                habilitado=False,
                publicar_telemetria_segundos=5.0,
                poll_consigna_propuesta_segundos=3.0,
                metricas_horno_endpoint="/api/v1/lotes/{id}/metricas-horno",
                consigna_propuesta_endpoint="/api/v1/lotes/{id}/consigna-propuesta",
                aplicar_consigna_endpoint="/api/v1/lotes/{id}/aplicar-consigna",
                # sensores/actuadores/lotes_modo_endpoint usan defaults del dataclass
            )
        return cfg