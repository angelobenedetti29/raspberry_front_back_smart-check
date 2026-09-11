from dataclasses import dataclass

@dataclass
class IoTDevice:
    id: str
    name: str
    is_on: bool
    type: str = "relay"
