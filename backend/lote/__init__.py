"""Servicio de lotes: coordina el lote del sector contra el backend Go.

Consume los eventos de pista que publica el streaming por un buzón acotado
(nunca bloquea la inferencia) y los reporta al backend desde su propio hilo.

Por rol:

- ENTRADA: abre el lote con el primer `alta`. No reporta estados.
- SALIDA: traduce los eventos a productos con estado (ok/crudo/quemado), los
  reporta en vivo y cierra el lote por inactividad (o a pedido del operador).
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from backend.config import AppConfig
from backend.control.tipos import LimitesAplicacion, ModoControl
from backend.device.almacen import AlmacenDispositivo
from backend.device.tipos import TipoDispositivo
from backend.etiquetas import normalizar_clave, separar_etiqueta
from backend.lote.cliente import ClienteLotes
from backend.lote.dlq import ColaDLQ
from backend.lote.estado import clasificar_estado
from backend.lote.tipos import (
    Conteos,
    ErrorLotes,
    EstadoLote,
    EstadoLotes,
    EstadoProducto,
    EventoDeteccion,
    Lote,
    MotivoCierre,
    Producto,
    Sector,
    dict_a_evento,
)
from backend.streaming import StreamingService
from backend.streaming.suscripcion import ColaEventos, EventoObservado

logger = logging.getLogger(__name__)

_ESPERA_SIN_REGISTRO = 1.0
_TIMEOUT_CIERRE = 5.0
_HISTORIAL_MAX = 20


def _ahora_iso() -> str:
    """Timestamp local con offset, informativo para el backend."""
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


class LoteService:
    """Coordina el lote del sector contra el backend Go.

    `start()` no bloquea: lanza un hilo que drena el buzón de eventos del
    streaming y reporta al backend. `stop()` señaliza y espera al hilo.
    """

    name = "lote"

    def __init__(
        self,
        config: AppConfig,
        streaming: StreamingService,
        on_credencial_invalida: Callable[[], None] | None = None,
        on_lote_abierto: Callable[
            [str, str, ModoControl, bool, LimitesAplicacion | None, bool], None
        ] | None = None,
        on_lote_cerrado: Callable[[], None] | None = None,
        on_modo_actualizado: Callable[[ModoControl, LimitesAplicacion | None], None] | None = None,
        modo_provider: Callable[[], ModoControl] | None = None,
        estado_inicial_provider: Callable[[], dict | None] | None = None,
    ) -> None:
        self._config = config
        self._streaming = streaming
        self._on_credencial_invalida = on_credencial_invalida
        self._on_lote_abierto = on_lote_abierto
        self._on_lote_cerrado = on_lote_cerrado
        self._on_modo_actualizado = on_modo_actualizado
        # Modo elegido en la UI de la Pi. Se envía al abrir el lote para que
        # AUTOMATICO/SUPERVISION arranquen sin un cambio de modo posterior.
        self._modo_provider = modo_provider
        # Foto del horno cuando el operario ya lo puso en marcha: evita que
        # AUTOMATICO pase por el precalentamiento y frene la cinta.
        self._estado_inicial_provider = estado_inicial_provider
        self._cliente = ClienteLotes(config.api)
        self._almacen = AlmacenDispositivo(Path(config.paths.root) / "device.json")

        self._lote_cfg = config.lote
        self._x = config.lote.cierre_sin_detecciones_segundos
        self._flush = config.lote.flush_segundos
        self._max_envio = config.lote.max_eventos_por_envio
        self._refresco = config.lote.refresco_catalogo_segundos
        self._backoff_ini = config.lote.reintento_inicial_segundos
        self._backoff_max = config.lote.reintento_maximo_segundos
        self._catalogo = config.models.catalog
        # Producto del catálogo del Go indexado por nombre normalizado: la clave
        # es el prefijo del label (convención ``<producto>_<estado>`` de
        # backend.etiquetas), no el modelo. Se llena en `_refrescar_si_toca`.
        self._producto_por_nombre: dict[str, Producto] = {}
        # Prefijos ya advertidos por etiqueta sin producto: evita log-spam.
        self._prefijos_advertidos: set[str] = set()
        # Estado para evitar emisiones espurias del callback de modo: se compara
        # contra el último modo que le pasamos al ControlService.
        self._ultimo_modo_emitido: ModoControl | None = None

        self._parar = threading.Event()
        self._hilo: threading.Thread | None = None
        self._cola: ColaEventos | None = None

        # F-R6: DLQ persistente bajo `paths.data_dir/dlq.jsonl`. Vive acá
        # (no se importa en `cliente.py` para no acoplar el transporte al
        # filesystem). Se inicia en `start()`.
        dlq_dir = Path(config.paths.root) / config.paths.data_dir
        self._dlq = ColaDLQ(
            dlq_dir / config.lote.dlq_path,
            max_bytes=config.lote.dlq_max_bytes,
            max_files=config.lote.dlq_max_files,
        )

        self._lock = threading.Lock()
        # --- estado compartido con la UI ---
        self._registrado = False
        self._tipo: TipoDispositivo | None = None
        self._secret: str | None = None
        self._sector: Sector | None = None
        self._productos: tuple[Producto, ...] = ()
        self._lote: Lote | None = None
        self._historial: tuple[Lote, ...] = ()
        self._backend_ok = False
        self._mensaje = ""
        self._rechazados = 0
        self._descartados_outbox = 0
        self._forzar_refresco = False
        self._cierre_manual = False

        # --- sólo el hilo del servicio ---
        self._pendientes: deque[EventoDeteccion] = deque(
            maxlen=max(1, config.lote.max_pendientes)
        )
        self._reportadas: set[tuple[str | None, int]] = set()
        self._conteos_locales: dict[EstadoProducto, int] = {}
        self._estados_por_modelo: dict[str, set[EstadoProducto]] = {}
        self._ultimo_modelo: str | None = None
        self._ultimo_envio = 0.0
        self._ultimo_refresco = 0.0
        self._proximo_chequeo_cierre = 0.0
        self._espera_hasta = 0.0
        self._backoff_actual = self._backoff_ini
        # F-R6: cuántos envíos consecutivos fallaron. Se resetea en
        # `_marcar_ok()`. Cuando supera el umbral de DLQ se loggea una
        # señal: el backend probablemente está caído y el DLQ está
        # creciendo.
        self._intentos_fallidos = 0

    # --- Protocolo Service ---

    def start(self) -> None:
        """Arranca el hilo de lotes (idempotente). No bloquea."""
        if not self._lote_cfg.habilitado:
            logger.info("lote: deshabilitado por configuración")
            return
        if self._hilo is not None:
            return
        self._cola = self._streaming.suscribir_eventos(self._lote_cfg.cola_eventos)
        # F-R6: si el proceso anterior dejó batches en el DLQ, los
        # re-encolamos al frente de `_pendientes` para que el próximo
        # `_reportar` los empuje. El drain puede fallar (JSONL inválido)
        # y se loggea; no aborta el arranque.
        self._drenar_dlq_en_arranque()
        self._parar.clear()
        self._hilo = threading.Thread(target=self._bucle, name=self.name, daemon=True)
        self._hilo.start()
        logger.info("lote: servicio iniciado")

    def stop(self) -> None:
        """Señaliza el cese y espera al hilo (bloquea)."""
        self._parar.set()
        if self._hilo is not None:
            self._hilo.join(timeout=_TIMEOUT_CIERRE)
            self._hilo = None
        if self._cola is not None:
            self._streaming.desuscribir_eventos(self._cola)
            self._cola = None
        logger.info("lote: servicio detenido")

    # --- comandos de la UI (no bloquean) ---

    def finalizar_lote(self) -> None:
        """Pide cerrar el lote manualmente (motivo 'manual')."""
        with self._lock:
            self._cierre_manual = True

    def refrescar(self) -> None:
        """Pide re-consultar catálogo, sector y lote abierto."""
        with self._lock:
            self._forzar_refresco = True

    # --- lectura ---

    def estado(self) -> EstadoLotes:
        """Instantánea del servicio para la UI."""
        with self._lock:
            lote = self._lote
            tipo = self._tipo
            descartados = self._descartados_outbox + (
                self._cola.descartados if self._cola is not None else 0
            )
            return EstadoLotes(
                habilitado=self._lote_cfg.habilitado,
                registrado=self._registrado,
                tipo=tipo,
                sector=self._sector,
                productos=self._productos,
                lote_activo=lote,
                segundos_para_cierre=self._segundos_para_cierre(lote, tipo),
                pendientes=len(self._pendientes),
                descartados=descartados,
                rechazados=self._rechazados,
                backend_ok=self._backend_ok,
                mensaje=self._mensaje,
                historial=self._historial,
            )

    def _segundos_para_cierre(
        self, lote: Lote | None, tipo: TipoDispositivo | None
    ) -> float | None:
        """Segundos estimados hasta el cierre automático (solo salida)."""
        if (
            tipo is not TipoDispositivo.SALIDA_HORNO
            or lote is None
            or lote.estado is not EstadoLote.ABIERTO
        ):
            return None
        local = self._streaming.segundos_sin_detecciones()
        if local is None:
            return None
        return max(0.0, self._x - min(local, lote.inactividad_segundos))

    # --- hilo de trabajo ---

    def _bucle(self) -> None:
        while not self._parar.is_set():
            try:
                self._ciclo()
            except Exception:  # noqa: BLE001 - el hilo no debe morir
                logger.exception("lote: error inesperado en el ciclo")

    def _ciclo(self) -> None:
        self._sincronizar_dispositivo()
        if not self._registrado:
            self._parar.wait(_ESPERA_SIN_REGISTRO)
            return
        self._refrescar_si_toca()
        cola = self._cola
        eventos = cola.recibir(timeout=self._flush) if cola is not None else []
        if eventos:
            self._encolar(eventos)
        self._reportar_si_toca()
        self._evaluar_cierre()

    def _sincronizar_dispositivo(self) -> None:
        estado = self._almacen.cargar()
        secret = estado.secret if estado is not None else None
        tipo = estado.tipo if estado is not None else None
        registrado = bool(secret) and tipo is not None
        with self._lock:
            self._registrado = registrado
            self._tipo = tipo
            self._secret = secret
            if not registrado:
                self._lote = None

    def _refrescar_si_toca(self) -> None:
        with self._lock:
            forzar = self._forzar_refresco
            self._forzar_refresco = False
            secret = self._secret
        ahora = time.monotonic()
        if not forzar and ahora < self._ultimo_refresco + self._refresco:
            return
        self._ultimo_refresco = ahora
        if not secret:
            return
        try:
            productos = self._cliente.productos(secret)
            sector = self._cliente.sector(secret)
            lote = self._cliente.lote_abierto(secret)
            historial = self._cliente.historial(secret)
        except ErrorLotes as exc:
            self._aplicar_error(exc)
            return
        with self._lock:
            self._productos = productos
            self._producto_por_nombre = {
                normalizar_clave(p.nombre): p for p in productos if p.activo
            }
            self._prefijos_advertidos.clear()
            self._sector = sector
            lote_anterior = self._lote
            self._lote = lote
            self._historial = historial
        self._marcar_ok()
        # Detección reactiva de cambio de modo: si el lote cambió de modo
        # desde la última notificación al ControlService, lo avisamos. Sólo
        # aplica cuando estamos enganchados al mismo lote que control (mismo
        # id); un cambio de lote va por on_lote_abierto/on_lote_cerrado.
        if lote is None or self._on_modo_actualizado is None:
            return
        with self._lock:
            enganchado_al_mismo = (
                lote_anterior is not None and lote_anterior.id == lote.id
            )
            ultimo_emitido = self._ultimo_modo_emitido
        if not enganchado_al_mismo:
            return
        modo_actual = self._modo_desde_lote(lote)
        if modo_actual == ultimo_emitido:
            return
        with self._lock:
            self._ultimo_modo_emitido = modo_actual
        try:
            self._on_modo_actualizado(modo_actual, None)
        except Exception:  # noqa: BLE001
            logger.exception("lote: on_modo_actualizado falló")

    # --- traducción de eventos a productos ---

    def _encolar(self, eventos: list[EventoObservado]) -> None:
        tipo = self._tipo
        if tipo is TipoDispositivo.ENTRADA_HORNO:
            self._encolar_entrada(eventos)
        elif tipo is TipoDispositivo.SALIDA_HORNO:
            self._encolar_salida(eventos)

    def _encolar_entrada(self, eventos: list[EventoObservado]) -> None:
        """La entrada sólo abre el lote con el primer `alta` resoluble."""
        with self._lock:
            if self._lote is not None:
                return
        for observado in eventos:
            if observado.evento.tipo != "alta":
                continue
            producto = self._resolver_producto(observado.evento.label)
            if producto is None:
                self._marcar_error(
                    "Etiqueta sin producto en el catálogo del backend (ver log)"
                )
                continue
            self._abrir_lote(producto.id)
            return

    def _encolar_salida(self, eventos: list[EventoObservado]) -> None:
        for observado in eventos:
            deteccion = self._traducir(observado)
            if deteccion is None:
                continue
            with self._lock:
                maxlen = self._pendientes.maxlen
                if maxlen is not None and len(self._pendientes) >= maxlen:
                    self._descartados_outbox += 1
                self._pendientes.append(deteccion)
            self._conteos_locales[deteccion.estado] = (
                self._conteos_locales.get(deteccion.estado, 0) + 1
            )

    def _resolver_producto(self, label: str) -> Producto | None:
        """Resuelve el producto del catálogo del backend desde el prefijo del label.

        La convención es ``<producto>_<estado>`` (ver ``backend.etiquetas``): el
        prefijo ya viene normalizado y se matchea contra el nombre del producto
        del catálogo. Sin match devuelve None y se advierte una sola vez por
        prefijo (hasta el próximo refresco de catálogo).
        """
        prefijo, _estado = separar_etiqueta(label)
        producto = self._producto_por_nombre.get(prefijo) if prefijo else None
        if producto is None and prefijo and prefijo not in self._prefijos_advertidos:
            self._prefijos_advertidos.add(prefijo)
            logger.warning(
                "lote: etiqueta %r sin producto en el catálogo del backend "
                "(prefijo %r); el evento no se reporta",
                label,
                prefijo,
            )
        return producto

    def _traducir(self, observado: EventoObservado) -> EventoDeteccion | None:
        """Un evento por producto confirmado.

        Con la línea de conteo habilitada, el conteo oficial se produce al
        ``cruce`` de la meta: la pista se reporta ahí con su estado final y no
        vuelve a contarse (el ``quemada``/``baja`` posterior cae en el dedupe).
        Sin línea, el fallback es el histórico: quemado al confirmar
        ``quemada``, ok/crudo al ``baja``.

        El producto se resuelve por etiqueta (prefijo ``<producto>_``) contra el
        catálogo del backend; sin producto o sin estado válido, el evento no se
        reporta (y no ocupa el dedupe, para no perder un ``baja`` posterior del
        mismo track).
        """
        evento = observado.evento
        clave = (observado.modelo_id, evento.pista_id)

        # Con línea de conteo habilitada, el conteo oficial se produce al cruzar
        # la meta; el resto de los eventos de esa pista ya no reporta.
        if evento.tipo == "cruce":
            if not self._config.stream.counting.enabled:
                return None
            if clave in self._reportadas:
                return None
            estado = clasificar_estado(evento.label)
            if estado is None:
                return None
            producto = self._resolver_producto(evento.label)
            if producto is None:
                return None
            self._reportadas.add(clave)
            self._ultimo_modelo = observado.modelo_id
            return self._evento(observado, producto, estado)

        # Fallback sin línea de conteo: quemado al confirmar 'quemada',
        # ok/crudo al 'baja'.
        if self._config.stream.counting.enabled:
            return None
        if evento.tipo == "quemada":
            if clave in self._reportadas:
                return None
            producto = self._resolver_producto(evento.label)
            if producto is None:
                return None
            self._reportadas.add(clave)
            self._ultimo_modelo = observado.modelo_id
            return self._evento(observado, producto, EstadoProducto.QUEMADO)
        if evento.tipo == "baja":
            estado = clasificar_estado(evento.label)
            if clave in self._reportadas or estado is None:
                return None
            producto = self._resolver_producto(evento.label)
            if producto is None:
                return None
            self._reportadas.add(clave)
            self._ultimo_modelo = observado.modelo_id
            return self._evento(observado, producto, estado)
        return None

    def _evento(
        self, observado: EventoObservado, producto: Producto, estado: EstadoProducto
    ) -> EventoDeteccion:
        evento = observado.evento
        return EventoDeteccion(
            evento_id=str(uuid.uuid4()),
            producto_id=producto.id,
            estado=estado,
            confianza=evento.confianza,
            pista=evento.pista_id,
            frame=observado.numero_frame,
            modelo_id=observado.modelo_id,
            momento=_ahora_iso(),
        )

    # --- reporte al backend ---

    def _reportar_si_toca(self) -> None:
        with self._lock:
            pendientes = len(self._pendientes)
        if pendientes == 0:
            return
        ahora = time.monotonic()
        if ahora < self._espera_hasta:
            return
        lleno = pendientes >= self._max_envio
        if not lleno and ahora - self._ultimo_envio < self._flush:
            return
        self._reportar()

    def _reportar(self) -> None:
        secret = self._secret
        if not secret:
            return
        with self._lock:
            lote = self._lote
            lote_id = (
                lote.id
                if lote is not None and lote.estado is EstadoLote.ABIERTO
                else None
            )
            producto_id = self._pendientes[0].producto_id if self._pendientes else None
            batch = list(self._pendientes)[: self._max_envio]
        if not batch:
            return
        if lote_id is None:
            lote_id = self._asegurar_lote(secret, producto_id)
            if lote_id is None:
                return
        try:
            self._cliente.reportar(secret, lote_id, batch)
        except ErrorLotes as exc:
            self._aplicar_error(exc, batch)
            return
        with self._lock:
            for _ in batch:
                if self._pendientes:
                    self._pendientes.popleft()
            self._ultimo_envio = time.monotonic()
        self._marcar_ok()

    def _asegurar_lote(self, secret: str, producto_id: str | None) -> str | None:
        """Attach al lote abierto del sector o abre uno degradado desde la salida."""
        try:
            abierto = self._cliente.lote_abierto(secret)
        except ErrorLotes as exc:
            self._aplicar_error(exc)
            return None
        if abierto is not None:
            # F-R11: ya hay lote en el sector (pudo haber sido abierto
            # manualmente por el operador desde el frontend). Nos
            # attachamos y notificamos al control para que opere en modo
            # monitor (no aplica consignas) si está marcado como manual.
            with self._lock:
                self._lote = abierto
                self._ultimo_modo_emitido = self._modo_desde_lote(abierto)
            self._notificar_lote_abierto(secret, abierto)
            return abierto.id
        if not producto_id:
            self._marcar_error("Producto sin resolver para abrir el lote (ver log)")
            return None
        iniciado_manualmente, estado_inicial = self._estado_inicial_para_abrir()
        try:
            lote, _ = self._cliente.abrir_lote(
                secret,
                idempotency_key=str(uuid.uuid4()),
                producto_id=producto_id,
                momento=_ahora_iso(),
                modo=self._modo_para_abrir(),
                iniciado_manualmente=iniciado_manualmente,
                estado_inicial=estado_inicial,
            )
        except ErrorLotes as exc:
            self._aplicar_error(exc)
            return None
        with self._lock:
            self._lote = lote
        self._notificar_lote_abierto(secret, lote)
        logger.info("lote: abierto degradado por la salida %s", lote.id)
        return lote.id

    def _abrir_lote(self, producto_id: str) -> None:
        secret = self._secret
        if not secret:
            return
        # F-R11: antes de abrir uno nuevo, miramos si el sector ya tiene
        # un lote abierto (puede haber sido iniciado manualmente por el
        # operador o por otra Pi del sector). Si lo hay, nos attachamos:
        # abrir un segundo lote en el mismo sector rompería el reporte
        # de productos y dejaría al horno "fuera de control" si el lote
        # preexistente fue abierto en modo MANUAL.
        try:
            abierto = self._cliente.lote_abierto(secret)
        except ErrorLotes as exc:
            self._aplicar_error(exc)
            return
        if abierto is not None:
            with self._lock:
                self._lote = abierto
                self._ultimo_modo_emitido = self._modo_desde_lote(abierto)
            logger.info(
                "lote: attach a lote preexistente %s (iniciado_manualmente=%s)",
                abierto.id,
                getattr(abierto, "iniciado_manualmente", False),
            )
            self._notificar_lote_abierto(secret, abierto)
            return
        iniciado_manualmente, estado_inicial = self._estado_inicial_para_abrir()
        try:
            lote, creado = self._cliente.abrir_lote(
                secret,
                idempotency_key=str(uuid.uuid4()),
                producto_id=producto_id,
                momento=_ahora_iso(),
                modo=self._modo_para_abrir(),
                iniciado_manualmente=iniciado_manualmente,
                estado_inicial=estado_inicial,
            )
        except ErrorLotes as exc:
            self._aplicar_error(exc)
            return
        with self._lock:
            self._lote = lote
            self._ultimo_modo_emitido = self._modo_desde_lote(lote)
        self._marcar_ok()
        logger.info("lote: abierto %s (creado=%s)", lote.id, creado)
        # Notificar al ControlService para que se enganche de forma reactiva,
        # sin esperar al próximo /lotes/abierto. Si el callback falla, lo
        # absorbemos: el polling de control sigue como respaldo.
        self._notificar_lote_abierto(secret, lote)

    def _modo_para_abrir(self) -> str | None:
        """Modo elegido en la UI de la Pi para sembrar el lote nuevo."""
        if self._modo_provider is None:
            return None
        try:
            return self._modo_provider().value
        except Exception:  # noqa: BLE001 - un provider roto no debe impedir abrir
            logger.exception("lote: modo_provider falló; se usa el default del backend")
            return None

    def _estado_inicial_para_abrir(self) -> tuple[bool, dict | None]:
        """Devuelve (iniciado_manualmente, estado_inicial) para el alta."""
        if self._estado_inicial_provider is None:
            return False, None
        try:
            estado = self._estado_inicial_provider()
        except Exception:  # noqa: BLE001 - no bloquear el alta por un sensor
            logger.exception("lote: estado_inicial_provider falló")
            return False, None
        return (estado is not None), estado

    def _notificar_lote_abierto(self, secret: str, lote: Lote) -> None:
        """Cable el callback `on_lote_abierto` con el flag manual propagado."""
        if self._on_lote_abierto is None:
            return
        try:
            self._on_lote_abierto(
                secret,
                lote.id,
                self._modo_desde_lote(lote),
                None,
                bool(getattr(lote, "iniciado_manualmente", False)),
            )
        except Exception:  # noqa: BLE001 - el callback no debe matar el hilo
            logger.exception("lote: on_lote_abierto falló")

    @staticmethod
    def _modo_desde_lote(lote: Lote) -> ModoControl:
        """Espejo de ControlService._modo_desde_lote (defensa es la firma)."""
        modo_str = str(getattr(lote, "modo", "MANUAL"))
        try:
            return ModoControl(modo_str)
        except ValueError:
            return ModoControl.MANUAL

    # --- cierre ---

    def _evaluar_cierre(self) -> None:
        with self._lock:
            tipo = self._tipo
            lote = self._lote
            manual = self._cierre_manual
        if (
            tipo is not TipoDispositivo.SALIDA_HORNO
            or lote is None
            or lote.estado is not EstadoLote.ABIERTO
        ):
            return
        if not manual:
            local = self._streaming.segundos_sin_detecciones()
            if local is None or local < self._x:
                return
            ahora = time.monotonic()
            if ahora < self._proximo_chequeo_cierre:
                return
            self._proximo_chequeo_cierre = ahora + self._flush
            inactividad = self._inactividad_sector()
            if inactividad is None or inactividad < self._x:
                return
        self._cerrar_lote(
            MotivoCierre.MANUAL if manual else MotivoCierre.SIN_DETECCIONES
        )

    def _inactividad_sector(self) -> float | None:
        secret = self._secret
        if not secret:
            return None
        try:
            abierto = self._cliente.lote_abierto(secret)
        except ErrorLotes as exc:
            self._aplicar_error(exc)
            return None
        if abierto is None:
            # El lote lo cerraron desde el backend (u otra Pi): el estado
            # local (conteos, dedupe, cierre manual) es de ese lote muerto.
            self._limpiar_estado_lote()
            return None
        with self._lock:
            self._lote = abierto
        return abierto.inactividad_segundos

    def _limpiar_estado_lote(self) -> None:
        """Olvida el lote local: sus conteos, dedupe y cierre manual.

        Se usa cuando el lote desaparece del backend (404/409 en el reporte o
        ``lote_abierto`` nulo). Si no se limpia, el cierre del lote siguiente
        reporta los productos del anterior y el dedupe descarta eventos nuevos.
        """
        with self._lock:
            self._lote = None
            self._reportadas.clear()
            self._conteos_locales.clear()
            self._ultimo_modo_emitido = None
            self._cierre_manual = False

    def _cerrar_lote(self, motivo: MotivoCierre) -> None:
        secret = self._secret
        with self._lock:
            lote = self._lote
            self._cierre_manual = False
            conteos = self._conteos_finales()
        if not secret or lote is None:
            return
        try:
            cerrado = self._cliente.cerrar(
                secret,
                lote.id,
                idempotency_key=str(uuid.uuid4()),
                motivo=motivo,
                conteos=conteos,
                momento=_ahora_iso(),
            )
        except ErrorLotes as exc:
            if exc.codigo == 409:
                # El backend todavía considera el sector activo: reintentar luego.
                self._espera_hasta = time.monotonic() + self._backoff_ini
                return
            self._aplicar_error(exc)
            return
        with self._lock:
            self._lote = None
            self._ultimo_modo_emitido = None
            self._historial = (cerrado, *self._historial)[:_HISTORIAL_MAX]
        self._reportadas.clear()
        self._conteos_locales.clear()
        self._marcar_ok()
        logger.info("lote: cerrado %s (%s)", cerrado.id, motivo.value)
        # Notificar al ControlService para que libere el enganche sin esperar
        # a su próximo polling. El callback no debe matar el hilo.
        if self._on_lote_cerrado is not None:
            try:
                self._on_lote_cerrado()
            except Exception:  # noqa: BLE001
                logger.exception("lote: on_lote_cerrado falló")

    def _conteos_finales(self) -> Conteos:
        """Conteo local por estado; None para los que el modelo no produce."""
        producibles = self._estados_del_modelo(self._ultimo_modelo)

        def cuenta(estado: EstadoProducto) -> int | None:
            if estado not in producibles:
                return None
            return self._conteos_locales.get(estado, 0)

        ok = cuenta(EstadoProducto.OK)
        crudo = cuenta(EstadoProducto.CRUDO)
        quemado = cuenta(EstadoProducto.QUEMADO)
        total = sum(valor for valor in (ok, crudo, quemado) if valor is not None)
        return Conteos(ok=ok, crudo=crudo, quemado=quemado, total=total)

    def _estados_del_modelo(self, modelo_id: str | None) -> set[EstadoProducto]:
        """Estados que el modelo puede emitir, leídos de su archivo de clases."""
        if modelo_id is None:
            return set()
        cache = self._estados_por_modelo.get(modelo_id)
        if cache is not None:
            return cache
        entrada = next(
            (m for m in self._catalogo if m.model_id == modelo_id), None
        )
        estados: set[EstadoProducto] = set()
        if entrada is not None:
            try:
                texto = entrada.names_path.read_text(encoding="utf-8")
            except OSError:
                logger.warning("lote: no se pudo leer %s", entrada.names_path)
            else:
                for linea in texto.splitlines():
                    estado = clasificar_estado(linea)
                    if estado is not None:
                        estados.add(estado)
        self._estados_por_modelo[modelo_id] = estados
        return estados

    # --- manejo de errores ---

    def _aplicar_error(
        self, exc: ErrorLotes, batch: list[EventoDeteccion] | None = None
    ) -> None:
        ahora = time.monotonic()
        if exc.reintentable:
            self._espera_hasta = ahora + self._backoff_actual
            self._backoff_actual = min(
                self._backoff_max, max(self._backoff_ini, self._backoff_actual * 2)
            )
            self._marcar_error(str(exc))
            # F-R6: si tenemos el batch que intentamos enviar, lo dejamos
            # en el DLQ. La copia en `_pendientes` sigue para reintento
            # en memoria (la próxima vez que el backend responda). El DLQ
            # es la red de seguridad para el caso de un corte largo.
            if batch:
                self._intentos_fallidos += 1
                lote_id = (
                    self._lote.id
                    if self._lote is not None and self._lote.estado is EstadoLote.ABIERTO
                    else None
                )
                self._dlq.encolar(
                    batch,
                    codigo_http=exc.codigo,
                    detalle=str(exc),
                    intento=self._intentos_fallidos,
                    lote_id=lote_id,
                )
                if self._intentos_fallidos == 10:
                    logger.warning(
                        "lote: 10 envíos fallidos consecutivos; DLQ creciendo"
                    )
            return
        if exc.codigo in (401, 403):
            # La credencial fue rechazada: se avisa al dueño (DeviceService),
            # que invalida y re-registra. El borrado de device.json no se hace
            # acá: _sincronizar_dispositivo deja de reportar hasta que haya
            # credencial nueva, así no se repite el 401.
            if self._on_credencial_invalida is not None:
                self._on_credencial_invalida()
            self._espera_hasta = ahora + self._backoff_max
            self._marcar_error("Dispositivo sin autorización; re-registrar")
            return
        if exc.codigo in (404, 409):
            self._limpiar_estado_lote()
            self._descartar(batch)
            self._marcar_error(str(exc))
            return
        # 400/413/422: es un bug, se descarta el batch para no trabarlo.
        self._descartar(batch)
        self._marcar_error(str(exc))

    def _drenar_dlq_en_arranque(self) -> None:
        """Recupera los eventos guardados en el DLQ de una corrida previa.

        Cada batch se re-encola al frente de `_pendientes` para que el
        próximo `_reportar` los empuje. Si el reenvío vuelve a fallar,
        el batch termina en el DLQ otra vez — así el ciclo es estable
        y los eventos sobreviven a apagones.

        Idempotencia: el `evento_id` (UUID4) es único por evento, así
        que un reenvío duplicado genera un 409 en el backend (deduplicado
        por la idempotency_key) y se descarta limpio.
        """
        batches = self._dlq.drenar()
        if not batches:
            return
        # Re-armar como `EventoDeteccion` y anteponer al deque. Los más
        # viejos van al frente para mantener orden aproximado.
        eventos_recuperados: list[EventoDeteccion] = []
        for batch in batches:
            for ev in batch:
                evento = dict_a_evento(ev)
                if evento is not None:
                    eventos_recuperados.append(evento)
        if not eventos_recuperados:
            return
        with self._lock:
            # Insertamos al frente del deque para que el orden se aproxime
            # al cronológico del proceso anterior.
            existentes = list(self._pendientes)
            self._pendientes.clear()
            for ev in eventos_recuperados:
                self._pendientes.append(ev)
            for ev in existentes:
                self._pendientes.append(ev)
        logger.info(
            "lote: drenados %d eventos del DLQ al boot", len(eventos_recuperados)
        )
        # Si el primer reenvío sale OK, `_reportar`/`_marcar_ok` resetea
        # `_intentos_fallidos` y vacía el DLQ. No lo hacemos acá: el
        # camino feliz lo cubre el lazo normal.

    def _vaciar_dlq_si_ok(self) -> None:
        """Limpia el DLQ sólo cuando la outbox quedó vacía.

        Llamado desde `_reportar()` cuando el batch se envió al backend
        sin error. Si todavía quedan batches en `_pendientes`, el DLQ es
        la única copia que sobrevive a un corte: borrarlo antes de
        reenviarlos los perdería para siempre.
        """
        if (
            self._intentos_fallidos == 0
            and not self._pendientes
            and self._dlq.path.exists()
        ):
            self._dlq.vaciar()

    def _descartar(self, batch: list[EventoDeteccion] | None) -> None:
        if not batch:
            return
        with self._lock:
            for _ in batch:
                if self._pendientes:
                    self._pendientes.popleft()
            self._rechazados += len(batch)

    def _marcar_ok(self) -> None:
        self._backoff_actual = self._backoff_ini
        self._espera_hasta = 0.0
        # F-R6: reset del contador de fallos para que el siguiente ciclo
        # de error empiece de 1. Si justo después de un OK el DLQ tiene
        # contenido, ya fue drenado en el arranque y/o re-enviado en
        # este ciclo: vaciarlo mantiene el archivo chico.
        self._intentos_fallidos = 0
        self._vaciar_dlq_si_ok()
        with self._lock:
            self._backend_ok = True
            self._mensaje = ""

    def _marcar_error(self, mensaje: str) -> None:
        with self._lock:
            self._backend_ok = False
            self._mensaje = mensaje
