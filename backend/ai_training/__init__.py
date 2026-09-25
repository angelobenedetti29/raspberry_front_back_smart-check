"""Herramientas de entrenamiento, exportación y compilación de modelos.

Módulo on-demand: `run.py` no lo arranca y la app no lo importa. Los entrypoints
viven en `scripts/` y las dependencias pesadas (ultralytics, torch, Hailo DFC)
son de desarrollo, no del runtime de la Raspberry.
"""
