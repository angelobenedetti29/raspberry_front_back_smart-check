# Recordatorios

Pendientes y trabajo diferido del repo: qué hay que actualizar y **cuándo** se
hace necesario cada uno. No es una lista de bugs: cada ítem tiene su disparador.
Al completar un ítem, borralo de acá.

Las referencias de línea son de octubre de 2026 y pueden correrse con ediciones.

## 1. Etiquetas cortas por modelo

Cada modelo declara sus propias clases (`tc`, `tcok`, `tcq`, `fajita`, `chaok`,
...); `config.json` → `etiquetas.clases` las traduce al producto del backend y
al estado opcional. `fajita` y `cha` resuelven a Fajitas. Si una clase no está
en el mapa, se interpreta como `<producto>_<estado>` (ver
`backend/etiquetas.py`).

| Qué | Dónde | Cuándo se hace necesario |
|-----|-------|--------------------------|
| Confirmar que el Go tenga activos `Tostada Común` y `Fajitas`: si no, la ENTRADA no abre lote y la SALIDA descarta los eventos | catálogo del Go / `GET /api/v1/productos` | Antes de desplegar |
| Al agregar un producto o clase nueva, sumar la entrada en `etiquetas.clases` y su threshold si aplica | `config.json` | Al cambiar modelos/dataset |
| Los alias viejos (`tostada_quemada`, `tostadas_ok`) y los códigos compartidos con modelos que se retiren (`tcq`, `tcok`) quedan en el mapa mientras esos `.names` estén en uso; no borrarlos antes | `config.json` y `CLASES_POR_DEFECTO` | Al retirar los modelos viejos |
| Los eventos viejos de `data/eventos.db` tienen labels crudos: no hay migración | `data/eventos.db` | Sólo si se necesitan métricas históricas unificadas |

## 2. Docs desactualizados

No hay pendientes conocidos en esta sección.

## 3. Hardware real (PLC / sensores / actuadores)

| Qué | Dónde | Cuándo |
|-----|-------|--------|
| Verificar contra el PLC real el mapa de registros (direcciones base-0, offsets, `unit_id`, read-back y las direcciones 100/104); hoy el único ejercicio es contra `tools/simulador_modbus.py` | `backend/control/modbus.py` (~41-60) | Al conectar el PLC |
| Los defaults de `_limites_del_producto` (`delta_temp_max_c=10.0`, `delta_vel_max=0.05`, temp 0-500, vel 0-5) se usan si el `Producto` del Go no trae límites; confirmar que coincidan con los productos reales | `backend/control/__init__.py` (~70-88) | Cuando se carguen los productos reales |
| Modo `stub` todavía requiere `pymodbus`: `sensores.py` importa `backend.control.modbus` a nivel de módulo y ese importa `pymodbus`. Los docstrings ya lo aclaran; queda como mejora opcional mover el import dentro de las ramas `modbus_*` | `backend/control/sensores.py`, `backend/control/modbus.py` | Si se quiere que el modo stub corra sin `pymodbus` |
| Definir el contrato de telemetría cuando la lectura está toda en `None`: hoy `_publicar_telemetria` filtra la lectura vacía y no publica (silencio); confirmar si el backend Go espera un heartbeat explícito | `backend/control/__init__.py` (`_lectura_tiene_datos`) | Al definir qué telemetría de horno espera el backend Go sin datos |

## 4. Backend Go

| Qué | Dónde | Cuándo |
|-----|-------|--------|
| El `secret` del dispositivo no tiene rotación: "se usa para autenticar hasta el próximo alta" | `backend/device/almacen.py` (~78) | Si el Go agrega refresh/rotación de credenciales |
| Eventos con label que no matchea ningún producto del Go se descartan con warning (una vez por prefijo, hasta el refresco de catálogo): el label se resuelve con `etiquetas.clases` y, si no matchea ni el mapa ni un producto normalizado del Go, se descarta. El `producto` del mapa debe coincidir, normalizado, con el nombre del Go | `backend/lote/__init__.py` (`_resolver_producto`) | Al crear o renombrar productos en el Go (verificar contra `backend.etiquetas.normalizar_clave`) |

## 5. Verificación en la Raspberry (no verificable en dev)

| Qué | Dónde | Cuándo |
|-----|-------|--------|
| Hardware real: PLC (mapa/`unit_id`/read-back), cámara (captura real) y Hailo (estrés NPU NPU→CPU→NPU sin `HAILO_OUT_OF_PHYSICAL_DEVICES`, RSS/handles estables, formas reales de cada HEF, sostenido ~30 min con inferencia + x264 y stream desde el otro frontend sin cortar la sesión RTSP) | `AGENTS.md` (§ Verificación en la Raspberry), `backend/ai_training/compile_instructions.md` (§ Verificación pendiente) | Antes de dar por buena la puesta en marcha en la Pi (idealmente con los dos modelos por rol ya deployados) |

## 6. Higiene del repo

| Qué | Dónde | Cuándo |
|-----|-------|--------|
| `multimedia/videos/t3.mp4` no se versiona porque supera el límite de GitHub: una copia o un clon nuevo no lo tiene. Usar `t1.mp4`/`t2.mp4` o copiarlo a mano | `multimedia/videos/`, `.gitignore` | Al preparar una instancia nueva o una demo |

## 7. Puntos de extensión previstos (no deuda)

- `backend/monitor/acelerador.py` (~93-103): sumar acá otros adaptadores de NPU (hoy sólo Hailo).
- `backend/http/routers/`: "hoy sólo renombre"; cualquier módulo suma el suyo sin tocar `ServidorHTTP`.
- `backend/inference/base.py` (~70-74): clase fuera del `.names` cae a `class_{id}` (reserva explícita, no error).
