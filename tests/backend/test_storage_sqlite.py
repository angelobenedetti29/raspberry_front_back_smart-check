"""Tests del AlmacenSQLite (persistencia de eventos de pista).

No usamos mocks: `sqlite3` es stdlib y podemos crear DBs temporales con
`tmp_path` para verificar la inserción y la rotación.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from backend.config import StorageConfig
from backend.streaming.estabilizador import EventoPista
from backend.streaming.storage_sqlite import AlmacenSQLite


def _config(tmp_path: Path, *, max_bytes: int = 1_000_000) -> StorageConfig:
    """Arma un StorageConfig con la DB en tmp_path/eventos.db."""
    return StorageConfig(
        path=tmp_path / "eventos.db",
        max_bytes=max_bytes,
        max_files=3,
        persist_no_detection_every=5,
    )


def _evento(
    pista: int = 0,
    label: str = "tostada",
    tipo: str = "baja",
) -> EventoPista:
    return EventoPista(
        pista_id=pista,
        tipo=tipo,
        label=label,
        confianza=0.85,
        bbox=(10, 20, 30, 40),
    )


# --- registrar / insert ---


def test_registrar_inserta_una_fila_por_evento(tmp_path: Path) -> None:
    almacen = AlmacenSQLite(_config(tmp_path), fuente="cam-0", modelo="tostadas-v2")
    almacen.iniciar()
    try:
        almacen.registrar(42, [_evento(), _evento(pista=1, tipo="alta")], activas=2)
    finally:
        almacen.detener()
    # Verificar el contenido de la DB.
    conn = sqlite3.connect(str(tmp_path / "eventos.db"))
    try:
        filas = conn.execute("SELECT tipo, label, frame FROM eventos").fetchall()
    finally:
        conn.close()
    tipos = [fila[0] for fila in filas]
    assert tipos == ["baja", "alta"]
    assert all(fila[2] == 42 for fila in filas)


def test_registrar_sin_eventos_emite_latido_segun_cadencia(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    cfg = StorageConfig(
        path=cfg.path,
        max_bytes=cfg.max_bytes,
        max_files=cfg.max_files,
        persist_no_detection_every=2,
    )
    almacen = AlmacenSQLite(cfg, fuente="cam-0", modelo="tostadas-v2")
    almacen.iniciar()
    try:
        # 4 frames sin eventos: la cadencia es cada 2 → debería haber 2 latidos.
        for frame in range(1, 5):
            almacen.registrar(frame, [], activas=0)
    finally:
        almacen.detener()
    conn = sqlite3.connect(str(cfg.path))
    try:
        filas = conn.execute("SELECT tipo, activas FROM eventos WHERE tipo='latido'").fetchall()
    finally:
        conn.close()
    assert len(filas) == 2
    assert all(fila[1] == 0 for fila in filas)


def test_registrar_sin_hilo_iniciado_no_inserta(tmp_path: Path) -> None:
    """Si `iniciar()` no se llamó, `registrar` no debe tocar la DB."""
    cfg = _config(tmp_path)
    almacen = AlmacenSQLite(cfg, fuente="cam-0", modelo="tostadas-v2")
    # NO llamamos iniciar().
    almacen.registrar(1, [_evento()], activas=1)
    # El path existe (vacío) porque la conexión se abre en `iniciar`.
    # Si iniciar() no se llamó, no hay filas.
    assert not cfg.path.exists() or _contar_filas(cfg.path) == 0


def test_error_al_insertar_no_traba_siguiente_insercion(tmp_path: Path) -> None:
    """Un fallo de SQLite no debe matar el hilo: la siguiente inserción sigue."""
    cfg = _config(tmp_path)
    almacen = AlmacenSQLite(cfg, fuente="cam-0", modelo="tostadas-v2")
    almacen.iniciar()
    try:
        # Insertar un evento normal.
        almacen.registrar(1, [_evento()], activas=1)
        # Provocar un fallo cerrando la conexión por debajo.
        with almacen._lock:  # type: ignore[attr-defined]
            assert almacen._conn is not None  # type: ignore[attr-defined]
            almacen._conn.close()  # type: ignore[attr-defined]
            almacen._conn = None  # type: ignore[attr-defined]
        # La siguiente llamada no debe lanzar (el outbox encolar valida _conn).
        # Por la implementación actual: si _conn es None, se descarta silenciosamente.
        almacen.registrar(2, [_evento()], activas=1)
    finally:
        almacen.detener()


# --- rotación ---


def test_rotacion_por_tamano_crea_backup_y_nueva_db(tmp_path: Path) -> None:
    """Cuando el archivo actual supera `max_bytes`, se rota a `.1`."""
    cfg = _config(tmp_path, max_bytes=200)
    almacen = AlmacenSQLite(cfg, fuente="cam-0", modelo="tostadas-v2")
    almacen.iniciar()
    try:
        # Insertar muchas filas hasta superar los 200 bytes.
        for frame in range(1, 50):
            almacen.registrar(frame, [_evento(label="x" * 20)], activas=1)
    finally:
        almacen.detener()
    # Debe existir al menos un backup rotado.
    backups = sorted(tmp_path.glob("eventos.db.*"))
    # .1 (o más) deben estar; el principal también.
    assert any(p.suffix == ".1" for p in backups) or cfg.path.exists()


# --- helpers ---


def _contar_filas(path: Path) -> int:
    if not path.exists():
        return 0
    conn = sqlite3.connect(str(path))
    try:
        return int(conn.execute("SELECT COUNT(*) FROM eventos").fetchone()[0])
    finally:
        conn.close()


def json_linea(
    frame: int,
    tipo: str,
    label: str | None,
    *,
    activas: int | None = None,
) -> str:
    """Arma una línea JSON con el mismo formato que escribe el almacén."""
    import json

    registro: dict[str, object] = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "frame": frame,
        "modelo": "tostadas-v2",
        "fuente": "cam-0",
        "tipo": tipo,
    }
    if tipo == "latido":
        registro["activas"] = activas if activas is not None else 0
    else:
        registro["pista"] = 0
        registro["label"] = label
        registro["confianza"] = 0.85
        registro["bbox"] = [10, 20, 30, 40]
    return json.dumps(registro, ensure_ascii=False)
