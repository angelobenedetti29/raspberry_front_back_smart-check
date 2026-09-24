# Backend Go — sector, lotes y estados (pendiente)

El backend Go que aprueba los registros y recibe la telemetría es **externo a este
repositorio**. Este documento define el contrato que debe implementar para soportar
**lotes coordinados entre dos Raspberrys de un mismo sector**, con **reporte de estados
en vivo** y **conteo final al cierre**. La app Python de la Raspberry (`backend/lote/`,
a implementar) es la que consume este contrato.

El backend **todavía no tiene estos cambios**: hoy expone un flujo de lote más simple
(`POST /api/v1/lotes` con conteos finales y `POST /api/v1/lotes/inicio`). Este documento
es la spec de lo nuevo; §15 detalla qué reemplaza.

Convenciones generales tomadas del backend actual (`docs/api-endpoints.md`): envelope
`{ "success", "message", "data", "errors" }`, rutas bajo `/api/v1/`, y SSE para lo vivo.

## 0. Modelo conceptual

- **Sector**: agrupación de un dispositivo `ENTRADA_HORNO` y uno `SALIDA_HORNO` que
  operan la misma línea. Entidad **nueva**, vive en el backend; la Pi lo resuelve con su
  credencial. Un dispositivo pertenece a lo sumo a un sector.
- **Producto**: entidad del **catálogo** del backend (`producto_id`, `nombre`, `activo`),
  ej. `"Tostada"`, `"Tostada Integral"`. El backend es dueño del catálogo.
- **Estado**: resultado de calidad de una detección: `ok` | `crudo` | `quemado`. Lo
  **define el modelo**: si un modelo no detecta crudos, ese campo viaja `null`. No hay
  tipos de producto separados por resultado.
- **Lote**: unidad de producción de un sector para un producto. Máximo **un lote
  `ABIERTO` por sector**. Lo abre la **entrada** con el primer producto; lo cierra la
  **salida** cuando el sector lleva X segundos inactivo (§12).
- **Detección**: un objeto detectado, con `producto_id`, `estado`, `confianza` y
  metadatos (`pista`, `frame`, `modelo_id`, `momento`).

## 1. Convenciones

- **Base**: `api.base_url` de la config de la Pi. Los endpoints se declaran como
  endpoints nombrados en `api.*` (§14).
- **Auth**: depende del consumidor (§1.1). La Raspberry usa el `secret` del `device.json`
  aprobado (`Authorization: Bearer <secret>`), que reemplaza al `X-API-Key` compartido
  para estos endpoints. El frontend web usa su OAuth existente (cookie JWT
  `session_token`). Los endpoints que consumen ambos aceptan cualquiera de las dos
  credenciales.
- El dispositivo y su sector se derivan del `secret` **solo** en las rutas que autentican
  por dispositivo. La Pi **nunca** manda `sector_id` ni `device_id`.
- **Fechas**: ISO-8601 con offset. Los timestamps del **servidor** mandan para orden,
  inactividad y cierre; el `momento` de la Pi es informativo.
- **Envelope**: `{ "success": bool, "message": str, "data": {...}, "errors": null | str }`.
- **Errores de negocio**: además del envelope, usar códigos HTTP coherentes con el
  backend actual (`400/401/403/404/409/422/500`).

### 1.1 Auth por consumidor

| Endpoint | Consumidor | Credencial |
|---|---|---|
| `GET /dispositivos/sector` | Raspberry | device (`Bearer secret`) |
| `POST /lotes/inicio` | Raspberry (entrada) | device |
| `POST /lotes/{id}/eventos` | Raspberry (salida) | device |
| `POST /lotes/{id}/cierre` | Raspberry (salida) | device |
| `GET /lotes/abierto` | Raspberry + web (panel en vivo) | device u OAuth |
| `GET /productos` | Raspberry (selector) + web (catálogo) | device u OAuth |
| `GET /lotes` | Raspberry (historial) + web (historial) | device u OAuth |
| `SSE /lotes/events` | web | OAuth (cookie `session_token`) |

Alcance según credencial:

- **device** → el dispositivo y su sector salen del `secret`; la Pi nunca manda
  `sector_id` ni `device_id`.
