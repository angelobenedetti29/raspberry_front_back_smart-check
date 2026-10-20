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
| Actualizar `class_thresholds` a claves compuestas (hoy quedaron `tcq`/`tcok`, que matchean el modelo actual; la clave es la etiqueta compuesta completa, en minúscula) | `config.json` → `models.catalog` | Al deployar el modelo reentrenado |
| Limpiar `producto_id` residual del `config.json` (el loader lo tolera, pero es basura del esquema viejo) | `config.json` en cada Pi | Al deployar esta versión en la Pi |
| Los eventos viejos de `data/eventos.db` tienen labels crudos (`TCQ`, `Tostada Quemada`): no hay migración | `data/eventos.db` | Sólo si se necesitan métricas históricas unificadas |
| Importa `backend.config.ModbusDriverConfig` directo en una sección (excepción al patrón adaptador) y el docstring del módulo menciona sólo `controlador_control` (ahora también consume `controlador_lotes`) | `frontend/secciones/horno_cinta.py` (~6, ~459) | Al tocar esa sección por otro motivo |

## 2. Docs desactualizados

| Qué | Dónde | Cuándo |
|-----|-------|--------|
| Gotcha de `catalog[].producto_id`: el campo ya no existe (el producto se deriva del label) | `AGENTS.md` (~146) | Antes de la próxima sesión de agentes |
| "No hay tests, linter ni build": hay 5 archivos en `tests/backend/` y la CI corre `ruff` + `black` + `pytest` | `AGENTS.md` (~12, ~202) | Antes de la próxima sesión de agentes |
| Falta documentar `backend/etiquetas.py` (convención `<producto>_<estado>`) y que `backend/lote/` resuelve el producto por prefijo de label contra el catálogo del Go | `AGENTS.md` (sección Arquitectura) | Antes de la próxima sesión de agentes |
| Gotcha de `device.*` con `url`/`listen_port`: los campos reales son `public_url` y `local_port` (desactualización previa a la migración) | `AGENTS.md` (~158) vs `backend/config.py` (~151-171) | Antes de la próxima sesión de agentes |
| `INFRAESTRUCTURA.md` stale de punta a punta: referencia `backend/device/servidor.py` (no existe; es `backend/http/servidor.py`), usa `config.device.url` y `listen_port` (ídem campos reales), y dice que el bind se resuelve sólo al arranque (hoy hay `rebind`/`actualizar_tunel` en caliente) | `INFRAESTRUCTURA.md` (12, 35, 44-63, 86-105, 119) | Antes de la próxima puesta a punto del túnel |
| Receta de túnel con nombre (todo el doc asume quick tunnel) y `systemd` unit para `cloudflared` ("no viene incluido") | `INFRAESTRUCTURA.md` (~158-161) | Al pasar el túnel a producción |

## 3. Hardware real (PLC / sensores / actuadores)

| Qué | Dónde | Cuándo |
|-----|-------|--------|
| Reemplazar los stubs de sensores/actuadores por el driver real (el diseño lo permite sin tocar `ControlService`) | `backend/control/sensores.py`, `config.json` (`sensores.driver`/`actuadores.driver` = `stub`) | Al conectar el PLC |
| Ajustar el mapa de registros Modbus (direcciones/offsets "PLC genérico") contra la documentación del PLC real | `backend/control/modbus.py` (~19-45) | Al conectar el PLC |
| Límites por producto hardcodeados (`delta_temp_max_c=10.0`, `delta_vel_max=0.05`, temp 0-500, vel 0-5): el `Producto` del Go no trae deltas | `backend/control/__init__.py` (~423-455) | Cuando el backend exponga deltas por producto |
| Docstrings de `modbus.py` ("con el default `stub` el módulo entero no se importa en runtime") y del factory de `sensores.py` ("seguro de llamar aunque `pymodbus` no esté instalado") no se cumplen: `sensores.py` importa `backend.control.modbus` a nivel de módulo y ese importa `pymodbus` arriba ⇒ en modo `stub` igual hace falta `pymodbus` (hoy no rompe porque está en `requirements.txt`) | `backend/control/sensores.py` (~19-27, ~104-108), `backend/control/modbus.py` (~24-25, ~34) | Si se quiere que el modo stub corra sin `pymodbus` (mover el import dentro de las ramas del factory), o al tocar estos módulos por el driver real |
| Docstring de `LectorSensores` ("el stub devuelve todo None: la Pi publica un heartbeat con los campos en null") vs comportamiento: `_publicar_telemetria` filtra la lectura todo-None con `_lectura_tiene_datos` y no publica telemetría del horno en modo stub | `backend/control/sensores.py` (~33-39) vs `backend/control/__init__.py` (~459-465) | Al definir qué telemetría de horno espera el backend Go cuando no hay datos (heartbeat explícito o silencio) |

