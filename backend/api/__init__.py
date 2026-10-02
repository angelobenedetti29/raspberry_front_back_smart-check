"""Capa de acceso HTTP compartida contra el backend Go.

Expone un `ClienteHTTP` (transporte) y el helper `desenvolver` (envelope
`{success, data}`), más los errores de transporte que cada subsistema traduce a
su tipo de dominio.
"""

from backend.api.cliente import ClienteHTTP, desenvolver
from backend.api.errores import ErrorApi, ErrorConexion, ErrorFormato, ErrorHttp

__all__ = [
    "ClienteHTTP",
    "desenvolver",
    "ErrorApi",
    "ErrorConexion",
    "ErrorFormato",
    "ErrorHttp",
]
