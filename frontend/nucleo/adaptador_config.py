"""Adaptador entre el archivo de configuración y el puerto de la interfaz.

Es el adaptador del puerto `ControladorConfig`: lee la config cruda y la edita
sobre una copia profunda para recién después validar y guardar con el backend.
Junto con `proveedor.py` es de los pocos módulos del frontend que importan
`backend.config`; nada de eso cruza hacia las secciones.
"""

from __future__ import annotations

import copy
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Mapping

from backend.config import LOCK_CONFIG, ConfigError, guardar_config, leer_config_cruda
from frontend.nucleo.controlador_config import (
    CampoConfigUI,
    ResultadoGuardadoUI,
    TipoCampo,
)

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from backend.config import AppConfig

# Valor centinela para distinguir "la clave no está en el JSON" de un None real.
_FALTA = object()

# Cotas por defecto de los editores numéricos cuando el esquema no fija una.
_MAX_ENTERO = 2_000_000_000
_MAX_DECIMAL = 1_000_000.0

_CLAVE_RTSP = "stream.publisher.rtsp_transport"
_CLAVE_MODELO = "models.default_model_id"
_CLAVE_DRIVER_SENSORES = "control.sensores.driver"
_CLAVE_DRIVER_ACTUADORES = "control.actuadores.driver"
_CLAVE_LOTES_MODO_ENDPOINT = "control.lotes_modo_endpoint"


@dataclass(frozen=True, slots=True)
class _CampoEsquema:
    """Definición declarativa de un campo editable."""

    clave: str
    grupo: str
    etiqueta: str
    tipo: TipoCampo
    ayuda: str = ""
    minimo: float | None = None
    maximo: float | None = None
    decimales: int = 0
    paso: float = 1.0


# --- Esquema del subconjunto operativo aprobado ---------------------------------
# El orden de esta tupla define el orden de los grupos y de los campos dentro de
# cada grupo en la pantalla. Los valores se resuelven contra el JSON crudo.

