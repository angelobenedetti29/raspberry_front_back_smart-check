"""Servidor HTTP general de la app.

Basado en FastAPI + uvicorn embebido, con routers enchufables: cada módulo del
backend que necesite uno o más endpoints HTTP implementa un `APIRouter` y lo
pasa al constructor de `ServidorHTTP`. El servidor no conoce a sus módulos de
dominio; sólo monta en una única app FastAPI los routers que recibe.
"""

from backend.http.servidor import ServidorHTTP

__all__ = ["ServidorHTTP"]
