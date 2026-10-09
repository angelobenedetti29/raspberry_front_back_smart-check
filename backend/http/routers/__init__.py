"""Routers HTTP enchufables que monta `backend.http.ServidorHTTP`."""

from backend.http.routers.renombre import construir_router_renombre

__all__ = ["construir_router_renombre"]
