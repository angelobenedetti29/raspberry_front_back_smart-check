# Instrucciones de Compilación de Modelo YOLOv11 a Hailo HEF (Otro PC)

Este documento te guía en el proceso de compilar tu modelo personalizado de tostadas (`tostadas_v2.onnx`) a formato `.hef` para usar en tu Raspberry Pi con el Hat de IA. 

Como elegiste compilarlo en otra PC (que tiene Linux Ubuntu o WSL2 con el compilador de Hailo), sigue estos pasos:

---

## 1. Archivos Requeridos en la PC de Compilación

Debes copiar los siguientes tres archivos desde esta computadora hacia la PC donde vayas a compilar:

1. **El Modelo ONNX:**
   `ai_training/models/tostadas_v2.onnx`
2. **El Dataset de Calibración pre-procesado:**
   `ai_training/models/calib_dataset.npy` (Ya lo generamos en tu carpeta local con éxito).
3. **El Script de Compilación:**
   `ai_training/scripts/compile_hailo.py`

*Nota: Para mantener las rutas relativas correctas, te recomendamos crear una carpeta llamada `ai_training` en la PC de compilación con la siguiente estructura:*
```text
ai_training/
├── models/
│   ├── tostadas_v2.onnx
│   └── calib_dataset.npy
└── scripts/
    └── compile_hailo.py
```

---

## 2. Preparación del Entorno (En la PC de Compilación)

En la terminal de la PC de compilación (Linux Ubuntu x86_64), sigue estos pasos para instalar el entorno de Hailo:

1. **Actualizar el sistema e instalar dependencias de Python:**
   ```bash
   sudo apt update && sudo apt upgrade -y
   sudo apt install python3-pip python3-virtualenv python3-tk -y
   ```

2. **Crear y activar un entorno virtual con Python 3.10:**
   *(El compilador de Hailo requiere estrictamente Python 3.10).*
   ```bash
   virtualenv -p python3.10 hailo_env
   source hailo_env/bin/activate
   ```

3. **Instalar el software de Hailo:**
   Debes instalar el paquete `.whl` del compilador de Hailo (Dataflow Compiler) que descargaste de la Developer Zone de Hailo:
   ```bash
   pip install path/a/tu/descarga/hailo_dataflow_compiler-X.Y.Z-py3-none-linux_x86_64.whl
   ```

---

## 3. Ejecutar la Compilación

Una vez activado el entorno virtual (`hailo_env`) y posicionado en la carpeta raíz `ai_training/`:

1. **Ejecutar el script compilador:**
   ```bash
   python scripts/compile_hailo.py
   ```

2. **Resultado esperado:**
   El script realizará de forma automática:
   * El parseo del modelo ONNX a HAR (`tostadas_v2.har`).
   * La optimización y cuantización a INT8 usando tu `calib_dataset.npy` (`tostadas_v2_quantized.har`).
   * La compilación final a HEF para Hailo-8L.

   Al finalizar, verás un mensaje de éxito y se habrá creado tu modelo compilado en:
   `ai_training/models/tostadas_v2.hef`

---

## 4. Retornar el archivo .hef a este Proyecto

1. Copia el archivo generado **`tostadas_v2.hef`** de regreso a esta PC.
2. Colócalo exactamente en la carpeta:
   `ai_training/models/tostadas_v2.hef`

*Gracias a los cambios que haremos a continuación en el backend de tu aplicación, en cuanto el archivo `tostadas_v2.hef` esté en esa carpeta y corras la aplicación en tu Raspberry Pi (donde sí está instalado el entorno Hailo), se ejecutará de manera automática y a máxima velocidad en la NPU.*
