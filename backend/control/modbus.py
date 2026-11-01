"""Cliente Modbus para sensores y actuadores del horno (SCA-400).

Wrapper sobre `pymodbus[serial]` (puro Python, instalable por pip).
Encapsula el cliente subyacente (RTU/TCP), expone operaciones batch con
verificación por lectura de retorno y maneja la reconexión sin propagar
excepciones al `ControlService`, que ya tiene su propia política de error
con backoff.

Notas de diseño:

- **Singleton por proceso**: hay un `ClienteModbus` por periférico (sensores y
  actuadores). Pueden apuntar al mismo PLC o a PLCs distintos según
  `config.control.sensores` y `config.control.actuadores`.
- **Lock explícito**: pymodbus es thread-safe pero la secuencia leer-leer no
  lo es sin sincronización. Tomamos el lock en cada operación.
- **No lanza**: `leer_bloque` y `escribir_verificado` devuelven `None`/`False`
  ante cualquier fallo (red, error Modbus, timeout). El caller decide si
  reintenta o degrada.
- **Mapa de registros centralizado**: las direcciones y tipos viven en
  constantes de módulo. Editar acá cuando llegue el PLC real; el resto del
  código no debería tener offsets mágicos.

Para no acoplar el control al resto de la app, este cliente vive en su
propio módulo y sólo se instancia cuando `cfg.driver != "stub"`. Con el
default `stub` el módulo entero no se importa en runtime.
"""

from __future__ import annotations

import logging
import threading
from typing import Sequence

from pymodbus.client import ModbusSerialClient, ModbusTcpClient
from pymodbus.exceptions import ModbusException

from backend.config import ModbusDriverConfig

logger = logging.getLogger(__name__)

# --- Mapa de registros (PLC genérico — ajustar cuando llegue el real) ------
#
# Todas las direcciones son base-0 (convención Modbus; restar 1 si la
# documentación del PLC usa base-1). Cada float32 son 2 holding regs.
# Los offsets dentro del bloque de sensores son **en pares de registros**.

DIRECCION_BASE_SENSORES = 0
# Bloque de lectura de sensores: 16 regs contiguos (los offsets 2 y 6 quedan
# sin usar, pero no se renumeran para no romper el PLC).
CANTIDAD_REGS_SENSORES = 16

# Offset dentro del bloque de sensores (en regs). Cada entrada es 1 float32.
_OFFSET_SENSORES: dict[str, int] = {
    "temp_horno_1": 0,
    "temp_comb_1": 4,
    "humedad_horno": 8,
    "temp_ambiente": 10,
    "humedad_ambiente": 12,
    "velocidad_cinta": 14,
}

# Direcciones del actuador (no son contiguas: cada setpoint es 2 regs).
DIRECCION_TEMP1_OBJETIVO = 100
DIRECCION_VEL_OBJETIVO = 104

# --- Tiempos ---------------------------------------------------------------
# 1 s y 1 reintenca son chicos a propósito: la Pi polea cada 3-5 s y un
# timeout mayor sólo escondería un PLC muerto. Si el backpressure es real,
# se baja resolución o fps, no se sube este número (ver AGENTS.md).
_TIMEOUT_SEGUNDOS = 1.0
_REINTENTOS = 1


