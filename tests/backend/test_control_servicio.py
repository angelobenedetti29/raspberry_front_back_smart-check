"""Tests del gating y ack del ControlService (sin hilos ni PLC real).

Cubren el contrato nuevo: en SUPERVISION la propuesta espera autorización,
el ack al backend es best-effort y un fallo de ack no vuelve a escribir el
PLC. Se usan fakes en memoria y un directorio temporal para el audit log.
"""

from __future__ import annotations

from dataclasses import replace

from backend.config import load_config
from backend.control import ControlService
from backend.control.tipos import (
    ErrorControl,
    LecturaHorno,
    LimitesAplicacion,
    ModoControl,
    SetpointPropuesto,
)

_LIMITES = LimitesAplicacion(
    delta_temp_max_c=10.0,
    delta_vel_max=0.05,
    temp_min=0.0,
    temp_max=500.0,
    vel_min=0.0,
    vel_max=5.0,
)


class _LectorFalso:
    def leer(self) -> None:
        return None


class _LectorConLectura:
    def __init__(self, lectura: LecturaHorno) -> None:
        self._lectura = lectura

    def leer(self) -> LecturaHorno:
        return self._lectura


def _lectura(temp: float | None, vel: float | None) -> LecturaHorno:
    return LecturaHorno(
        temp_horno_1=temp,
        temp_comb_1=None,
        humedad_horno=None,
        temp_ambiente=None,
        humedad_ambiente=None,
        velocidad_cinta=vel,
        temp_objetivo=None,
        vel_objetivo=None,
        confianza_deteccion=None,
    )


class _AplicadorFalso:
    def __init__(self) -> None:
        self.aplicados: list[tuple[float, float]] = []

    def aplicar(self, temp: float, vel: float) -> None:
        self.aplicados.append((temp, vel))


class _ClienteFalso:
    def __init__(
        self,
        propuesta: SetpointPropuesto | None = None,
        *,
        error_ack: ErrorControl | None = None,
        error_aprobar: ErrorControl | None = None,
    ) -> None:
        self.propuesta = propuesta
        self.error_ack = error_ack
        self.error_aprobar = error_aprobar
        self.acks: list[dict] = []
        self.aprobaciones = 0
        self.aprobaciones_body: list[dict | None] = []

    def obtener_propuesta(self, secret: str, lote_id: str, endpoint: str):
        return self.propuesta

    def ack_consigna(self, secret: str, lote_id: str, endpoint: str, body: dict) -> None:
        if self.error_ack is not None:
            raise self.error_ack
        self.acks.append(body)

    def aprobar_consigna(
        self, secret: str, lote_id: str, endpoint: str, body: dict | None = None
    ) -> None:
        if self.error_aprobar is not None:
            raise self.error_aprobar
        self.aprobaciones += 1
        self.aprobaciones_body.append(body)


def _servicio(tmp_path, cliente: _ClienteFalso) -> tuple[ControlService, _AplicadorFalso]:
    config = load_config()
    config = replace(config, paths=replace(config.paths, data_dir=tmp_path))
    aplicador = _AplicadorFalso()
    servicio = ControlService(
        config,
        lector=_LectorFalso(),
        aplicador=aplicador,
        tipo_provider=lambda: None,
        secret_provider=lambda: None,
    )
    servicio._cliente = cliente
    servicio._secret = "secret"
    servicio._lote_id = "lote-1"
    servicio._limites = _LIMITES
    return servicio, aplicador


def _propuesta(**cambios: object) -> SetpointPropuesto:
    base: dict = {
        "temp_horno_1": 180.0,
        "velocidad_cinta": 1.2,
        "aplicado_por": "MPC",
        "ventana": None,
        "decision_id": "dec-1",
    }
    base.update(cambios)
    return SetpointPropuesto(**base)


def test_propuesta_no_autorizada_se_bufferea_y_no_se_aplica(tmp_path) -> None:
    propuesta = _propuesta(autorizada=False)
    servicio, aplicador = _servicio(tmp_path, _ClienteFalso(propuesta))
    servicio._modo = ModoControl.SUPERVISION

    servicio._buscar_y_aplicar_propuesta("secret", "lote-1")

    assert aplicador.aplicados == []
    assert servicio.estado()["propuesta_pendiente"] is propuesta


def test_propuesta_autorizada_se_aplica_y_ackea(tmp_path) -> None:
    propuesta = _propuesta(autorizada=True)
    cliente = _ClienteFalso(propuesta)
    servicio, aplicador = _servicio(tmp_path, cliente)
    servicio._modo = ModoControl.AUTOMATICO

    servicio._buscar_y_aplicar_propuesta("secret", "lote-1")

    assert aplicador.aplicados == [(180.0, 1.2)]
    assert cliente.acks == [propuesta.a_payload_ack()]
    assert servicio.estado()["propuesta_pendiente"] is None


