# Manual de instalación y operación

App de escritorio para control de calidad en Raspberry Pi 5 (PySide6 + backend
Python). Este manual cubre la puesta en marcha de una instancia y la receta para
correr **dos instancias en la misma PC/Raspberry**: una `ENTRADA_HORNO` y una
`SALIDA_HORNO`.

El entrypoint es siempre `run.py`: carga `config.json`, arranca los servicios de
backend en hilos y corre la GUI en el hilo principal.

---

## 1. Requisitos

### 1.1 Plataforma

| Componente | Requisito |
|---|---|
| Hardware | Raspberry Pi 5 (entorno industrial) |
| SO | Raspberry Pi OS Bookworm (64 bits) |
| Python | 3.10+; el objetivo es 3.11 (el de Bookworm) |
| Red | La Pi tiene que alcanzar `api.base_url` (backend Go) y el RTSP de MediaMTX |


### 1.2 Dependencias por origen

| Dependencia | Origen | Por qué |
|---|---|---|
| `PySide6>=6.5`, `numpy>=1.24`, `psutil>=5.9.6` | pip (`requirements.txt`) | GUI, cálculo y telemetría |
| `pymodbus[serial]>=3.11,<4` | pip | Cliente Modbus RTU/TCP del horno (SCA-400) |
| `fastapi>=0.110`, `uvicorn>=0.27` | pip | Servidor HTTP local (push de renombre del backend Go) |
| OpenCV (`cv2`) | **sistema** (apt o build propio) | Motor de inferencia CPU (`cv2.dnn`) |
| `hailo_platform` | **sistema** (paquete de Hailo) | Motor NPU (Hailo-8L) |
| `ffmpeg` | **sistema**, en `PATH` | `stream.publisher.ffmpeg_executable` lo ejecuta como subproceso |
| MediaMTX | externo al repo | Servidor RTSP donde se publica el stream |
| `cloudflared` | opcional | Túnel para que el backend Go alcance el push de renombre |

**OpenCV y `hailo_platform` no se instalan por pip.** Son builds del sistema
enlazados con GStreamer/Hailo; instalar los wheels de pip rompe ese enlace y la
NPU deja de funcionar. `pymodbus` sí va por pip: es Python puro y no toca
GStreamer.

### 1.3 El venv con `--system-site-packages`

El venv tiene que poder **ver** los paquetes del sistema. Si se crea aislado,
`import cv2` y `import hailo_platform` fallan y el motor NPU aparece como no
disponible:

```bash
python3 -m venv .venv --system-site-packages
```

Con ese flag, `cv2` y `hailo_platform` se resuelven desde el Python del sistema y
el resto de las dependencias desde el venv. En Debian/Bookworm, si `venv` no está
instalado, hace falta el paquete `python3-venv` (`sudo apt install python3-venv`).

Que falte `hailo_platform` **no impide arrancar**: el import del motor NPU es
perezoso y el catálogo detecta la ausencia (`hailo_disponible`), así que no
redirige un `.onnx` a su `.hef` hermano y los modelos ONNX siguen funcionando en
CPU. Lo que no se puede es usar un `.hef` en esa máquina.

---

## 2. Instalación

### 2.1 Copiar el repo y crear el venv

