import csv
import hashlib
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host"))
from logic_capture import CaptureSession, save_capture, unpack_samples
from protocol import CAPTURE_START, CAPTURE_STATUS, CAPTURE_READ, CAPTURE_CANCEL


class CaptureDevice:
    def __init__(self):
        self.calls = []
        self.state = 1
        self.flags = 0
        self.capture_id = 7
        self.corrupt = False

    def info(self):
        return dict(capabilities=3, max_rate=25000000, max_samples=262144, serial="test")

    def status_bytes(self):
        return struct.pack("<7I2Q", self.capture_id, self.state, self.rate, self.rate,
                           self.samples, self.samples // 2, self.flags, 10, 20)

    def request(self, kind, payload=b""):
        self.calls.append(kind)
        if kind == CAPTURE_START:
            self.rate, self.samples = struct.unpack("<II", payload)
            self.data = bytes(index % 256 for index in range(self.samples // 2))
            return self.status_bytes()
        if kind == CAPTURE_STATUS:
            self.state = 2
            return self.status_bytes()
        if kind == CAPTURE_READ:
            capture_id, offset, count = struct.unpack("<III", payload)
            return struct.pack("<II", capture_id, offset + int(self.corrupt)) + self.data[offset:offset + count]
        if kind == CAPTURE_CANCEL:
            self.state = 3
            return b""
        raise AssertionError(kind)


class CaptureTests(unittest.TestCase):
    def test_multichunk_capture_preserves_order_and_metadata(self):
        device = CaptureDevice()
        session = CaptureSession(device, 1000000, 4096)
        while session.result is None:
            session.step()
        self.assertEqual(session.result.data, device.data)
        self.assertEqual(device.calls.count(CAPTURE_READ), 3)
        self.assertEqual(session.result.metadata()["sha256"], hashlib.sha256(device.data).hexdigest())
        self.assertEqual(session.step(), session.result)

    def test_unpack_low_nibble_first_and_channel_order(self):
        self.assertEqual(unpack_samples(bytes([0x21, 0x84, 0xF0])), bytes([1, 2, 4, 8, 0, 15]))

    def test_bad_arguments_never_start_hardware(self):
        for rate, count in ((1999, 8), (25000001, 8), (2000, 9), (2000, 0), (2000, 262152)):
            device = CaptureDevice()
            with self.assertRaises(ValueError):
                CaptureSession(device, rate, count)
            self.assertEqual(device.calls, [])

    def test_cancel_discards_partial_download(self):
        device = CaptureDevice()
        session = CaptureSession(device, 2000, 4096)
        session.step()
        session.step()
        self.assertTrue(session.data)
        session.cancel()
        self.assertEqual(device.calls[-1], CAPTURE_CANCEL)
        self.assertEqual(session.data, b"")
        self.assertIsNone(session.result)
        with self.assertRaises(RuntimeError):
            session.step()

    def test_timeout_cancels_hardware(self):
        device = CaptureDevice()
        session = CaptureSession(device, 2000, 8)
        with patch("logic_capture.time.monotonic", return_value=session.deadline + 1):
            with self.assertRaises(TimeoutError):
                session.step()
        self.assertTrue(session.cancelled)
        self.assertEqual(device.calls[-1], CAPTURE_CANCEL)

    def test_capture_replaced_or_fifo_error_never_returns_result(self):
        for field in ("capture_id", "flags"):
            device = CaptureDevice()
            session = CaptureSession(device, 2000, 8)
            setattr(device, field, getattr(device, field) + 1)
            with self.assertRaises((ValueError, RuntimeError)):
                session.step()
            self.assertIsNone(session.result)

    def test_wrong_chunk_offset_rejected(self):
        device = CaptureDevice()
        session = CaptureSession(device, 2000, 8)
        session.step()
        device.corrupt = True
        with self.assertRaises(ValueError):
            session.step()
        self.assertEqual(session.data, b"")

    def test_bin_and_csv_export(self):
        device = CaptureDevice()
        session = CaptureSession(device, 2000, 8)
        while session.result is None:
            session.step()
        with tempfile.TemporaryDirectory() as directory:
            raw, csv_path = Path(directory) / "capture.bin", Path(directory) / "capture.csv"
            save_capture(session.result, raw)
            self.assertEqual(raw.read_bytes(), device.data)
            save_capture(session.result, csv_path, as_csv=True)
            with csv_path.open(newline="") as stream:
                rows = list(csv.reader(stream))
            self.assertEqual(rows[0], ["sample", "time_s", "GP16", "GP17", "GP18", "GP19"])
            self.assertEqual(len(rows), 9)
            self.assertEqual(rows[3], ["2", "0.001", "1", "0", "0", "0"])
            meta = json.loads(Path(str(csv_path) + ".json").read_text())
            self.assertEqual(meta["file_sha256"], hashlib.sha256(csv_path.read_bytes()).hexdigest())
            self.assertEqual(meta["sha256"], hashlib.sha256(device.data).hexdigest())


if __name__ == "__main__":
    unittest.main()
