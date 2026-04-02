"""CRC helpers for Modbus."""
from __future__ import annotations


def crc16_modbus(data: bytes) -> int:
    """Compute Modbus RTU CRC (little-endian)."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x0001:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc & 0xFFFF
