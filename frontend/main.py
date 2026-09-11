import os
import sys

# Añadir el directorio raíz del proyecto al sys.path para poder importar el backend
root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

import argparse

from PySide6.QtWidgets import QApplication

from frontend.app import FactoryControlApp
from frontend.config import DEFAULT_SOURCE


def main():
    parser = argparse.ArgumentParser(description="Ejecutar la interfaz gráfica de detección")
    parser.add_argument(
        "--source",
        type=str,
        default=DEFAULT_SOURCE,
        help="Fuente del video: '0' para la cámara de la Raspberry Pi, o la ruta de un video"
    )
    args, unknown = parser.parse_known_args()

    app = QApplication(sys.argv)
    window = FactoryControlApp(default_source=args.source)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