```bash
cd /home/graso/projects/tesis
git clone <url-del-repo> raspberry_front_back_smart-check   # si todavía no existe
cd raspberry_front_back_smart-check

python3 --version                    # 3.10 o superior
python3 -m venv .venv --system-site-packages
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

### 2.2 Verificar imports

```bash
python -c "import PySide6, pymodbus, fastapi; print('pip OK')"
python -c "import uvicorn, numpy, psutil; print('extras OK')"
```

Y las del sistema (sólo en la Pi):

```bash
python -c "import cv2; print('cv2', cv2.__version__)"
python -c "import hailo_platform; print('hailo_platform OK')"
```

Si `cv2` o `hailo_platform` fallan:

- confirmá que el venv se creó con `--system-site-packages`;
- confirmá que el paquete del sistema está instalado (no lo instales por pip);
- si sólo falla `hailo_platform`, la app igual corre con el motor CPU.

### 2.3 Directorios que tienen que existir

- `models/` — **obligatorio**: el loader falla si `paths.models_dir` no existe.
  Es la base de `models.catalog[].file` y `names_file`.
- `multimedia/videos/` — base de los archivos que puede tomar
  `stream.capture.source` (por ejemplo `t1.mp4`, `t2.mp4`).
- `data/` — salida en runtime (DB de eventos, DLQ, audit de control). Se crea
  bajo demanda; no hace falta crearla a mano.

---

## 3. Configuración

### 3.1 `config.json` vs `device.json`

| Archivo | ¿Se versiona? | Contenido |
|---|---|---|
| `config.json` | Sí | Configuración operativa de la app (rutas, stream, API, device, lotes, control) |
| `device.json` | **No** (`.gitignore`: `/device*.json`) | Estado del alta y el `secret` que devuelve el backend; se escribe con permisos `0600` |

El `secret` del dispositivo **nunca** va en `config.json`. Lo entrega el backend
al aprobar el alta y `DeviceService` lo persiste en `device.json`
(`backend/device/almacen.py`).

Las rutas relativas de `config.json` se resuelven contra el ROOT del proyecto
(derivado de `backend/config.py`), **nunca** contra el directorio desde donde
ejecutás el comando.

### 3.2 Claves relevantes

Valores del `config.json` actual:

| Clave | Valor actual | Qué controla |
|---|---|---|
| `paths.models_dir` | `models` | Base de los archivos de modelo; debe existir |
| `paths.videos_dir` | `multimedia/videos` | Base de los archivos de `stream.capture.source` |
| `paths.data_dir` | `data` | DB de eventos, DLQ y audit de control |
| `stream.capture.source` | `"0"` | Fuente por defecto: índice numérico = cámara; cualquier otro valor = archivo dentro de `videos_dir` |
| `stream.publisher.server_url` | `rtsp://smartcheck.duckdns.org:8554` | Base RTSP (esquema + host + puerto, sin `/` final) |
| `api.base_url` | `http://localhost:8080` | Backend Go (esquema + host + puerto, sin `/` final) |
| `device.hostname` | `rb-1` | Nombre lógico con el que la Pi se registra |
| `device.public_url` | `http://192.168.100.8:9000` | URL con la que el backend Go alcanza a esta Pi (`rename_url`) |
| `device.listen_address` | `0.0.0.0` | Bind de respaldo cuando `public_url` está vacío |
| `device.local_port` | `9000` | Puerto local del servidor HTTP y respaldo del bind |
| `models.default_model_id` | `salida-horno-v4` | Fallback si el dispositivo no tiene tipo registrado |
| `models.default_entrada_model_id` | `entrada-horno-v1` | Modelo que carga una Raspberry `ENTRADA_HORNO` |
| `models.default_salida_model_id` | `salida-horno-v4` | Modelo que carga una Raspberry `SALIDA_HORNO` |
| `control.habilitado` | `true` | Habilita el ControlService (SCA-400) |
| `control.sensores.driver` | `modbus_tcp` | Backend del lector: `stub`, `simulado`, `modbus_rtu`, `modbus_tcp` |
| `control.actuadores.driver` | `modbus_tcp` | Backend del aplicador de consignas |
| `control.consigna_reg_temp1` | `100` | Dirección Modbus base-0 del setpoint de T1 |
| `control.consigna_reg_vel` | `104` | Dirección Modbus base-0 del setpoint de velocidad |
| `control.lotes_modo_endpoint` | `/api/v1/lotes/{id}/modo` | Endpoint para sincronizar el modo del lote |

Notas de cada grupo:

- **`paths.*`**: `models_dir` se valida al cargar; si no existe, la app no
  arranca. `stream.storage.path` queda fijo en `paths.data_dir/eventos.db`.
- **`stream.capture.source`**: `"0"` es cámara; `"t1.mp4"` es un archivo de
  `multimedia/videos/`. Ancho y alto deben ser pares (H.264/yuv420p).
- **`stream.publisher.server_url`**: sólo esquema, host y puerto. El path de
  publicación se deriva como `<slug(hostname)>-<entrada|salida>`; no lo pongas
  en el config.
- **`api.base_url`**: sin `/` final. Las rutas de cada recurso van en los
  `api.*_endpoint` (todos empiezan con `/`).
