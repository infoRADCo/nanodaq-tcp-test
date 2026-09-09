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

import re
import socket
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Iterator

START = 0x3E  # '>'
END = 0x3C  # '<'
ACK_POS = b"**"
ACK_NEG = b"!!"

HEADER_16BIT = bytes((0x00, 0xFF, 0x00))

# Status responses (section 3 of the User Programming Guide) use a
# different framing to command acks: `>` + 2 raw status bytes + `<`,
# optionally followed by plain-ASCII comma separated fields.
STATUS_HEADER_LEN = 4

_FIELD_RE = re.compile(r"\[([^\]]+)\]\s*([^,\[]*)")


class Channel(IntEnum):
    TCP_UDP = 1
    CAN = 2


class Protocol(IntEnum):
    BINARY_LE = 0
    BINARY_BE = 1
    ENGINEERING_UNITS = 2


class DataRate(IntEnum):
    """Rate command 'b' nibble (section 2.3 of the User Programming Guide)."""

    OFF = 0
    HZ_200 = 7
    HZ_150 = 8
    HZ_100 = 9
    HZ_50 = 10
    HZ_25 = 11
    HZ_20 = 12
    HZ_10 = 13
    HZ_5 = 14
    HZ_1 = 15


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


class StatusLevel(IntEnum):
    SHORT = 0
    WITH_TEMP = 1
    FULL = 2
    RAW_PRESSURE = 3
    RAW_TEMP = 4


@dataclass
class ChannelData:
    header_ok: bool
    values: list[int]


@dataclass
class StatusResult:
    status_word: int
    temperatures: list[float] = field(default_factory=list)
    fields: dict[str, str] = field(default_factory=dict)


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


def scale_differential(raw: int, full_scale: float) -> float:
    """Convert a raw 16-bit differential reading to +/-full_scale units."""
    return (raw / 65535.0) * 2.0 * full_scale - full_scale


def parse_status_trailer(trailer: bytes, level: "StatusLevel") -> tuple[list[float], dict[str, str]]:
    """Parse the ASCII text following the 4-byte status header.

    - WITH_TEMP: a plain comma-separated list of temperatures.
    - FULL: temperatures first, then `[Field] value` comma-separated entries.

    Best-effort only; nanoDAQ firmware versions may vary the exact field
    set/order (see the note in section 3 of the User Programming Guide).
    """
    text = trailer.decode("ascii", errors="replace")
    fields = {name.strip(): value.strip() for name, value in _FIELD_RE.findall(text)}

    # Whatever precedes the first '[' (if any) is the temperature list.
    temp_part = text.split("[", 1)[0]
    temperatures: list[float] = []
    for token in temp_part.split(","):
        token = token.strip()
        if not token:
            continue
        try:
            temperatures.append(float(token))
        except ValueError:
            pass  # not a temperature reading (e.g. stray status chars)

    if level == StatusLevel.SHORT:
        temperatures = []
    return temperatures, fields