- **OAuth** → no hay sector implícito: `GET /lotes/abierto` y `GET /lotes` requieren
  `sector_id` (query) y quedan habilitados para cualquier usuario autenticado, igual que
  las lecturas actuales del backend.

## 2. `GET /api/v1/productos`

Catálogo de productos del backend. Es lo que la Pi usa para vincular lo que detecta y
para poblar el selector de la UI.

**Auth**: device u OAuth (§1.1).

```json
{
    "success": true,
    "message": "Productos obtenidos exitosamente",
    "data": [
        { "id": "a1b2c3d4-5678-90ab-cdef-1234567890ab", "nombre": "Tostada", "activo": true },
        { "id": "b2c3d4e5-6789-01ab-cdef-234567890abc", "nombre": "Tostada Integral", "activo": true }
    ],
    "errors": null
}
```

Reglas:

- `id` es **estable, único e inmutable**. `nombre` es para mostrar.
- `activo: false` = producto retirado: no se ofrece para elegir, pero sigue resolviendo
  lotes históricos.
- Errores: `401`, `403`, `500`.

## 3. `GET /api/v1/dispositivos/sector`

Sector del dispositivo autenticado, con sus compañeros.

**Auth**: device (§1.1).

```json
{
    "success": true,
    "message": "Sector del dispositivo obtenido exitosamente",
    "data": {
        "sector_id": "horno-1",
        "nombre": "Horno 1",
        "tipo": "ENTRADA_HORNO",
        "companeros": [
            { "device_id": "f3e331e4-...", "hostname": "smartcheck-rbpi-02", "type": "SALIDA_HORNO" }
        ]
    },
    "errors": null
}
```

Reglas:

- Idempotente. La Pi lo consulta al arrancar, al refrescar y ante `401/403`.
- `tipo` es el del **servidor** y es inmutable. Si difiere del `device.json` local, la
  Pi lo registra y lo muestra (dispositivo a re-registrar).
- `companeros` permite ver una configuración incompleta (sector con una sola Pi).
- Errores: `401`, `403 dispositivo_no_aprobado`, `404 sin_sector`.

## 4. `GET /api/v1/lotes/abierto`

Lote abierto del sector, o `null`.

**Auth**: device u OAuth (§1.1). Con OAuth, `sector_id` es obligatorio (query).

```json
{
    "success": true,
    "message": "Lote abierto consultado",
    "data": {
        "lote": {
            "id": "lote-2026-09-23-0007",
            "sector_id": "horno-1",
            "estado": "ABIERTO",
            "producto_id": "a1b2c3d4-...",
            "producto_nombre": "Tostada",
            "abierto_en": "2026-09-23T10:15:30.123-03:00",
            "abierto_por": { "device_id": "f3e331e4-...", "type": "ENTRADA_HORNO" },
            "conteos": { "ok": 12, "crudo": null, "quemado": 2, "total": 14 },
            "ultimo_evento_en": "2026-09-23T10:20:00.000-03:00",
            "inactividad_segundos": 12.5
        }
    },
    "errors": null
}
```

Reglas:

- Idempotente. La ausencia de lote es `lote: null` con `200`, **no** `404`.
- `inactividad_segundos` lo calcula **el servidor** (último evento de cualquiera de los
  dos dispositivos del sector, reloj del servidor). Evita comparar relojes entre
  Raspberrys.
- `conteos.crudo` (o cualquier estado que el modelo no produzca) es `null`, no `0`.
- `abierto_por.type != ENTRADA_HORNO` indica un **lote degradado** (§12.2).

## 5. `POST /api/v1/lotes/inicio` — abrir / continuar

Lo llama la **entrada** con el primer producto detectado. La entrada **solo abre** (o se
attach a) el lote: **no reporta estados**. Los estados los reporta la salida (§6).

**Auth**: device (§1.1).

Request:

```json
{
    "idempotency_key": "8b0e4f3a-...-uuid4",
    "producto_id": "a1b2c3d4-5678-90ab-cdef-1234567890ab",
    "momento": "2026-09-23T10:15:30.123-03:00"
}
```

