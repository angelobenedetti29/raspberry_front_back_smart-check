# Backend Go — tipo de dispositivo (pendiente)

El backend Go que aprueba los registros es **externo a este repositorio**. La app de
la Raspberry (`backend/device/`) ya envía, valida y persiste el tipo del dispositivo,
pero los endpoints remotos todavía no lo contemplan. Este documento describe lo que
**debe implementarse del lado Go** para que el flujo quede consistente.

Valores permitidos (enum, en mayúsculas exactas):

- `ENTRADA_HORNO`
- `SALIDA_HORNO`

## 1. Alta de registro — aceptar y validar `type`

Endpoint: `POST /api/v1/registration-requests/`

El cuerpo de la solicitud ahora incluye `type` (obligatorio):

```json
{
    "hostname": "smartcheck-rbpi-01",
    "type": "ENTRADA_HORNO"
}
```

Reglas:

- `type` es obligatorio en la solicitud.
- Sólo se aceptan `ENTRADA_HORNO` o `SALIDA_HORNO`.
- Ausente, `null`, vacío o valor desconocido → responder `400` con detalle del error.
- No existe un estado "sin tipo" para una solicitud ni para un dispositivo.

## 2. Persistencia

- Persistir `type` junto al dispositivo aprobado.
- Un dispositivo tiene **un solo** tipo.
- Puede haber múltiples dispositivos del mismo tipo.
- El tipo **no se puede modificar** una vez registrado. Si una Raspberry necesita
  otro tipo, el flujo es volver a registrarla con el nuevo tipo (no hay endpoint de
  cambio de tipo). Esto evita inconsistencias con los datos históricos asociados.

## 3. Consulta de la solicitud — devolver `type`

Endpoint: `GET /api/v1/registration-requests/{request_id}`

La respuesta (hoy `{status, device_id, secret}`) debe incluir además `type`:

```json
{
    "status": "APPROVED",
    "device_id": "f3e331e4-...",
    "secret": "...",
    "type": "ENTRADA_HORNO"
}
```

## 4. Respuestas con información del dispositivo

Cualquier endpoint que devuelva información de un dispositivo debe incluir `type`.
No se debe inferir el tipo a partir de otras propiedades del dispositivo.

```json
{
    "id": "...",
    "uuid": "...",
    "type": "ENTRADA_HORNO"
}
```

## 5. Fuera de alcance (no implementar por ahora)

- Conteo de productos, identificación/clasificación de productos.
- Detección de crudos/quemados, temperatura, velocidad de cinta.
- Procesamiento de archivos, estadísticas, inferencia.
- Comunicación específica por tipo de dispositivo.
- Roles o permisos para determinar el tipo (el tipo es una propiedad directa del
  dispositivo, no un rol).

## Compatibilidad ya resuelta del lado Python

- Si el `device.json` local no tiene `type` (registro previo a este cambio), el
  cliente Python lo descarta y el dispositivo arranca como `NO_REGISTRADO`, forzando
  un nuevo registro con tipo. No hay migración silenciosa.
- Mientras el backend Go no implemente `type`, la solicitud viaja igual y el cliente
  conserva localmente el tipo solicitado (si la respuesta de aprobación trae `type`,
  ese valor tiene prioridad). El backend Go debe igualmente persistir y devolver
  `type` para que el tipo sea consistente del lado servidor.
