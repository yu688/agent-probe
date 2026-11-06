"""可供 CLI、GUI 和自动化脚本复用的设备会话。"""
import struct
import time

if __package__:
    from .protocol import Frame, Parser, encode, INFO, STATUS, UART_CONFIG, LOG_STREAM, UART_DATA
else:
    from protocol import Frame, Parser, encode, INFO, STATUS, UART_CONFIG, LOG_STREAM, UART_DATA


class Client:
    def __init__(self, transport, on_event=None):
        self.transport = transport
        self.parser = Parser()
        self.request_id = 0
        self.on_event = on_event

    def request(self, kind, payload=b"", channel=0, timeout=3):
        # 分配请求号后将整帧编码为字节，通过 USB Bulk OUT 发送。
        self.request_id += 1
        request_id = self.request_id
        self.transport.write(encode(Frame(kind, channel=channel, request_id=request_id, payload=payload)))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            response = None
            for frame in self.parser.feed(self.transport.read()):
                # Bulk IN 同时承载设备主动推送的日志和命令应答。
                if frame.kind == UART_DATA:
                    if self.on_event:
                        self.on_event(frame)
                elif frame.request_id == request_id and frame.kind == kind | 0x80:
                    if len(frame.payload) < 4:
                        raise RuntimeError("设备应答缺少状态码")
                    # 先处理小端状态码，成功时只把后续业务数据交给调用方。
                    status = struct.unpack_from("<I", frame.payload)[0]
                    if status:
                        raise RuntimeError(f"设备拒绝命令：status={status}")
                    response = frame.payload[4:]
            # 同一USB读取中的应答后面也可能跟着日志，必须全部消费。
            if response is not None:
                return response
        raise TimeoutError("命令超时；有副作用命令不会自动重试")

    def info(self):
        return info_dict(self.request(INFO))

    def status(self):
        return status_dict(self.request(STATUS))

    def configure_uart(self, channel, enabled, baud):
        if channel not in (0, 1) or not 1200 <= baud <= 1000000:
            raise ValueError("通道必须为 0 或 1，波特率范围为 1200–1000000")
        self.request(UART_CONFIG, struct.pack("<B3xI", bool(enabled), baud), channel=channel)

    def set_log_stream(self, enabled):
        self.request(LOG_STREAM, bytes([bool(enabled)]))

    def poll_events(self):
        """读取一批日志事件，与 request 串行使用同一个解析器。"""
        for frame in self.parser.feed(self.transport.read()):
            if frame.kind == UART_DATA and self.on_event:
                self.on_event(frame)


def info_dict(data):
    if len(data) != 47:
        raise ValueError("设备 INFO 数据长度错误")
    values = struct.unpack_from("<6I", data)
    return dict(zip(("capabilities", "max_samples", "max_rate", "sys_hz", "uart_a_rx", "uart_b_rx"), values),
                serial=data[24:41].split(b"\0")[0].decode(),
                firmware=data[41:].split(b"\0")[0].decode())


def status_dict(data):
    if len(data) != 64:
        raise ValueError("设备 STATUS 数据长度错误")
    uptime, bad_frames, streaming = struct.unpack_from("<QII", data)
    ports = []
    for channel in range(2):
        values = struct.unpack_from("<6I", data, 16 + channel * 24)
        ports.append(dict(zip(("enabled", "baud", "received", "dropped", "errors", "buffered"), values)))
    return {"uptime_us": uptime, "bad_frames": bad_frames, "streaming": streaming, "uart": ports}


def decode_uart_event(frame):
    if frame.kind != UART_DATA or len(frame.payload) < 24:
        raise ValueError("串口事件类型或长度错误")
    timestamp, received, dropped, errors, timestamp_kind = struct.unpack_from("<Q4I", frame.payload)
    raw = frame.payload[24:]
    record = {"sequence": frame.sequence, "channel": frame.channel,
              "dequeue_us": timestamp, "timestamp_kind": timestamp_kind,
              "received": received, "dropped": dropped, "errors": errors, "bytes": len(raw)}
    return record, raw
