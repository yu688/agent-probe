"""与固件共享的字节流协议，只依赖 Python 标准库。"""
from dataclasses import dataclass
import struct
import zlib

MAGIC = b"RPD1"
VERSION = 1
MAX_PAYLOAD = 1024
HEADER = struct.Struct("<4sBBBBIIHHI")
INFO, STATUS, UART_CONFIG, LOG_STREAM = 1, 2, 3, 4
CAPTURE_START, CAPTURE_STATUS, CAPTURE_READ, CAPTURE_CANCEL = range(0x10, 0x14)
UART_DATA = 0x40


@dataclass(frozen=True)
class Frame:
    kind: int
    channel: int = 0
    flags: int = 0
    request_id: int = 0
    sequence: int = 0
    payload: bytes = b""


def encode(frame):
    if len(frame.payload) > MAX_PAYLOAD:
        raise ValueError("业务负载超过1024字节")
    header = HEADER.pack(MAGIC, VERSION, frame.kind, frame.channel, frame.flags,
                         frame.request_id, frame.sequence, len(frame.payload), 0, 0)
    crc = zlib.crc32(header[:20] + frame.payload)
    return header[:20] + struct.pack("<I", crc) + frame.payload


class Parser:
    def __init__(self):
        self.buffer = bytearray()
        self.bad_frames = 0

    def feed(self, data):
        self.buffer.extend(data)
        frames = []
        while len(self.buffer) >= 4:
            if self.buffer[:4] != MAGIC:
                del self.buffer[0]
                continue
            if len(self.buffer) < HEADER.size:
                break
            _, version, kind, channel, flags, request, sequence, length, reserved, crc = HEADER.unpack_from(self.buffer)
            if version != VERSION or length > MAX_PAYLOAD or reserved:
                self.bad_frames += 1
                del self.buffer[0]
                continue
            total = HEADER.size + length
            if len(self.buffer) < total:
                break
            payload = bytes(self.buffer[HEADER.size:total])
            if zlib.crc32(bytes(self.buffer[:20]) + payload) != crc:
                self.bad_frames += 1
                del self.buffer[0]
                continue
            frames.append(Frame(kind, channel, flags, request, sequence, payload))
            del self.buffer[:total]
        return frames


def decode_capture(data):
    if len(data) != 44:
        raise ValueError("采集状态长度错误")
    values = struct.unpack("<7I2Q", data)
    names = ("capture_id", "state", "requested_rate", "actual_rate", "samples",
             "bytes", "flags", "start_us", "end_us")
    return dict(zip(names, values))