_ESQUEMA: tuple[_CampoEsquema, ...] = (
    # Captura
    _CampoEsquema(
        "stream.capture.source",
        "Captura",
        "Fuente por defecto",
        TipoCampo.TEXTO,
        'Índice de cámara como "0" o nombre de un archivo de la carpeta de videos.',
    ),
    _CampoEsquema(
        "stream.capture.width",
        "Captura",
        "Ancho",
        TipoCampo.ENTERO,
        "Ancho de captura en píxeles. Debe ser par.",
        minimo=2,
    ),
    _CampoEsquema(
        "stream.capture.height",
        "Captura",
        "Alto",
        TipoCampo.ENTERO,
        "Alto de captura en píxeles. Debe ser par.",
        minimo=2,
    ),
    _CampoEsquema(
        "stream.capture.fps",
        "Captura",
        "Cuadros por segundo",
        TipoCampo.ENTERO,
        "Ritmo con el que se capturan y publican los cuadros.",
        minimo=1,
    ),
    _CampoEsquema(
        "stream.capture.loop_video",
        "Captura",
        "Repetir video",
        TipoCampo.BOOLEANO,
        "Al terminar el archivo de video, vuelve a empezar.",
    ),
    _CampoEsquema(
        "stream.capture.buffer_size",
        "Captura",
        "Tamaño de buffer",
        TipoCampo.ENTERO,
        "Cuadros que retiene la captura antes de descartar.",
        minimo=0,
    ),
    _CampoEsquema(
        "stream.capture.stable_frames",
        "Captura",
        "Cuadros estables",
        TipoCampo.ENTERO,
        "Cuadros consecutivos que deben llegar para dar la fuente por estable.",
        minimo=0,
    ),
    _CampoEsquema(
        "stream.capture.read_timeout_seconds",
        "Captura",
        "Timeout de lectura (s)",
        TipoCampo.DECIMAL,
        "Espera máxima por un cuadro antes de reintentar la conexión.",
        minimo=0,
        decimales=1,
        paso=0.5,
    ),
    # Publicación
    _CampoEsquema(
        "stream.publisher.output_url",
        "Publicación",
        "URL de salida",
        TipoCampo.TEXTO,
        "Destino RTSP al que se publica el stream.",
    ),
    _CampoEsquema(
        "stream.publisher.bitrate",
        "Publicación",
        "Bitrate",
        TipoCampo.TEXTO,
        'Tasa objetivo del codificador, ej. "2M".',
    ),
    _CampoEsquema(
        "stream.publisher.ffmpeg_executable",
        "Publicación",
        "Ejecutable de ffmpeg",
        TipoCampo.TEXTO,
        "Nombre o ruta del binario de ffmpeg.",
    ),
    _CampoEsquema(
        "stream.publisher.encoder",
        "Publicación",
        "Codificador",
        TipoCampo.TEXTO,
        "Ej. libx264 en CPU o el codificador de la NPU.",
    ),
    _CampoEsquema(
        "stream.publisher.preset",
        "Publicación",
        "Preset",
        TipoCampo.TEXTO,
        "Velocidad del codificador (calidad contra CPU).",
    ),
    _CampoEsquema(
        "stream.publisher.tune",
        "Publicación",
        "Tune",
        TipoCampo.TEXTO,
        "Ajuste fino del codificador; zerolatency prioriza latencia.",
    ),
    _CampoEsquema(
        "stream.publisher.pixel_format",
        "Publicación",
        "Formato de píxel",
        TipoCampo.TEXTO,
        "Formato de color de salida, ej. yuv420p.",
    ),
    _CampoEsquema(
        _CLAVE_RTSP,
        "Publicación",
        "Transporte RTSP",
        TipoCampo.OPCION,
        "TCP es más confiable; UDP puede bajar la latencia.",
    ),
    _CampoEsquema(
        "stream.publisher.gop_seconds",
        "Publicación",
        "GOP (s)",
        TipoCampo.ENTERO,
        "Cada cuántos segundos va un cuadro clave.",
        minimo=0,
    ),
    _CampoEsquema(
        "stream.publisher.b_frames",
        "Publicación",
        "Cuadros B",
        TipoCampo.ENTERO,
        "Cuadros B del codificador; 0 los desactiva (mejor latencia).",
        minimo=0,
    ),
    _CampoEsquema(
        "stream.publisher.queue_size",
        "Publicación",
        "Tamaño de cola",
        TipoCampo.ENTERO,
        "Cuadros en espera antes de descartar los más viejos.",
        minimo=1,
    ),
    _CampoEsquema(
        "stream.publisher.write_timeout",
        "Publicación",
        "Timeout de escritura (s)",
        TipoCampo.DECIMAL,
        "Espera máxima para entregar un cuadro a ffmpeg antes de reiniciarlo.",
        minimo=0,
        decimales=1,
        paso=0.1,
    ),
    _CampoEsquema(
        "stream.publisher.stable_seconds",
        "Publicación",
        "Segundos estables",
        TipoCampo.DECIMAL,
        "Tiempo publicando sin errores para dar la salida por estable.",
        minimo=0,
        decimales=1,
        paso=0.5,
    ),
    # Inferencia
    _CampoEsquema(
        "stream.inference.image_size",
        "Inferencia",
        "Tamaño de imagen",
        TipoCampo.ENTERO,
        "Lado al que se redimensiona el cuadro antes de inferir.",
        minimo=1,
    ),
    _CampoEsquema(
        "stream.inference.confidence_threshold",
        "Inferencia",
        "Umbral de confianza",
        TipoCampo.DECIMAL,
        "Confianza mínima global para aceptar una detección (0 a 1).",
        minimo=0,
        maximo=1,
        decimales=2,
        paso=0.01,
    ),
    _CampoEsquema(
        "stream.inference.nms_threshold",
        "Inferencia",
        "Umbral NMS",
        TipoCampo.DECIMAL,
        "Solapamiento máximo entre cajas antes de descartar (0 a 1).",
        minimo=0,
        maximo=1,
        decimales=2,
        paso=0.01,
    ),
    # Vista previa
    _CampoEsquema(
        "stream.preview.fps",
        "Vista previa",
        "Cuadros por segundo",
        TipoCampo.ENTERO,
        "Ritmo de la vista local; no afecta lo que se publica.",
        minimo=1,
    ),
    # Almacenamiento
    _CampoEsquema(
        "stream.storage.max_bytes",
        "Almacenamiento",
        "Tamaño máximo (bytes)",
        TipoCampo.ENTERO,
        "Tamaño del log antes de rotar.",
        minimo=1,
    ),
    _CampoEsquema(
        "stream.storage.max_files",
        "Almacenamiento",
        "Archivos máximos",
        TipoCampo.ENTERO,
        "Cantidad de archivos rotados que se conservan.",
        minimo=1,
    ),
    _CampoEsquema(
        "stream.storage.persist_no_detection_every",
        "Almacenamiento",
        "Latido cada N cuadros",
        TipoCampo.ENTERO,
        "Cada cuántos cuadros se escribe una marca sin detecciones.",
        minimo=1,
    ),
    # Reconexión
    _CampoEsquema(
        "stream.reconnect.initial_seconds",
        "Reconexión",
        "Espera inicial (s)",
        TipoCampo.DECIMAL,
        "Primera espera tras perder la fuente.",
        minimo=0,
        decimales=1,
        paso=0.5,
    ),
    _CampoEsquema(
        "stream.reconnect.max_seconds",
        "Reconexión",
        "Espera máxima (s)",
        TipoCampo.DECIMAL,
        "Tope del tiempo de espera entre reintentos.",
        minimo=0,
        decimales=1,
        paso=1.0,
    ),
    # Modelos
    _CampoEsquema(
        _CLAVE_MODELO,
        "Modelos",
        "Modelo por defecto",
        TipoCampo.OPCION,
        "Modelo que se carga al iniciar la aplicación.",
    ),
    # API
    _CampoEsquema(
        "api.base_url",
        "API",
        "URL base",
        TipoCampo.TEXTO,
        "Esquema, host y puerto del backend, sin barra final.",
    ),
    _CampoEsquema(
        "api.registration_requests_endpoint",
        "API",
        "Endpoint de altas",
        TipoCampo.TEXTO,
        "Ruta para pedir el alta del dispositivo.",
    ),
    _CampoEsquema(
        "api.dispositivos_ping_endpoint",
        "API",
        "Endpoint de ping",
        TipoCampo.TEXTO,
        "Ruta del latido del dispositivo.",
    ),
    _CampoEsquema(
        "api.dispositivos_nombre_endpoint",
        "API",
        "Endpoint de renombre",
        TipoCampo.TEXTO,
        "Ruta para que la Raspberry sincronice su nombre.",
    ),
    # Dispositivo
    _CampoEsquema(
        "device.hostname",
        "Dispositivo",
        "Nombre del dispositivo",
        TipoCampo.TEXTO,
        "Nombre lógico con el que la Raspberry se identifica.",
    ),
    _CampoEsquema(
        "device.public_url",
        "Dispositivo",
        "URL pública del túnel",
        TipoCampo.TEXTO,
        "URL completa con la que el backend alcanza esta Pi (ej.: "
        "http://localhost:8090 en pruebas locales o "
        "https://mi-tunel.trycloudflare.com con un túnel). Vacía = la Pi "
        "escucha directo en listen_address:local_port sin túnel.",
    ),
    _CampoEsquema(
        "device.local_port",
        "Dispositivo",
        "Puerto local",
        TipoCampo.ENTERO,
        "Puerto interno donde escucha el servidor de renombre. Si usás "
        "cloudflared con `--url http://localhost:PUERTO`, tiene que "
        "coincidir con ese PUERTO. Rango 1-65535.",
        minimo=1,
        maximo=65535,
    ),
    _CampoEsquema(
        "device.ping_interval_seconds",
        "Dispositivo",
        "Intervalo de ping (s)",
        TipoCampo.DECIMAL,
        "Cada cuánto se avisa al backend que el equipo sigue vivo.",
        minimo=0,
        decimales=1,
        paso=5.0,
    ),
    _CampoEsquema(
        "device.registration_poll_interval_seconds",
        "Dispositivo",
        "Intervalo de consulta (s)",
        TipoCampo.DECIMAL,
        "Cada cuánto se consulta si el alta fue aprobada.",
        minimo=0,
        decimales=1,
        paso=1.0,
    ),
    # Lotes
    _CampoEsquema(
        "lote.habilitado",
        "Lotes",
        "Coordinación de lotes",
        TipoCampo.BOOLEANO,
        "Activa el reporte de lotes por sector contra el backend.",
    ),
    _CampoEsquema(
        "lote.cierre_sin_detecciones_segundos",
        "Lotes",
        "Cierre sin detecciones (s)",
        TipoCampo.DECIMAL,
        "Segundos sin detecciones para cerrar el lote; debe superar el "
        "tránsito por el horno.",
        minimo=0,
        decimales=1,
        paso=1.0,
    ),
    _CampoEsquema(
        "lote.flush_segundos",
        "Lotes",
        "Envío cada (s)",
        TipoCampo.DECIMAL,
        "Cadencia de envío de eventos y de chequeo de cierre.",
        minimo=0,
        decimales=1,
        paso=0.5,
    ),
    _CampoEsquema(
        "lote.max_eventos_por_envio",
        "Lotes",
        "Máximo de eventos por envío",
        TipoCampo.ENTERO,
        "Tope de eventos en cada lote enviado (1 a 100).",
        minimo=1,
        maximo=100,
    ),
    _CampoEsquema(
        "lote.cola_eventos",
        "Lotes",
        "Tamaño de cola",
        TipoCampo.ENTERO,
        "Buzón entre la inferencia y el servicio de lotes.",
        minimo=1,
    ),
    _CampoEsquema(
        "lote.max_pendientes",
        "Lotes",
        "Máximo de pendientes",
        TipoCampo.ENTERO,
        "Eventos retenidos si el backend no responde.",
        minimo=1,
    ),
    _CampoEsquema(
        "lote.refresco_catalogo_segundos",
        "Lotes",
        "Refresco de catálogo (s)",
        TipoCampo.DECIMAL,
        "Cada cuánto se refrescan catálogo, sector y lote abierto.",
        minimo=0,
        decimales=1,
        paso=5.0,
    ),
    _CampoEsquema(
        "lote.reintento_inicial_segundos",
        "Lotes",
        "Reintento inicial (s)",
        TipoCampo.DECIMAL,
        "Primera espera tras un error reintentable.",
        minimo=0,
        decimales=1,
        paso=0.5,
    ),
    _CampoEsquema(
        "lote.reintento_maximo_segundos",
        "Lotes",
        "Reintento máximo (s)",
        TipoCampo.DECIMAL,
        "Tope del backoff entre reintentos.",
        minimo=0,
        decimales=1,
        paso=1.0,
    ),
    # Control (SCA-400)
    _CampoEsquema(
        _CLAVE_DRIVER_SENSORES,
        "Control",
        "Driver de sensores",
        TipoCampo.OPCION,
        'Backend del lector de sensores. "stub" no requiere PLC real; '
        '"simulado" usa una planta en memoria; "modbus_rtu" y "modbus_tcp" '
        "requieren hardware Modbus cableado.",
    ),
    _CampoEsquema(
        _CLAVE_DRIVER_ACTUADORES,
        "Control",
        "Driver de actuadores",
        TipoCampo.OPCION,
        'Backend del PLC que recibe las consignas. "stub" no escribe '
        'hardware; "simulado" fija los objetivos en una planta en memoria; '
        '"modbus_rtu"/"modbus_tcp" usan el mismo PLC que sensores '
        "o uno distinto según la sección.",
    ),
    _CampoEsquema(
        _CLAVE_LOTES_MODO_ENDPOINT,
        "Control",
        "Endpoint de cambio de modo",
        TipoCampo.TEXTO,
        "Ruta que la Pi usa para sincronizar el modo del lote con el "
        'backend (ej.: "/api/v1/lotes/{id}/modo").',
    ),
)


