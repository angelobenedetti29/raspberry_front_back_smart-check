# Infraestructura: exponer la Raspberry por Cloudflare

Esta guía explica cómo dejar la Raspberry accesible desde internet para que el
backend Go pueda enviarle el push de renombre
(`PUT /api/v1/dispositivos/{device_id}/nombre`) a través de un **quick tunnel**
efímero de Cloudflare. No requiere cuenta de Cloudflare ni dominio propio: el
túnel genera un subdominio `*.trycloudflare.com` cada vez que se levanta.

> Para la variante con túnel con nombre y dominio fijo, este flujo se reemplaza
> por la receta de Cloudflare (`cloudflared tunnel create ...`). La configuración
> del lado de la app no cambia: lo único que importa es qué valor terminás
> poniendo en `device.public_url`.
>
> Instalación de la app, venv y receta de dos instancias:
> `MANUAL_INSTALACION.md`.

## Prerrequisitos

- `cloudflared` instalado en la Raspberry. Es un binario; **no va en
  `requirements.txt`** porque en la Raspberry las librerías que tocan GStreamer y
  Hailo vienen del sistema. Descargá el `.deb` desde
  <https://github.com/cloudflare/cloudflared/releases> o instalalo con
  `sudo apt install cloudflared` si está disponible.
- La Raspberry debe poder alcanzar `api.trycloudflare.com` por 443/TCP (salida
  HTTPS estándar).
- La aplicación (`python run.py`) debe estar levantada antes de iniciar el
  túnel: cloudflared proxifica un servidor que ya tiene que estar escuchando.