class NanoDAQClient:
    """Minimal TCP client for a Chell nanoDAQ-LT / nanoDAQ-LTS unit."""

    def __init__(self, ip: str, port: int = 101, timeout: float = 5.0):
        self.ip = ip
        self.port = port
        self.timeout = timeout
        self._sock: socket.socket | None = None

    def connect(self) -> None:
        self._sock = socket.create_connection((self.ip, self.port), timeout=self.timeout)

    def set_timeout(self, timeout: float | None) -> None:
        self._require_socket().settimeout(timeout)

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

    def _recv_exact(self, n: int) -> bytes:
        """Read exactly n bytes. Plain recv(n) may return fewer bytes than
        requested for a TCP stream - this loops until n bytes are collected
        (or the connection closes)."""
        sock = self._require_socket()
        data = b""
        while len(data) < n:
            chunk = sock.recv(n - len(data))
            if not chunk:
                raise ConnectionError("socket closed while waiting for data")
            data += chunk
        return data

    def flush_input(self, timeout: float = 0.3) -> bytes:
        """Discard any bytes already buffered/in-flight (e.g. leftover from
        a stream the device was still sending when we connected). Should be
        called right after connect(), before sending the first command.
        """
        sock = self._require_socket()
        old_timeout = sock.gettimeout()
        sock.settimeout(timeout)
        discarded = b""
        try:
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                discarded += chunk
        except socket.timeout:
            pass
        finally:
            sock.settimeout(old_timeout)
        return discarded

    def send_command(self, command: int, parameter: int = 0x00, read_ack: bool = True) -> bool | None:
        """Send a command frame; returns True/False for ack, None if not requested."""
        sock = self._require_socket()
        sock.sendall(build_command(command, parameter))
        if not read_ack:
            return None
        ack = self._recv_exact(2)
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

    def set_rate(self, rate: DataRate, channel: Channel = Channel.TCP_UDP) -> bool | None:
        # The User Programming Guide (section 2.3) documents the channel
        # nibble as 4=TCP/UDP, 8=CAN for this command specifically, but the
        # real hardware (confirmed against a live nanoDAQ-LTS-16) actually
        # expects the SAME channel encoding as the Protocol command
        # (1=TCP/UDP, 2=CAN) - a=4/a=8 is silently NACKed. Use Channel's
        # own values here, not a doc-literal 4/8 shift.
        parameter = (int(channel) << 4) | int(rate)
        return self.send_command(Command.RATE, parameter)

    def rezero(self) -> bool | None:
        return self.send_command(Command.REZERO)

    def get_status(self, level: StatusLevel = StatusLevel.SHORT, read_timeout: float = 1.0) -> StatusResult:
        """Send Get Status and read back the status frame (section 3 of the guide).

        Unlike the other user commands, Get Status does NOT appear to send a
        separate `**`/`!!` ack first - the status frame itself (`>` + 2 raw
        bytes + `<`) is the response, confirmed against the real device
        (observed first bytes back were `>\\x00`, i.e. the start of the
        short-status header, not an ack).
        """
        sock = self._require_socket()
        sock.sendall(build_command(Command.GET_STATUS, int(level)))

        # Read the fixed 4-byte header first.
        header = self._recv_exact(STATUS_HEADER_LEN)
        if header[0] != START or header[3] != END:
            raise RuntimeError(f"unexpected status header: {header!r}")
        status_word = header[1] | (header[2] << 8)

        trailer = b""
        if level != StatusLevel.SHORT:
            # No explicit terminator for the trailing ASCII text - read until
            # the device goes quiet for `read_timeout` seconds.
            old_timeout = sock.gettimeout()
            sock.settimeout(read_timeout)
            try:
                while True:
                    try:
                        chunk = sock.recv(4096)
                    except socket.timeout:
                        break
                    if not chunk:
                        break
                    trailer += chunk
            finally:
                sock.settimeout(old_timeout)

        temperatures, fields = parse_status_trailer(trailer, level)
        return StatusResult(status_word=status_word, temperatures=temperatures, fields=fields)

    def get_raw_readings(self, level: StatusLevel, read_timeout: float = 1.0) -> list[float]:
        """Levels 3 (Pressure reading) and 4 (Temp. readings) - raw, uncalibrated
        sensor values as read from the ADC. Unlike levels 0-2, these are NOT
        framed with '>' ... '<' - the response is a plain comma-separated
        ASCII list starting from the very first byte (confirmed against a
        live nanoDAQ-LTS-16).
        """
        sock = self._require_socket()
        sock.sendall(build_command(Command.GET_STATUS, int(level)))

        old_timeout = sock.gettimeout()
        sock.settimeout(read_timeout)
        data = b""
        try:
            while True:
                try:
                    chunk = sock.recv(4096)
                except socket.timeout:
                    break
                if not chunk:
                    break
                data += chunk
        finally:
            sock.settimeout(old_timeout)

        text = data.decode("ascii", errors="replace")
        return [float(x) for x in text.split(",") if x.strip()]

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


class PacketBuffer:
    """Resync-capable buffer for pulling one pressure packet at a time.

    Unlike `iter_binary_packets`, this tolerates leading noise/partial data
    (e.g. leftover bytes from a status response) by scanning for the
    `00 FF 00` header before decoding, and is meant to be polled repeatedly
    from a loop that also needs to send other commands in between reads.
    """

    def __init__(self, num_channels: int):
        self.num_channels = num_channels
        self.packet_size = 3 + num_channels * 2
        self._buf = bytearray()

    def feed(self, data: bytes) -> None:
        self._buf.extend(data)

    def pop_packet(self) -> ChannelData | None:
        idx = self._buf.find(HEADER_16BIT)
        if idx == -1:
            # No header at all yet; keep at most the last 2 bytes in case a
            # header is split across reads.
            if len(self._buf) > 2:
                del self._buf[:-2]
            return None
        if idx > 0:
            del self._buf[:idx]  # drop leading garbage before the header
        if len(self._buf) < self.packet_size:
            return None
        chunk = bytes(self._buf[: self.packet_size])
        del self._buf[: self.packet_size]
        return parse_binary_packet(chunk, self.num_channels)
