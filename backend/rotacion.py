"""Rotación de archivos por tamaño, compartida por los tres logs del repo.

El DLQ de lotes, el audit de control y el SQLite de eventos usan el mismo
esquema: el archivo principal pasa a `.1`, los backups se desplazan y se
conservan `max_files` archivos en total (principal incluido).
"""

from __future__ import annotations

from pathlib import Path


def rotar_archivo(ruta: Path, max_files: int) -> None:
    """Mueve `ruta` a `.1` y desplaza los backups, conservando `max_files`.

    Con `max_files == 1` no hay backups: se borra el principal.
    """
    backups = max(0, max_files - 1)
    if backups == 0:
        ruta.unlink(missing_ok=True)
        return
    ruta.with_name(f"{ruta.name}.{backups}").unlink(missing_ok=True)
    for indice in range(backups - 1, 0, -1):
        origen = ruta.with_name(f"{ruta.name}.{indice}")
        if origen.exists():
            origen.replace(ruta.with_name(f"{ruta.name}.{indice + 1}"))
    if ruta.exists():
        ruta.replace(ruta.with_name(f"{ruta.name}.1"))
