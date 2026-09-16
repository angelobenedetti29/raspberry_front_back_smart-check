from datetime import datetime, timedelta, timezone

from backend.domain.entities.sensor_readings import SensorReadings
from backend.use_cases.build_lote_payload import (
    DEFAULT_PRODUCT_ID,
    TOAST_WEIGHT_KG,
    SensorAccumulator,
    SensorAverages,
    build_lote_payload,
)

INICIO = datetime(2024, 1, 1, 8, 0, 0)
FIN = datetime(2024, 1, 1, 9, 0, 0)


def _samples():
    return [
        SensorReadings(220.0, 315.0, 218.0, 312.0, 1.10),
        SensorReadings(221.0, 317.0, 220.0, 314.0, 1.20),
    ]


def _averages(samples) -> SensorAverages:
    accumulator = SensorAccumulator()
    for sample in samples:
        accumulator.add(sample)
    return accumulator.averages()


def _parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def test_counts_and_total():
    seen = {1: "ok", 2: "ok", 3: "burnt", 4: "ok", 5: "burnt"}
    payload = build_lote_payload(INICIO, FIN, seen, _averages(_samples()))

    assert payload["quemados"] == 2
    assert payload["correctos"] + payload["crudas"] == 3
    assert payload["totalUnidades"] == 5
    assert payload["totalUnidades"] == (
        payload["correctos"] + payload["quemados"] + payload["crudas"]
    )


def test_weights_use_0025_kg_and_two_decimals():
    seen = {1: "ok", 2: "ok", 3: "burnt"}
    payload = build_lote_payload(INICIO, FIN, seen, _averages(_samples()))

    assert TOAST_WEIGHT_KG == 0.025
    assert payload["correctosKg"] == round(payload["correctos"] * 0.025, 2)
    assert payload["quemadosKg"] == round(payload["quemados"] * 0.025, 2)
    assert payload["crudosKg"] == round(payload["crudas"] * 0.025, 2)
    assert payload["quemadosKg"] == 0.03
    for key in ("correctosKg", "quemadosKg", "crudosKg"):
        assert round(payload[key], 2) == payload[key]


def _turno_at(hour: int) -> str:
    seen = {1: "burnt"}  # un solo burnt: sin correctos
    inicio = datetime(2024, 1, 1, hour, 0, 0)
    payload = build_lote_payload(inicio, FIN, seen, _averages([]))
    return payload["turno"]


def test_turno_boundaries():
    assert _turno_at(5) == "noche"
    assert _turno_at(6) == "mañana"
    assert _turno_at(13) == "mañana"
    assert _turno_at(14) == "tarde"
    assert _turno_at(18) == "tarde"
    assert _turno_at(19) == "noche"


def test_sensor_averages_and_iso_dates():
    seen = {1: "ok"}
    payload = build_lote_payload(INICIO, FIN, seen, _averages(_samples()))

    assert payload["tempHorno1"] == 220.5
    assert payload["tempCombHorno1"] == 316.0
    assert payload["tempHorno2"] == 219.0
    assert payload["tempCombHorno2"] == 313.0
    assert payload["velocidadCinta"] == 1.15
    # El naive se interpreta como hora local y se serializa en UTC; el instante
    # debe conservarse sea cual sea la zona del equipo que corre el test.
    assert _parse_utc(payload["inicioAt"]) == INICIO.astimezone(timezone.utc)
    assert _parse_utc(payload["finAt"]) == FIN.astimezone(timezone.utc)
    assert payload["productoId"] == DEFAULT_PRODUCT_ID


def test_aware_datetimes_are_converted_to_utc():
    seen = {1: "ok"}
    offset = timezone(timedelta(hours=-3))
    inicio = datetime(2024, 1, 1, 8, 0, 0, tzinfo=offset)
    fin = datetime(2024, 1, 1, 9, 0, 0, tzinfo=offset)
    payload = build_lote_payload(inicio, fin, seen, _averages([]))

    assert payload["inicioAt"] == "2024-01-01T11:00:00Z"
    assert payload["finAt"] == "2024-01-01T12:00:00Z"


def test_sensor_fallback_defaults_without_samples():
    seen = {1: "ok"}
    payload = build_lote_payload(INICIO, FIN, seen, _averages([]))

    assert payload["tempHorno1"] == 220.0
    assert payload["tempCombHorno1"] == 315.0
    assert payload["tempHorno2"] == 218.0
    assert payload["tempCombHorno2"] == 312.0
    assert payload["velocidadCinta"] == 1.10


def test_crudas_are_always_zero():
    seen = {i: "ok" for i in range(15)}
    payload = build_lote_payload(INICIO, FIN, seen, _averages(_samples()))
    assert payload["crudas"] == 0
    assert payload["crudosKg"] == 0.0
    assert payload["correctos"] == 15


def test_no_crudas_when_no_correctos():
    seen = {1: "burnt", 2: "burnt"}
    payload = build_lote_payload(INICIO, FIN, seen, _averages([]))
    assert payload["correctos"] == 0
    assert payload["crudas"] == 0


def test_accumulator_averages_cover_the_whole_lote_not_the_tail():
    # Regresión: con el deque(maxlen=600) un lote largo de 5000 muestras
    # promediaba solo las últimas 600. El acumulador debe promediar el lote
    # completo (124.0), no la cola (300.0).
    accumulator = SensorAccumulator()
    for _ in range(4400):
        accumulator.add(SensorReadings(100.0, 100.0, 100.0, 100.0, 1.0))
    for _ in range(600):
        accumulator.add(SensorReadings(300.0, 300.0, 300.0, 300.0, 3.0))

    averages = accumulator.averages()

    assert accumulator.count == 5000
    assert averages.tempHorno1 == round((4400 * 100.0 + 600 * 300.0) / 5000, 2)
    assert averages.tempHorno1 == 124.0
    assert averages.tempHorno1 != 300.0


def test_accumulator_state_is_constant_in_memory():
    accumulator = SensorAccumulator()
    for i in range(5000):
        accumulator.add(SensorReadings(100.0 + i, 315.0, 218.0, 312.0, 1.0))

    assert accumulator.count == 5000
    # Solo sumas y conteo: ninguna colección por muestra retenida.
    state = accumulator.__dict__
    assert set(state) == {
        "_temp_horno1",
        "_temp_comb_horno1",
        "_temp_horno2",
        "_temp_comb_horno2",
        "_velocidad_cinta",
        "_count",
    }
    assert all(
        not isinstance(value, (list, tuple, dict, set)) for value in state.values()
    )
