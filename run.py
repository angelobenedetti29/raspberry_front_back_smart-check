"""Punto de entrada de la aplicación.

Carga la configuración, arranca los servicios de backend en segundo plano
y levanta la interfaz gráfica en el hilo principal.
"""

import logging
import signal

from backend.config import AppConfig, ConfigError, load_config
from backend.device import DeviceService
from backend.lote import LoteService
from backend.monitor import MonitorService
from backend.monitor.acelerador import habilitar_monitor_hailo
from backend.service import Service
from backend.streaming import StreamingService
from frontend.app import request_gui_quit, run_gui
from frontend.nucleo.adaptador import AdaptadorStreaming
from frontend.nucleo.adaptador_config import AdaptadorConfig
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
    # las secciones nunca tocan el backend.
    streaming = StreamingService(config)
    controlador = AdaptadorStreaming(config, streaming)
    dispositivo = DeviceService(config)
    controlador_dispositivo = AdaptadorDispositivo(dispositivo)
    monitor = MonitorService(
        config, on_credencial_invalida=dispositivo.invalidar_credencial
    )
    controlador_monitor = AdaptadorMonitor(monitor)
    # La configuración se edita desde la UI, pero no necesita servicios
    # arrancados: sólo lee y escribe el archivo.
    controlador_config = AdaptadorConfig(config)
    # Los lotes se suscriben al streaming: arrancan después y, por el orden
    # inverso de apagado, se detienen antes que el streaming.
    lotes = LoteService(
        config, streaming, on_credencial_invalida=dispositivo.invalidar_credencial
    )
    controlador_lotes = AdaptadorLotes(lotes)

    services = _start_services([streaming, dispositivo, monitor, lotes])
    logger.info("Aplicación iniciada")
    try:
        return run_gui(
            config,
            controlador,
            controlador_dispositivo,
            controlador_monitor,
            controlador_config,
            controlador_lotes,
        )
    finally:
        _stop_services(services)
        logger.info("Aplicación finalizada")


if __name__ == "__main__":
    raise SystemExit(main())
