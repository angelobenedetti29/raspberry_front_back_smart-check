import sys
import os

# Asegurar que el path del proyecto esté en el PYTHONPATH
sys.path.append(r"c:\Users\UsuarioCaja\Desktop\Proyectos\raspberry_front_back_smart-check")

# Inicializar una aplicación de Qt básica para poder usar QObjects y Signals
from PySide6.QtCore import QCoreApplication
app = QCoreApplication(sys.argv)

from frontend.main import YOLODetectionThread

class MockDetector:
    def get_class_names(self):
        return ["TCOK", "TCQ"]

class MockUseCase:
    def __init__(self):
        self.detector = MockDetector()

# Crear el hilo
use_case = MockUseCase()
thread = YOLODetectionThread("road.mp4", use_case)

# Simular que se detectaron tostadas
thread.seen_toasts[1] = "ok"
thread.seen_toasts[2] = "ok"
thread.seen_toasts[3] = "burnt"
thread.seen_toasts[4] = "ok"
thread.seen_toasts[5] = "burnt"
thread.seen_toasts[6] = "ok"
thread.seen_toasts[7] = "ok"
thread.seen_toasts[8] = "ok"
thread.seen_toasts[9] = "ok"
thread.seen_toasts[10] = "ok"

# Simular lecturas de sensores
import random
for i in range(100):
    thread.temperatures_horno1.append(220.0 + random.uniform(-1.5, 1.5))
    thread.temperatures_comb1.append(315.0 + random.uniform(-2.0, 2.0))
    thread.temperatures_horno2.append(218.0 + random.uniform(-1.5, 1.5))
    thread.temperatures_comb2.append(312.0 + random.uniform(-2.0, 2.0))
    thread.velocidades_cinta.append(1.10 + random.uniform(-0.05, 0.05))

# Conectar la señal a una función de verificación
received_payload = {}
def on_lote_completed(payload):
    global received_payload
    received_payload = payload
    print("\n[TEST] ¡Señal lote_completed_signal recibida!")
    print("[TEST] Payload generado:")
    for k, v in payload.items():
        print(f"  - {k}: {v}")

thread.lote_completed_signal.connect(on_lote_completed)

# Ejecutar el emisor
print("Llamando a emit_batch_metrics()...")
thread.emit_batch_metrics()

# Validaciones sobre el payload recibido
assert received_payload["productoId"] == "a1b2c3d4-5678-90ab-cdef-1234567890ab"
assert received_payload["quemados"] == 2
# correctos debe ser menor o igual a 8 debido a la simulación de crudas
assert received_payload["correctos"] + received_payload["quemados"] + received_payload["crudas"] == received_payload["totalUnidades"]
assert received_payload["totalUnidades"] == 10
assert abs(received_payload["tempHorno1"] - 220.0) < 2.0
assert abs(received_payload["velocidadCinta"] - 1.10) < 0.1

print("\n[OK] Todas las aserciones pasaron con éxito. Las métricas del frontend son correctas y estables.")