- **`device.*`**: el bind del servidor HTTP se deriva de `public_url`:
  - URL vacía → `listen_address:local_port`;
  - host local o nombre de túnel → bind en `127.0.0.1` (cloudflared reenvía);
  - IP literal de la LAN → bind en `0.0.0.0`.

  Si `public_url` trae puerto explícito, el bind usa ese puerto. Si no lo trae,
  un host loopback usa 80 (`http`) / 443 (`https`) y el resto cae a
  `local_port`. Mantené `public_url` y `local_port` alineados.
- **`models.*`**: el modelo inicial sale del tipo persistido en `device.json`:
  `ENTRADA_HORNO` usa `default_entrada_model_id` y `SALIDA_HORNO` usa
  `default_salida_model_id`. Si falta el tipo o la clave, usa
  `default_model_id`. La elección es al arrancar, así que después del alta
  inicial hay que reiniciar para que tome el modelo del rol. Las tres
  referencias tienen que existir en `models.catalog`.
- **`control.*`**: los drivers Modbus aceptan `stub` (no hace nada),
  `simulado` (planta en memoria dentro del proceso), `modbus_rtu`
  (`serial_port`, `baudrate`, `unit_id`) y `modbus_tcp` (`host`, `port`,
  `unit_id`). `consigna_reg_temp1`/`consigna_reg_vel` deben coincidir con las
  direcciones del mapa del PLC/simulador; la Pi ENTRADA es la única que escribe.

El `config.json` actual apunta **ambos** drivers a `modbus_tcp` en
`127.0.0.1:5020`, que es el simulador de la sección 6.

### 3.3 Qué se puede editar desde la UI

La sección **Configuración** edita un subconjunto operativo del archivo (captura,
publicación, API, `device`, modelos, lotes y control). También permite elegir el
modelo de entrada y el de salida por separado
(`models.default_entrada_model_id` / `default_salida_model_id`); el cambio
requiere reiniciar. Al elegir un driver `modbus_tcp`, aparecen los campos de host
y puerto de sensores/actuadores; con `stub` o `simulado` quedan ocultos porque no
se usan. Los valores de `control.consigna_reg_temp1`/`consigna_reg_vel` no están
en la UI: se editan en `config.json`.

Guardar desde la UI **valida antes de escribir** y, en general, requiere
reiniciar la app: los servicios ya corren con la configuración del arranque. La
excepción es el par `device.public_url` + `device.local_port`: al guardarlo, la
app re-bindea el servidor HTTP y agenda la sincronización de la nueva
`rename_url` con el backend Go sin reiniciar. Ese cambio en caliente es para una
Pi ya aprobada: `DeviceService` conserva la URL del arranque para el alta, así
que si arrancó con `public_url` vacía, "Registrar dispositivo" sigue bloqueado
hasta reiniciar con la URL puesta.

La escritura es atómica (temporal + `os.replace`) y está serializada en proceso
por `LOCK_CONFIG`. El `.gitignore` cubre los temporales de escritura (`*.tmp`).

### 3.4 `device.public_url` vacío bloquea el alta

`DeviceService` exige `device.public_url` no vacía para registrar: es la URL que
el backend Go guarda como `rename_url` para enviarle los renombres a esta Pi. Si
apretás "Registrar dispositivo" con la URL vacía, la tarjeta de Inicio muestra un
error y el hilo de registro no se despierta. La app igual arranca y permite
navegar las secciones.

Con cloudflared (quick tunnel):

1. Levantá primero la app (`python run.py`), para que el puerto local escuche.
2. En otra terminal:
   ```bash
   cloudflared tunnel --url http://127.0.0.1:9000
   ```
3. Copiá la URL `https://algo-random.trycloudflare.com` que imprime cloudflared.
4. Pegala en `device.public_url` y **reiniciá la app** antes de registrar. Si la
   Pi ya estaba aprobada, en cambio, podés rotar la URL desde Configuración sin
   reiniciar (re-bindea y sincroniza).
5. Recién entonces registrá el dispositivo.

Un quick tunnel **no conserva el dominio** entre reinicios. Si cambia, actualizá
`public_url` (desde Configuración se re-bindea y sincroniza en caliente) o volvé
a registrar (olvidar + registrar): la `rename_url` nueva viaja en el alta. Si la
Pi ya está aprobada, la sincronización usa
`PATCH api.dispositivos_rename_url_endpoint` (`/api/v1/dispositivos/rename-url`)
con el `secret`; si el backend no expone ese endpoint (versión vieja), la Pi
reintenta con backoff y el Go sigue con la URL vieja.
Guía ampliada: `INFRAESTRUCTURA.md` (los campos reales son `public_url` y
`local_port`).

