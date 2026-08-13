"""
Chell nanoDAQ-LT / nanoDAQ-LTS TCP client helper.

Implements the framing used by the nanoDAQ-LT user command protocol
(see the "nanoDAQ-LT User Programming Guide"):

    '>' (0x3E) + command + parameter + parity + '<' (0x3C)

The device listens as a TCP *server* on port 101 and only accepts a
single connection, so this client always connects out to the device
(TCP client / "TCP-C").
"""

from __future__ import annotations

import socket
from dataclasses import dataclass
from enum import IntEnum
from typing import Iterator

START = 0x3E  # '>'
END = 0x3C  # '<'
ACK_POS = b"**"
ACK_NEG = b"!!"

HEADER_16BIT = bytes((0x00, 0xFF, 0x00))


class Channel(IntEnum):
    TCP_UDP = 1
    CAN = 2


class Protocol(IntEnum):
    BINARY_LE = 0
    BINARY_BE = 1
    ENGINEERING_UNITS = 2


class Command(IntEnum):
    STANDBY = ord("S")
    RESET = ord("R")
    REZERO = ord("Z")
    RATE = ord("V")
    INPUT_FILTERS = ord("F")
    PROTOCOL = ord("P")
    STREAM_ON = ord("1")
    STREAM_OFF = ord("0")
    GET_STATUS = ord("?")
    POLL = ord("O")
    HARDWARE_TRIGGER = ord("T")
    DATASTREAM_TIMESTAMP = ord("t")
    PRESSURE_TYPE = ord("a")
    BURN_TO_EEPROM = ord("e")


def block_parity(payload: bytes) -> int:
    """Even block parity across all bytes (bitwise XOR of every byte)."""
    parity = 0
    for b in payload:
        parity ^= b
    return parity


def build_command(command: int, parameter: int = 0x00) -> bytes:
    """Build a `> command parameter parity <` frame, computing parity."""
    body = bytes((START, command & 0xFF, parameter & 0xFF, END))
    # parity is computed over start+command+parameter+end, then inserted
    # before the end delimiter.
    parity = block_parity(body)
    return bytes((START, command & 0xFF, parameter & 0xFF, parity, END))


@dataclass
class ChannelData:
    header_ok: bool
    values: list[int]


def parse_binary_packet(data: bytes, num_channels: int) -> ChannelData | None:
    """Parse one `00 FF 00 <ch1 LSB> <ch1 MSB> ...` packet (16-bit LE)."""
    if len(data) < 3 + num_channels * 2:
        return None
    if data[:3] != HEADER_16BIT:
        return None
    values = []
    offset = 3
    for _ in range(num_channels):
        lsb, msb = data[offset], data[offset + 1]
        values.append(lsb | (msb << 8))
        offset += 2
    return ChannelData(header_ok=True, values=values)


class NanoDAQClient:
    """Minimal TCP client for a Chell nanoDAQ-LT / nanoDAQ-LTS unit."""

    def __init__(self, ip: str, port: int = 101, timeout: float = 5.0):
        self.ip = ip
        self.port = port
        self.timeout = timeout
        self._sock: socket.socket | None = None

    def connect(self) -> None:
        self._sock = socket.create_connection((self.ip, self.port), timeout=self.timeout)

    def close(self) -> None:
        if self._sock is not None:
            self._sock.close()
            self._sock = None

    def __enter__(self) -> "NanoDAQClient":
        self.connect()
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def _require_socket(self) -> socket.socket:
        if self._sock is None:
            raise RuntimeError("not connected - call connect() first")
        return self._sock

    def send_command(self, command: int, parameter: int = 0x00, read_ack: bool = True) -> bool | None:
        """Send a command frame; returns True/False for ack, None if not requested."""
        sock = self._require_socket()
        sock.sendall(build_command(command, parameter))
        if not read_ack:
            return None
        ack = sock.recv(2)
        if ack == ACK_POS:
            return True
        if ack == ACK_NEG:
            return False
        raise RuntimeError(f"unexpected ack bytes: {ack!r}")

    def stream_on(self, channel: Channel = Channel.TCP_UDP) -> bool | None:
        return self.send_command(Command.STREAM_ON, int(channel))

    def stream_off(self, channel: Channel = Channel.TCP_UDP) -> bool | None:
        return self.send_command(Command.STREAM_OFF, int(channel))

    def set_protocol(self, protocol: Protocol, channel: Channel = Channel.TCP_UDP) -> bool | None:
        # parameter byte = 0xab, a = channel (1=TCP/UDP, 2=CAN), b = protocol
        parameter = (int(channel) << 4) | int(protocol)
        return self.send_command(Command.PROTOCOL, parameter)

    def rezero(self) -> bool | None:
        return self.send_command(Command.REZERO)

    def read_raw(self, bufsize: int = 4096) -> bytes:
        sock = self._require_socket()
        return sock.recv(bufsize)

    def iter_binary_packets(self, num_channels: int) -> Iterator[ChannelData]:
        """Read the TCP stream and yield decoded 16-bit LE packets.

        This is a simple/best-effort reader: it does not resync mid-stream
        if a partial packet is split across TCP reads. For production use,
        buffer bytes and scan for the `00 FF 00` header before decoding.
        """
        packet_size = 3 + num_channels * 2
        buf = b""
        while True:
            buf += self.read_raw()
            while len(buf) >= packet_size:
                chunk, buf = buf[:packet_size], buf[packet_size:]
                parsed = parse_binary_packet(chunk, num_channels)
                if parsed is not None:
                    yield parsed
