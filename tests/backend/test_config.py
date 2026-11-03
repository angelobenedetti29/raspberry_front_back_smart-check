"""Tests del loader de configuración (backend/config.py).

Cubre el happy path, las validaciones de integridad referencial y la
escritura atómica. Usa un fixture `repo_root` que aisla el `ROOT` del
módulo a un tmp_path vía monkeypatch, así los tests no dependen del
path real del repo.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

import backend.config as config_module
from backend.config import (
    ConfigError,
    guardar_config,
    load_config,
)


@pytest.fixture
def repo_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Crea un root temporal con `models/` y lo inyecta como `backend.config.ROOT`.

    El loader usa el `ROOT` del módulo para resolver `paths.models_dir` y
    componer `StorageConfig.path`. Sin este fixture, los tests caerían en
    el repo real y pisarían el `models/` de la raíz.
    """
    (tmp_path / "models").mkdir()
    monkeypatch.setattr(config_module, "ROOT", tmp_path)
    return tmp_path


def _config_minima() -> dict:
    """Devuelve un config.json válido mínimo para los tests.

    Las rutas se interpretan contra el ROOT del loader (no contra el
    filesystem del test), así que da igual dónde corra pytest.
    """
    return {
        "paths": {
            "videos_dir": "multimedia/videos",
            "models_dir": "models",
            "data_dir": "data",
        },
        "stream": {
            "capture": {
                "source": "0",
                "width": 640,
                "height": 480,
                "fps": 30,
                "loop_video": True,
                "buffer_size": 1,
                "stable_frames": 30,
                "read_timeout_seconds": 5.0,
            },
            "publisher": {
                "server_url": "rtsp://localhost:8554",
                "bitrate": "1M",
                "ffmpeg_executable": "ffmpeg",
                "encoder": "libx264",
                "preset": "ultrafast",
                "tune": "zerolatency",
                "pixel_format": "yuv420p",
                "rtsp_transport": "tcp",
                "gop_seconds": 1,
                "b_frames": 0,
                "queue_size": 2,
                "write_timeout": 0.5,
                "stable_seconds": 5.0,
            },
            "inference": {
                "image_size": 640,
                "confidence_threshold": 0.5,
                "nms_threshold": 0.4,
            },
            "preview": {"fps": 15},
            "storage": {
                "max_bytes": 1024,
                "max_files": 3,
                "persist_no_detection_every": 5,
            },
            "reconnect": {"initial_seconds": 1.0, "max_seconds": 30.0},
        },
        "models": {
            "default_model_id": "mod-1",
            "catalog": [
                {
                    "model_id": "mod-1",
                    "label": "Test",
                    "file": "test.onnx",
                    "names_file": "test.names",
                    "class_thresholds": {"Clase A": 0.5},
                }
            ],
        },
        "api": {
            "base_url": "http://localhost:8080",
            "registration_requests_endpoint": "/api/v1/registration-requests/",
            "dispositivos_ping_endpoint": "/api/v1/dispositivos/ping",
            "dispositivos_nombre_endpoint": "/api/v1/dispositivos/nombre",
            "dispositivos_rename_url_endpoint": "/api/v1/dispositivos/rename-url",
            "productos_endpoint": "/api/v1/productos",
            "dispositivos_sector_endpoint": "/api/v1/dispositivos/sector",
            "lotes_endpoint": "/api/v1/lotes",
        },
        "device": {
            "hostname": "pi-test",
            "ping_interval_seconds": 60.0,
            "registration_poll_interval_seconds": 5.0,
            "listen_address": "0.0.0.0",
            "local_port": 9000,
        },
        "lote": {
            "habilitado": True,
            "cierre_sin_detecciones_segundos": 30.0,
            "flush_segundos": 2.0,
            "max_eventos_por_envio": 50,
            "cola_eventos": 256,
            "max_pendientes": 5000,
            "refresco_catalogo_segundos": 300.0,
            "reintento_inicial_segundos": 1.0,
            "reintento_maximo_segundos": 30.0,
        },
    }


def _escribir(root: Path, data: dict, nombre: str = "config.json") -> Path:
    ruta = root / nombre
    ruta.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return ruta


# --- happy path ---


def test_load_config_paths_absolutos_anclados_a_root(repo_root: Path) -> None:
    _escribir(repo_root, _config_minima())
    cfg = load_config(repo_root / "config.json")
    assert cfg.paths.root == repo_root
    assert cfg.paths.models_dir.is_absolute()
    assert cfg.paths.models_dir == (repo_root / "models").resolve()


def test_load_config_control_default_si_no_existe_seccion(repo_root: Path) -> None:
    """Sin sección `control`, devuelve un ControlConfig deshabilitado."""
    _escribir(repo_root, _config_minima())
    cfg = load_config(repo_root / "config.json")
    assert cfg.control.habilitado is False
    # Los defaults tolerantes a None: el resto del código puede leer .sensores.
    assert cfg.control.sensores.driver == "stub"