class ClienteModbus:
    """Cliente singleton con reconexión automática.

    Un `ClienteModbus` por periférico (sensores y actuadores). El lock
    interno serializa las transacciones; el cliente pymodbus subyacente
    mantiene su propia conexión y reconecta sólo si se cae.
    """

    def __init__(self, cfg: ModbusDriverConfig) -> None:
        self._cfg = cfg
        self._lock = threading.Lock()
        self._cliente = self._construir_cliente()
        self._unit_id = cfg.unit_id

    def _construir_cliente(self):
        if self._cfg.driver == "modbus_tcp":
            assert self._cfg.host is not None
            assert self._cfg.port is not None
            return ModbusTcpClient(
                host=self._cfg.host,
                port=self._cfg.port,
                timeout=_TIMEOUT_SEGUNDOS,
                retries=_REINTENTOS,
            )
        # modbus_rtu
        assert self._cfg.serial_port is not None
        return ModbusSerialClient(
            port=self._cfg.serial_port,
            baudrate=self._cfg.baudrate,
            timeout=_TIMEOUT_SEGUNDOS,
            retries=_REINTENTOS,
        )

    def _asegurar(self) -> bool:
        if self._cliente.connect():
            return True
        logger.warning("modbus: no se pudo conectar (%s)", self._cfg)
        return False

    def leer_bloque(
        self, direccion: int, cantidad: int, unit: int | None = None
    ) -> list[int] | None:
        """Lee `cantidad` holding registers contiguos desde `direccion`.

        Devuelve `None` ante cualquier fallo. Nunca propaga `ModbusException`
        ni `OSError` — el caller hace backoff.
        """
        device = unit if unit is not None else self._unit_id
        with self._lock:
            try:
                if not self._asegurar():
                    return None
                resultado = self._cliente.read_holding_registers(
                    address=direccion,
                    count=cantidad,
                    device_id=device,
                )
                if resultado.isError():
                    logger.warning(
                        "modbus: error de lectura addr=%d count=%d: %s",
                        direccion,
                        cantidad,
                        resultado,
                    )
                    return None
                return list(resultado.registers)
            except (ModbusException, OSError) as exc:
                logger.warning("modbus: excepción en lectura: %s", exc)
                return None

    def escribir_verificado(
        self,
        direccion: int,
        valores: Sequence[int],
        unit: int | None = None,
    ) -> bool:
        """Escribe `valores` y luego relee para confirmar la escritura.

        Devuelve `True` sólo si la lectura de retorno coincide exactamente
        con lo escrito. `False` en cualquier otro caso (sin lanzar).
        """
        device = unit if unit is not None else self._unit_id
        valores_lista = list(valores)
        with self._lock:
            try:
                if not self._asegurar():
                    return False
                resultado = self._cliente.write_registers(
                    address=direccion,
                    values=valores_lista,
                    device_id=device,
                )
                if resultado.isError():
                    logger.warning(
                        "modbus: error de escritura addr=%d: %s",
                        direccion,
                        resultado,
                    )
                    return False
                rb = self._cliente.read_holding_registers(
                    address=direccion,
                    count=len(valores_lista),
                    device_id=device,
                )
                if rb.isError():
                    return False
                return list(rb.registers) == valores_lista
            except (ModbusException, OSError) as exc:
                logger.warning("modbus: excepción en escritura: %s", exc)
                return False

    def convertir_float32(self, regs: Sequence[int]) -> float | None:
        """Decodifica 2 holding registers contiguos como float32 LE.

        `DataType` vive como atributo de clase en `ModbusClientMixin` desde
        pymodbus 3.7 (antes estaba en `pymodbus.constants`). Word order
        explicito en `'little'` para mantener la convención previa al cambio
        de default de pymodbus 3.15.
        """
        if len(regs) != 2:
            return None
        try:
            valor = self._cliente.convert_from_registers(
                list(regs),
                data_type=self._cliente.DATATYPE.FLOAT32,
                word_order="little",
            )
            return float(valor)
        except (ModbusException, ValueError, TypeError) as exc:
            logger.warning("modbus: decode float32 falló: %s", exc)
            return None

    def codificar_float32(self, valor: float) -> list[int]:
        """Codifica un float32 a 2 holding registers LE."""
        try:
            return list(
                self._cliente.convert_to_registers(
                    valor,
                    data_type=self._cliente.DATATYPE.FLOAT32,
                    word_order="little",
                )
            )
        except (ModbusException, ValueError, TypeError) as exc:
            logger.warning("modbus: encode float32 falló: %s", exc)
            return []

    def cerrar(self) -> None:
        """Cierra el transporte subyacente. Idempotente."""
        with self._lock:
            try:
                self._cliente.close()
            except Exception:  # noqa: BLE001 - cierre defensivo
                logger.exception("modbus: error al cerrar")