---

## 4. Ejecución

### 4.1 Arranque normal

```bash
cd /home/graso/projects/tesis/raspberry_front_back_smart-check
source .venv/bin/activate
python run.py
```

`run.py` hace, en este orden:

1. Configura logging y manejadores de `SIGINT`/`SIGTERM`.
2. Carga y valida `config.json`; si falla, registra `Error de configuración: ...`
   y sale con código 1.
3. Habilita el monitor de HailoRT (sin Hailo no tiene efecto).
4. Construye y arranca los servicios, en este orden:
   `StreamingService`, `DeviceService`, `MonitorService`, `LoteService`,
   `ControlService`, `ServidorHTTP`.
5. Corre la GUI en el hilo principal (Qt lo exige).

Al apagar (cerrar la GUI, `Ctrl+C` o `SIGTERM`), los servicios se detienen en
**orden inverso**: `ServidorHTTP` → `ControlService` → `LoteService` →
`MonitorService` → `DeviceService` → `StreamingService`. `stop()` bloquea, así
que el apagado es ordenado.

### 4.2 Sin display (verificación)

```bash
QT_QPA_PLATFORM=offscreen python run.py
```

El proceso queda bloqueado en el loop de Qt: usalo con `timeout` para una
verificación acotada.

```bash
QT_QPA_PLATFORM=offscreen timeout 15 python run.py; echo "exit=$?"
```

`timeout` manda `SIGTERM`, la app apaga los servicios y el código esperado es
`124` (lo devuelve `timeout`, no la app).

### 4.3 Logs

Todo va a stdout con formato:

```
2026-11-03 10:00:00,000 INFO device: solicitud req_... creada (PENDING)
```

Nivel `INFO` por defecto (`logging.basicConfig` en `run.py`). Líneas útiles:

| Log | Significado |
|---|---|
| `http: servidor escuchando en <host>:<puerto>` | Bind efectivo del servidor de renombre |
| `streaming: servicio iniciado` | Pipeline de captura/inferencia/publicación arriba |
| `control: servicio iniciado` | ControlService arriba (sólo tiene efecto en ENTRADA) |
| `control: deshabilitado por configuración` | `control.habilitado = false` |
| `device: solicitud <id> creada (PENDING)` | Alta enviada al backend |
| `device: aprobado como <uuid>` | Alta aprobada; `device.json` persistido |
| `Aplicación iniciada` / `Aplicación finalizada` | Arranque y cierre ordenado |

---

## 5. Alta del dispositivo desde la UI

El alta la dispara el operador; **nunca es automática**.

1. Con la app corriendo y `device.public_url` no vacía, andá a **Inicio**.
2. En la fila **Tipo**, elegí `Entrada de horno` o `Salida de horno`. La primera
   opción ("Seleccionar tipo") no tiene valor y mantiene el botón deshabilitado.
3. Apretá **Registrar dispositivo**.
4. La tarjeta queda en **Pendiente / Esperando aprobación**.
5. Un Supervisor/Admin aprueba la solicitud desde el panel del backend Go
   (`POST /api/v1/registration-requests/{id}/approve`). La aprobación crea el
   dispositivo con `nombre = hostname` y devuelve `device_id`, `secret` y
   `rename_token` una sola vez.
6. La app pasa a **Aprobado** y guarda `device.json` (permisos `0600`).

Puntos importantes:

- **El tipo se elige al registrar.** Si el backend devuelve `type` en la
  aprobación, ese valor manda sobre el elegido.
- El botón cambia según el estado: `Registrar dispositivo` / `Reintentar` /
  `Cancelar` (pendiente) / `Olvidar` (aprobado). **Olvidar** borra
  `device.json` y vuelve a `NO_REGISTRADO`.
- **El ControlService SCA-400 sólo corre en `ENTRADA_HORNO`.** En `SALIDA_HORNO`
  el hilo arranca (si `control.habilitado`) pero se autodeshabilita en cada
  ciclo: no lee ni escribe Modbus ni publica telemetría del horno.
- El tipo y el `secret` quedan persistidos en `device.json`: al reiniciar, la app
  retoma el registro aprobado sin volver a pedir el alta.
- Mientras no haya tipo (dispositivo sin registrar), el streaming **no publica**:
  el path se deriva del nombre y el tipo vivos.

