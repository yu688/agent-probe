"""发现设备、读取状态、保存串口日志及四路采集数据。"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import sys
import time

from protocol import (Frame, Parser, encode, decode_capture, INFO, STATUS, LOG_STREAM,
                      UART_DATA, CAPTURE_START, CAPTURE_STATUS, CAPTURE_READ, CAPTURE_CANCEL)
from winusb_transport import WinUSB, list_devices


class Client:
    def __init__(self, transport, on_event=None):
        self.transport = transport
        self.parser = Parser()
        self.request_id = 0
        self.on_event = on_event

    def request(self, kind, payload=b"", channel=0, timeout=3):
        # 分配请求号后将整帧编码为字节，通过 WinUSB Bulk OUT 发送。
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


def info_dict(data):
    values = struct.unpack_from("<6I", data)
    return dict(zip(("capabilities", "max_samples", "max_rate", "sys_hz", "uart_a_rx", "uart_b_rx"), values),
                serial=data[24:41].split(b"\0")[0].decode(),
                firmware=data[41:].split(b"\0")[0].decode())


def status_dict(data):
    uptime, bad_frames, streaming = struct.unpack_from("<QII", data)
    ports = []
    for channel in range(2):
        values = struct.unpack_from("<6I", data, 16 + channel * 24)
        ports.append(dict(zip(("enabled", "baud", "received", "dropped", "errors", "buffered"), values)))
    return {"uptime_us": uptime, "bad_frames": bad_frames, "streaming": streaming, "uart": ports}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", type=int, default=0, help="设备序号，默认0")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("list", "info", "status"):
        commands.add_parser(name)
    logs = commands.add_parser("log")
    logs.add_argument("--seconds", type=float, default=10)
    logs.add_argument("--output", type=Path, default=Path("uart.bin"))
    logs.add_argument("--channel", type=int, choices=(0, 1), default=0)
    capture = commands.add_parser("capture")
    capture.add_argument("--rate", type=int, default=1000000)
    capture.add_argument("--samples", type=int, default=65536)
    capture.add_argument("--output", type=Path, default=Path("capture.bin"))
    args = parser.parse_args()
    if args.command == "log" and args.seconds <= 0:
        parser.error("seconds必须大于0")
    if args.command == "capture" and (not 2000 <= args.rate <= 25000000 or
                                       not 0 < args.samples <= 262144 or args.samples % 8):
        parser.error("rate需为2000~25000000，samples需为8的倍数且不超过262144")
    paths = list_devices()
    if args.command == "list":
        print(json.dumps(paths, ensure_ascii=False, indent=2))
        return
    if not 0 <= args.device < len(paths):
        raise RuntimeError("未发现指定设备，请检查原生USB连接和WinUSB驱动绑定")
    with WinUSB(paths[args.device]) as transport:
        client = Client(transport)
        if args.command == "info":
            print(json.dumps(info_dict(client.request(INFO)), indent=2))
        elif args.command == "status":
            print(json.dumps(status_dict(client.request(STATUS)), indent=2))
        elif args.command == "log":
            session = {"device": info_dict(client.request(INFO)),
                       "initial_status": status_dict(client.request(STATUS)),
                       "channel": args.channel, "timestamp_kind": "主循环出队时间"}
            if not session["initial_status"]["uart"][args.channel]["enabled"]:
                raise RuntimeError("所选UART未启用，默认仅启用通道0的GP5")
            with args.output.open("wb") as raw, Path(str(args.output) + ".jsonl").open("w", encoding="utf-8") as metadata:
                def save(frame):
                    if frame.channel != args.channel:
                        return
                    if len(frame.payload) < 24:
                        raise RuntimeError("日志事件长度错误")
                    timestamp, received, dropped, errors, timestamp_kind = struct.unpack_from("<Q4I", frame.payload)
                    record = {"sequence": frame.sequence, "channel": frame.channel,
                              "dequeue_us": timestamp, "timestamp_kind": timestamp_kind,
                              "received": received, "dropped": dropped, "errors": errors,
                              "file_offset": raw.tell(), "bytes": len(frame.payload) - 24}
                    raw.write(frame.payload[24:])
                    metadata.write(json.dumps(record) + "\n")
                client.on_event = save
                client.request(LOG_STREAM, b"\x01")
                deadline = time.monotonic() + args.seconds
                try:
                    while time.monotonic() < deadline:
                        for frame in client.parser.feed(transport.read()):
                            if frame.kind == UART_DATA:
                                save(frame)
                finally:
                    client.request(LOG_STREAM, b"\x00")
                session["final_status"] = status_dict(client.request(STATUS))
                print(json.dumps(session["final_status"], indent=2))
            with args.output.open("rb") as raw:
                session["sha256"] = hashlib.file_digest(raw, "sha256").hexdigest()
            Path(str(args.output) + ".meta.json").write_text(json.dumps(session, ensure_ascii=False, indent=2), encoding="utf-8")
        elif args.command == "capture":
            device_info = info_dict(client.request(INFO))
            current = decode_capture(client.request(CAPTURE_START, struct.pack("<II", args.rate, args.samples)))
            deadline = time.monotonic() + args.samples / args.rate + 5
            try:
                while current["state"] == 1 and time.monotonic() < deadline:
                    time.sleep(0.02)
                    current = decode_capture(client.request(CAPTURE_STATUS))
            finally:
                if current["state"] == 1:
                    client.request(CAPTURE_CANCEL)
            if current["state"] != 2:
                raise RuntimeError(f"采集未成功：{current}")
            with args.output.open("wb") as output:
                for offset in range(0, current["bytes"], 1012):
                    count = min(1012, current["bytes"] - offset)
                    data = client.request(CAPTURE_READ, struct.pack("<III", current["capture_id"], offset, count))
                    if len(data) != count + 8 or struct.unpack_from("<II", data) != (current["capture_id"], offset):
                        raise RuntimeError("采集数据编号或偏移不匹配")
                    output.write(data[8:])
            record = {"device": device_info, "capture": current, "pins": [16, 17, 18, 19],
                      "packing": "每字节低四位为较早样本，高四位为下一样本",
                      "timestamp_kind": "CPU启动和完成标记，非精确采样沿"}
            with args.output.open("rb") as raw:
                record["sha256"] = hashlib.file_digest(raw, "sha256").hexdigest()
            Path(str(args.output) + ".json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps(current, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, ValueError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
