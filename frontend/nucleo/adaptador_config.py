"""Adaptador entre el archivo de configuración y el puerto de la interfaz.

Es el adaptador del puerto `ControladorConfig`: lee la config cruda y la edita
sobre una copia profunda para recién después validar y guardar con el backend.
Junto con `proveedor.py` es de los pocos módulos del frontend que importan
`backend.config`; nada de eso cruza hacia las secciones.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Mapping

from backend.config import ConfigError, guardar_config, leer_config_cruda
from backend.device.almacen import AlmacenDispositivo
from backend.lote.cliente import ClienteLotes
from backend.lote.tipos import ErrorLotes
from frontend.nucleo.controlador_config import (
    CampoConfigUI,
    ModeloConfigUI,
    ProductoConfigUI,
    ResultadoGuardadoUI,
    TipoCampo,
)

if TYPE_CHECKING:
    from backend.config import AppConfig

# Valor centinela para distinguir "la clave no está en el JSON" de un None real.
_FALTA = object()

# Cotas por defecto de los editores numéricos cuando el esquema no fija una.
_MAX_ENTERO = 2_000_000_000
_MAX_DECIMAL = 1_000_000.0

_CLAVE_RTSP = "stream.publisher.rtsp_transport"
_CLAVE_MODELO = "models.default_model_id"


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
        "stream.storage.file",
        "Almacenamiento",
        "Archivo de eventos",
        TipoCampo.TEXTO,
        "Nombre del log de eventos dentro de la carpeta de datos.",
    ),
    _CampoEsquema(
        "stream.storage.queue_size",
        "Almacenamiento",
        "Tamaño de cola",
        TipoCampo.ENTERO,
        "Eventos en espera de escribirse a disco.",
        minimo=1,
    ),
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
)


class AdaptadorConfig:
    """Implementa `ControladorConfig` sobre el archivo `config.json`.

    La configuración cargada (`AppConfig`) se guarda sólo como referencia; los
    valores editables se leen siempre crudos desde disco para no perder lo que
    otra edición haya dejado en el archivo.
    """

    def __init__(self, config: AppConfig) -> None:
        self._config = config

    def campos(self) -> tuple[CampoConfigUI, ...]:
        """Resuelve el esquema contra el JSON crudo actual."""
        cruda = leer_config_cruda()
        return tuple(self._campo(esquema, cruda) for esquema in _ESQUEMA)

    def guardar(self, valores: Mapping[str, object]) -> ResultadoGuardadoUI:
        """Aplica los valores sobre una copia y guarda; no levanta excepciones."""
        try:
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
        return ResultadoGuardadoUI(
            ok=True,
            mensaje="Cambios guardados. Reiniciá la aplicación para aplicarlos.",
        )

    def modelos(self) -> tuple[ModeloConfigUI, ...]:
        """Catálogo local de modelos con su vínculo al producto del backend."""
        cruda = leer_config_cruda()
        return tuple(
            ModeloConfigUI(model_id=model_id, label=label, producto_id=producto_id)
            for model_id, label, producto_id in _modelos_crudos(cruda)
        )

    def productos(self) -> tuple[ProductoConfigUI, ...]:
        """Productos del backend para vincular. Vacío si no se pudo consultar."""
        secret = _leer_secret(self._config)
        if not secret:
            return ()
        try:
            catalogo = ClienteLotes(self._config.api).productos(secret)
        except ErrorLotes:
            return ()
        return tuple(
            ProductoConfigUI(id=producto.id, nombre=producto.nombre)
            for producto in catalogo
            if producto.activo
        )

    def guardar_productos(
        self, por_modelo: Mapping[str, str | None]
    ) -> ResultadoGuardadoUI:
        """Guarda el producto vinculado a cada modelo; no levanta excepciones."""
        try:
            copia = copy.deepcopy(leer_config_cruda())
            _asignar_productos(copia, por_modelo)
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
        return ResultadoGuardadoUI(
            ok=True,
            mensaje="Vínculos guardados. Reiniciá la aplicación para aplicarlos.",
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
        """Opciones de los combos: transporte RTSP y catálogo de modelos."""
        if clave == _CLAVE_RTSP:
            return (("TCP", "tcp"), ("UDP", "udp"))
        if clave == _CLAVE_MODELO:
            return _opciones_modelos(cruda)
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


def _modelos_crudos(cruda: dict) -> list[tuple[str, str, str | None]]:
    """(model_id, label, producto_id) del catálogo crudo, en su orden."""
    modelos = cruda.get("models")
    if not isinstance(modelos, dict):
        return []
    catalogo = modelos.get("catalog")
    if not isinstance(catalogo, list):
        return []

    resultado: list[tuple[str, str, str | None]] = []
    for entrada in catalogo:
        if not isinstance(entrada, dict):
            continue
        model_id = entrada.get("model_id")
        if not isinstance(model_id, str) or not model_id:
            continue
        label = entrada.get("label")
        if not isinstance(label, str) or not label:
            label = model_id
        producto_id = entrada.get("producto_id")
        if not isinstance(producto_id, str) or not producto_id:
            producto_id = None
        resultado.append((model_id, label, producto_id))
    return resultado


def _asignar_productos(copia: dict, por_modelo: Mapping[str, str | None]) -> None:
    """Setea `producto_id` en cada entrada del catálogo según el mapping."""
    modelos = copia.get("models")
    if not isinstance(modelos, dict):
        return
    catalogo = modelos.get("catalog")
    if not isinstance(catalogo, list):
        return
    for entrada in catalogo:
        if not isinstance(entrada, dict):
            continue
        model_id = entrada.get("model_id")
        if isinstance(model_id, str) and model_id in por_modelo:
            entrada["producto_id"] = por_modelo[model_id]


def _leer_secret(config: AppConfig) -> str | None:
    """Secret persistido por el registro; None si todavía no hay alta."""
    estado = AlmacenDispositivo(Path(config.paths.root) / "device.json").cargar()
    return None if estado is None else estado.secret


__all__ = ["AdaptadorConfig"]