class AdaptadorConfig:
    """Implementa `ControladorConfig` sobre el archivo `config.json`.

    La configuración cargada (`AppConfig`) se guarda sólo como referencia; los
    valores editables se leen siempre crudos desde disco para no perder lo que
    otra edición haya dejado en el archivo.

    `on_device_tunel_changed` se invoca cuando un `guardar()` exitoso detecta
    que cambió `device.public_url` o `device.local_port` respecto al valor
    en disco. Es el único side-effect hacia backend que dispara el guardado:
    permite re-bindear el servidor HTTP local (`backend.http.ServidorHTTP`) y
    sincronizar la nueva URL con
    el backend Go sin reiniciar la app. Si el guardado falla (validación o
    disco), el callback no se llama.
    """

    def __init__(
        self,
        config: AppConfig,
        on_device_tunel_changed: Callable[[str, int], None] | None = None,
    ) -> None:
        self._config = config
        self._on_device_tunel_changed = on_device_tunel_changed

    def campos(self) -> tuple[CampoConfigUI, ...]:
        """Resuelve el esquema contra el JSON crudo actual."""
        cruda = leer_config_cruda()
        return tuple(self._campo(esquema, cruda) for esquema in _ESQUEMA)

    def guardar(self, valores: Mapping[str, object]) -> ResultadoGuardadoUI:
        """Aplica los valores sobre una copia y guarda; no levanta excepciones.

        Antes de tocar el JSON, captura `device.public_url` y `device.local_port`
        actuales: si alguno difiere respecto al valor entrante, después del
        guardado exitoso llama al callback `on_device_tunel_changed` con la
        nueva URL y el nuevo puerto para que `DeviceService` re-arranque el
        bind local y sincronice con el backend Go. No se dispara si el
        guardado falló (validación o disco).
        """
        try:
            cruda_pre = leer_config_cruda()
            url_pre = str(_resolver(cruda_pre, "device.public_url") or "").strip()
            puerto_pre_raw = _resolver(cruda_pre, "device.local_port")
            if isinstance(puerto_pre_raw, bool):
                puerto_pre = 0
            elif isinstance(puerto_pre_raw, (int, float)):
                puerto_pre = int(puerto_pre_raw)
            else:
                puerto_pre = 0
        except (ConfigError, OSError):
            url_pre = ""
            puerto_pre = 0
        try:
            # Bajo el lock compartido: leer y escribir sin que el push de
            # renombre (otro hilo) modifique el archivo en el medio.
            with LOCK_CONFIG:
                copia = copy.deepcopy(leer_config_cruda())
                for clave, valor in valores.items():
                    _asignar(copia, clave, valor)
                guardar_config(copia)
        except ConfigError as exc:
            return ResultadoGuardadoUI(
                ok=False,
                mensaje=str(exc).strip() or "El backend rechazó la configuración.",
            )
        except OSError as exc:
            return ResultadoGuardadoUI(
                ok=False,
                mensaje=f"No se pudo escribir la configuración: {exc}",
            )

        # Notificar al backend el cambio de par túnel (URL pública + puerto
        # local). Se hace después del guardado porque si el guardado falla el
        # archivo no cambió: la Pi debe seguir con el bind y la URL viejos.
        # El callback no debe levantar: si lo hace, lo registramos pero igual
        # informamos éxito del guardado.
        url_post = str(valores.get("device.public_url") or "").strip()
        puerto_post_raw = valores.get("device.local_port")
        if isinstance(puerto_post_raw, bool):
            puerto_post = 0
        elif isinstance(puerto_post_raw, (int, float)):
            puerto_post = int(puerto_post_raw)
        elif isinstance(puerto_post_raw, str):
            try:
                puerto_post = int(puerto_post_raw)
            except ValueError:
                puerto_post = 0
        else:
            puerto_post = 0
        cambio_url = url_pre != url_post and bool(url_post)
        cambio_puerto = puerto_pre != puerto_post and puerto_post > 0
        if (cambio_url or cambio_puerto) and self._on_device_tunel_changed is not None:
            try:
                self._on_device_tunel_changed(url_post, puerto_post)
            except Exception as exc:  # noqa: BLE001 - side-effect debe no romper
                logger.warning(
                    "config: no se pudo notificar el cambio de túnel: %s", exc
                )

        return ResultadoGuardadoUI(
            ok=True,
            mensaje="Cambios guardados. Reiniciá la aplicación para aplicarlos.",
        )

    # --- internos ---

    def _campo(self, esquema: _CampoEsquema, cruda: dict) -> CampoConfigUI:
        """Arma un campo de la UI, con valor por defecto si falta en el JSON."""
        opciones = self._opciones(esquema.clave, cruda)
        crudo = _resolver(cruda, esquema.clave)
        valor = (
            _defecto(esquema.tipo, opciones)
            if crudo is _FALTA
            else _convertir(crudo, esquema.tipo)
        )
        return CampoConfigUI(
            clave=esquema.clave,
            grupo=esquema.grupo,
            etiqueta=esquema.etiqueta,
            tipo=esquema.tipo,
            valor=valor,
            opciones=opciones,
            ayuda=esquema.ayuda,
            minimo=esquema.minimo,
            maximo=esquema.maximo,
            decimales=esquema.decimales,
            paso=esquema.paso,
        )

    @staticmethod
    def _opciones(
        clave: str, cruda: dict
    ) -> tuple[tuple[str, object], ...]:
        """Opciones de los combos: transporte RTSP, drivers Modbus y modelos."""
        if clave == _CLAVE_RTSP:
            return (("TCP", "tcp"), ("UDP", "udp"))
        if clave == _CLAVE_MODELO:
            return _opciones_modelos(cruda)
        if clave in (_CLAVE_DRIVER_SENSORES, _CLAVE_DRIVER_ACTUADORES):
            return (
                ("Stub (sin hardware)", "stub"),
                ("Simulado (planta en memoria)", "simulado"),
                ("Modbus RTU (RS-485)", "modbus_rtu"),
                ("Modbus TCP (Ethernet)", "modbus_tcp"),
            )
        return ()


