"""Composición de dependencias del backend y recarga en caliente de configuración.

El grafo de objetos vive en un ``RuntimeState`` inmutable dentro de un
``Runtime``. Los getters leen el snapshot actual (una asignación de referencia,
sin lock); ``Runtime.apply`` construye un grafo nuevo, lo publica en una sola
asignación y solo entonces detiene el anterior. Los transportes retirados se
cierran de forma diferida en el apply siguiente para no cortar envíos en vuelo.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, fields
from pathlib import Path

from fastapi import Depends, Request

from device_enrollment.identity import DeviceIdentity, IdentityStore
from device_enrollment.transport import SignedTransport

from backend.app import config as config_module
from backend.app.config import Settings
from backend.app.config_store import ConfigStore
from backend.app.telemetry import TelemetryLoop
from backend.domain.interfaces.image_detector import IImageDetector
from backend.infrastructure.ai.fallback_detector import FallbackDetector
from backend.infrastructure.ai.yolo_detector import YoloDetector
from backend.infrastructure.system.system_metrics import LinuxSystemMetricsProvider
from backend.use_cases.detect_and_notify import DetectAndNotifyUseCase
from backend.use_cases.finalize_lote import FinalizeLoteUseCase
from backend.use_cases.send_lote_inicio import SendLoteInicioUseCase
from backend.use_cases.send_lote_request import SendLoteRequestUseCase
from backend.use_cases.send_ping_request import SendPingRequestUseCase

logger = logging.getLogger(__name__)

# Campos cuyo cambio obliga a reconstruir identidad + transporte + emisores.
_TRANSPORT_FIELDS = frozenset(
    {"device_api_base_url", "device_auth_audience", "device_identity_dir"}
)
# Campos de inferencia que reconfiguran el detector. Son nombres de ``Settings``
# (el dataclass del backend), que ``settings_from_config`` mapea desde la
# subsección anidada ``AppConfig.stream.inference``; no son campos del esquema.
_INFERENCE_FIELDS = frozenset(
    {
        "inference_enabled",
        "require_hailo",
        "inference_model_path",
        "inference_labels_path",
        "inference_confidence_threshold",
    }
)
# Campos que obligan a reiniciar la telemetría.
_TELEMETRY_FIELDS = frozenset(
    {"ping_interval_seconds", "horno_id", "default_producto_id"}
)
# ``config_revision`` cambia en cada guardado pero no reconfigura el grafo.
_NON_MATERIAL_FIELDS = frozenset({"config_revision"})


class LazyYoloDetector(IImageDetector):
    """Proxy perezoso para no reservar la NPU Hailo durante el arranque.

    La carga real del modelo (y con ella el bloqueo de la NPU) se pospone hasta
    la primera detección. Si esa carga falla, se usa un ``FallbackDetector`` para
    no tumbar el servicio. ``reload`` reconfigura el proxy en caliente sin cargar
    el modelo hasta la próxima inferencia.
    """

    def __init__(
        self,
        model_path: str | None = None,
        names_path: str | None = None,
        confidence_threshold: float = 0.60,
    ):
        """Inicializa el proxy sin construir el detector real."""
        self._detector = None
        self._model_path = model_path
        self._names_path = names_path
        self._confidence_threshold = confidence_threshold
        # Evita que dos requests concurrentes (el endpoint corre en el
        # threadpool) construyan el detector dos veces y reserven la NPU de más.
        self._lock = threading.Lock()

    def _build_detector(self):
        """Construye ``YoloDetector`` con la config vigente.

        Si no hay overrides (modelo/etiquetas en None y umbral por defecto) se
        llama sin argumentos, preservando el constructor histórico que resuelve
        sus propios defaults.
        """
        if (
            self._model_path is None
            and self._names_path is None
            and self._confidence_threshold == 0.60
        ):
            return YoloDetector()
        return YoloDetector(
            self._model_path,
            self._names_path,
            self._confidence_threshold,
        )

    @property
    def detector(self):
        """Devuelve el detector real, construyéndolo en la primera llamada.

        Usa double-checked locking: el lock solo se toma si aún no está resuelto.
        """
        if self._detector is None:
            with self._lock:
                if self._detector is None:
                    try:
                        self._detector = self._build_detector()
                        logger.info("Detector YOLO NPU/ONNX cargado perezosamente con éxito.")
                    except Exception as e:
                        logger.error("Error al cargar YOLO en inicialización diferida: %s", e)
                        self._detector = FallbackDetector(e)
        return self._detector

    def reload(
        self,
        model_path: str | None,
        names_path: str | None,
        confidence_threshold: float,
    ) -> None:
        """Reconfigura el proxy: suelta el detector viejo y aplica la nueva config.

        Bajo el lock existente (no se agregan locks). La construcción del nuevo
        detector sigue siendo perezosa; si el viejo estaba cargado se libera
        primero (``release_hailo`` si existe).
        """
        with self._lock:
            old = self._detector
            if old is not None and hasattr(old, "release_hailo"):
                try:
                    old.release_hailo()
                except Exception:
                    logger.warning("No se pudo liberar el detector previo.", exc_info=True)
            self._detector = None
            self._model_path = model_path
            self._names_path = names_path
            self._confidence_threshold = confidence_threshold

    def detect(self, image_path: str):
        """Delega en el detector subyacente la inferencia sobre una imagen."""
        return self.detector.detect(image_path)

    def detect_frame(self, frame):
        """Delega en el detector subyacente la inferencia sobre un frame."""
        return self.detector.detect_frame(frame)

    def get_class_names(self):
        """Devuelve los nombres de clase del modelo subyacente."""
        return self.detector.get_class_names()

    def release_hailo(self):
        """Libera la NPU Hailo solo si el detector ya se había cargado."""
        # No se fuerza la carga perezosa solo para liberar la NPU.
        if self._detector is not None:
            self._detector.release_hailo()

    @property
    def loaded(self) -> bool:
        """True si la carga perezosa ya se resolvió (modelo real o fallback).

        No dispara la carga del modelo.
        """
        return self._detector is not None

    @property
    def model_path(self) -> str | None:
        """Ruta del modelo cargado, o None si todavía no se cargó.

        No dispara la carga perezosa (a diferencia de la version anterior).
        """
        if self._detector is None:
            return None
        return getattr(self._detector, "model_path", None)

    @property
    def error(self) -> str:
        """Motivo de degradación si la carga derivó en FallbackDetector."""
        if self._detector is None:
            return ""
        return str(getattr(self._detector, "error", "") or "")


@dataclass(frozen=True)
class RuntimeState:
    """Snapshot inmutable del grafo de dependencias del backend."""

    settings: Settings
    identity: DeviceIdentity | None
    transport: SignedTransport
    detect_use_case: DetectAndNotifyUseCase
    send_lote: SendLoteRequestUseCase
    finalize_lote: FinalizeLoteUseCase
    send_lote_inicio: SendLoteInicioUseCase
    send_ping: SendPingRequestUseCase
    telemetry: TelemetryLoop


def _changed_fields(old: Settings, new: Settings) -> list[str]:
    """Nombres de campos materiales que difieren entre dos ``Settings``."""
    return sorted(
        field.name
        for field in fields(Settings)
        if field.name not in _NON_MATERIAL_FIELDS
        and getattr(old, field.name) != getattr(new, field.name)
    )


class Runtime:
    """Contenedor del grafo de dependencias con recarga en caliente."""

    def __init__(self, store: ConfigStore | None = None):
        self._store = store if store is not None else ConfigStore()
        self._lock = threading.Lock()
        self._retired: list[SignedTransport] = []
        # Serializa las transiciones de telemetría y el flag de lifecycle sin
        # mantener el lock principal durante el ``stop()`` (join de 5 s).
        self._telemetry_lock = threading.Lock()
        # Transiciones pendientes de telemetría (viejo -> nuevo) acumuladas para
        # no perder ninguna si dos applies se solapan antes del drain.
        self._pending_telemetry: list[tuple[TelemetryLoop, TelemetryLoop]] = []
        self._telemetry_active = False
        self.detector = LazyYoloDetector()
        self._system_metrics = LinuxSystemMetricsProvider()
        # Primer snapshot: ``get`` carga el archivo una sola vez.
        initial_settings = self._store.get()
        self._state = self._build_state(initial_settings)
        # El detector nace sin cargar, pero con la config de inferencia inicial.
        self._reload_detector(initial_settings)

    # -- introspección ---------------------------------------------------------
    @property
    def store(self) -> ConfigStore:
        return self._store

    @property
    def state(self) -> RuntimeState:
        return self._state

    @property
    def telemetry_active(self) -> bool:
        return self._telemetry_active

    # -- construcción ----------------------------------------------------------
    def _build_state(self, settings: Settings) -> RuntimeState:
        return self._build_candidate(settings, base=None)[0]

    def _build_candidate(
        self, settings: Settings, base: RuntimeState | None
    ) -> tuple[RuntimeState, bool, bool, bool]:
        """Construye un ``RuntimeState`` reutilizando lo que no cambió.

        Devuelve ``(state, rebuild_transport, restart_telemetry, detector_changed)``.
        """
        if base is None:
            changed: set[str] = set(_INFERENCE_FIELDS)
            rebuild_transport = True
            restart_telemetry = True
            enrollment_changed = False
        else:
            changed = set(_changed_fields(base.settings, settings))
            rebuild_transport = bool(changed & _TRANSPORT_FIELDS)
            restart_telemetry = bool(changed & _TELEMETRY_FIELDS) or rebuild_transport
            enrollment_changed = False

        identity_store = IdentityStore(Path(settings.device_identity_dir))
        identity = identity_store.try_load_enrolled()

        if base is not None:
            # Detección acotada del enrolado en vivo: fingerprint + dispositivo.
            enrollment_changed = _identity_differs(base.identity, identity)
            restart_telemetry = restart_telemetry or enrollment_changed

        if rebuild_transport or base is None:
            transport = SignedTransport(
                lambda: self._state.identity,
                settings.device_api_base_url,
                settings.device_auth_audience,
            )
            send_lote = SendLoteRequestUseCase(transport, settings.device_api_base_url)
            finalize_lote = FinalizeLoteUseCase(send_lote)
            send_ping = SendPingRequestUseCase(transport, settings.device_api_base_url)
            send_lote_inicio = SendLoteInicioUseCase(
                transport, settings.device_api_base_url
            )
        else:
            transport = base.transport
            send_lote = base.send_lote
            finalize_lote = base.finalize_lote
            send_ping = base.send_ping
            send_lote_inicio = base.send_lote_inicio

        if restart_telemetry or base is None:
            telemetry = TelemetryLoop(
                send_ping,
                self._system_metrics,
                identity.dispositivo_id if identity else "",
                settings.ping_interval_seconds,
                enabled=_telemetry_enabled(identity, settings),
            )
        else:
            telemetry = base.telemetry

        detect_use_case = (
            base.detect_use_case if base is not None else DetectAndNotifyUseCase(self.detector)
        )

        state = RuntimeState(
            settings=settings,
            identity=identity,
            transport=transport,
            detect_use_case=detect_use_case,
            send_lote=send_lote,
            finalize_lote=finalize_lote,
            send_lote_inicio=send_lote_inicio,
            send_ping=send_ping,
            telemetry=telemetry,
        )
        detector_changed = bool(changed & _INFERENCE_FIELDS) or base is None
        return state, rebuild_transport, restart_telemetry, detector_changed

    # -- recarga ---------------------------------------------------------------
    def refresh(self, force: bool = False) -> tuple[bool, list[str]]:
        """Recarga el store y aplica si cambió. Devuelve ``(changed, applied)``.

        ``refresh_with_changed`` entrega el flag de cambio de forma atómica, así
        dos refrescos concurrentes no pueden pisarse el flag y dejar el runtime
        sin aplicar. Con ``force=True`` se llama a ``apply`` aunque el
        ``AppConfig`` no haya cambiado, para adoptar una identidad recién
        enrolada (ver ``_build_candidate``).
        """
        settings, config_changed = self._store.refresh_with_changed(force=force)
        applied = self.apply(settings) if (config_changed or force) else []
        return (config_changed or bool(applied)), applied

    def apply(self, new_settings: Settings) -> list[str]:
        """Aplica un snapshot nuevo. Devuelve los campos materiales modificados.

        La construcción es tentativa fuera del lock; bajo el lock se revisa que
        nadie haya publicado otro estado mientras tanto y, si pasó, se
        reconstruye contra el estado vigente y se cierra el candidato descartado.
        El swap es una sola asignación. La telemetría se detiene/arranca fuera
        del lock principal (``_drain_telemetry``), preservando stop→join→start.
        """
        base = self._state
        candidate, rebuild_transport, restart_telemetry, detector_changed = (
            self._build_candidate(new_settings, base)
        )
        identity_changed = _identity_differs(base.identity, candidate.identity)
        need_drain = False

        with self._lock:
            if self._state is not base:
                discarded = candidate
                base = self._state
                (
                    candidate,
                    rebuild_transport,
                    restart_telemetry,
                    detector_changed,
                ) = self._build_candidate(new_settings, base)
                identity_changed = _identity_differs(base.identity, candidate.identity)
                self._close_discarded(discarded, current=base, kept=candidate)

            changed = _changed_fields(base.settings, new_settings)
            if new_settings == base.settings and not identity_changed:
                return []

            self._close_retired()
            old_transport = base.transport
            old_telemetry = base.telemetry

            self._state = candidate

            if rebuild_transport and old_transport is not candidate.transport:
                self._retired.append(old_transport)

            if detector_changed:
                self._reload_detector(new_settings)

            if restart_telemetry and old_telemetry is not candidate.telemetry:
                self._pending_telemetry.append((old_telemetry, candidate.telemetry))
                need_drain = True

        if need_drain:
            # ``stop()`` (join de hasta 5 s) ocurre fuera del lock principal, así
            # que los getters del snapshot no se bloquean.
            self._drain_telemetry()

        return changed + (["identity"] if identity_changed else [])

    def reload(self) -> tuple[bool, list[str]]:
        """Fuerza una recarga desde disco. Devuelve ``(changed, applied)``."""
        return self.refresh(force=True)

    # -- telemetría ------------------------------------------------------------
    def start_telemetry(self) -> None:
        """Arranca la telemetría del snapshot vigente (idempotente)."""
        with self._telemetry_lock:
            self._telemetry_active = True
            self._state.telemetry.start()

    def stop_telemetry(self) -> None:
        """Detiene la telemetría y marca que el lifespan ya no la controla."""
        with self._telemetry_lock:
            self._telemetry_active = False
            self._state.telemetry.stop()

    def _drain_telemetry(self) -> None:
        """Aplica las transiciones pendientes de telemetría fuera del lock principal.

        Se serializa con ``_telemetry_lock`` (no con ``_lock``) para que el
        ``join`` de ``stop`` no bloquee a los getters. Detiene todos los loops de
        las transiciones acumuladas y arranca solo el vigente (el del estado
        publicado más reciente), de modo que nunca quedan dos loops activos ni
        loops intermedios huérfanos.
        """
        with self._telemetry_lock:
            while True:
                with self._lock:
                    pending, self._pending_telemetry = self._pending_telemetry, []
                    active = self._telemetry_active
                    current = self._state.telemetry
                if not pending:
                    break
                for old, new in pending:
                    if old is not None and old is not new:
                        old.stop()
                # Arranca solo el loop vigente (el de la última transición ya
                # aplicada); los intermedios quedaron detenidos.
                if active:
                    current.start()

    def shutdown(self) -> None:
        """Cierra los transportes retirados pendientes (llamado en el lifespan)."""
        with self._lock:
            retired, self._retired = self._retired, []
        for transport in retired:
            try:
                transport.close()
            except Exception:
                logger.warning(
                    "No se pudo cerrar un transporte retirado.", exc_info=True
                )

    # -- helpers ---------------------------------------------------------------
    def _reload_detector(self, settings: Settings) -> None:
        self.detector.reload(
            _resolve_optional_path(settings.inference_model_path),
            _resolve_optional_path(settings.inference_labels_path),
            settings.inference_confidence_threshold,
        )

    def _close_discarded(
        self,
        discarded: RuntimeState,
        *,
        current: RuntimeState,
        kept: RuntimeState,
    ) -> None:
        """Cierra el transporte de un candidato construido y descartado.

        Evita fugas de ``requests.Session`` cuando otro hilo publicó primero y el
        candidato tentativo nunca llega a ser el estado vigente.
        """
        transport = discarded.transport
        if (
            transport is not None
            and transport is not current.transport
            and transport is not kept.transport
        ):
            try:
                transport.close()
            except Exception:
                logger.warning(
                    "No se pudo cerrar el transporte de un candidato descartado.",
                    exc_info=True,
                )

    def _close_retired(self) -> None:
        retired, self._retired = self._retired, []
        for transport in retired:
            try:
                transport.close()
            except Exception:
                logger.warning("No se pudo cerrar un transporte retirado.", exc_info=True)


def _identity_differs(
    old: DeviceIdentity | None, new: DeviceIdentity | None
) -> bool:
    """True si la identidad enrolada cambió (fingerprint o dispositivo).

    Es la detección acotada del enrolado en vivo: permite que un
    ``refresh(force=True)`` (``POST /api/config/reload``) reconstruya el grafo
    aunque el ``AppConfig`` no haya cambiado.
    """
    if (old is None) != (new is None):
        return True
    if old is None or new is None:
        return False
    return (
        old.fingerprint != new.fingerprint
        or old.dispositivo_id != new.dispositivo_id
    )


def _telemetry_enabled(identity: DeviceIdentity | None, settings: Settings) -> bool:
    return bool(
        identity
        and identity.dispositivo_id
        and settings.device_api_base_url
        and settings.device_auth_audience
    )


def _resolve_optional_path(path: str | None) -> str | None:
    """Resuelve rutas relativas contra la raíz del repositorio."""
    if not path:
        return path
    from smartcheck_config import resolve_project_path

    return resolve_project_path(path)


# ---------------------------------------------------------------------------
# Singleton del runtime y getters usados como dependencias FastAPI
# ---------------------------------------------------------------------------

_runtime = Runtime()


def get_detector():
    """Dependencia FastAPI: proxy perezoso del detector YOLO."""
    return _runtime.detector


def get_detect_use_case():
    """Dependencia FastAPI: caso de uso de detección y notificación."""
    return _runtime.state.detect_use_case


def get_finalize_lote_use_case():
    """Dependencia FastAPI: caso de uso de finalización de lote."""
    return _runtime.state.finalize_lote


def get_send_lote_inicio_use_case():
    """Dependencia FastAPI: caso de uso de inicio de lote."""
    return _runtime.state.send_lote_inicio


def get_telemetry_loop():
    """Dependencia FastAPI: hilo de telemetría periódica."""
    return _runtime.state.telemetry


def get_settings() -> Settings:
    """Dependencia FastAPI: snapshot actual de configuración."""
    return _runtime.state.settings


def get_runtime() -> Runtime:
    """Dependencia FastAPI: contenedor del runtime (recarga incluida)."""
    return _runtime


def refresh_runtime(
    request: Request,
    runtime: Runtime = Depends(get_runtime),
) -> None:
    """Dependencia de app: refresca la configuración una vez por request.

    Deja en ``request.state.config_changed`` si el archivo cambió en esta
    petición, de modo que quien publique también pueda reportarlo. Resuelve el
    runtime vía ``Depends`` para que los overrides de tests se propaguen.
    """
    changed, applied = runtime.refresh()
    request.state.config_changed = changed
    request.state.config_applied = applied


def start_telemetry() -> None:
    """Arranca la telemetría (usado por el lifespan)."""
    _runtime.start_telemetry()


def stop_telemetry() -> None:
    """Detiene la telemetría (usado por el lifespan)."""
    _runtime.stop_telemetry()


def shutdown_runtime() -> None:
    """Cierra transportes retirados pendientes (usado por el lifespan)."""
    _runtime.shutdown()


# El resto del backend consume ``config.get_settings`` (y los tests lo
# sobreescriben por nombre); apuntamos ese provider al snapshot del runtime.
config_module.set_settings_provider(get_settings)