## 4. Backend Go

| Qué | Dónde | Cuándo |
|-----|-------|--------|
| `dispositivos_rename_url_endpoint` no implementado: la Pi reintenta con backoff infinito (warning una sola vez) | `backend/config.py` (~139-143), `backend/device/__init__.py` | Cuando el Go exponga el endpoint (o quitar el sync) |
| El `secret` del dispositivo no tiene rotación: "se usa para autenticar hasta el próximo alta" | `backend/device/almacen.py` (~78) | Si el Go agrega refresh/rotación de credenciales |
| Eventos con label que no matchea ningún producto del Go se descartan con warning (una vez por prefijo, hasta el refresco de catálogo): los nombres de productos del Go tienen que coincidir con el prefijo normalizado | `backend/lote/__init__.py` (`_resolver_producto`) | Al crear o renombrar productos en el Go (verificar contra `backend.etiquetas.normalizar_clave`) |

## 5. Verificación en la Raspberry (no verificable en dev)

| Qué | Dónde | Cuándo |
|-----|-------|--------|
| Estrés NPU (NPU→CPU→NPU sin `HAILO_OUT_OF_PHYSICAL_DEVICES`, RSS/handles estables), formas reales de cada HEF, sostenido ~30 min (inferencia + x264) y stream desde el otro frontend sin cortar la sesión RTSP | `AGENTS.md` (§ Verificación en la Raspberry), `backend/ai_training/compile_instructions.md` (§ Verificación pendiente) | Antes de dar por buena la puesta en marcha en la Pi (idealmente con el modelo reentrenado ya deployado) |

## 6. Higiene del repo

| Qué | Dónde | Cuándo |
|-----|-------|--------|
| `.gitignore` no ignora `data/` (eventos.db, dlq.jsonl, control_audit.jsonl aparecen untracked) ni `multimedia/output/` (salida de `validar.py`); sí ignora `streaming/data/*`, ruta que ya no existe | `.gitignore` (~36-38) | Antes del próximo commit con datos de runtime |
| `yolo26n-coco` referencia `yolo26n.onnx`, que no existe en `models/` (elegirlo falla por archivo ausente); `yolo11n.onnx` existe pero no tiene entrada de catálogo | `config.json` (~51-54), `models/` | Al elegir ese modelo desde la UI, o al ordenar `models/` |
| Config de lint/format inexistente: la CI pasa `ruff` (select E,F,I,B,UP,SIM) y `black` inline; contradice el "no asumas comandos de lint/build" de AGENTS.md | `.github/workflows/ci.yml` | Cuando se quiera lint local (migrar a `pyproject.toml`/`ruff.toml`) |

## 7. Puntos de extensión previstos (no deuda)

- `backend/monitor/acelerador.py` (~93-103): sumar acá otros adaptadores de NPU (hoy sólo Hailo).
- `backend/http/routers/`: "hoy sólo renombre"; cualquier módulo suma el suyo sin tocar `ServidorHTTP`.
- `backend/inference/base.py` (~70-74): clase fuera del `.names` cae a `class_{id}` (reserva explícita, no error).
