# Recordatorios

Pendientes y trabajo diferido del repo: qué hay que actualizar y **cuándo** se
hace necesario cada uno. No es una lista de bugs: cada ítem tiene su disparador.
Al completar un ítem, borralo de acá.

Las referencias de línea son de octubre de 2026 y pueden correrse con ediciones.

## 1. Migración a modelo único `<producto>_<estado>`

Convención vigente: las clases del modelo son `<producto>_<estado>`; el estado
es el sufijo tras el último `_` y sólo puede ser `crudo`|`ok`|`quemado`; el
producto se resuelve solo, por nombre normalizado, contra el catálogo de
productos del backend (ver `backend/etiquetas.py`).

| Qué | Dónde | Cuándo se hace necesario |
|-----|-------|--------------------------|
| Reentrenar con clases compuestas (`tostada_comun_ok`, `tostada_comun_quemado`, `fajita_crudo`, …), siguiendo `backend/ai_training/compile_instructions.md` §2 | dataset + `backend/ai_training/` | Al armar el dataset multi-producto. Hasta entonces, el modelo actual (TCQ/TCOK) sigue contando vía alias |
| Borrar `_ALIAS_TRANSITORIOS` (mapea `tcq`/`tcok`/`tostada quemada`/`tostadas ok` → `tostada_comun`) y sus casos en los tests | `backend/etiquetas.py` + `tests/backend/test_etiquetas.py` | Al deployar el modelo reentrenado |
| Actualizar `class_thresholds` a claves compuestas (hoy quedaron `tcq`/`tcok`/`chaq`/`chaok`/`chacr`, que matchean el modelo actual; la clave es la etiqueta compuesta completa, en minúscula) | `config.json` → `models.catalog` | Al deployar el modelo reentrenado |
| Los eventos viejos de `data/eventos.db` tienen labels crudos (`TCQ`, `Tostada Quemada`): no hay migración | `data/eventos.db` | Sólo si se necesitan métricas históricas unificadas |
| El docstring del módulo menciona sólo `controlador_control` (ahora también consume `controlador_lotes`) | `frontend/secciones/horno_cinta.py` (~6) | Al tocar esa sección por otro motivo |

## 2. Docs desactualizados

| Qué | Dónde | Cuándo |
|-----|-------|--------|
| Receta de túnel con nombre (todo el doc asume quick tunnel) y `systemd` unit para `cloudflared` ("no viene incluido") | `INFRAESTRUCTURA.md` | Al pasar el túnel a producción |

## 3. Hardware real (PLC / sensores / actuadores)

| Qué | Dónde | Cuándo |
|-----|-------|--------|
| Verificar contra el PLC real el mapa de registros (direcciones base-0, offsets, `unit_id`, read-back y las direcciones 100/104); hoy el único ejercicio es contra `tools/simulador_modbus.py` | `backend/control/modbus.py` (~41-60) | Al conectar el PLC |
| Los defaults de `_limites_del_producto` (`delta_temp_max_c=10.0`, `delta_vel_max=0.05`, temp 0-500, vel 0-5) se usan si el `Producto` del Go no trae límites; confirmar que coincidan con los productos reales | `backend/control/__init__.py` (~70-88) | Cuando se carguen los productos reales |
| Docstrings de `modbus.py` ("con el default `stub` el módulo entero no se importa en runtime") y del factory de `sensores.py` ("seguro de llamar aunque `pymodbus` no esté instalado") no se cumplen: `sensores.py` importa `backend.control.modbus` a nivel de módulo y ese importa `pymodbus` arriba ⇒ en modo `stub` igual hace falta `pymodbus` (hoy no rompe porque está en `requirements.txt`) | `backend/control/sensores.py` (~19-27, ~104-108), `backend/control/modbus.py` (~24-25, ~34) | Si se quiere que el modo stub corra sin `pymodbus` (mover el import dentro de las ramas del factory), o al tocar estos módulos por el driver real |
| Docstring de `LectorSensores` ("el stub devuelve todo None: la Pi publica un heartbeat con los campos en null") vs comportamiento: `_publicar_telemetria` filtra la lectura todo-None con `_lectura_tiene_datos` y no publica telemetría del horno en modo stub | `backend/control/sensores.py` (~33-39) vs `backend/control/__init__.py` (~570-607) | Al definir qué telemetría de horno espera el backend Go cuando no hay datos (heartbeat explícito o silencio) |

