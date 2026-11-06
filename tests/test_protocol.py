import random
import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host"))
from protocol import Frame, Parser, encode, decode_capture, INFO, UART_DATA
from probe_cli import Client


class ProtocolTests(unittest.TestCase):
    def test_fragmented_and_coalesced_frames(self):
        rng = random.Random(2350)
        expected = [Frame(INFO, request_id=i + 1, payload=rng.randbytes(size))
                    for i, size in enumerate((0, 1, 63, 64, 65, 1000, 1024))]
        data = b"".join(map(encode, expected))
        parser = Parser()
        actual = []
        while data:
            count = rng.randint(1, 113)
            actual.extend(parser.feed(data[:count]))
            data = data[count:]
        self.assertEqual(actual, expected)
        self.assertEqual(parser.bad_frames, 0)

    def test_corrupt_crc_resynchronizes(self):
        broken = bytearray(encode(Frame(INFO, payload=b"bad")))
        broken[-1] ^= 0x80
        good = Frame(INFO, request_id=5, payload=b"RPD1 inside payload")
        parser = Parser()
        self.assertEqual(parser.feed(b"noise" + broken + encode(good)), [good])
        self.assertEqual(parser.bad_frames, 1)

    def test_invalid_header_does_not_allocate_unbounded_payload(self):
        for offset, value in ((4, b"\x02"), (16, b"\xff\xff"), (18, b"\x01\x00")):
            broken = bytearray(encode(Frame(INFO)))
            broken[offset:offset + len(value)] = value
            parser = Parser()
            self.assertEqual(parser.feed(broken + encode(Frame(INFO, request_id=2))), [Frame(INFO, request_id=2)])
            self.assertEqual(parser.bad_frames, 1)

    def test_oversized_outgoing_payload_rejected(self):
        with self.assertRaises(ValueError):
            encode(Frame(INFO, payload=b"x" * 1025))

    def test_capture_metadata(self):
        data = struct.pack("<7I2Q", 9, 2, 1000000, 1000000, 65536, 32768, 0, 100, 66000)
        self.assertEqual(decode_capture(data)["capture_id"], 9)
        with self.assertRaises(ValueError):
            decode_capture(data[:-1])

    def test_response_does_not_discard_trailing_log_event(self):
        event_before = Frame(UART_DATA, payload=b"before")
        event_after = Frame(UART_DATA, payload=b"after")
        response = Frame(INFO | 0x80, request_id=1, payload=struct.pack("<I", 0) + b"ok")

        class Transport:
            def write(self, data):
                self.last_write = data

            def read(self):
                return encode(event_before) + encode(response) + encode(event_after)

        events = []
        client = Client(Transport(), events.append)
        self.assertEqual(client.request(INFO), b"ok")
        self.assertEqual(events, [event_before, event_after])

    def test_device_error_propagates(self):
        class Transport:
            def write(self, data):
                pass

            def read(self):
                return encode(Frame(INFO | 0x80, request_id=1, payload=struct.pack("<I", 3)))

        with self.assertRaises(RuntimeError):
            Client(Transport()).request(INFO)


if __name__ == "__main__":
    unittest.main()