Semántica **get-or-create por sector**, con máximo un lote `ABIERTO`:

- Si ya hay un lote abierto → `200` con ese lote y `"creado": false` (la Pi hace
  *attach*, nunca lo trata como error).
- Si no hay → `201` y `"creado": true`. `abierto_por` queda con el dispositivo
  autenticado y `producto_id` con el enviado.

Respuesta `data`: `{ "creado": bool, "lote": { ...objeto lote de §4... } }`.

Errores: `400 payload_invalido`, `401`, `403`, `404 sin_sector`,
`422 producto_desconocido`.

## 6. `POST /api/v1/lotes/{lote_id}/eventos` — reporte en vivo

Lo llama la **salida** a medida que detecta productos, para conteo y monitoreo en tiempo
real. Es la que ve el estado final (después del horno). La **entrada solo abre el lote**
(§5) y no reporta estados, para no contar el mismo producto dos veces.

**Auth**: device (§1.1).

Request (máximo 100 eventos por request):

```json
{
    "eventos": [
        {
            "evento_id": "c1a2...-uuid4",
            "producto_id": "a1b2c3d4-...",
            "estado": "quemado",
            "confianza": 0.91,
            "pista": 7,
            "frame": 1234,
            "modelo_id": "tostadas-v2",
            "momento": "2026-09-23T10:15:30.123-03:00"
        }
    ]
}
```

- `estado` ∈ `ok | crudo | quemado` (los que el modelo pueda emitir; `null` si no se
  pudo determinar).
- `producto_id` es el de la entidad vinculada (§13). Si el lote ya tiene producto, debe
  coincidir; si no coincide, `422 producto_inconsistente`.

Respuesta `data`:

```json
{ "aceptados": 1, "duplicados": 0, "lote": { ...resumen actualizado... } }
```

Reglas:

- **Idempotencia por `evento_id`** (UUIDv4 que la Pi genera una sola vez y reutiliza en
  cada reintento). Los duplicados se cuentan en `duplicados` y se ignoran; un batch
  parcialmente aplicado es válido y el reintento es seguro.
- Los eventos **incrementan** los `conteos` del lote (`ok`/`crudo`/`quemado`/`total`).
- Errores: `401`, `403 lote_ajeno`, `404 lote_no_encontrado`, `409 lote_cerrado`,
  `413 demasiados_eventos`, `422 producto_inconsistente`.
- Si el lote está cerrado: la Pi limpia su lote activo, descarta el batch (eventos
  tardíos de un lote ya contabilizado) y espera el próximo producto.

## 7. `POST /api/v1/lotes/{lote_id}/cierre` — conteo final y cierre

Lo llama la **salida** al cumplirse la condición de inactividad (§12).

**Auth**: device (§1.1).

Request:

```json
{
    "idempotency_key": "9d2f...-uuid4",
    "motivo": "sin_detecciones",
    "conteos": { "ok": 118, "crudo": null, "quemado": 2, "total": 120 },
    "momento": "2026-09-23T10:21:00.000-03:00"
}
```

`motivo` ∈ `sin_detecciones | manual | apagado` (el backend puede agregar `seguridad`).

- `conteos` es el **conteo final autoritativo** que envía la salida. Los estados que el
  modelo no produce van `null`. El backend puede compararlo con lo recibido en vivo y
  registrar discrepancias.
- `total` debe cumplir `ok + crudo + quemado == total` (ignorando `null`).

Respuesta `data`: `{ "cerrado": true, "lote": { ...CERRADO con conteos finales... } }`.

Reglas:

- **Idempotente**: si ya estaba cerrado, `200` con el mismo estado (nunca `409` por
  reintento del mismo cierre). Si dos Pis cierran a la vez, gana el primero.
- El backend **puede** rechazar con `409 sector_activo` si su criterio de inactividad no
  se cumple. La Pi espera y reintenta.

## 8. `GET /api/v1/lotes` — historial del sector

Lotes del sector, más nuevos primero. Query: `limite` (default 20, máx 100),
`antes_de` (cursor opaco), `producto_id` (opcional).

**Auth**: device u OAuth (§1.1). Con OAuth, `sector_id` es obligatorio (query).

