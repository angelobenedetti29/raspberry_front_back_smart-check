"""Contrato de los servicios de backend que arranca run.py."""

from typing import Protocol


class Service(Protocol):
    """Servicio de backend con ciclo de vida start/stop.

    start() no debe bloquear: el servicio gestiona su propio hilo interno.
    stop() debe ser bloqueante: señala el fin y espera a que termine.
    """

    name: str

    def start(self) -> None: ...

    def stop(self) -> None: ...
