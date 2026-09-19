"""Punto de entrada del frontend: abre la ventana principal de Factory Control.

Uso:
    python -m frontend.main

La fuente de vídeo inicial se toma de ``config.json`` (sección ``paths`` y el
fallback de ``frontend.config``). Se acepta ``--source`` solo por compatibilidad
con ``run.py``: se parsea pero se ignora, porque la configuración es la única
fuente de verdad.
"""

import os
import sys

# El frontend importa los paquetes `backend` y `streaming` desde la raíz del
# repositorio, por lo que esa raíz debe estar en sys.path antes de importarlos.
# Esto permite lanzar el módulo desde cualquier directorio de trabajo.
root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

import argparse

from PySide6.QtWidgets import QApplication

from frontend.app import FactoryControlApp


def main() -> None:
    """Parsea los argumentos, crea la QApplication y muestra la ventana."""
    parser = argparse.ArgumentParser(
        description="Ejecutar la interfaz gráfica de detección"
    )
    parser.add_argument(
        "--source",
        type=str,
        default=None,
        help=(
            "Ignorado: la fuente se toma de config.json. Se acepta por "
            "compatibilidad con run.py."
        ),
    )
    # parse_known_args (y no parse_args) porque Qt añade sus propios flags
    # (por ejemplo "-platform offscreen") que no deben hacer fallar el parseo.
    parser.parse_known_args()

    app = QApplication(sys.argv)
    window = FactoryControlApp()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
