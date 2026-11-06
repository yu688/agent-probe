"""CLI 和 GUI 共用的定长采集会话、样本解包与导出。"""
from dataclasses import dataclass
import csv
import hashlib
import io
import json
from pathlib import Path
import struct
import time

if __package__:
    from .protocol import decode_capture, CAPTURE_START, CAPTURE_STATUS, CAPTURE_READ, CAPTURE_CANCEL
else:
    from protocol import decode_capture, CAPTURE_START, CAPTURE_STATUS, CAPTURE_READ, CAPTURE_CANCEL


def unpack_samples(data):
    return bytes(value for packed in data for value in (packed & 15, packed >> 4))


@dataclass(frozen=True)
class CaptureResult:
    data: bytes
    capture: dict
    device: dict

    def metadata(self):
        return {"device": self.device, "capture": self.capture, "pins": [16, 17, 18, 19],
                "packing": "每字节低四位为较早样本，高四位为下一样本",
                "timestamp_kind": "CPU启动和完成标记，非精确采样沿",
                "sha256": hashlib.sha256(self.data).hexdigest()}


class CaptureSession:
    """每次 step 只处理一次 USB 请求，让 GUI 线程间的取消操作可被调度。"""
    def __init__(self, client, rate, samples):
        if not 2000 <= rate <= 25000000 or not 0 < samples <= 262144 or samples % 8:
            raise ValueError("采样率需为 2000～25000000 S/s；点数需为 8 的倍数且不超过 262144")
        self.client = client
        self.device = client.info()
        if not self.device["capabilities"] & 2:
            raise ValueError("当前固件不支持逻辑采集")
        if rate > self.device["max_rate"] or samples > self.device["max_samples"]:
            raise ValueError("采样参数超出当前设备能力")
        self.rate, self.samples = rate, samples
        self.data = bytearray()
        self.result = None
        self.cancelled = False
        self.deadline = time.monotonic() + samples / rate + 5
        reply = client.request(CAPTURE_START, struct.pack("<II", rate, samples))
        try:
            self.current = decode_capture(reply)
            self.capture_id = self.current["capture_id"]
            self._validate()
        except Exception:
            self.cancel()
            raise

    def _validate(self):
        c = self.current
        if (c["capture_id"] != self.capture_id or not self.capture_id or
                c["samples"] != self.samples or c["bytes"] != self.samples // 2 or
                c["requested_rate"] != self.rate or not 0 < c["actual_rate"] <= self.rate):
            raise ValueError("采集编号或采样参数与请求不匹配")
        if c["state"] not in (1, 2) or c["flags"]:
            raise RuntimeError(f'采集未成功：state={c["state"]}, flags={c["flags"]}')

    def step(self):
        if self.cancelled:
            raise RuntimeError("采集已取消")
        if self.result is not None:
            return self.result
        if self.current["state"] == 1:
            if time.monotonic() > self.deadline:
                self.cancel()
                raise TimeoutError("逻辑采集超时，已取消")
            self.current = decode_capture(self.client.request(CAPTURE_STATUS))
            self._validate()
            return None
        offset = len(self.data)
        count = min(1012, self.current["bytes"] - offset)
        payload = self.client.request(CAPTURE_READ, struct.pack("<III", self.capture_id, offset, count))
        if len(payload) != count + 8 or struct.unpack_from("<II", payload) != (self.capture_id, offset):
            raise ValueError("采集数据编号、偏移或长度不匹配")
        self.data.extend(payload[8:])
        if len(self.data) == self.current["bytes"]:
            self.result = CaptureResult(bytes(self.data), dict(self.current), dict(self.device))
        return self.result

    def cancel(self):
        try:
            self.client.request(CAPTURE_CANCEL)
        finally:
            self.cancelled = True
            self.data.clear()


def save_capture(result, path, as_csv=False):
    path = Path(path)
    data = result.data
    if as_csv:
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(["sample", "time_s", "GP16", "GP17", "GP18", "GP19"])
        rate = result.capture["actual_rate"]
        for index, sample in enumerate(unpack_samples(data)):
            writer.writerow([index, format(index / rate, ".12g"),
                             *((sample >> channel) & 1 for channel in range(4))])
        data = output.getvalue().encode("utf-8")
    path.write_bytes(data)
    metadata = result.metadata()
    metadata["format"] = "csv" if as_csv else "packed-bin"
    metadata["file_sha256"] = hashlib.sha256(data).hexdigest()
    Path(str(path) + ".json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