def test_ack_reporta_el_setpoint_efectivo_despues_del_clamp(tmp_path) -> None:
    """El backend debe auditar lo que realmente quedó en el PLC."""
    propuesta = _propuesta(autorizada=True)
    cliente = _ClienteFalso(propuesta)
    servicio, aplicador = _servicio(tmp_path, cliente)
    servicio._modo = ModoControl.AUTOMATICO
    servicio._ultima_lectura = LecturaHorno(
        temp_horno_1=100.0,
        temp_comb_1=None,
        humedad_horno=None,
        temp_ambiente=None,
        humedad_ambiente=None,
        velocidad_cinta=1.0,
        temp_objetivo=100.0,
        vel_objetivo=1.0,
        confianza_deteccion=None,
    )

    servicio._buscar_y_aplicar_propuesta("secret", "lote-1")

    assert aplicador.aplicados == [(110.0, 1.05)]
    assert cliente.acks == [
        {
            "decision_id": "dec-1",
            "temp_aplicado": 110.0,
            "vel_aplicado": 1.05,
        }
    ]


def test_fallo_de_ack_no_reescribe_el_plc_y_queda_pendiente(tmp_path) -> None:
    propuesta = _propuesta(autorizada=True)
    cliente = _ClienteFalso(propuesta, error_ack=ErrorControl("sin red"))
    servicio, aplicador = _servicio(tmp_path, cliente)
    servicio._modo = ModoControl.AUTOMATICO

    servicio._buscar_y_aplicar_propuesta("secret", "lote-1")
    # El backend reintenta la misma decisión: el dedup evita otro write.
    servicio._buscar_y_aplicar_propuesta("secret", "lote-1")

    assert aplicador.aplicados == [(180.0, 1.2)]
    assert servicio._acks_pendientes == {"dec-1": (180.0, 1.2)}

    # Recuperada la red, el ack pendiente se reenvía y se limpia.
    cliente.error_ack = None
    servicio._reintentar_acks("secret", "lote-1")

    assert cliente.acks == [propuesta.a_payload_ack()]
    assert servicio._acks_pendientes == {}


def test_aprobacion_llama_al_backend_y_despues_aplica(tmp_path) -> None:
    propuesta = _propuesta(autorizada=False)
    cliente = _ClienteFalso(propuesta)
    servicio, aplicador = _servicio(tmp_path, cliente)
    servicio._modo = ModoControl.SUPERVISION
    servicio._buscar_y_aplicar_propuesta("secret", "lote-1")

    assert servicio.aplicar_pendiente() is True

    assert cliente.aprobaciones == 1
    assert cliente.aprobaciones_body == [{"decision_id": "dec-1"}]
    assert aplicador.aplicados == [(180.0, 1.2)]
    assert servicio.estado()["propuesta_pendiente"] is None


def test_si_falla_la_aprobacion_la_propuesta_queda_pendiente(tmp_path) -> None:
    propuesta = _propuesta(autorizada=False)
    cliente = _ClienteFalso(
        propuesta, error_aprobar=ErrorControl("backend caído")
    )
    servicio, aplicador = _servicio(tmp_path, cliente)
    servicio._modo = ModoControl.SUPERVISION
    servicio._buscar_y_aplicar_propuesta("secret", "lote-1")

    assert servicio.aplicar_pendiente() is False

    assert cliente.aprobaciones == 0
    assert aplicador.aplicados == []
    assert servicio.estado()["propuesta_pendiente"] is propuesta


def test_iniciado_manualmente_conserva_el_modo_del_lote(tmp_path) -> None:
    servicio, _ = _servicio(tmp_path, _ClienteFalso())

    servicio.on_lote_abierto(
        "secret",
        "lote-1",
        ModoControl.AUTOMATICO,
        iniciado_manualmente=True,
    )

    assert servicio.estado()["modo"] == "AUTOMATICO"


def test_estado_inicial_detecta_horno_en_marcha(tmp_path) -> None:
    servicio, _ = _servicio(tmp_path, _ClienteFalso())
    servicio._lector = _LectorConLectura(_lectura(180.0, 1.2))

    estado = servicio.estado_inicial_lote()

    assert estado is not None
    assert estado["temp_horno_1"] == 180.0
    assert estado["velocidad_cinta"] == 1.2
    assert estado["temp_objetivo"] == 180.0
    assert estado["vel_objetivo"] == 1.2


def test_estado_inicial_no_dispara_con_horno_frio(tmp_path) -> None:
    servicio, _ = _servicio(tmp_path, _ClienteFalso())
    servicio._lector = _LectorConLectura(_lectura(20.0, 0.0))

    assert servicio.estado_inicial_lote() is None


def test_estado_inicial_respeta_los_umbrales_configurados(tmp_path) -> None:
    """Temp 40 °C + vel 0.005 no arrancan; 40 °C + 0.02 o 60 °C + 0 sí."""
    servicio, _ = _servicio(tmp_path, _ClienteFalso())
    servicio._ctrl_cfg = replace(
        servicio._ctrl_cfg,
        umbral_marcha_temp_c=50.0,
        umbral_marcha_velocidad=0.01,
    )

    servicio._lector = _LectorConLectura(_lectura(40.0, 0.005))
    assert servicio.estado_inicial_lote() is None

    servicio._lector = _LectorConLectura(_lectura(40.0, 0.02))
    assert servicio.estado_inicial_lote() is not None

    servicio._lector = _LectorConLectura(_lectura(60.0, 0.0))
    assert servicio.estado_inicial_lote() is not None
