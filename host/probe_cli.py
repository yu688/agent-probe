"""发现设备、读取状态、保存串口日志及四路采集数据。"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import sys
import time

if __package__:
    from .probe_client import Client, decode_uart_event
    from .protocol import decode_capture, CAPTURE_START, CAPTURE_STATUS, CAPTURE_READ, CAPTURE_CANCEL
    from .winusb_transport import WinUSB, list_devices
else:
    from probe_client import Client, decode_uart_event
    from protocol import decode_capture, CAPTURE_START, CAPTURE_STATUS, CAPTURE_READ, CAPTURE_CANCEL
    from winusb_transport import WinUSB, list_devices


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", type=int, default=0, help="设备序号，默认0")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("list", "info", "status"):
        commands.add_parser(name)
    uart = commands.add_parser("uart-config", help="配置目标串口接收")
    uart.add_argument("--channel", type=int, choices=(0, 1), default=0)
    uart.add_argument("--baud", type=int, default=100000)
    uart.add_argument("--disable", action="store_true")
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
            print(json.dumps(client.info(), indent=2))
        elif args.command == "status":
            print(json.dumps(client.status(), indent=2))
        elif args.command == "uart-config":
            client.configure_uart(args.channel, not args.disable, args.baud)
            print(json.dumps(client.status(), indent=2))
        elif args.command == "log":
            session = {"device": client.info(), "initial_status": client.status(),
                       "channel": args.channel, "timestamp_kind": "主循环出队时间"}
            if not session["initial_status"]["uart"][args.channel]["enabled"]:
                raise RuntimeError("所选UART未启用，默认仅启用通道0的GP5")
            with args.output.open("wb") as raw, Path(str(args.output) + ".jsonl").open("w", encoding="utf-8") as metadata:
                def save(frame):
                    if frame.channel != args.channel:
                        return
                    record, data = decode_uart_event(frame)
                    record["file_offset"] = raw.tell()
                    raw.write(data)
                    metadata.write(json.dumps(record) + "\n")
                client.on_event = save
                client.set_log_stream(True)
                deadline = time.monotonic() + args.seconds
                try:
                    while time.monotonic() < deadline:
                        client.poll_events()
                finally:
                    client.set_log_stream(False)
                session["final_status"] = client.status()
                print(json.dumps(session["final_status"], indent=2))
            with args.output.open("rb") as raw:
                session["sha256"] = hashlib.file_digest(raw, "sha256").hexdigest()
            Path(str(args.output) + ".meta.json").write_text(json.dumps(session, ensure_ascii=False, indent=2), encoding="utf-8")
        elif args.command == "capture":
            device_info = client.info()
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
