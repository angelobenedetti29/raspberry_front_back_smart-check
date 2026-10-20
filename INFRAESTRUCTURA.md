# Infraestructura: exponer la Raspberry por Cloudflare

Esta guía explica cómo dejar la Raspberry accesible desde internet para que el
backend Go pueda enviarle el push de renombre (`PUT /api/v1/dispositivos/nombre`)
a través de un **quick tunnel** efímero de Cloudflare. No requiere cuenta de
Cloudflare ni dominio propio: el túnel genera un subdominio `*.trycloudflare.com`
cada vez que se levanta.

> Para la variante con túnel con nombre y dominio fijo, este flujo se reemplaza
> por la receta de Cloudflare (`cloudflared tunnel create ...`). La configuración
> del lado de la app no cambia: lo único que importa es qué valor terminás
> poniendo en `config.device.public_url`.

## Prerrequisitos

- `cloudflared` instalado en la Raspberry. Es un binario; **no va en
  `requirements.txt`** porque en la Raspberry las librerías que tocan GStreamer y
  Hailo vienen del sistema. Descargá el `.deb` desde
  <https://github.com/cloudflare/cloudflared/releases> o instalalo con
  `sudo apt install cloudflared` si está disponible.
- La Raspberry debe poder alcanzar `api.trycloudflare.com` por 443/TCP (salida
  HTTPS estándar).
- La aplicación (`python run.py`) debe estar levantada antes de iniciar el
  túnel: cloudflared proxyfifica un servidor que ya tiene que estar escuchando.
- `config.device.public_url` no puede estar vacío al momento de registrar el
  dispositivo. La app igual arranca sin él y permite navegar las secciones,
  sólo bloquea el alta: si el operador aprieta "Registrar dispositivo" con
  `device.public_url` vacío, el `DeviceService` rechaza el comando con un mensaje
  claro en la tarjeta de Inicio y no despierta el hilo de registro. Hay que
  levantar el túnel, pegar la URL pública en `config.device.public_url` y reiniciar
  la app antes de reintentar.

## Configuración del lado de la app

El servidor HTTP local vive en `backend/http/servidor.py`. Se levanta solo
cuando se inicia la app, dentro del `DeviceService`, en el puerto y dirección
que resuelve a partir de `config.device`:

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

- `public_url` es la URL pública (esquema + host + puerto) con la que el backend Go
  alcanza a esta Raspberry. **Mientras esté vacío**, el operador todavía no
  definió a dónde apunta el túnel; el bind sigue siendo loopback.
- `listen_address` = `127.0.0.1` y `local_port` = `9000` son el **bind de
  respaldo** que se usa cuando `public_url` está vacío. Por seguridad queda en
  loopback: cloudflared (que corre local) lo alcanza, pero el puerto no queda
  expuesto en la LAN.

Si `public_url` no está vacío y su host es remoto (caso típico
`https://xxx.trycloudflare.com`), `DeviceService.__init__` ignora
`listen_address` y hace bind a `127.0.0.1` de todos modos; el puerto lo toma
de la URL o, si no trae, cae al esquema (`http` → 80, `https` → 443) y, como
último recurso, a `local_port` (9000). El resultado siempre es: el servidor
escucha local y cloudflared es el único que lo ve desde afuera.

## Levantar el túnel

Una vez que la app está corriendo (`python run.py`) y el puerto 9000 está
escuchando en loopback, abrí otra terminal en la Raspberry y ejecutá:

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

## Pegar la URL en `config.json`

Editá `config.json` y reemplazá el valor vacío de `device.public_url`:

```json
"device": {
    "public_url": "https://algo-random.trycloudflare.com",
    ...
}
```

`public_url` se manda al backend Go como `rename_url` al hacer el alta del dispositivo
(`backend/device/cliente.py:66`), y el backend la usa para resolver a dónde
mandar los renombres.

## Reiniciar la app

`DeviceService` lee `device.public_url` **en el arranque**, no en caliente. Después de
editar `config.json`:

1. Bajá la app (cerrá la GUI o mandá SIGTERM al `run.py`).
2. Volvé a levantarla con `python run.py`.

El log debería mostrar el bind efectivo al iniciar:

```
device: servidor de renombre escuchando en 127.0.0.1:9000
```

## Si el dominio cambia (rotación del túnel efímero)

Un quick tunnel **no** conserva el dominio entre reinicios: si bajás y volvés
a arrancar `cloudflared`, te asigna un subdominio nuevo. Cuando eso pase:

1. Levantá el túnel y copiá la URL nueva.
2. Actualizá `config.device.public_url` en `config.json`.
3. Reiniciá la app.
4. Desde la UI, **olvidá el dispositivo** y volvé a registrarlo. El alta nueva
   lleva la URL actualizada al backend; el `rename_token` viejo queda
   revocado y se emite uno nuevo asociado al `rename_url` actual.

Si te olvidás de este paso, el push de renombre del Go va a llegar a un túnel
que ya no existe y la Pi no va a enterarse; el resto de la app sigue
funcionando, sólo se pierde el camino inverso de renombre.

## Diagnóstico rápido

- **¿El servidor está escuchando?** Desde la propia Pi:
  ```bash
  curl -i http://127.0.0.1:9000/api/v1/dispositivos/nombre
  ```
  Sin `Authorization: Bearer <rename_token>` tiene que responder `401` con
  cuerpo JSON (no `404`). Eso confirma que el socket está abierto y el
  handler enruta la ruta.

- **¿El túnel reenvía al servidor?** Desde internet (o con `curl` desde otra
  máquina):
  ```bash
  curl -i https://algo-random.trycloudflare.com/api/v1/dispositivos/nombre
  ```
  Misma respuesta esperada (`401` con JSON). Si ves error de Cloudflare
  (`1033`, `502`), el túnel no se terminó de establecer o el `--url` interno
  apunta a un puerto donde no hay nada escuchando.

- **¿La Pi está registrada?** Revisá la sección "Dispositivo" de la GUI: el
  estado tiene que ser `APROBADO`. Sin aprobación, el handler responde `403`
  ("El dispositivo no está registrado") porque todavía no hay `rename_token`.

## Notas de operación

- El `--url` interno siempre tiene que ser `http://127.0.0.1:9000` (loopback).
  `127.0.0.1` porque el bind de la app vive en loopback; `9000` porque es el
  `local_port` configurado. Si cambiás el puerto en `config.json`, cambialo
  también acá.
- Si querés que el túnel arranque con la Pi, la receta más simple es un
  `systemd` unit que ejecute `cloudflared tunnel --url http://127.0.0.1:9000`
  después de la app. No viene incluido: cada línea tiene su propio `.service`
  y reinicios de `cloudflared` rotan el dominio (ver arriba).
- El `rename_token` (que entrega el backend al aprobar el alta) se guarda en
  `device.json` con permisos `0600`. Es el único secreto que la Pi necesita
  para autenticar el push del Go; no la expongas en logs ni la mandes por
  canales inseguros.