- `device.public_url` no puede estar vacío al momento de registrar el
  dispositivo. La app igual arranca sin él y permite navegar las secciones,
  sólo bloquea el alta: si el operador aprieta "Registrar dispositivo" con
  `device.public_url` vacío, el `DeviceService` rechaza el comando con un mensaje
  claro en la tarjeta de Inicio y no despierta el hilo de registro. Hay que
  levantar el túnel, pegar la URL pública en `device.public_url` y **reiniciar
  la app** antes de reintentar. Si la Pi ya estaba aprobada, en cambio, podés
  rotar la URL desde Configuración sin reiniciar (ver "Cambiar el par en
  caliente").

## Configuración del lado de la app

El servidor HTTP local vive en `backend/http/servidor.py` (`ServidorHTTP`,
FastAPI + uvicorn embebido). `run.py` lo construye con el router del push de
renombre y lo arranca como un servicio más:

```python
bind_host, bind_port = DeviceService.derivar_bind(
    config.device.public_url,
    config.device.listen_address,
    config.device.local_port,
)
servidor_http = ServidorHTTP(
    routers=[construir_router_renombre(dispositivo)],
    bind_host=bind_host,
    bind_port=bind_port,
)
```

El bind sale de `config.device`:

```json
"device": {
    "hostname": "smartcheck-rbpi-03",
    "ping_interval_seconds": 60.0,
    "registration_poll_interval_seconds": 5.0,
    "public_url": "",
    "listen_address": "127.0.0.1",
    "local_port": 9000
}
```

- `public_url` es la URL pública (esquema + host + puerto) con la que el backend
  Go alcanza a esta Raspberry. **Mientras esté vacío**, el operador todavía no
  definió a dónde apunta el túnel; el bind sigue siendo el de respaldo.
- `listen_address` y `local_port` son el **bind de respaldo** que se usa cuando
  `public_url` está vacía. Por seguridad conviene dejarlo en loopback: cloudflared
  (que corre local) lo alcanza, pero el puerto no queda expuesto en la LAN.

`DeviceService.derivar_bind` decide el bind efectivo:

| `public_url` | Host de bind | Puerto |
|---|---|---|
| vacía | `listen_address` | `local_port` |
| host loopback (`localhost`, `127.0.0.1`, `::1`) | `127.0.0.1` | el de la URL; si no trae, 80 (`http`) / 443 (`https`) / `local_port` |
| hostname sin IP (túnel, ej. `*.trycloudflare.com`) | `127.0.0.1` | el de la URL; si no trae, `local_port` |
| IP literal de la LAN (ej. `192.168.100.8`) | `0.0.0.0` | el de la URL; si no trae, `local_port` |

Con un quick tunnel (`https://xxx.trycloudflare.com`) y `local_port: 9000`, el
resultado es `127.0.0.1:9000`: la Pi escucha local y cloudflared es el único que
lo ve desde afuera. La URL pública **no** se bindea.

## Cambiar el par en caliente

`device.public_url` + `device.local_port` son la única parte de la configuración
que se aplica sin reiniciar:

1. Al guardar desde Configuración, `AdaptadorConfig` detecta el cambio y llama a
   `on_device_tunel_changed`.
2. `run.py` (`_al_cambiar_tunel`) re-deriva el bind con `derivar_bind`, llama a
   `ServidorHTTP.rebind(host, puerto)` y, si el bind nuevo funcionó, a
   `DeviceService.actualizar_tunel(url, puerto)`.
3. Si la Pi ya está aprobada, la URL nueva se sincroniza con el Go con un `PATCH`
   autenticado con el `secret` (`api.dispositivos_rename_url_endpoint`). Si el
   backend todavía no expone ese endpoint, la Pi reintenta con backoff (5 s →
   300 s).
4. Si el re-bind falla (puerto ocupado, sin permisos), la URL nueva **no** se
   sincroniza: la Pi sigue anunciando la anterior y el servidor HTTP queda
   detenido (no hay reintento automático del bind).

El resto de `config.json` (incluido `listen_address`) se lee al arrancar:
requiere reiniciar la app.

> **Ojo con el alta:** `DeviceService` fija `self._url` (y el `ClienteRegistro`)
> en el arranque. El re-bind en caliente cambia el servidor HTTP y sincroniza la
> `rename_url` de una Pi ya aprobada, pero **no** cambia lo que el alta anuncia:
> si la app arrancó con `public_url` vacía, "Registrar dispositivo" sigue
> bloqueado hasta reiniciar con la URL puesta. Para el primer alta, editá el
> archivo y reiniciá; el cambio en caliente es para rotar la URL de una Pi ya
> aprobada.

## Levantar el túnel

Una vez que la app está corriendo (`python run.py`) y el puerto local está
escuchando, abrí otra terminal en la Raspberry y ejecutá:

```bash
cloudflared tunnel --url http://127.0.0.1:9000
```

Al arrancarlo, cloudflared imprime en stdout una línea con la URL pública que
te asignó, parecida a:

```
Your quick Tunnel has been created! Visit it at (it may take some time to be reachable):
https://algo-random.trycloudflare.com
```

Copiá esa URL completa, incluido el `https://`.

## Pegar la URL en `config.json` (o en Configuración)

Opción A, editando el archivo y reiniciando:

```json
"device": {
    "public_url": "https://algo-random.trycloudflare.com",
    ...
}
```

Opción B, desde **Configuración** → grupo Dispositivo → "URL pública del túnel"
(y "Puerto local" si hace falta): se guarda y re-bindea en caliente, sin
reiniciar. Sirve para rotar la URL de una Pi **ya aprobada**; si todavía no
registraste, usá la Opción A (el alta toma la URL del arranque).

`public_url` se manda al backend Go como `rename_url` al hacer el alta
(`backend/device/cliente.py`) y el Go la usa para resolver a dónde mandar el
push de renombre. Ese push es
`PUT {rename_url}/api/v1/dispositivos/{device_id}/nombre` con
`Authorization: Bearer <rename_token>`; el router que lo atiende es
`backend/http/routers/renombre.py`.

## Reiniciar la app (si editaste el archivo a mano)

`DeviceService` y `run.py` leen `device.public_url` **en el arranque**. Después
de editar `config.json` a mano:

1. Bajá la app (cerrá la GUI o mandá SIGTERM al `run.py`).
2. Volvé a levantarla con `python run.py`.

El log debería mostrar el bind efectivo al iniciar:

```
http: servidor escuchando en 127.0.0.1:9000
```

## Si el dominio cambia (rotación del túnel efímero)

Un quick tunnel **no** conserva el dominio entre reinicios: si bajás y volvés
a arrancar `cloudflared`, te asigna un subdominio nuevo. Cuando eso pase:

1. Levantá el túnel y copiá la URL nueva.
2. Actualizá `device.public_url` en `config.json` (o desde Configuración, que
   re-bindea en caliente).
3. Si la Pi ya está aprobada, la app sincroniza la `rename_url` nueva con el Go
   (`PATCH /api/v1/dispositivos/rename-url`). Si preferís re-registrar, olvidá el
   dispositivo y volvé a darlo de alta: el alta nueva lleva la URL actualizada.
   Ojo: re-registrar crea un dispositivo nuevo en el backend; no hay rotación de
   secret sobre el dispositivo existente (limitación conocida del alta).

Si no actualizás la URL ni re-registrás, el push de renombre del Go va a llegar
a un túnel que ya no existe y la Pi no va a enterarse; el resto de la app sigue
funcionando, sólo se pierde el camino inverso de renombre.

## Diagnóstico rápido

- **¿El servidor está escuchando?** Desde la propia Pi, con el dispositivo ya
  aprobado y usando el `device_id` que devolvió el alta (está en `device.json`):

  ```bash
  curl -i -X PUT "http://127.0.0.1:9000/api/v1/dispositivos/<device_id>/nombre"
  ```

  Sin `Authorization: Bearer <rename_token>` tiene que responder `401` con
  cuerpo JSON (`{"error": "Credencial inválida"}`). Eso confirma que el socket
  está abierto y el handler enruta la ruta. Si el dispositivo todavía no está
  aprobado (o el `device_id` no coincide con el local), la misma ruta responde
  `403`; un `GET` responde `405`.

- **¿El túnel reenvía al servidor?** Desde internet (o con `curl` desde otra
  máquina):

  ```bash
  curl -i -X PUT "https://algo-random.trycloudflare.com/api/v1/dispositivos/<device_id>/nombre"
  ```

  Misma respuesta esperada (`401` con JSON). Si ves error de Cloudflare
  (`1033`, `502`), el túnel no se terminó de establecer o el `--url` interno
  apunta a un puerto donde no hay nada escuchando.

- **¿La Pi está registrada?** Revisá la sección "Dispositivo" de la GUI: el
  estado tiene que ser `APROBADO`. Sin aprobación, el handler responde `403`
  porque todavía no hay `rename_token`.

## Notas de operación

- El `--url` interno siempre tiene que apuntar al bind local. Con una URL de
  túnel es `http://127.0.0.1:9000` (`127.0.0.1` porque el bind vive en loopback;
  `9000` porque es el `local_port` configurado). Si cambiás el puerto en
  `config.json` o desde Configuración, cambialo también acá.
- Si en vez de túnel usás una IP literal de la LAN en `public_url`, el bind pasa
  a `0.0.0.0:<puerto>` y cloudflared no hace falta: el backend Go alcanza la Pi
  directo en la red local.
- Si querés que el túnel arranque con la Pi, la receta más simple es un
  `systemd` unit que ejecute `cloudflared tunnel --url http://127.0.0.1:9000`
  después de la app. No viene incluido: cada línea tiene su propio `.service`
  y reinicios de `cloudflared` rotan el dominio (ver arriba).
- El `rename_token` (que entrega el backend al aprobar el alta) se guarda en
  `device.json` con permisos `0600`. Es el único secreto que la Pi necesita
  para autenticar el push del Go; no lo expongas en logs ni lo mandes por
  canales inseguros.

## Túnel con nombre (dominio fijo)

Si en vez del quick tunnel usás un túnel con nombre:

1. `cloudflared tunnel login`
2. `cloudflared tunnel create smartcheck-pi`
3. `cloudflared tunnel route dns smartcheck-pi renombre.example.com`
4. `cloudflared tunnel run --url http://127.0.0.1:9000 smartcheck-pi`
5. Poné `https://renombre.example.com` en `device.public_url`.

El bind local sigue siendo `127.0.0.1:9000`; cloudflared es el único que lo
alcanza. Para que arranque con la Pi, creá un unit
`cloudflared-smartcheck.service` con
`ExecStart=/usr/bin/cloudflared tunnel run --url http://127.0.0.1:9000 smartcheck-pi`
y `Restart=on-failure`.
