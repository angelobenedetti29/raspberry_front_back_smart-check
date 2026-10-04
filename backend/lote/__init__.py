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
from backend.device.almacen import AlmacenDispositivo
from backend.device.tipos import TipoDispositivo
from backend.lote.cliente import ClienteLotes
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
    ) -> None:
        self._config = config
        self._streaming = streaming
        self._on_credencial_invalida = on_credencial_invalida
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
        self._producto_por_modelo: dict[str | None, str | None] = {
            entrada.model_id: entrada.producto_id for entrada in config.models.catalog
        }

        self._parar = threading.Event()
        self._hilo: threading.Thread | None = None
        self._cola: ColaEventos | None = None

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

    # --- Protocolo Service ---

    def start(self) -> None:
        """Arranca el hilo de lotes (idempotente). No bloquea."""
        if not self._lote_cfg.habilitado:
            logger.info("lote: deshabilitado por configuración")
            return
        if self._hilo is not None:
            return
        self._cola = self._streaming.suscribir_eventos(self._lote_cfg.cola_eventos)
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
            self._sector = sector
            self._lote = lote
            self._historial = historial
        self._marcar_ok()

    # --- traducción de eventos a productos ---

    def _encolar(self, eventos: list[EventoObservado]) -> None:
        tipo = self._tipo
        if tipo is TipoDispositivo.ENTRADA_HORNO:
            self._encolar_entrada(eventos)
        elif tipo is TipoDispositivo.SALIDA_HORNO:
            self._encolar_salida(eventos)

    def _encolar_entrada(self, eventos: list[EventoObservado]) -> None:
        """La entrada sólo abre el lote con el primer `alta`."""
        with self._lock:
            if self._lote is not None:
                return
        for observado in eventos:
            if observado.evento.tipo != "alta":
                continue
            producto_id = self._producto_por_modelo.get(observado.modelo_id)
            if not producto_id:
                self._marcar_error(
                    "Modelo sin producto vinculado (Configuración)"
                )
                return
            self._abrir_lote(producto_id)
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

    def _traducir(self, observado: EventoObservado) -> EventoDeteccion | None:
        """Un evento por producto confirmado: cruce de línea (o quemada/baja como fallback)."""
        evento = observado.evento
        clave = (observado.modelo_id, evento.pista_id)

        # Con línea de conteo habilitada, el conteo oficial se produce al cruzar la meta.
        if evento.tipo == "cruce":
            estado = clasificar_estado(evento.label)
            if clave in self._reportadas or estado is None:
                return None
            self._reportadas.add(clave)
            self._ultimo_modelo = observado.modelo_id
            return self._evento(observado, estado)

        # Fallback si la línea de conteo está deshabilitada:
        # quemado al confirmar 'quemada', ok/crudo al 'baja'.
        if not self._config.stream.counting.enabled:
            if evento.tipo == "quemada":
                if clave in self._reportadas:
                    return None
                self._reportadas.add(clave)
                self._ultimo_modelo = observado.modelo_id
                return self._evento(observado, EstadoProducto.QUEMADO)
            if evento.tipo == "baja":
                estado = clasificar_estado(evento.label)
                if clave in self._reportadas or estado is None:
                    return None
                self._reportadas.add(clave)
                self._ultimo_modelo = observado.modelo_id
                return self._evento(observado, estado)

        return None

    def _evento(
        self, observado: EventoObservado, estado: EstadoProducto
    ) -> EventoDeteccion:
        evento = observado.evento
        return EventoDeteccion(
            evento_id=str(uuid.uuid4()),
            producto_id=self._producto_por_modelo.get(observado.modelo_id),
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
            with self._lock:
                self._lote = abierto
            return abierto.id
        if not producto_id:
            self._marcar_error("Modelo sin producto vinculado (Configuración)")
            return None
        try:
            lote, _ = self._cliente.abrir_lote(
                secret,
                idempotency_key=str(uuid.uuid4()),
                producto_id=producto_id,
                momento=_ahora_iso(),
            )
        except ErrorLotes as exc:
            self._aplicar_error(exc)
            return None
        with self._lock:
            self._lote = lote
        logger.info("lote: abierto degradado por la salida %s", lote.id)
        return lote.id

    def _abrir_lote(self, producto_id: str) -> None:
        secret = self._secret
        if not secret:
            return
        try:
            lote, creado = self._cliente.abrir_lote(
                secret,
                idempotency_key=str(uuid.uuid4()),
                producto_id=producto_id,
                momento=_ahora_iso(),
            )
        except ErrorLotes as exc:
            self._aplicar_error(exc)
            return
        with self._lock:
            self._lote = lote
        self._marcar_ok()
        logger.info("lote: abierto %s (creado=%s)", lote.id, creado)

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
            with self._lock:
                self._lote = None
            return None
        with self._lock:
            self._lote = abierto
        return abierto.inactividad_segundos

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
            self._historial = (cerrado, *self._historial)[:_HISTORIAL_MAX]
        self._reportadas.clear()
        self._conteos_locales.clear()
        self._marcar_ok()
        logger.info("lote: cerrado %s (%s)", cerrado.id, motivo.value)

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
            with self._lock:
                self._lote = None
            self._descartar(batch)
            self._marcar_error(str(exc))
            return
        # 400/413/422: es un bug, se descarta el batch para no trabarlo.
        self._descartar(batch)
        self._marcar_error(str(exc))

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
        with self._lock:
            self._backend_ok = True
            self._mensaje = ""

    def _marcar_error(self, mensaje: str) -> None:
        with self._lock:
            self._backend_ok = False
            self._mensaje = mensaje
