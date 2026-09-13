import threading


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
        self.ping_use_case = ping_use_case
        self.metrics_provider = metrics_provider
        self.dispositivo_id = dispositivo_id
        self.interval_seconds = interval_seconds
        self.enabled = enabled
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if not self.enabled or self.running:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop_event.is_set():
            self._send_once()
            self._stop_event.wait(self.interval_seconds)

    def _send_once(self) -> None:
        try:
            metrics = self.metrics_provider.sample()
            payload = metrics.to_ping_payload(self.dispositivo_id)
            sent = self.ping_use_case.execute(payload)
            if sent:
                print(
                    f"[TelemetryLoop] Ping enviado (dispositivo {self.dispositivo_id})."
                )
            else:
                last_error = getattr(self.ping_use_case, "get_last_error", lambda: None)()
                print(f"[TelemetryLoop] Falló el ping: {last_error}")
        except Exception as exc:
            # La telemetría es best-effort: nunca debe tumbar el loop ni la app.
            print(f"[TelemetryLoop] Error al enviar telemetría: {exc}")

    def stop(self, timeout: float = 5.0) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
