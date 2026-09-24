"""Arranque de la interfaz gráfica."""

import sys

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from backend.config import AppConfig
from frontend.main_window import MainWindow
from frontend.nucleo import tema
from frontend.nucleo.controlador import ControladorStreaming
from frontend.nucleo.controlador_config import ControladorConfig
from frontend.nucleo.controlador_dispositivo import ControladorDispositivo
from frontend.nucleo.controlador_lotes import ControladorLotes
from frontend.nucleo.controlador_monitor import ControladorMonitor

_SIGNAL_PUMP_MS = 200


def request_gui_quit() -> bool:
    """Pide a la QApplication activa que salga. Devuelve False si no existe."""
    app = QApplication.instance()
    if app is None:
        return False
    app.quit()
    return True


def run_gui(
    config: AppConfig,
    controlador: ControladorStreaming,
    controlador_dispositivo: ControladorDispositivo,
    controlador_monitor: ControladorMonitor,
    controlador_config: ControladorConfig,
    controlador_lotes: ControladorLotes,
) -> int:
    """Crea la QApplication, muestra la ventana y corre el loop de eventos.

    Debe llamarse desde el hilo principal: Qt lo exige. Bloquea hasta que
    se cierra la ventana y devuelve el código de salida del loop.
    """
    app = QApplication(sys.argv)
    app.setApplicationName("Rework RB")
    app.setApplicationDisplayName("Rework RB")
    tema.cargar_tema(app)
    window = MainWindow(
        config,
        controlador,
        controlador_dispositivo,
        controlador_monitor,
        controlador_config,
        controlador_lotes,
    )
    window.show()

    # Qt corre su loop en C y no le devuelve el control a Python: sin este
    # timer, los handlers de señal nunca se ejecutan y el proceso queda vivo.
    pump = QTimer()
    pump.setInterval(_SIGNAL_PUMP_MS)
    pump.timeout.connect(lambda: None)
    pump.start()

    return app.exec()
