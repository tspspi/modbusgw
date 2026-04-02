
"""Helpers for assembling/disassembling Modbus ADUs."""
from __future__ import annotations

from typing import Tuple


def crc16_modbus(data: bytes) -> int:
    """Compute the Modbus RTU CRC."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            lsb = crc & 0x0001
            crc >>= 1
            if lsb:
                crc ^= 0xA001
    return crc & 0xFFFF


def build_rtu_frame(adu: bytes) -> bytes:
    """Append CRC to an ADU for RTU transmission."""
    crc = crc16_modbus(adu)
    return adu + crc.to_bytes(2, byteorder='little')


def strip_rtu_frame(frame: bytes) -> bytes:
    """Validate CRC and remove it, yielding the ADU payload."""
    if len(frame) < 4:
        raise ValueError('RTU frame too short')
    body, crc_bytes = frame[:-2], frame[-2:]
    crc_expected = int.from_bytes(crc_bytes, byteorder='little')
    if crc_expected != crc16_modbus(body):
        raise ValueError('RTU CRC mismatch')
    return body


def build_mbap_frame(transaction_id: int, adu: bytes) -> bytes:
    """Wrap an ADU inside a Modbus/TCP MBAP header."""
    if len(adu) < 2:
        raise ValueError('ADU must contain unit id and function')
    unit_id = adu[0]
    pdu = adu[1:]
    length = len(pdu) + 1  # unit id + pdu
    header = (
        transaction_id.to_bytes(2, 'big')
        + b"\x00\x00"
        + length.to_bytes(2, 'big')
        + bytes([unit_id])
    )
    return header + pdu


def parse_mbap_frame(header: bytes, pdu: bytes) -> Tuple[int, bytes]:
    """Return transaction id and reconstructed ADU from MBAP components."""
    if len(header) != 7:
        raise ValueError('MBAP header must be 7 bytes')
    transaction_id = int.from_bytes(header[0:2], 'big')
    protocol_id = int.from_bytes(header[2:4], 'big')
    if protocol_id != 0:
        raise ValueError('Unsupported protocol id')
    length = int.from_bytes(header[4:6], 'big')
    unit_id = header[6]
    if length - 1 != len(pdu):
        raise ValueError('MBAP length mismatch')
    return transaction_id, bytes([unit_id]) + pdu
