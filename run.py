"""Punto de entrada de la aplicación.

Carga la configuración, arranca los servicios de backend en segundo plano
y levanta la interfaz gráfica en el hilo principal.
"""

import logging
import signal

from backend.config import AppConfig, ConfigError, load_config
from backend.control import ControlService
from backend.control.sensores import crear_aplicador, crear_lector
from backend.device import DeviceService
from backend.http import ServidorHTTP
from backend.http.routers import construir_router_renombre
from backend.lote import LoteService
from backend.monitor import MonitorService
from backend.monitor.acelerador import habilitar_monitor_hailo
from backend.service import Service
from backend.streaming import StreamingService
from frontend.app import request_gui_quit, run_gui
from frontend.nucleo.adaptador import AdaptadorStreaming
from frontend.nucleo.adaptador_config import AdaptadorConfig
from frontend.nucleo.adaptador_control import AdaptadorControl
from frontend.nucleo.adaptador_dispositivo import AdaptadorDispositivo
from frontend.nucleo.adaptador_lotes import AdaptadorLotes
from frontend.nucleo.adaptador_monitor import AdaptadorMonitor

logger = logging.getLogger(__name__)


def _configure_logging() -> None:
    """Configura el logging global del proceso."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _load_config_or_exit() -> AppConfig:
    """Carga la configuración o registra el error y termina el proceso."""
    try:
        return load_config()
    except ConfigError as exc:
        logger.error("Error de configuración: %s", exc)
        raise SystemExit(1) from exc


def _handle_signal(signum: int, _frame) -> None:
    """Inicia el apagado ordenado ante SIGINT/SIGTERM."""
    logger.info("Señal %s recibida; iniciando apagado", signal.Signals(signum).name)
    if not request_gui_quit():
        # La GUI todavía no arrancó (o ya terminó): salir por la vía normal.
        raise SystemExit(128 + signum)


def _install_signal_handlers() -> None:
    """Registra SIGINT y SIGTERM para apagado ordenado."""
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)


def _start_services(services: list[Service]) -> list[Service]:
    """Arranca los servicios de backend y devuelve los que quedaron activos."""
    started: list[Service] = []
    for service in services:
        try:
            service.start()
        except Exception:
            logger.exception("No se pudo iniciar el servicio '%s'", service.name)
            _stop_services(started)
            raise
        started.append(service)
    return started


def _stop_services(services: list[Service]) -> None:
    """Apaga los servicios en orden inverso al arranque."""
    for service in reversed(services):
        try:
            service.stop()
        except Exception:
            logger.exception("Error al detener el servicio '%s'", service.name)


def main() -> int:
    """Orquesta carga, arranque, GUI y apagado ordenado."""
    _configure_logging()
    _install_signal_handlers()
    config = _load_config_or_exit()

    # Habilita el monitor de HailoRT antes de que el motor de inferencia cree el
    # VDevice: es lo único que deja la utilización de la NPU en /tmp. Sin Hailo
    # no tiene efecto.
    habilitar_monitor_hailo()

    # El adaptador se arma antes de arrancar y es lo único que recibe la GUI: así
    # las secciones nunca tocan el backend. DeviceService va primero porque el
    # streaming deriva la URL de publicación de su nombre y tipo vivos.
    dispositivo = DeviceService(config)
    streaming = StreamingService(
        config,
        nombre_provider=lambda: dispositivo.estado().hostname,
        tipo_provider=lambda: dispositivo.estado().tipo,
    )
    controlador = AdaptadorStreaming(config, streaming)
    controlador_dispositivo = AdaptadorDispositivo(dispositivo)
    # Servidor HTTP general de la app (FastAPI + uvicorn embebido). El bind
    # sale de device.public_url / device.local_port con la misma regla que usaba
    # el DeviceService para su servidor de renombre; hoy monta sólo el router
    # del push de renombre del backend Go, pero admite routers nuevos.
    bind_host, bind_port = DeviceService.derivar_bind(
        config.device.public_url,
        config.device.listen_address,
        config.device.local_port,
    )
    servidor_http = ServidorHTTP(
        routers=[construir_router_renombre(dispositivo)],
        bind_host=bind_host,
        bind_port=bind_port,
    )
    monitor = MonitorService(
        config, on_credencial_invalida=dispositivo.invalidar_credencial
    )
    controlador_monitor = AdaptadorMonitor(monitor)

    def _al_cambiar_tunel(nueva_url: str, nuevo_puerto: int) -> None:
        """Re-bindea el servidor HTTP y agenda la URL para el sync con el Go.

        Si el re-bind falla (puerto ocupado, sin permisos), no se sincroniza la
        URL nueva con el backend Go: la Pi sigue anunciando la anterior.
        """
        bind_host_nuevo, bind_port_nuevo = DeviceService.derivar_bind(
            nueva_url, config.device.listen_address, nuevo_puerto
        )
        try:
            servidor_http.rebind(bind_host_nuevo, bind_port_nuevo)
        except Exception:
            logger.exception(
                "http: no se pudo re-bindear el servidor; URL no sincronizada"
            )
            return
        dispositivo.actualizar_tunel(nueva_url, nuevo_puerto)

    # La configuración se edita desde la UI, pero no necesita servicios
    # arrancados: sólo lee y escribe el archivo. El callback conecta el
    # guardado con el servidor HTTP y el servicio de dispositivo para que un
    # cambio del par túnel (`device.public_url` + `device.local_port`)
    # re-bindee el servidor y sincronice la nueva URL con el backend Go sin
    # reiniciar la app.
    controlador_config = AdaptadorConfig(
        config,
        on_device_tunel_changed=_al_cambiar_tunel,
    )
    # Control automático (SCA-400): sólo aplica a Pi ENTRADA. El servicio se
    # autodeshabilita si el tipo es SALIDA_HORNO. Hasta que exista driver
    # real, se usan stubs (no leen ni escriben hardware). Se construye antes
    # que LoteService para cablear los callbacks reactivos (on_lote_abierto,
    # on_lote_cerrado, actualizar_modo) — sin esto, control sólo descubría
    # lotes por su propio polling.
    control = ControlService(
        config,
        lector=crear_lector(config.control.sensores),
        aplicador=crear_aplicador(config.control.actuadores),
        tipo_provider=lambda: dispositivo.estado().tipo,
        secret_provider=dispositivo.secret,
        on_credencial_invalida=dispositivo.invalidar_credencial,
    )
    # Adaptador UI: traduce el dict de ControlService.estado() a dataclasses
    # propias del frontend (frontend/nucleo/adaptador_control.py). Único
    # módulo del frontend que importa backend.control.
    controlador_control = AdaptadorControl(control)

    # Los lotes se suscriben al streaming: arrancan después y, por el orden
    # inverso de apagado, se detienen antes que el streaming. Los callbacks
    # que apuntan a control hacen que el cambio de modo sea reactivo (sin
    # esperar al próximo /lotes/abierto); el polling de control sigue como
    # respaldo por si el callback falla.
    lotes = LoteService(
        config,
        streaming,
        on_credencial_invalida=dispositivo.invalidar_credencial,
        on_lote_abierto=control.on_lote_abierto,
        on_lote_cerrado=control.on_lote_cerrado,
        on_modo_actualizado=control.actualizar_modo,
        modo_provider=control.modo_actual,
        estado_inicial_provider=control.estado_inicial_lote,
    )
    controlador_lotes = AdaptadorLotes(lotes)

    services = _start_services(
        [streaming, dispositivo, monitor, lotes, control, servidor_http]
    )
    logger.info("Aplicación iniciada")
    try:
        return run_gui(
            config,
            controlador,
            controlador_dispositivo,
            controlador_monitor,
            controlador_config,
            controlador_lotes,
            controlador_control,
        )
    finally:
        _stop_services(services)
        logger.info("Aplicación finalizada")


if __name__ == "__main__":
    raise SystemExit(main())