---

## 6. Simulador Modbus (sin PLC)

`tools/simulador_modbus.py` es un PLC falso standalone: no importa nada de la
app, sólo `pymodbus` y la stdlib. Hace de esclavo Modbus TCP y mueve una planta
de primer orden según las consignas que la app escribe en los registros.

### 6.1 Arranque

```bash
python tools/simulador_modbus.py
```

Defaults:

| Servicio | Dirección |
|---|---|
| Modbus TCP | `127.0.0.1:5020` |
| Panel web | `http://127.0.0.1:8091/` |

El simulador usa la API `pymodbus.simulator` (su docstring pide `pymodbus >= 3.15`;
`requirements.txt` fija `>=3.11,<4`, así que pip instala la última 3.x). Si el
import falla, revisá la versión instalada con `pip show pymodbus`.

Opciones útiles:

```bash
python tools/simulador_modbus.py --sin-ui              # sin panel web
python tools/simulador_modbus.py --rechazar-setpoints  # escrituras de consigna fallan
python tools/simulador_modbus.py --puerto 1502 --semilla 42
python tools/simulador_modbus.py --ui-host 0.0.0.0     # EXPONE el panel en la red
```

**Aviso:** el panel **no tiene autenticación**. Por defecto escucha en
`127.0.0.1`; con `--ui-host 0.0.0.0` queda accesible desde toda la red. Usalo
sólo en un entorno de pruebas controlado.

### 6.2 Mapa de registros

Espejo del driver real (`backend/control/modbus.py`); si cambiás uno, cambiá el
otro.

- Sensores: dirección base `0`, 16 holding regs contiguos, cada magnitud un
  float32 en 2 regs (`[LSW, MSW]`).
- Consignas: T1 en `100`, velocidad en `104` (2 regs cada una).
- Direcciones base-0. Los huecos (16–99) responden con excepción, como un PLC
  real.

### 6.3 Apuntar la app al simulador

En `config.json`, en **ambas** secciones:

```json
"control": {
    "sensores":  { "driver": "modbus_tcp", "host": "127.0.0.1", "port": 5020 },
    "actuadores": { "driver": "modbus_tcp", "host": "127.0.0.1", "port": 5020 }
}
```

Reiniciá la app después de cambiarlo. También podés hacerlo desde
**Configuración**: al elegir `Modbus TCP (Ethernet)` como driver aparecen los
campos de host y puerto; guardá y reiniciá.

### 6.4 Probar el lazo cerrado

1. Simulador levantado (con panel).
2. App corriendo como `ENTRADA_HORNO` y aprobada.
3. Tiene que haber un **lote abierto**: la ENTRADA lo abre con la primera
   detección `alta` resoluble; si no querés esperar detecciones, abrí el lote
   contra el backend con el `secret` del `device.json`:
   ```bash
   curl -X POST http://localhost:8080/api/v1/lotes/inicio \
     -H "Content-Type: application/json" \
     -H "Authorization: Bearer <secret-de-device.json>" \
     -d '{"idempotency_key":"<uuid>","producto_id":"<uuid-de-producto>","momento":"2026-11-03T12:00:00Z"}'
   ```
4. En el panel del simulador, el badge de conexión debe pasar a **En línea**
   (hay un request Modbus en los últimos 5 s).
5. Cuando la app aplica una consigna, el log del panel muestra una fila con
   origen **MODBUS** (las escrituras desde el panel muestran **UI**).

**Sin lote abierto el ControlService no lee ni escribe Modbus**, así que el panel
queda en "Sin datos": no es una falla de conexión.

Con `--rechazar-setpoints`, las escrituras a los registros 100/104 devuelven
excepción: la app registra el fallo y el read-back no coincide.

---

## 7. Dos instancias en la misma PC/Raspberry (ENTRADA + SALIDA)

Objetivo: correr dos copias de la app, cada una con su propio `config.json`,
`device.json` y `data/`, contra el mismo backend Go y el mismo MediaMTX.

### 7.1 Crear las copias

```bash
cd /home/graso/projects/tesis

rsync -a --exclude .venv --exclude .git --exclude data \
      --exclude __pycache__ --exclude device.json \
      raspberry_front_back_smart-check/ raspberry_entrada/

rsync -a --exclude .venv --exclude .git --exclude data \
      --exclude __pycache__ --exclude device.json \
      raspberry_front_back_smart-check/ raspberry_salida/
```

