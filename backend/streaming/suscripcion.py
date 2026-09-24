"""Suscripción a los eventos de pista del streaming, sin bloquear la inferencia.

El lazo de inferencia es el productor: ofrece cada evento a un buzón acotado con
`put_nowait`. Si el buzón está lleno, el evento se descarta y se suma un contador;
el productor **nunca** se bloquea. El consumidor (p. ej. el `LoteService`) drena el
buzón desde su propio hilo con `recibir()`.

`RegistroSuscriptores` es copy-on-write: el lazo de inferencia lee `instantanea()`
en cada frame sin tomar lock, y agregar/quitar reemplaza la tupla entera.
"""

from __future__ import annotations

import queue
import threading
from dataclasses import dataclass

from backend.streaming.estabilizador import EventoPista


@dataclass(frozen=True, slots=True)
class EventoObservado:
    """Evento de pista con el contexto del modelo y del frame que lo produjo."""

    evento: EventoPista
    modelo_id: str | None
    numero_frame: int


class ColaEventos:
    """Buzón acotado entre el lazo de inferencia y un consumidor.

    `ofrecer()` no bloquea nunca: si la cola está llena descarta y suma en
    `descartados`. `recibir()` es para el hilo consumidor y puede esperar.
    """

    def __init__(self, maxsize: int = 512) -> None:
        self._cola: queue.Queue[EventoObservado] = queue.Queue(maxsize=max(1, maxsize))
        self._descartados = 0

    @property
    def descartados(self) -> int:
        """Eventos descartados por cola llena. El productor nunca se bloquea."""
        return self._descartados

    def ofrecer(self, observado: EventoObservado) -> None:
        """Encola sin bloquear; si no hay lugar, descarta el evento."""
        try:
            self._cola.put_nowait(observado)
        except queue.Full:
            self._descartados += 1

    def recibir(self, timeout: float) -> list[EventoObservado]:
        """Drena lo disponible, esperando hasta `timeout` si no hay nada.

        Devuelve los eventos acumulados; puede ser vacío si venció el timeout
        sin novedades.
        """
        eventos: list[EventoObservado] = []
        try:
            eventos.append(self._cola.get(timeout=timeout))
        except queue.Empty:
            return eventos
        while True:
            try:
                eventos.append(self._cola.get_nowait())
            except queue.Empty:
                return eventos


class RegistroSuscriptores:
    """Conjunto de colas suscriptas, copy-on-write para leer sin lock.

    El lazo de inferencia llama a `instantanea()` en cada frame y obtiene una
    tupla inmutable que no cambia bajo sus pies. Los escritores (arranque y
    parada de servicios) se serializan con un lock; la lectura no lo toma.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._instantanea: tuple[ColaEventos, ...] = ()

    def agregar(self, cola: ColaEventos) -> None:
        with self._lock:
            if cola in self._instantanea:
                return
            self._instantanea = (*self._instantanea, cola)

    def quitar(self, cola: ColaEventos) -> None:
        with self._lock:
            self._instantanea = tuple(c for c in self._instantanea if c is not cola)

    def instantanea(self) -> tuple[ColaEventos, ...]:
        """Tupla actual de suscriptores. Lectura sin lock (asignación atómica)."""
        return self._instantanea