# --- errores de validación ---


def test_flush_mayor_o_igual_a_cierre_falla(repo_root: Path) -> None:
    data = _config_minima()
    data["lote"]["flush_segundos"] = 100.0
    data["lote"]["cierre_sin_detecciones_segundos"] = 30.0
    _escribir(repo_root, data)
    with pytest.raises(ConfigError, match="flush_segundos"):
        load_config(repo_root / "config.json")


def test_max_pendientes_menor_a_max_envio_falla(repo_root: Path) -> None:
    data = _config_minima()
    data["lote"]["max_eventos_por_envio"] = 50
    data["lote"]["max_pendientes"] = 10  # < 50
    _escribir(repo_root, data)
    with pytest.raises(ConfigError, match="max_pendientes"):
        load_config(repo_root / "config.json")


def test_default_model_id_inexistente_falla(repo_root: Path) -> None:
    data = _config_minima()
    data["models"]["default_model_id"] = "no-existe"
    _escribir(repo_root, data)
    with pytest.raises(ConfigError, match="default_model_id"):
        load_config(repo_root / "config.json")


def test_thresholds_normalizados_a_minuscula(repo_root: Path) -> None:
    """Las claves del class_thresholds se guardan en minúscula."""
    data = _config_minima()
    data["models"]["catalog"][0]["class_thresholds"] = {"Tostada OK": 0.7}
    _escribir(repo_root, data)
    cfg = load_config(repo_root / "config.json")
    umbrales = cfg.models.catalog[0].class_thresholds
    assert "tostada ok" in umbrales
    assert "Tostada OK" not in umbrales


def test_thresholds_fuera_de_rango_falla(repo_root: Path) -> None:
    data = _config_minima()
    data["models"]["catalog"][0]["class_thresholds"] = {"clase": 1.5}
    _escribir(repo_root, data)
    with pytest.raises(ConfigError, match="entre 0 y 1"):
        load_config(repo_root / "config.json")


def test_rtsp_transport_invalido_falla(repo_root: Path) -> None:
    data = _config_minima()
    data["stream"]["publisher"]["rtsp_transport"] = "sctp"
    _escribir(repo_root, data)
    with pytest.raises(ConfigError, match="rtsp_transport"):
        load_config(repo_root / "config.json")


def test_server_url_debe_ser_rtsp(repo_root: Path) -> None:
    """La base de publicación sólo acepta rtsp:// o rtsps://."""
    data = _config_minima()
    data["stream"]["publisher"]["server_url"] = "http://localhost:8554"
    _escribir(repo_root, data)
    with pytest.raises(ConfigError, match="server_url"):
        load_config(repo_root / "config.json")


def test_server_url_sin_barra_final(repo_root: Path) -> None:
    data = _config_minima()
    data["stream"]["publisher"]["server_url"] = "rtsp://localhost:8554/"
    _escribir(repo_root, data)
    cfg = load_config(repo_root / "config.json")
    assert cfg.stream.publisher.server_url == "rtsp://localhost:8554"


def test_server_url_con_path_falla(repo_root: Path) -> None:
    """La base no puede traer el path: se deriva del nombre y el tipo."""
    data = _config_minima()
    data["stream"]["publisher"]["server_url"] = "rtsp://localhost:8554/entrada"
    _escribir(repo_root, data)
    with pytest.raises(ConfigError, match="server_url"):
        load_config(repo_root / "config.json")


def test_server_url_sin_host_falla(repo_root: Path) -> None:
    data = _config_minima()
    data["stream"]["publisher"]["server_url"] = "rtsp://"
    _escribir(repo_root, data)
    with pytest.raises(ConfigError, match="server_url"):
        load_config(repo_root / "config.json")


# --- guardar_config ---


def test_guardar_config_escritura_atomica(repo_root: Path, tmp_path: Path) -> None:
    """guardar_config vuelca a .tmp y hace replace; si falla no deja basura."""
    ruta = _escribir(repo_root, _config_minima(), nombre="config-guardar.json")
    data = json.loads(ruta.read_text(encoding="utf-8"))
    data["device"]["hostname"] = "pi-modificado"
    cfg = guardar_config(data, ruta)
    contenido = json.loads(ruta.read_text(encoding="utf-8"))
    assert contenido["device"]["hostname"] == "pi-modificado"
    # No quedó ningún temporal (el nombre es único por escritura).
    assert not list(ruta.parent.glob("*.tmp"))
    # El AppConfig devuelto refleja el cambio.
    assert cfg.device.hostname == "pi-modificado"