```json
{
    "success": true,
    "message": "Lotes obtenidos exitosamente",
    "data": [ { "id": "lote-...", "producto_id": "...", "producto_nombre": "Tostada",
                "abierto_en": "...", "cerrado_en": "...", "motivo_cierre": "sin_detecciones",
                "conteos": { "ok": 118, "crudo": null, "quemado": 2, "total": 120 } } ],
    "total": 1, "page": 1, "pageSize": 20,
    "errors": null
}
```

## 9. SSE — lotes en vivo

`GET /api/v1/lotes/events` — **Auth: OAuth** (cookie JWT `session_token`, como los SSE
actuales). Es solo para el frontend web, que ve los lotes en vivo:

- `lote.creado` — al abrirse un lote.
- `lote.actualizado` — con cada batch de eventos (§6).
- `lote.cerrado` — al cerrarse (§7).

Payload: el objeto lote de §4. Heartbeat `: heartbeat` cada 30 s.

## 10. Reglas de conteo y estados

- Los `conteos` del lote son `{ ok, crudo, quemado, total }`.
- Un estado que el modelo no produce viaja **`null`**, nunca `0`. Ej.: el modelo de
  tostadas v2 solo emite `ok`/`quemado` → `crudo: null`.
- `total = ok + crudo + quemado` (los `null` no suman).
- Los `conteos` se construyen **solo con los eventos de la salida** (§6); la entrada no
  aporta conteos. Los conteos en vivo son incrementales y el conteo final (§7) es
  autoritativo.

## 11. Idempotencia y reintentos (lado Pi)

| Operación | Clave | Reintento seguro |
|---|---|---|
| Abrir lote | `idempotency_key` | Sí: misma clave → mismo lote, `creado:false` |
| Reportar eventos | `evento_id` por evento | Sí: dedupe individual, batch parcial válido |
| Cerrar lote | `idempotency_key` + estado | Sí: cerrado → `200` |

Política de la Pi (para que el backend no tenga que defenderla):

- **Reintentable** (backoff 1 s → 30 s): error de red, `5xx`, `429`. El batch queda en la
  outbox con los mismos `evento_id`.
- **No reintentable**: `400`/`413`/`422 producto_inconsistente` → descarta el batch y lo
  registra (es un bug); `401`/`403` → pausa el reporte y expone "re-registrar";
  `404`/`409 lote_cerrado` → limpia el lote activo y descarta.
- **Cola llena**: se descartan eventos con un contador visible en la UI. El conteo puede
  quedar incompleto, y eso **se muestra**; nunca se presenta como cero.

## 12. Inactividad y cierre

### 12.1 Condición de cierre

La salida cierra el lote cuando lleva **X segundos sin detecciones en la salida** y
además el **sector entero** lleva **X segundos inactivo** (incluye eventos de la
entrada). Así no se parten entre lotes productos que todavía están en el horno.

- `X` es `lote.cierre_sin_detecciones_segundos` (config de la Pi) y **debe ser mayor que
  el tránsito máximo por el horno**.
- La inactividad local de la salida la mide la Pi; la del sector la da el servidor
  (`inactividad_segundos`, §4).
- La Pi pide el cierre cuando se cumple su parte; el backend puede reforzar con
  `409 sector_activo`.

### 12.2 Sector incompleto

Si no hay ENTRADA registrada, la **salida puede abrir el lote** (degradado): `abierto_por`
queda con la salida y la UI lo avisa. El flujo normal es que abra la entrada.

## 13. Vinculación detección → producto

No hay tabla de alias ni mapeo de labels. El vínculo es **por modelo**:

- Cada entrada de `models.catalog` en `config.json` declara el `producto_id` del backend
  que ese modelo detecta (§14).
- Cuando corre el modelo de tostadas, toda detección pertenece a esa entidad del backend.
- El **estado** (`ok`/`crudo`/`quemado`) lo define el modelo: la Pi traduce la clase
  detectada a estado y omite (`null`) los estados que ese modelo no tiene.