- `--exclude device.json` evita heredar el secreto de la instancia original. Si
  copiaste sin ese flag, **borrá** `device.json` de cada copia antes de
  arrancar.
- `--exclude data` deja que cada copia cree su propia `data/` (DB de eventos,
  DLQ, audit). No las compartas.
- `models/` y `multimedia/` se copian; `models/` es obligatorio para que el
  loader arranque.

Podés usar el venv original para ambas copias (no hace falta crear uno por
copia). El ROOT de cada instancia se deriva de su propio `backend/config.py`, así
que las rutas relativas apuntan a la copia correcta:

```bash
/home/graso/projects/tesis/raspberry_front_back_smart-check/.venv/bin/python run.py
```

Si preferís venv por copia, repetí `python3 -m venv .venv --system-site-packages`
y `pip install -r requirements.txt` dentro de cada una.

### 7.2 Configuración por instancia

| Ajuste | ENTRADA (`raspberry_entrada/`) | SALIDA (`raspberry_salida/`) |
|---|---|---|
| `device.hostname` | `rb-entrada` | `rb-salida` |
| `device.public_url` | `http://<IP-LAN>:9001` | `http://<IP-LAN>:9000` |
| `device.local_port` | `9001` | `9000` |
| `stream.capture.source` | `t1.mp4` | `t2.mp4` (o cámaras distintas) |
| `control.sensores.driver` | `modbus_tcp` → `127.0.0.1:5020` | `stub` o `simulado` |
| `control.actuadores.driver` | `modbus_tcp` → `127.0.0.1:5020` | `stub` o `simulado` |
| `data/` | propia de la copia | propia de la copia |
| `device.json` | propio, tipo `ENTRADA_HORNO` | propio, tipo `SALIDA_HORNO` |

Reglas:

- **Hostnames únicos.** El backend crea el dispositivo con `nombre = hostname` y
  no admite dos solicitudes `PENDING` para el mismo hostname. Con nombres
  repetidos el alta se pisa o el renombre apunta al dispositivo equivocado.
- **Puertos únicos.** Los números 9000/9001 son intercambiables; lo que importa
  es que no se repitan y que el puerto de `public_url` coincida con
  `local_port`. Con `public_url` apuntando a una IP de la LAN, el bind es
  `0.0.0.0:<puerto>`.
- **Fuentes de video distintas.** `stream.capture.source` numérico es índice de
  cámara (`"0"`); cualquier otro valor es un archivo de
  `multimedia/videos/`. Dos instancias no pueden abrir la misma cámara.
- **SALIDA sin Modbus.** El ControlService sólo corre en ENTRADA; en SALIDA no
  hay lecturas ni escrituras. Aunque dejes `modbus_tcp`, no se emite tráfico,
  pero `stub`/`simulado` deja la intención explícita.
- **Modelos por rol.** Cada copia puede llevar los mismos
  `models.default_entrada_model_id` y `default_salida_model_id`: la app elige
  según el `device.json` de esa copia. Confirmá que cada copia tenga en `models/`
  el modelo y el `.names` de su rol.
- **`<IP-LAN>`**: la IP de esta PC que el backend Go (en Docker) puede alcanzar.
  El `config.json` actual usa `192.168.100.8`; reemplazala por la tuya.
- **Túneles (opcional).** Si en vez de la IP de la LAN usás cloudflared, cada
  instancia necesita su propio túnel y su propia URL:
  `cloudflared tunnel --url http://127.0.0.1:9001` para ENTRADA y
  `cloudflared tunnel --url http://127.0.0.1:9000` para SALIDA. En ese caso el
  bind queda en loopback y `public_url` lleva el dominio `*.trycloudflare.com`.

### 7.2.1 Verificación de modelos y etiquetas

1. `GET /api/v1/productos` devuelve activos `Tostada Común` y `Chalitas`.
2. El `config.json` de ambas copias tiene `etiquetas.clases` y los dos defaults
   por tipo (`models.default_entrada_model_id` / `default_salida_model_id`).
3. En **En vivo** cada instancia muestra el modelo de su rol
   (`entrada-horno-v1` / `salida-horno-v4`).
4. Un lote de prueba: la ENTRADA abre con `tc` (Tostada Común) o `Fajita`
   (Chalitas); la SALIDA reporta `TCOK`/`TCQ`/`CHAOK`/`CHAQ`.