def _opciones_modelos(cruda: dict) -> tuple[tuple[str, object], ...]:
    """Pares (etiqueta, model_id) del catálogo crudo, en su orden."""
    modelos = cruda.get("models")
    if not isinstance(modelos, dict):
        return ()
    catalogo = modelos.get("catalog")
    if not isinstance(catalogo, list):
        return ()

    opciones: list[tuple[str, object]] = []
    for entrada in catalogo:
        if not isinstance(entrada, dict):
            continue
        model_id = entrada.get("model_id")
        if not isinstance(model_id, str) or not model_id:
            continue
        etiqueta = entrada.get("label")
        if not isinstance(etiqueta, str) or not etiqueta:
            etiqueta = model_id
        opciones.append((etiqueta, model_id))
    return tuple(opciones)


def _resolver(datos: dict, clave: str) -> object:
    """Sigue la ruta punteada; devuelve `_FALTA` si algún tramo no existe."""
    actual: object = datos
    for parte in clave.split("."):
        if not isinstance(actual, dict) or parte not in actual:
            return _FALTA
        actual = actual[parte]
    return actual


def _asignar(datos: dict, clave: str, valor: object) -> None:
    """Escribe `valor` en la ruta punteada, creando los tramos que falten."""
    partes = clave.split(".")
    actual = datos
    for parte in partes[:-1]:
        siguiente = actual.get(parte)
        if not isinstance(siguiente, dict):
            siguiente = {}
            actual[parte] = siguiente
        actual = siguiente
    actual[partes[-1]] = valor


def _convertir(valor: object, tipo: TipoCampo) -> object:
    """Normaliza el valor crudo del JSON al tipo que espera el editor."""
    if tipo is TipoCampo.TEXTO:
        return valor if isinstance(valor, str) else str(valor)
    if tipo is TipoCampo.ENTERO:
        if isinstance(valor, bool) or not isinstance(valor, (int, float)):
            return 0
        return int(valor)
    if tipo is TipoCampo.DECIMAL:
        if isinstance(valor, bool) or not isinstance(valor, (int, float)):
            return 0.0
        return float(valor)
    if tipo is TipoCampo.BOOLEANO:
        return bool(valor)
    return valor


def _defecto(tipo: TipoCampo, opciones: tuple[tuple[str, object], ...]) -> object:
    """Valor razonable cuando la clave no está en el archivo."""
    if tipo is TipoCampo.TEXTO:
        return ""
    if tipo is TipoCampo.ENTERO:
        return 0
    if tipo is TipoCampo.DECIMAL:
        return 0.0
    if tipo is TipoCampo.BOOLEANO:
        return False
    return opciones[0][1] if opciones else ""


__all__ = ["AdaptadorConfig"]