Consecuencia operativa: para configurar un modelo hay que conocer el `producto_id` del
backend. La sección **Configuración** ofrece, por cada modelo del catálogo, un **selector**
poblado desde `GET /api/v1/productos`, con **fetch propio** (`api.base_url` + el `secret`
de `device.json`), independiente de que el lote esté habilitado. Al elegir, guarda
`models.catalog[].producto_id` en `config.json`. Si el backend no responde, muestra el
error y conserva el valor actual (permite pegar el id a mano).

## 14. Config del lado Pi

`config.json` → `models.catalog[]` suma `producto_id`:

```json
{
    "model_id": "tostadas-v2",
    "label": "YOLOv11 Tostadas V2 (Custom)",
    "file": "tostadas_v2.onnx",
    "names_file": "tostadas_v2.names",
    "producto_id": "a1b2c3d4-5678-90ab-cdef-1234567890ab",
    "class_thresholds": { "tcq": 0.3, "tcok": 0.6 }
}
```

`config.json` → `api` suma (nombres propuestos):

```json
{
    "productos_endpoint": "/api/v1/productos",
    "dispositivos_sector_endpoint": "/api/v1/dispositivos/sector",
    "lotes_endpoint": "/api/v1/lotes"
}
```

`{lotes_endpoint}/abierto`, `{lotes_endpoint}/inicio`, `{lotes_endpoint}/{id}/eventos`,
`{lotes_endpoint}/{id}/cierre` y `{lotes_endpoint}/events` se **derivan** del endpoint
base: no duplicar rutas en el JSON.

`config.json` → bloque `lote`:

```json
{
    "lote": {
        "habilitado": true,
        "cierre_sin_detecciones_segundos": 30.0,
        "flush_segundos": 2.0,
        "max_eventos_por_envio": 50,
        "cola_eventos": 512,
        "max_pendientes": 5000,
        "refresco_catalogo_segundos": 300.0,
        "reintento_inicial_segundos": 1.0,
        "reintento_maximo_segundos": 30.0
    }
}
```

## 15. Relación con los endpoints actuales

El backend hoy tiene (según `docs/api-endpoints.md`):

- `POST /api/v1/lotes` — persiste un lote **ya finalizado** con `productoId`, `turno`,
  `inicioAt`, `finAt`, `totalUnidades`, `correctos`, `quemados`, `crudas`, kg, etc.
- `POST /api/v1/lotes/inicio` — dispara consigna automática al identificar el producto;
  no persiste lote.
- `GET /api/v1/lotes-productivos` — historial paginado.

Este contrato los reemplaza/extiende:

- `POST /api/v1/lotes/inicio` pasa a **abrir y persistir** un lote abierto por sector
  (§5), en vez de solo generar un id de correlación.
- `POST /api/v1/lotes/{id}/eventos` y `POST /api/v1/lotes/{id}/cierre` son nuevos (§6, §7).
- `GET /api/v1/lotes` reemplaza a `GET /api/v1/lotes-productivos` para el historial del
  sector (§8). El vocabulario `correctos/quemados/crudas` se reemplaza por
  `ok/crudo/quemado` en `conteos`.
- Se agregan `GET /api/v1/productos` (§2) y `GET /api/v1/dispositivos/sector` (§3).

Queda a decisión del backend mantener los endpoints viejos por compatibilidad o
reemplazarlos; la Pi nueva usa solo los de este documento.

## 16. Fuera de alcance

- Dashboard/estadísticas agregadas más allá del historial de lotes.
- Turnos, operarios y causas de descarte.
- Consigna térmica al horno (ya existe en el backend actual; este contrato no la toca).
- Inferencia: el backend no procesa video ni imágenes.

## 17. Estado del lado Python

Pendiente de implementar. Este contrato es lo que consumirá:

- `backend/lote/cliente.py` — cliente `urllib` (estilo `backend/device/cliente.py`).
- `backend/lote/__init__.py` — `LoteService` (Protocol `Service`), suscripto a los
  eventos del streaming por un buzón acotado que **nunca** bloquea la inferencia.
- `frontend/nucleo/controlador_lotes.py` + `adaptador_lotes.py` y la sección `Lotes`
  (role-aware: entrada abre, salida cuenta y cierra).
- `config.json`: `producto_id` por modelo + bloque `lote` + endpoints (§14).
