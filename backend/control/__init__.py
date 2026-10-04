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
from datetime import datetime, timezone
from typing import Callable

from backend.config import AppConfig
from backend.control.cliente import ClienteControl
from backend.control.sensores import AplicadorConsigna, LectorSensores
from backend.control.tipos import (
    ConfigControl,
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
        limites: LimitesAplicacion,
    ) -> None:
        """Conecta el control con un lote recién abierto.

        El LoteService notifica cuando el lote está abierto; hasta entonces
        este servicio está inactivo.
        """
        with self._lock:
            self._secret = secret
            self._lote_id = lote_id
            self._modo = modo
            self._horno2_habilitado = horno2_habilitado
            self._limites = limites
        logger.info(
            "control: enganchado al lote %s modo=%s horno2=%s",
            lote_id,
            modo.value,
            horno2_habilitado,
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
        logger.info("control: desenganchado del lote")

    def actualizar_modo(self, modo: ModoControl, limites: LimitesAplicacion) -> None:
        """Cambio de modo en vivo (el operador lo cambia desde el panel)."""
        with self._lock:
            self._modo = modo
            self._limites = limites
        logger.info("control: modo actualizado a %s", modo.value)

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
        with self._lock:
            enganchado = self._lote_id == lote.id
            self._lote_id = lote.id
            self._modo = modo
            self._horno2_habilitado = horno2
            self._limites = limites
            if not enganchado:
                logger.info(
                    "control: enganchado al lote %s modo=%s horno2=%s",
                    lote.id, modo.value, horno2,
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

        limites = self._limites_snapshot()
        if limites is None:
            logger.warning("control: propuesta recibida sin límites cargados")
            return

        aplicada = self._clampear_y_aplicar(propuesta, limites)
        if not aplicada:
            return

        # Confirmar al backend para que la decisión pase a "aplicada".
        try:
            self._cliente.aplicar_consigna(
                secret,
                lote_id,
                self._ctrl_cfg.aplicar_consigna_endpoint,
                propuesta.a_payload_aplicacion(),
            )
        except ErrorControl as exc:
            self._manejar_error(exc, "ack de aplicación")
            return
        with self._lock:
            self._ultimo_setpoint_aplicado = propuesta
            self._aplicaciones_ok += 1
            self._backend_ok = True
            self._ultimo_error = ""
        self._marcar_ok()

    def _limites_snapshot(self) -> LimitesAplicacion | None:
        with self._lock:
            return self._limites

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
    def _cargar_config(config: AppConfig) -> ConfigControl:
        """Lee config.control. Si falta la sección, devuelve defaults seguros.

        Mantener compat con configs viejos: si la sección no existe, se
        deshabilita el servicio (sin telemetría ni aplicación).
        """
        cfg = getattr(config, "control", None)
        if cfg is None:
            return ConfigControl(
                habilitado=False,
                publicar_telemetria_segundos=5.0,
                poll_consigna_propuesta_segundos=3.0,
                metricas_horno_endpoint="/api/v1/lotes/{id}/metricas-horno",
                consigna_propuesta_endpoint="/api/v1/lotes/{id}/consigna-propuesta",
                aplicar_consigna_endpoint="/api/v1/lotes/{id}/aplicar-consigna",
            )
        return cfg