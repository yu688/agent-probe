"""验证 macOS USB 故障处理以及 Windows 传输层选择，不依赖实板。"""
from pathlib import Path
import runpy
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host"))
import macos_usb_transport as macos
from winusb_transport import WinUSB


class USBTimeout(OSError):
    pass


class TransportTests(unittest.TestCase):
    def setUp(self):
        interface = Mock()
        interface.bInterfaceClass = 0xFF
        interface.__iter__ = Mock(return_value=iter([
            SimpleNamespace(bEndpointAddress=1, bmAttributes=2),
            SimpleNamespace(bEndpointAddress=0x81, bmAttributes=2),
        ]))
        config = Mock()
        config.bConfigurationValue = 1
        config.__getitem__ = Mock(return_value=interface)
        self.device = Mock(bus=1, address=2)
        self.device.get_active_configuration.return_value = config
        self.core = Mock(USBTimeoutError=USBTimeout)
        self.core.find.return_value = [self.device]
        self.util = Mock()
        self.backend = object()
        patcher = patch.object(macos, "_usb", return_value=(self.core, self.util, self.backend))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_discovery_and_session(self):
        self.assertEqual(macos.list_devices(), ["libusb:1:2"])
        with macos.USB("libusb:1:2") as transport:
            self.device.write.return_value = 3
            transport.write(b"abc")
            self.device.write.assert_called_once_with(1, b"abc", timeout=200)
            self.device.read.return_value = [1, 2, 3]
            self.assertEqual(transport.read(), b"\x01\x02\x03")
            self.device.set_configuration.assert_not_called()
        self.util.claim_interface.assert_called_once_with(self.device, 0)
        self.util.release_interface.assert_called_once_with(self.device, 0)
        self.util.dispose_resources.assert_called_once_with(self.device)
        transport.close()
        self.util.dispose_resources.assert_called_once()

    def test_only_timeout_is_empty_read(self):
        with macos.USB("libusb:1:2") as transport:
            self.device.read.side_effect = USBTimeout("timeout")
            self.assertEqual(transport.read(), b"")
            self.device.read.side_effect = OSError("disconnected")
            with self.assertRaisesRegex(OSError, "disconnected"):
                transport.read()

    def test_short_write_and_closed_session(self):
        transport = macos.USB("libusb:1:2")
        self.device.write.return_value = 1
        with self.assertRaisesRegex(OSError, "写入不完整"):
            transport.write(b"abc")
        transport.close()
        with self.assertRaisesRegex(OSError, "已关闭"):
            transport.read()

    def test_claim_failure_releases_resources(self):
        self.util.claim_interface.side_effect = OSError("busy")
        with self.assertRaisesRegex(OSError, "busy"):
            macos.USB("libusb:1:2")
        self.util.release_interface.assert_not_called()
        self.util.dispose_resources.assert_called_once_with(self.device)

    def test_windows_uses_original_winusb(self):
        with patch.object(sys, "platform", "win32"):
            namespace = runpy.run_path(str(Path(macos.__file__).with_name("usb_transport.py")))
        self.assertIs(namespace["USB"], WinUSB)


if __name__ == "__main__":
    unittest.main()
