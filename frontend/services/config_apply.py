"""Planificación pura de cambios de configuración (sin Qt, sin I/O).

``plan_changes`` compara dos ``AppConfig`` campo por campo y clasifica cada hoja
cambiada según el trabajo que exige aplicarla:

- ``detector``: hay que reconstruir el detector (modelo, etiquetas, umbral).
- ``pipeline``: hay que reiniciar el pipeline de vídeo. Engloba todo el estado
  que el worker congela al construirse: captura, publicación, almacenamiento y
  reconexión.
- ``restart_app``: solo se aplica reiniciando la aplicación completa (modelos,
  rutas, host/puerto de la API y el resto de ``device``).
- ``hot``: se puede propagar en vivo sin reiniciar nada. Solo lo que la
  aplicación realmente aplica: ``device.producto_id`` y ``api.lote_endpoint``.

Las hojas se devuelven con su ruta *anidada* tal como aparece en ``config.json``
(por ejemplo ``stream.publisher.output_url``), y en el orden en que las declara
``AppConfig.to_dict``.
"""

from __future__ import annotations

from smartcheck_config import AppConfig

__all__ = ["plan_changes"]

# Secciones del plan que siempre aparecen en el resultado.
_SECTIONS = ("detector", "pipeline", "restart_app", "hot")

# Metadatos que no representan un cambio funcional de configuración.
_IGNORED_LEAVES = frozenset({"revision", "schema_version"})

# Hojas que la aplicación aplica en vivo, sin reiniciar el worker ni el proceso.
_HOT_LEAVES = frozenset(
    {
        # Se propaga al worker vivo; aplica al próximo lote.
        "device.producto_id",
        # Se relee al construir cada petición de lote.
        "api.lote_endpoint",
    }
)


def _collect_changed_leaves(old, new, prefix, out):
    """Anexa a ``out`` las rutas punteadas de los valores que difieren.

    Los diccionarios se recorren recursivamente; las listas y escalares se
    comparan como un todo (una lista distinta se reporta como una sola hoja).
    """
    if isinstance(old, dict) and isinstance(new, dict):
        keys = list(new) + [key for key in old if key not in new]
        for key in keys:
            path = f"{prefix}.{key}" if prefix else key
            if key in old and key in new:
                _collect_changed_leaves(old[key], new[key], path, out)
            else:
                out.append(path)
        return
    if old != new:
        out.append(prefix)


def _classify(path: str) -> str:
    """Devuelve la sección del plan que corresponde a una ruta punteada."""
    if path in _HOT_LEAVES:
        return "hot"
    if path == "stream.inference" or path.startswith("stream.inference."):
        return "detector"
    if path.startswith("stream."):
        # Captura, publicación, almacenamiento y reconexión los congela el
        # worker al construirse: se aplican reiniciándolo (pipeline).
        return "pipeline"
    # ``models.*``, ``paths.*``, ``device.*`` (salvo producto_id) y ``api.*``
    # (salvo lote_endpoint) no se aplican en vivo: exigen reinicio.
    return "restart_app"


def plan_changes(old: AppConfig, new: AppConfig) -> dict:
    """Clasifica los cambios entre ``old`` y ``new`` en cuatro listas.

    Returns:
        ``{"detector": [...], "pipeline": [...], "restart_app": [...], "hot": [...]}``
        con los nombres de campos hoja cambiados (rutas anidadas de config.json).
    """
    changed: list[str] = []
    _collect_changed_leaves(old.to_dict(), new.to_dict(), "", changed)

    plan = {section: [] for section in _SECTIONS}
    for path in changed:
        if path in _IGNORED_LEAVES:
            continue
        plan[_classify(path)].append(path)
    return plan
