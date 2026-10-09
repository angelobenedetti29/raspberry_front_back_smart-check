"""Servicio de registro del dispositivo de la Raspberry contra el backend."""

import ipaddress
import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from urllib.parse import urlsplit

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
        self._url = config.device.public_url.strip()
        self._local_port = config.device.local_port
        self._cliente = ClienteRegistro(
            config.api.base_url,
            config.api.registration_requests_endpoint,
            url=self._url,
        )
        self._cliente_dispositivo = ClienteDispositivo(
            config.api.base_url, config.api.dispositivos_nombre_endpoint
        )
        self._cliente_rename_url = ClienteDispositivo(
            config.api.base_url, config.api.dispositivos_rename_url_endpoint
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
        self._rename_token: str | None = None
        self._created_at: str | None = None
        self._mensaje: str | None = None
        self._tipo: TipoDispositivo | None = None
        self._hostname_sincronizado = self._hostname
        # El alta no es automática: el hilo solo pide registro cuando el
        # operador lo dispara (o cuando retoma una solicitud ya persistida).
        self._solicitado = False



        #! Si quedó una rename_url que el backend todavía no conoce (porque el
        #! operador la editó en Configuración y la Pi ya estaba aprobada), el
        #! lazo principal la manda al Go con backoff infinito. Se sincroniza
        #! apenas el endpoint del backend esté disponible; mientras tanto, la
        #! Pi sigue atendiendo renombres en el bind local ya re-arrancado.
        self._url_pendiente: str | None = None
        self._url_backoff_segundos: float = 5.0
        self._endpoint_rename_url_ausente: bool = False

        self._recuperar_estado_guardado()

    @staticmethod
    def _local_host(hostname: str) -> bool:
        """True si el host es local (loopback); cualquier otro es remoto.

        `urlsplit` devuelve el IPv6 loopback sin corchetes (`::1`), por eso se
        normaliza antes de comparar.
        """
        host = hostname.strip().strip("[]").lower()
        return host in {"localhost", "127.0.0.1", "::1"}

    @staticmethod
    def derivar_bind(
        url: str,
        listen_address: str,
        local_port: int,
    ) -> tuple[str, int]:
        """Decide en qué (host, puerto) escuchar según `device.public_url`.

        Una URL vacía cae al bind legacy (`listen_address` + `local_port`); un
        host local se bindea tal cual; un host remoto (túnel) cae a loopback
        porque la Pi no puede bindear el dominio público. Es una función pura:
        no toca sockets, no lee config, sólo parsea. La usa `run.py` para el
        bind inicial del servidor HTTP (`backend.http.ServidorHTTP`) y para
        re-derivarlo cuando el operador cambia la URL o el puerto desde
        Configuración, con el mismo criterio en ambos casos.
        """
        url_normalizada = url.strip()
        parsed = None
        if url_normalizada:
            try:
                parsed = urlsplit(url_normalizada)
            except ValueError:
                parsed = None
            # Sin esquema y sin netloc no hay URL utilizable: fallback legacy.
            if parsed is not None and not parsed.scheme and not parsed.netloc:
                parsed = None
        if parsed is None:
            return listen_address, local_port
        host = (parsed.hostname or "").strip().lower()
        if not host:
            return listen_address, local_port
        if DeviceService._local_host(host):
            # Host local: normalizamos a 127.0.0.1 (IPv4 puro) en vez de dejar
            # "localhost". uvicorn al recibir "localhost" puede bindear en una
            # sola familia de sockets según el sistema; en este host bindeó
            # sólo IPv4 y el backend Go (que prefiere ::1 al resolver
            # "localhost") rebotaba con connection refused. El fallback
            # legacy ya era 127.0.0.1, así que alineamos todo a IPv4.
            bind_host = "127.0.0.1"
        else:
            # Host no loopback. Dos sub-casos honestos:
            # - hostname (no parsea como IP, ej. *.trycloudflare.com): la Pi no
            #   puede bindear el dominio del túnel, así que escucha en loopback
            #   y cloudflared reenvía el tráfico ahí.
            # - IP literal routable (ej. 192.168.100.8): el backend Go en Docker
            #   la necesita alcanzable en la LAN, así que bindeamos 0.0.0.0.
            try:
                ipaddress.ip_address(host)
            except ValueError:
                bind_host = "127.0.0.1"
            else:
                bind_host = "0.0.0.0"
        try:
            puerto_url = parsed.port
        except ValueError:
            puerto_url = None
        if puerto_url is not None:
            return bind_host, puerto_url
        if DeviceService._local_host(host):
            # Host local: el puerto del scheme tiene sentido.
            if parsed.scheme == "http":
                return bind_host, 80
            if parsed.scheme == "https":
                return bind_host, 443
            return bind_host, local_port
        # Host remoto (túnel cloudflared, etc): el puerto del scheme es el
        # público (443 en quick tunnels), pero el bind local lo decide el
        # túnel. Caemos al `local_port` configurado como fallback.
        return bind_host, local_port

    # --- Protocolo Service ---

    def start(self) -> None:
        """Arranca el hilo de registro (idempotente).

        El servidor HTTP local del renombre Go→Pi lo arranca `run.py` aparte
        (`backend.http.ServidorHTTP`); este servicio sólo corre su polling.
        """
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
        estado sin despertar al hilo. La `device.public_url` también es
        obligatoria para poder dar el alta, ya que es lo que el backend guarda
        como `rename_url` para enviar los renombres: si está vacía, el alta
        falla con un mensaje claro y el hilo no se despierta.
        """
        # Requisito al alta: el backend guarda `rename_url` para mandar el
        # push de renombre a esta Pi. Sin URL no podemos registrar.
        if not self._url:
            mensaje = (
                "No se puede registrar: device.public_url está vacío. Levantá "
                "el túnel cloudflared, pegá la URL pública en config.json > "
                "device.public_url y reiniciá la app antes de reintentar."
            )
            logger.warning("device: %s", mensaje)
            self._marcar_error(mensaje)
            return
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
            self._rename_token = None
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
            self._rename_token = None
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

    # --- Push de renombre desde el backend Go ---

    def secret(self) -> str | None:
        """Devuelve el secret del dispositivo (sólo lectura, con lock breve)."""
        with self._lock:
            return self._secret

    def device_id(self) -> str | None:
        """Devuelve el device_id asignado por el backend (sólo lectura, lock breve).

        Lo usa el router del renombre (`backend.http.routers.renombre`) para
        validar que el `device_id` del path del push Go→Pi coincide con el del
        dispositivo aprobado localmente (defensa en profundidad: el
        `rename_token` ya autentica, pero evita aceptar un push dirigido a otro
        UUID).
        """
        with self._lock:
            return self._device_id

    def rename_token(self) -> str | None:
        """Token de autenticación del push de renombre Go→Pi (lock breve).

        Es distinto del `secret`: lo usa el router del renombre
        (`backend.http.routers.renombre`) para validar el Bearer del backend
        Go, mientras que el `secret` autentica a la Pi contra los endpoints
        del Go.
        """
        with self._lock:
            return self._rename_token

    def aplicar_renombre_externo(self, nombre: str) -> None:
        """Aplica un renombre que viene del backend Go (push).

        A diferencia del flujo Pi→Go, acá el origen del cambio es el Go: ya
        está sincronizado por construcción, así que seteo _hostname y
        _hostname_sincronizado juntos para que el hilo de registro no lo
        re-pushee. Se reescribe config.json (consistencia en disco) y se
        re-persiste device.json para que un reinicio no revierta el nombre
        desde el estado guardado; la UI ve el cambio en el próximo snapshot.
        """
        nombre_limpio = nombre.strip()
        if not nombre_limpio:
            raise ValueError("El nombre del dispositivo no puede estar vacío")
        # Releer config crudo, mutar device.hostname, guardar atómicamente.
        from backend.config import guardar_config, leer_config_cruda

        crudo = leer_config_cruda()
        dispositivo = crudo.get("device", {})
        if not isinstance(dispositivo, dict):
            raise RuntimeError("config.json: 'device' no es un objeto JSON")
        dispositivo["hostname"] = nombre_limpio
        crudo["device"] = dispositivo
        guardar_config(crudo)  # valida + escribe atómico; levanta ConfigError si rompe
        with self._lock:
            self._hostname = nombre_limpio
            self._hostname_sincronizado = nombre_limpio
            self._mensaje = None
        # Refrescar device.json con el nombre nuevo (fuera del lock: _persistir
        # toma el lock brevemente). Sin esto, al reiniciar la Pi retomaría el
        # hostname viejo y lo re-sincronizaría contra el Go.
        self._persistir()
        # El hilo ya estaba durmiendo en _despertar: no hace falta despertarlo
        # explícitamente porque _renombre_pendiente va a devolver False.

    # --- Cambio de device.public_url / device.local_port en caliente ------------

    def actualizar_tunel(self, public_url: str, local_port: int) -> None:
        """Agenda un cambio de `device.public_url` y/o `device.local_port`.

        La llama el callback `_al_cambiar_tunel` de `run.py` después de que el
        operador guardó la sección Configuración con un par túnel distinto al
        del arranque. El re-bind local del servidor HTTP **no** lo hace este
        método: lo maneja `run.py` mediante `ServidorHTTP.rebind` (que conoce
        el `AppConfig`). Acá sólo se agenda la URL pública nueva para que el
        lazo principal la sincronice con el backend Go si la Pi ya está
        aprobada (backoff infinito: si el endpoint todavía no existe, se
        reintenta hasta que aparezca).

        `self._url` y `self._local_port` no se actualizan acá: quedan con los
        valores del arranque. El único consumidor de `self._url` es el guard de
        `solicitar_registro`, cuyo flujo de URL vacía→no vacía ya era
        inconsistente porque `ClienteRegistro` fija la URL al construirse en
        `__init__`; no hay dependencia real de esta mutación.
        """
        nueva_url = public_url.strip()
        with self._lock:
            url_actual = self._url
            puerto_actual = self._local_port
        url_cambia = nueva_url != url_actual
        puerto_cambia = local_port != puerto_actual
        if not (url_cambia or puerto_cambia):
            return  # noop: el operador "guardó" sin tocar el par efectivo.

        with self._lock:
            if (
                url_cambia
                and self._estado is EstadoRegistro.APROBADO
                and self._secret is not None
            ):
                self._url_pendiente = nueva_url
                # Si había un mensaje de error viejo, lo pisamos con el de sync;
                # si no, lo dejamos en None hasta el primer intento.
                mensaje_anterior = self._mensaje
                if mensaje_anterior is None or "rename_url" not in mensaje_anterior:
                    self._mensaje = "Sincronizando la nueva URL con el backend…"
                self._url_backoff_segundos = 5.0
                self._endpoint_rename_url_ausente = False
                debe_despertar = True
            else:
                # No aprobado todavía, o sólo cambió el puerto local: la URL
                # nueva ya la persistió la sección Configuración; no hay nada
                # que sincronizar contra el Go ahora.
                debe_despertar = False
        if debe_despertar:
            self._despertar.set()

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
            self._rename_token = guardado.rename_token
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

            # Sincronización de la rename_url pendiente con el backend. Si el
            # endpoint todavía no existe, se reintenta con backoff infinito;
            # si está todo al día, _hay_url_pendiente() corta en seguida.
            if self._hay_url_pendiente():
                try:
                    self._sincronizar_url()
                except Exception as exc:  # noqa: BLE001 - el hilo no debe morir
                    logger.exception("device: sincronizar url inesperado")
                with self._lock:
                    espera = self._url_backoff_segundos
                self._despertar.wait(timeout=espera)
                self._despertar.clear()
                continue

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
        # Una rename_url pendiente de sincronizar con el Go también exige
        # trabajar: sin esto, una Pi aprobada se queda colgada esperando
        # un comando explícito hasta que llegue el primer ping.
        if self._hay_url_pendiente():
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

    def _hay_url_pendiente(self) -> bool:
        """True si hay una `rename_url` que el Go todavía no conoce.

        Sólo tiene sentido sincronizarla si la Pi ya está aprobada: antes del
        alta, la URL se manda dentro del payload del alta mismo. Se consulta
        fuera del lock de los trabajadores para evitar self-deadlock.
        """
        with self._lock:
            return (
                self._estado is EstadoRegistro.APROBADO
                and self._secret is not None
                and self._url_pendiente is not None
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

    def _sincronizar_url(self) -> None:
        """Manda la `rename_url` pendiente al endpoint del backend.

        Llamada por el lazo principal cuando `_hay_url_pendiente()`. Backoff
        infinito: arranca en 5 s, se duplica hasta un tope de 300 s, y se
        resetea al concretarse. Si el backend todavía no expone el endpoint,
        responde 404 (o 405); eso se loguea una sola vez por corrida (no cada
        5 s) pero el reintento sigue.
        """
        with self._lock:
            url = self._url_pendiente
            secret = self._secret
            backoff = self._url_backoff_segundos
            ya_aviso_endpoint_ausente = self._endpoint_rename_url_ausente
        if url is None or secret is None:
            return
        try:
            self._cliente_rename_url.actualizar_rename_url(secret, url)
        except ErrorRegistro as exc:
            with self._lock:
                # 404/405: el endpoint todavía no existe en el Go. Log una vez
                # por corrida; después seguimos en silencio a nivel info.
                if exc.codigo in (404, 405):
                    if not ya_aviso_endpoint_ausente:
                        logger.warning(
                            "device: el backend no expone aún "
                            "dispositivos_rename_url_endpoint (%s); se reintenta "
                            "con backoff hasta que esté disponible.",
                            exc,
                        )
                    self._endpoint_rename_url_ausente = True
                else:
                    logger.warning("device: reintento sync rename_url: %s", exc)
                # Duplicar backoff, cap 300 s. Aplica a todos los fallos.
                self._url_backoff_segundos = min(backoff * 2, 300.0)
            return
        with self._lock:
            self._url_pendiente = None
            self._url_backoff_segundos = 5.0
            self._endpoint_rename_url_ausente = False
            self._mensaje = None
        logger.info("device: rename_url sincronizada con el backend")

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
                self._rename_token = respuesta.rename_token
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
                rename_token=self._rename_token,
            )
        try:
            self._almacen.guardar(estado)
        except ErrorAlmacen as exc:
            logger.warning("device: %s", exc)
