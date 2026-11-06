"""读取交叉编译后的 ELF 对象，验证实际生成的 USB 描述符字节。"""
from pathlib import Path
import struct
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "host"))
from winusb_transport import INTERFACE_GUID


def sections(path):
    data = path.read_bytes()
    if data[:6] != b"\x7fELF\x01\x01":
        raise ValueError("需要32位小端ELF对象")
    offset = struct.unpack_from("<I", data, 32)[0]
    size, count, names_index = struct.unpack_from("<HHH", data, 46)
    headers = [struct.unpack_from("<10I", data, offset + i * size) for i in range(count)]
    names_header = headers[names_index]
    names = data[names_header[4]:names_header[4] + names_header[5]]
    result = {}
    for header in headers:
        name = names[header[0]:].split(b"\0", 1)[0].decode()
        result[name] = data[header[4]:header[4] + header[5]]
    return result


class DescriptorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = ROOT / "build/rp2350/CMakeFiles/agent_probe.dir/src/usb_descriptors.c.obj"
        if not path.exists():
            raise unittest.SkipTest("请先执行 scripts/build.ps1")
        cls.sections = sections(path)

    def test_ms_os_lengths_and_guid(self):
        data = self.sections[".rodata.ms_os_descriptor"]
        self.assertEqual(len(data), 162)
        self.assertEqual(struct.unpack_from("<HHIH", data), (10, 0, 0x06030000, 162))
        self.assertEqual(struct.unpack_from("<HH", data, 10), (20, 3))
        self.assertEqual(data[14:22], b"WINUSB\0\0")
        self.assertEqual(struct.unpack_from("<4H", data, 30), (132, 4, 7, 42))
        self.assertEqual(data[38:80].decode("utf-16le"), "DeviceInterfaceGUIDs\0")
        self.assertEqual(struct.unpack_from("<H", data, 80)[0], 80)
        self.assertEqual(data[82:].decode("utf-16le"), "{" + INTERFACE_GUID + "}\0\0")

    def test_single_vendor_function_and_endpoints(self):
        data = self.sections[".rodata.configuration_descriptor"]
        self.assertEqual(len(data), 32)
        self.assertEqual(data[4], 1)
        self.assertEqual(data[9:18], bytes((9, 4, 0, 0, 2, 0xFF, 0, 0, 4)))
        self.assertEqual(data[20], 0x01)
        self.assertEqual(data[27], 0x81)
        self.assertEqual(struct.unpack_from("<H", data, 22)[0], 64)
        self.assertEqual(struct.unpack_from("<H", data, 29)[0], 64)

    def test_bos_matches_ms_os_descriptor(self):
        data = self.sections[".rodata.bos_descriptor"]
        self.assertEqual(len(data), 33)
        self.assertEqual(data[:5], bytes((5, 15, 33, 0, 1)))
        self.assertEqual(struct.unpack_from("<H", data, 29)[0], 162)
        self.assertEqual(data[31:], b"\x20\x00")


if __name__ == "__main__":
    unittest.main()