5. Revisá el log por "etiqueta ... sin producto en el catálogo" y por
   "se descarta ... el lote es del producto".

### 7.3 `device.json`: no se copia

Cada copia tiene que registrar **su propio** tipo y recibir la aprobación del
backend:

1. Borrá `device.json` de la copia (si existe).
2. Arrancá la app y andá a **Inicio**.
3. Elegí el tipo (`Entrada de horno` / `Salida de horno`) y apretá
   **Registrar dispositivo**.
4. Esperá la aprobación del Supervisor/Admin en el backend.
5. Verificá sin exponer el secret:
   ```bash
   python -c "import json;d=json.load(open('device.json'));print({k:d.get(k) for k in ('hostname','status','tipo','device_id')})"
   ```

Qué pasa si copiás el `device.json` de una instancia a la otra:

- La copia arranca **APROBADA** con el `secret` y el **tipo del original** (el
  alta no es automática y no se re-registra).
- Si el original era SALIDA, la copia publica `<hostname>-salida` y el
  ControlService no corre, aunque la config diga ENTRADA.
- Si el `hostname` de la copia difiere del guardado, la app además intenta
  renombrar el dispositivo original en el backend (`PUT` con el `secret`
  copiado), porque detecta un renombre pendiente.

La única forma limpia es borrar `device.json` y registrar de nuevo.

### 7.4 Arranque

Orden sugerido:

1. **Backend Go + predictor** (desde el repo del backend):
   ```bash
   cd /home/graso/projects/tesis/backend_smart-check-automation_go
   docker compose up --build
   ```
   Levanta la app (puerto 8080), PostgreSQL y el sidecar `predictor-svc`.
2. **MediaMTX** escuchando en el host:puerto de `stream.publisher.server_url`
   (el config actual apunta a `rtsp://smartcheck.duckdns.org:8554`). Este repo no
   incluye la configuración ni un comando para levantarlo.
3. **Simulador Modbus** (si vas a probar el lazo):
   ```bash
   cd /home/graso/projects/tesis/raspberry_front_back_smart-check
   python tools/simulador_modbus.py
   ```
4. **SALIDA** (terminal 1):
   ```bash
   cd /home/graso/projects/tesis/raspberry_salida
   /home/graso/projects/tesis/raspberry_front_back_smart-check/.venv/bin/python run.py
   ```
5. **ENTRADA** (terminal 2):
   ```bash
   cd /home/graso/projects/tesis/raspberry_entrada
   /home/graso/projects/tesis/raspberry_front_back_smart-check/.venv/bin/python run.py
   ```

Para verificar sin display, prefijá cada arranque con
`QT_QPA_PLATFORM=offscreen` (y usá `timeout` si querés que termine solo).

El ControlService de la ENTRADA **sólo empieza a leer/escribir Modbus cuando
detecta un lote abierto** (`/api/v1/lotes/abierto` o el callback del
`LoteService`). Sin lote, el simulador no muestra "En línea" para esa instancia.

### 7.5 Troubleshooting de la doble instancia

| Síntoma | Causa probable | Solución |
|---|---|---|
| `Address already in use` al arrancar la segunda | Mismo `local_port` o mismo puerto en `public_url` | Puertos distintos (9000/9001) y `public_url`/`local_port` alineados |
| La instancia publica `<hostname>-salida` (p. ej. `rb-1-salida`) | `device.json` copiado desde la SALIDA (el tipo persistido manda) | Borrar `device.json` y registrar como ENTRADA |
| `SALIDA_HORNO` no habla Modbus | Comportamiento esperado: el ControlService sólo corre en ENTRADA | No es un bug; dejá `stub`/`simulado` |
| La captura falla con `source: "0"` | No hay cámara 0 en la PC | Usar `t1.mp4` / `t2.mp4` (archivos en `multimedia/videos/`) |
| Falta `t3.mp4` después de clonar | Está ignorado por tamaño (uso local) | Usar `t1.mp4`/`t2.mp4` o copiar el archivo a mano |
| Alta rechazada o renombre al dispositivo equivocado | Hostname repetido o solicitud `PENDING` previa | Hostnames únicos; revisar/olvidar el registro en la UI |
| Ambas instancias publican al mismo path de MediaMTX | Hostnames iguales o `device.json` copiado | Paths `<hostname>-entrada` / `<hostname>-salida` con nombres únicos |
| El panel del simulador dice "Sin datos" | No hay lote abierto (o la app es SALIDA) | Abrir lote y usar la instancia ENTRADA |

