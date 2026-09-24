"""Sección Horno y cinta: línea de entrada (contenido pendiente).

Es una sección del grupo ENTRADA: sólo aparece en el rail cuando el dispositivo
registrado es de entrada de horno. Todavía no muestra temperaturas ni velocidad
de cinta; el panel vacío deja claro que el contenido está pendiente.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget

from frontend.nucleo.contrato import ContextoApp, GrupoSeccion, SeccionBase
from frontend.nucleo.iconos import icono
from frontend.nucleo.tema import color


class HornoYCinta(SeccionBase):
    """Pantalla del horno y la cinta de entrada, todavía sin contenido."""

    id = "horno_cinta"
    titulo = "Horno y cinta"
    icono = "herramienta"
    grupo = GrupoSeccion.ENTRADA

    def __init__(self, ctx: ContextoApp, parent: QWidget | None = None) -> None:
        super().__init__(ctx, parent)
        self._construir()

    # --- construcción ---

    def _construir(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 14, 20, 16)
        layout.setSpacing(10)

        subtitulo = QLabel("Estado del horno y la cinta de entrada.")
        subtitulo.setProperty("rol", "subtitulo")
        layout.addWidget(subtitulo)

        layout.addWidget(self._construir_pendiente(), 1)

    def _construir_pendiente(self) -> QFrame:
        """Panel vacío: aclara que el contenido de la sección está pendiente."""
        marco = QFrame()
        marco.setObjectName("estadoVacio")

        layout = QVBoxLayout(marco)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(6)

        glifo = QLabel()
        glifo.setPixmap(
            icono("herramienta", color=color("texto_muted"), px=36).pixmap(36, 36)
        )
        glifo.setFixedSize(36, 36)

        titulo = QLabel("Contenido pendiente")
        titulo.setObjectName("vacioTitulo")
        titulo.setAlignment(Qt.AlignmentFlag.AlignCenter)

        texto = QLabel(
            "Esta sección todavía no está implementada.\n"
            "Por ahora sólo ocupa un lugar en la navegación."
        )
        texto.setObjectName("vacioTexto")
        texto.setAlignment(Qt.AlignmentFlag.AlignCenter)

        layout.addStretch(1)
        layout.addWidget(glifo, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(titulo)
        layout.addWidget(texto)
        layout.addStretch(1)
        return marco
