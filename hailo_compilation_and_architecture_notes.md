# Lecciones Aprendidas y Lecciones de Arquitectura (Hailo-8L & DFC)

Este documento resume los conocimientos clave y las decisiones de diseño de software adquiridos durante la resolución de los problemas de compilación e inicialización del modelo de tostadas con la NPU Hailo-8L.

---

## 1. Gestión de Recursos Físicos de la NPU (Hardware Lock)

### El problema (`HAILO_OUT_OF_PHYSICAL_DEVICES` / error 74)
La NPU física de Hailo (`/dev/hailo0` en Linux) es un recurso exclusivo. El driver de HailoRT no permite que múltiples contextos de dispositivo (`VDevice`) controlen el chip al mismo tiempo de forma nativa. 

### Solución de Arquitectura
1. **Carga Perezosa (Lazy Loading):** En sistemas compuestos (como tener una API de FastAPI de fondo y una aplicación GUI de PySide6 al frente ejecutándose en la misma máquina), **ningún componente secundario debe inicializar el detector al arrancar**. Al implementar un proxy perezoso (`LazyYoloDetector`), la API levanta de forma instantánea sin tocar el driver, dejando la NPU libre para que la GUI tome el control exclusivo del hardware.
2. **Ciclo de Vida del Garbage Collector en Python:** Reasignar una variable directamente (`self.detector = YoloDetector(...)`) evalúa primero el constructor del nuevo objeto (que intenta abrir la NPU) antes de destruir el objeto anterior (que aún retiene la NPU). Para evitar conflictos en el mismo proceso al cambiar de modelo, se debe llamar explícitamente a un método de liberación de recursos (`release_hailo()`) y limpiar la referencia asignándola a `None` antes de instanciar el nuevo detector.

---

## 2. Ciclo de Vida en el Compilador de Hailo (DFC)

### El problema (HEF sin NMS tras compilar)
El archivo model script (`.alls`) que contiene la directiva `nms_postprocess` no puede ser aplicado tardíamente. Si el model script se proporciona únicamente al comando CLI `hailo compiler` en el paso final, el compilador procesa un archivo `.har` que ya fue optimizado y cuantizado con una estructura fija, ignorando la directiva de NMS y exponiendo las 6 salidas crudas del modelo.

### Solución de Compilación
Las directivas del model script que modifican la estructura del grafo del modelo (como `normalization` y `nms_postprocess`) **deben cargarse en el runner antes del paso de optimización/calibración**.
```python
runner = ClientRunner(har=har_path)
runner.load_model_script(alls_path) # <- Cargar aquí
runner.optimize(calib_data)        # <- La optimización se aplica sobre la estructura modificada
```

---

## 3. Estructura y Mapeo del JSON de NMS para YOLOv8

### Clave Obligatoria (`bbox_decoders`)
El parser de scripts de Hailo DFC para la arquitectura YOLOv8 busca estrictamente la clave raíz `bbox_decoders` para procesar la decodificación de las cajas de regresión de forma ordenada por escalas. Un formato plano (donde las capas de regresión y clasificación se definan en listas planas en la raíz) arrojará un error de clave (`KeyError: 'bbox_decoders'`).

### Selección de Nodos: Salidas vs. Capas de Convolución
Al definir el JSON de NMS, las capas de regresión y clasificación no deben apuntar a las capas de salida finales de la red (`output_layer1` a `output_layer6`), sino a las **capas de convolución internas inmediatamente anteriores** (`conv51`, `conv54`, etc.):
* El parser de NMS busca estas capas internas y verifica que tengan exactamente **un nodo de salida** (el cual es la capa de formato o reshape generada automáticamente durante el parsing). 
* Si se apunta directamente a los nodos terminales (`output_layer1`), el compilador fallará con la excepción `NMSConfigPostprocessException` indicando que la capa de salida no posee a su vez otra capa de salida.

---

## 4. Limitaciones de Arquitectura de Hardware (Hailo-8 vs Hailo-8L)

### El problema (Fallo de compilación con `engine=nn_core`)
Intentar forzar el procesamiento del NMS de YOLOv8 dentro de la NPU (`engine=nn_core` o `engine=nn`) en un chip **Hailo-8L** (el procesador incluido en el Raspberry Pi AI Kit) arrojará el error `UnsupportedMetaArchError: The specified meta architecture yolov8 cannot be run on chip`.

### Solución
El NMS en hardware para YOLOv8 no está soportado en la arquitectura simplificada del chip Hailo-8L (a diferencia del chip Hailo-8 estándar). La compilación debe realizarse usando **`engine=cpu`**. 
Al compilar con `engine=cpu`, el compilador de Hailo genera la metadata correspondiente dentro del archivo `.hef` para que la librería HailoRT en el dispositivo host intercepte los tensores del chip y ejecute el NMS de forma transparente y optimizada utilizando la CPU de la Raspberry Pi, entregando a la aplicación de Python un único stream final unificado.
