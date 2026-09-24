"""Servicio de registro del dispositivo de la Raspberry contra el backend."""

import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from backend.config import AppConfig
from backend.device.almacen import AlmacenDispositivo, EstadoDispositivo, ErrorAlmacen
from backend.device.cliente import (
    ClienteDispositivo,
    ClienteRegistro,
    ErrorRegistro,
)
from backend.device.tipos import ErrorTipoDispositivo, TipoDispositivo

logger = logging.getLogger(__name__)


class EstadoRegistro(str, Enum):
    """Estado del registro tal como lo consume la UI."""

    NO_REGISTRADO = "NO_REGISTRADO"
    PENDIENTE = "PENDIENTE"
    APROBADO = "APROBADO"
    ERROR = "ERROR"
    REVOCADO = "REVOCADO"


@dataclass(frozen=True, slots=True)
class EstadoRegistroUI:
    """Foto del estado del registro para la UI."""

    estado: EstadoRegistro
    hostname: str
    request_id: str | None = None
    device_id: str | None = None
    mensaje: str | None = None
    tipo: TipoDispositivo | None = None


class DeviceService:
    """Registra la Raspberry contra el backend y persiste sus credenciales.

    start() no bloquea: lanza un hilo daemon que hace el alta y luego el
    polling hasta que el supervisor aprueba. stop() señaliza y espera al hilo.
    """

    name = "device"

    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._hostname = config.device.hostname
        self._intervalo = config.device.registration_poll_interval_seconds
        self._cliente = ClienteRegistro(
            config.api.base_url, config.api.registration_requests_endpoint
        )
        self._cliente_dispositivo = ClienteDispositivo(
            config.api.base_url, config.api.dispositivos_nombre_endpoint
        )
        self._almacen = AlmacenDispositivo(Path(config.paths.root) / "device.json")

        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._despertar = threading.Event()
        self._thread: threading.Thread | None = None
        self._estado = EstadoRegistro.NO_REGISTRADO
        self._request_id: str | None = None
        self._device_id: str | None = None
        self._secret: str | None = None
        self._created_at: str | None = None
        self._mensaje: str | None = None
        # Tipo elegido por el operador al registrar; propiedad del dispositivo
        # registrado, no de la config de arranque.
        self._tipo: TipoDispositivo | None = None
        # Nombre que el backend ya conoce: se parte del deseado (config) y se
        # ajusta al leer device.json / al registrar o renombrar con éxito.
        self._hostname_sincronizado = self._hostname
        # El alta no es automática: el hilo solo pide registro cuando el
        # operador lo dispara (o cuando retoma una solicitud ya persistida).
        self._solicitado = False

        self._recuperar_estado_guardado()

    # --- Protocolo Service ---

    def start(self) -> None:
        """Arranca el hilo de registro (idempotente)."""
        if self._thread is not None:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name=self.name, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Señaliza el cese, despierta al hilo y lo espera (bloquea)."""
        self._stop_event.set()
        self._despertar.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None

    # --- Comandos que consume la UI ---

    def solicitar_registro(self, tipo: TipoDispositivo) -> None:
        """Pide (o reintenta) el alta ante el backend y despierta al hilo.

        Si ya hay un request_id pendiente, el hilo retoma el polling de ese
        mismo request en vez de crear uno nuevo. El tipo es obligatorio y se
        valida antes de disparar nada; si es inválido, el fallo queda en el
        estado sin despertar al hilo.
        """
        try:
            tipo_valido = TipoDispositivo.desde(tipo)
        except ErrorTipoDispositivo as exc:
            logger.warning("device: %s", exc)
            self._marcar_error(str(exc))
            return
        with self._lock:
            self._mensaje = None
            self._solicitado = True
            self._tipo = tipo_valido
            if self._request_id is None:
                self._estado = EstadoRegistro.NO_REGISTRADO
        self._despertar.set()

    def olvidar(self) -> None:
        """Borra las credenciales persistidas y vuelve a NO_REGISTRADO."""
        with self._lock:
            self._estado = EstadoRegistro.NO_REGISTRADO
            self._request_id = None
            self._device_id = None
            self._secret = None
            self._created_at = None
            self._mensaje = None
            self._hostname_sincronizado = self._hostname
            self._solicitado = False
            self._tipo = None
        try:
            self._almacen.borrar()
        except ErrorAlmacen as exc:
            logger.warning("device: no se pudo borrar device.json: %s", exc)

    def invalidar_credencial(self, motivo: str | None = None) -> None:
        """Invalida la credencial local y deja el registro a la espera de la UI.

        La llaman monitor y lotes cuando el backend rechaza el secret de forma
        sostenida. Es idempotente porque ambos pueden coincidir; conserva el
        tipo ya conocido para que la UI muestre el registro revocado.
        """
        with self._lock:
            if self._estado is EstadoRegistro.REVOCADO:
                return
            self._estado = EstadoRegistro.REVOCADO
            self._request_id = None
            self._device_id = None
            self._secret = None
            self._created_at = None
            self._mensaje = motivo
            self._hostname_sincronizado = self._hostname
            # La baja del dispositivo no dispara un alta automática: queda en
            # REVOCADO hasta que el operador pida el registro desde la UI.
            self._solicitado = False
        try:
            self._almacen.borrar()
        except ErrorAlmacen as exc:
            logger.warning("device: no se pudo borrar device.json: %s", exc)
        self._despertar.set()

    def estado(self) -> EstadoRegistroUI:
        """Foto inmutable del estado del registro."""
        with self._lock:
            return EstadoRegistroUI(
                estado=self._estado,
                hostname=self._hostname,
                request_id=self._request_id,
                device_id=self._device_id,
                mensaje=self._mensaje,
                tipo=self._tipo,
            )

    # --- Recuperación al arrancar ---

    def _recuperar_estado_guardado(self) -> None:
        """Retoma el registro desde device.json si había algo persistido.

        Si había un request_id, el hilo continúa el polling de esa solicitud;
        si ya estaba aprobado, arranca directamente en APROBADO.
        """
        guardado = self._almacen.cargar()
        if guardado is None:
            return
        with self._lock:
            self._request_id = guardado.request_id or None
            self._device_id = guardado.device_id
            self._secret = guardado.secret
            self._created_at = guardado.created_at
            self._hostname_sincronizado = guardado.hostname
            self._tipo = guardado.tipo
            if guardado.status == EstadoRegistro.APROBADO.value:
                self._estado = EstadoRegistro.APROBADO
            elif self._request_id is not None:
                self._estado = EstadoRegistro.PENDIENTE
            else:
                self._estado = EstadoRegistro.NO_REGISTRADO

    # --- Hilo de trabajo ---

    def _run(self) -> None:
        while not self._stop_event.is_set():
            if not self._debe_trabajar():
                self._despertar.wait(timeout=0.5)
                self._despertar.clear()
                continue
            try:
                if self._renombre_pendiente():
                    self._renombrar()
                elif self._request_id is None:
                    self._solicitar()
                else:
                    self._consultar()
            except ErrorRegistro as exc:
                logger.warning("device: %s", exc)
                if exc.codigo in (401, 403):
                    # Credencial rechazada por el backend: invalidar y re-registrar.
                    self.invalidar_credencial("Credencial rechazada al renombrar")
                elif self._estado is EstadoRegistro.APROBADO:
                    # Ya registrado: el fallo es del renombre, no del alta.
                    self._marcar_fallo_renombre(str(exc))
                else:
                    self._marcar_error(str(exc))
            except Exception as exc:  # noqa: BLE001 - el hilo no debe morir
                logger.exception("device: error inesperado")
                self._marcar_error(str(exc))

            if self._estado is EstadoRegistro.APROBADO and not self._renombre_pendiente():
                self._despertar.wait()  # ya registrado: esperar comando explícito
                self._despertar.clear()
                continue
            self._despertar.wait(timeout=self._intervalo)
            self._despertar.clear()

    def _debe_trabajar(self) -> bool:
        # Un nombre distinto al que conoce el backend exige sincronizar,
        # aunque el registro ya esté aprobado. Se consulta fuera del lock
        # porque _renombre_pendiente toma el mismo lock (no reentrante).
        if self._renombre_pendiente():
            return True
        with self._lock:
            # Ya registrado: no hay nada que sondear hasta que el operador
            # olvide el dispositivo.
            if self._estado is EstadoRegistro.APROBADO:
                return False
            # Solicitud ya iniciada (pedida ahora o recuperada del disco):
            # corresponde seguir consultando hasta la aprobación.
            if self._request_id is not None:
                return True
            # Sin solicitud en curso, solo se pide registro si el operador lo
            # disparó: el alta nunca es automática al arrancar.
            if not self._solicitado:
                return False
            return self._estado in (
                EstadoRegistro.NO_REGISTRADO,
                EstadoRegistro.ERROR,
                EstadoRegistro.REVOCADO,
            )

    def _renombre_pendiente(self) -> bool:
        """True si el nombre deseado difiere del que conoce el backend."""
        with self._lock:
            return (
                self._estado is EstadoRegistro.APROBADO
                and self._secret is not None
                and self._hostname_sincronizado != self._hostname
            )

    def _solicitar(self) -> None:
        # Guarda defensiva: el hilo sólo llega acá con un tipo válido ya
        # fijado por solicitar_registro o recuperado de device.json.
        if self._tipo is None:
            raise ErrorRegistro("No hay tipo de dispositivo definido para el registro")
        respuesta = self._cliente.solicitar(self._hostname, self._tipo)
        with self._lock:
            self._request_id = respuesta.request_id
            self._created_at = datetime.now(timezone.utc).isoformat()
            self._estado = EstadoRegistro.PENDIENTE
            self._mensaje = None
            # El hostname recién enviado es el que el backend tomará como
            # propio al aprobar: no queda renombre pendiente.
            self._hostname_sincronizado = self._hostname
        self._persistir()
        logger.info("device: solicitud %s creada (PENDING)", respuesta.request_id)

    def _renombrar(self) -> None:
        """Sincroniza el nombre nuevo con el backend usando el secret del device."""
        with self._lock:
            secret = self._secret
            nombre = self._hostname
        self._cliente_dispositivo.renombrar(secret or "", nombre)
        with self._lock:
            self._hostname_sincronizado = nombre
            self._mensaje = None
        self._persistir()
        logger.info("device: nombre sincronizado como %s", nombre)

    def _consultar(self) -> None:
        with self._lock:
            request_id = self._request_id
        if request_id is None:
            return
        respuesta = self._cliente.consultar(request_id)
        if respuesta.status == "APPROVED":
            if respuesta.device_id is None or respuesta.secret is None:
                raise ErrorRegistro(
                    "El backend aprobó la solicitud sin device_id o secret"
                )
            with self._lock:
                self._device_id = respuesta.device_id
                self._secret = respuesta.secret
                self._estado = EstadoRegistro.APROBADO
                self._mensaje = None
                # Si el backend devuelve el tipo, ese manda; si no, se conserva
                # el solicitado por el operador.
                if respuesta.tipo is not None:
                    self._tipo = respuesta.tipo
            self._persistir()
            logger.info("device: aprobado como %s", respuesta.device_id)
        elif respuesta.status == "PENDING":
            with self._lock:
                self._estado = EstadoRegistro.PENDIENTE
                self._mensaje = None

    def _marcar_error(self, detalle: str) -> None:
        """Registra un fallo sin perder una solicitud ya iniciada.

        Si hay request_id, la solicitud sigue viva y el supervisor todavía
        puede aprobarla: se mantiene PENDIENTE con el mensaje de error. Sin
        request_id no hay nada pendiente, así que el estado es ERROR.
        """
        with self._lock:
            if self._request_id is not None:
                self._estado = EstadoRegistro.PENDIENTE
            else:
                self._estado = EstadoRegistro.ERROR
            self._mensaje = detalle

    def _marcar_fallo_renombre(self, detalle: str) -> None:
        """Registra un fallo de renombre sin perder el registro ya aprobado."""
        with self._lock:
            self._mensaje = detalle

    def _persistir(self) -> None:
        with self._lock:
            estado = EstadoDispositivo(
                hostname=self._hostname_sincronizado,
                request_id=self._request_id or "",
                status=self._estado.value,
                device_id=self._device_id,
                secret=self._secret,
                created_at=self._created_at,
                tipo=self._tipo,
            )
        try:
            self._almacen.guardar(estado)
        except ErrorAlmacen as exc:
            logger.warning("device: %s", exc)