def test_guardar_config_no_toca_disco_si_es_invalido(repo_root: Path) -> None:
    """Si el dict no valida, no se crea ni el .tmp ni el .json final."""
    ruta = _escribir(repo_root, _config_minima(), nombre="config-bad.json")
    original = json.loads(ruta.read_text(encoding="utf-8"))
    data = dict(original)
    data["lote"]["max_eventos_por_envio"] = 200  # > 100
    with pytest.raises(ConfigError):
        guardar_config(data, ruta)
    # El archivo original sigue intacto.
    contenido = json.loads(ruta.read_text(encoding="utf-8"))
    assert contenido["lote"]["max_eventos_por_envio"] == 50
    assert not list(ruta.parent.glob("*.tmp"))


def test_guardar_config_concurrente_no_corrompe(repo_root: Path) -> None:
    """Escritores concurrentes no dejan JSON a medias ni temporales sueltos."""
    ruta = _escribir(repo_root, _config_minima(), nombre="config-concurrente.json")
    base = json.loads(ruta.read_text(encoding="utf-8"))

    def _guardar(hostname: str) -> None:
        for _ in range(10):
            data = json.loads(json.dumps(base))
            data["device"]["hostname"] = hostname
            guardar_config(data, ruta)

    hilos = [threading.Thread(target=_guardar, args=(f"pi-{indice}",)) for indice in range(4)]
    for hilo in hilos:
        hilo.start()
    for hilo in hilos:
        hilo.join()

    # El archivo final es JSON válido y de uno de los escritores.
    contenido = json.loads(ruta.read_text(encoding="utf-8"))
    assert contenido["device"]["hostname"].startswith("pi-")
    assert not list(ruta.parent.glob("*.tmp"))


# --- paths derivados ---


def test_storage_path_siempre_es_eventos_db(repo_root: Path) -> None:
    """El path del storage siempre es `data/eventos.db` (SQLite único)."""
    _escribir(repo_root, _config_minima())
    cfg = load_config(repo_root / "config.json")
    assert cfg.stream.storage.path.parent == (repo_root / "data").resolve()
    assert cfg.stream.storage.path.name == "eventos.db"


# --- control: registros de consigna y umbrales de marcha ---


def _control_valido() -> dict:
    """Bloque `control` con los campos obligatorios (el resto usa defaults)."""
    return {
        "habilitado": True,
        "publicar_telemetria_segundos": 5.0,
        "poll_consigna_propuesta_segundos": 3.0,
        "metricas_horno_endpoint": "/api/v1/lotes/{id}/metricas-horno",
        "consigna_propuesta_endpoint": "/api/v1/lotes/{id}/consigna-propuesta",
        "aplicar_consigna_endpoint": "/api/v1/lotes/{id}/aplicar-consigna",
        "lotes_modo_endpoint": "/api/v1/lotes/{id}/modo",
    }


def test_control_defaults_de_config_json() -> None:
    """El config.json versionado trae los registros y umbrales acordados."""
    cfg = load_config()
    assert cfg.control.consigna_reg_temp1 == 100
    assert cfg.control.consigna_reg_vel == 104
    assert cfg.control.umbral_marcha_temp_c == 50.0
    assert cfg.control.umbral_marcha_velocidad == 0.01


def test_control_parsea_registros_y_umbrales(repo_root: Path) -> None:
    data = _config_minima()
    control = _control_valido()
    control["consigna_reg_temp1"] = 110
    control["consigna_reg_vel"] = 114
    control["umbral_marcha_temp_c"] = 70.0
    control["umbral_marcha_velocidad"] = 0.5
    data["control"] = control
    _escribir(repo_root, data)

    cfg = load_config(repo_root / "config.json")

    assert cfg.control.consigna_reg_temp1 == 110
    assert cfg.control.consigna_reg_vel == 114
    assert cfg.control.umbral_marcha_temp_c == 70.0
    assert cfg.control.umbral_marcha_velocidad == 0.5


def test_control_registro_de_consigna_cero_falla(repo_root: Path) -> None:
    data = _config_minima()
    control = _control_valido()
    control["consigna_reg_vel"] = 0
    data["control"] = control
    _escribir(repo_root, data)
    with pytest.raises(ConfigError, match="consigna_reg_vel"):
        load_config(repo_root / "config.json")


def test_control_umbral_marcha_negativo_falla(repo_root: Path) -> None:
    data = _config_minima()
    control = _control_valido()
    control["umbral_marcha_temp_c"] = -1.0
    data["control"] = control
    _escribir(repo_root, data)
    with pytest.raises(ConfigError, match="umbral_marcha_temp_c"):
        load_config(repo_root / "config.json")


def test_control_sin_seccion_usa_defaults_nuevos(repo_root: Path) -> None:
    """Un config viejo sin `control` sigue funcionando con los defaults."""
    _escribir(repo_root, _config_minima())
    cfg = load_config(repo_root / "config.json")
    assert cfg.control.consigna_reg_temp1 == 100
    assert cfg.control.consigna_reg_vel == 104
    assert cfg.control.umbral_marcha_temp_c == 50.0
    assert cfg.control.umbral_marcha_velocidad == 0.01