## 4. Backend Go

| Qué | Dónde | Cuándo |
|-----|-------|--------|
| El `secret` del dispositivo no tiene rotación: "se usa para autenticar hasta el próximo alta" | `backend/device/almacen.py` (~78) | Si el Go agrega refresh/rotación de credenciales |
| Eventos con label que no matchea ningún producto del Go se descartan con warning (una vez por prefijo, hasta el refresco de catálogo): los nombres de productos del Go tienen que coincidir con el prefijo normalizado | `backend/lote/__init__.py` (`_resolver_producto`) | Al crear o renombrar productos en el Go (verificar contra `backend.etiquetas.normalizar_clave`) |
| La doc del contrato del Go (`docs/device-registration-contract.md`) describe el push como `PUT {rename_url}/api/v1/dispositivos/nombre`, sin `{device_id}`; el handler real usa `RenamePushPath(deviceID)` (`/api/v1/dispositivos/{id}/nombre`) y la Pi expone esa ruta | `backend_smart-check-automation_go/docs/device-registration-contract.md` | Al tocar el contrato de renombre |

## 5. Verificación en la Raspberry (no verificable en dev)

| Qué | Dónde | Cuándo |
|-----|-------|--------|
| Hardware real: PLC (mapa/`unit_id`/read-back), cámara (captura real) y Hailo (estrés NPU NPU→CPU→NPU sin `HAILO_OUT_OF_PHYSICAL_DEVICES`, RSS/handles estables, formas reales de cada HEF, sostenido ~30 min con inferencia + x264 y stream desde el otro frontend sin cortar la sesión RTSP) | `AGENTS.md` (§ Verificación en la Raspberry), `backend/ai_training/compile_instructions.md` (§ Verificación pendiente) | Antes de dar por buena la puesta en marcha en la Pi (idealmente con el modelo reentrenado ya deployado) |

## 6. Higiene del repo

| Qué | Dónde | Cuándo |
|-----|-------|--------|
| `.gitignore` ya cubre `data/*.db*`, `data/control_audit.jsonl*` y `multimedia/videos/t3.mp4`, pero no `data/dlq.jsonl*` ni `multimedia/output/` (salida de `validar.py`); además conserva `streaming/data/*`, ruta que ya no existe | `.gitignore` (~36-47) | Antes del próximo commit con datos de runtime |
| Piso de `pymodbus`: `requirements.txt` declara `>=3.11,<4`, pero el simulador usa `pymodbus.simulator` y necesita `>=3.15`. Pip resuelve la última 3.x, pero una instalación con 3.11/3.12 deja el simulador sin arrancar | `requirements.txt`, `tools/simulador_modbus.py` | Si se fija la versión para producción o se instala en un entorno con 3.11/3.12 |
| `multimedia/videos/t3.mp4` no se versiona porque supera el límite de GitHub: una copia o un clon nuevo no lo tiene. Usar `t1.mp4`/`t2.mp4` o copiarlo a mano | `multimedia/videos/`, `.gitignore` | Al preparar una instancia nueva o una demo |
| `yolo26n-coco` referencia `yolo26n.onnx`, que no existe en `models/` (elegirlo falla por archivo ausente); `yolo11n.onnx` existe pero no tiene entrada de catálogo | `config.json` (~51-54), `models/` | Al elegir ese modelo desde la UI, o al ordenar `models/` |
| No hay config local de lint/format (`pyproject.toml`/`ruff.toml`): la CI pasa `ruff` (select E,F,I,B,UP,SIM) y `black` inline | `.github/workflows/ci.yml` | Cuando se quiera lint local |

## 7. Puntos de extensión previstos (no deuda)

- `backend/monitor/acelerador.py` (~93-103): sumar acá otros adaptadores de NPU (hoy sólo Hailo).
- `backend/http/routers/`: "hoy sólo renombre"; cualquier módulo suma el suyo sin tocar `ServidorHTTP`.
- `backend/inference/base.py` (~70-74): clase fuera del `.names` cae a `class_{id}` (reserva explícita, no error).