---

## 8. Tests

`pytest` **no** está en `requirements.txt`; la CI lo instala aparte. Para correr
los tests del backend:

```bash
source .venv/bin/activate
pip install pytest
python -m pytest tests/backend -q
```

Los tests importan `backend.streaming`, que importa `cv2` a nivel de módulo; por
eso el CI instala `opencv-python-headless` sólo en el job de tests. En la Pi,
`cv2` viene del sistema y no se instala por pip. El repo también tiene
`tests/frontend/`; este manual no cubre esa suite.

Smoke del simulador (sin PLC):

```bash
python tools/simulador_modbus.py &
sleep 1
curl -s http://127.0.0.1:8091/api/estado
kill %1
```

`/api/estado` devuelve el snapshot de la planta (sensores, consignas,
parámetros, `ultimo_request`, log y `modbus` con host/puerto). Con `--sin-ui` el
panel no arranca y esa ruta no existe.

---

## 9. Seguridad y operación

- **`device.json` no se commitea ni se comparte.** Contiene el `secret` del
  dispositivo y el `rename_token`; se escribe con `0600`. Está ignorado por git
  (`/device*.json`).
- **El panel del simulador no tiene autenticación.** Dejalo en `127.0.0.1`; no
  uses `--ui-host 0.0.0.0` fuera de un entorno de pruebas aislado.
- **`public_url` / cloudflared**: la URL pública es la que el backend Go usa
  para el push de renombre. Con quick tunnel cambia en cada reinicio de
  `cloudflared`; actualizala (Configuración la sincroniza en caliente si la Pi
  ya está aprobada) o volvé a registrar. No expongas el puerto local
  más allá de lo necesario: con una URL de túnel, el bind queda en loopback.
  Si `public_url` está vacía y `listen_address` es `0.0.0.0`, el servidor de
  renombre escucha en toda la LAN (el handler exige `rename_token`, pero el
  socket queda abierto).
- **`config.json`**: se versiona, no lleva secretos. La escritura es atómica y
  serializada por `LOCK_CONFIG`; el `.gitignore` cubre los temporales de
  escritura (`*.tmp`).
- **Alta aprobada**: sin aprobación del Supervisor/Admin no hay `secret` y la Pi
  no puede autenticar ping, lotes ni telemetría.
- **Dos instancias**: cada una con su `device.json`, su `data/` y su puerto. No
  copies credenciales entre copias.

---

## 10. Troubleshooting general

| Síntoma | Causa / solución |
|---|---|
| `Error de configuración: El directorio de modelos no existe: ...` | Falta `models/` o `paths.models_dir` apunta mal. Crealo o corregí la ruta |
| `Error de configuración: JSON inválido en ...` | `config.json` mal formado; la app sale con código 1 |
| `No se puede registrar: device.public_url está vacío...` | Completá `device.public_url` (túnel o IP de la LAN) y reiniciá |
| `import cv2` falla | OpenCV no está a nivel sistema o el venv se creó sin `--system-site-packages` |
| `import hailo_platform` falla | Falta el paquete de Hailo; la NPU no está disponible, la app sigue con CPU |
| `Address already in use` en 9000/9001 | Otra instancia u otro proceso ocupa el puerto; cambiá `public_url`/`local_port` |
| No se ve el stream publicado | Sin alta no hay tipo y no se publica; verificá el registro y que MediaMTX escuche en `server_url` |
| La captura no abre | `source: "0"` sin cámara, o el archivo no existe en `multimedia/videos/` |
| `ffmpeg` no arranca / reinicia el publisher | `ffmpeg` no está en `PATH` o no coincide con `stream.publisher.ffmpeg_executable` |
| El backend no responde | Revisá `api.base_url`, que el Go esté arriba (`curl http://localhost:8080/health`) y la red |
| Un modelo falla al elegirse por archivo ausente | Revisá que el `file` del catálogo exista en `models/` |
| El control no publica telemetría | `control.habilitado = false`, no hay lote abierto, o la instancia es SALIDA |
| El simulador no muestra "En línea" | No hubo request Modbus en 5 s: sin lote abierto el ControlService no lee |
