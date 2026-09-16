import logging
import threading

logger = logging.getLogger(__name__)


class TelemetryLoop:
    """Hilo daemon que envía telemetría periódica al servidor central."""

    def __init__(
        self,
        ping_use_case,
        metrics_provider,
        dispositivo_id: str,
        interval_seconds: float,
        enabled: bool = True,
    ):
        """Guarda las dependencias y prepara el hilo detenido."""
        self.ping_use_case = ping_use_case
        self.metrics_provider = metrics_provider
        self.dispositivo_id = dispositivo_id
        self.interval_seconds = interval_seconds
        self.enabled = enabled
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        # Serializa ``start()``: sin él, dos llamadas concurrentes podrían ver
        # ``running`` en False y arrancar dos hilos para el mismo loop.
        self._start_lock = threading.Lock()

    @property
    def running(self) -> bool:
        """True si el hilo de telemetría está vivo."""
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        """Arranca el hilo daemon si está habilitado y no corre ya.

        Es idempotente y thread-safe: el lock interno serializa llamadas
        concurrentes de modo que, aun invocándolo desde varios hilos a la vez,
        nunca se arrancan dos hilos para el mismo loop.
        """
        with self._start_lock:
            if not self.enabled or self.running:
                return
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()

    def _run(self) -> None:
        """Bucle principal: envía un ping y espera el intervalo o el stop.

        Se usa ``Event.wait`` (en vez de ``sleep``) para que ``stop`` corte la
        espera de inmediato.
        """
        while not self._stop_event.is_set():
            self._send_once()
            self._stop_event.wait(self.interval_seconds)

    def _send_once(self) -> None:
        """Muestrea métricas del sistema y envía un ping al servidor central."""
        try:
            metrics = self.metrics_provider.sample()
            payload = metrics.to_ping_payload(self.dispositivo_id)
            result = self.ping_use_case.execute(payload)
            if result.ok:
                logger.info("Ping enviado (dispositivo %s).", self.dispositivo_id)
            else:
                logger.warning("Falló el ping: %s", result.error)
        except Exception as exc:
            # La telemetría es best-effort: nunca debe tumbar el loop ni la app.
            logger.warning("Error al enviar telemetría: %s", exc)

    def stop(self, timeout: float = 5.0) -> None:
        """Señala el stop y espera a que el hilo termine (con timeout)."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
