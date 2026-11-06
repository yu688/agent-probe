"""可选 PyQt6 测试：采集任务调度、取消和断连清理。"""
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host"))
from test_logic_capture import CaptureDevice

try:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication
    from probe_gui import DeviceWorker
except ImportError:
    QApplication = None


@unittest.skipIf(QApplication is None, "安装 GUI 依赖后执行界面测试")
class LogicWorkerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.worker = DeviceWorker()
        self.device = CaptureDevice()
        self.device.transport = Mock()
        self.worker.client = self.device
        self.addCleanup(self.worker.timer.stop)

    def test_download_completes_and_stops_timer(self):
        results, states = [], []
        self.worker.capture_ready.connect(results.append)
        self.worker.capturing.connect(states.append)
        self.worker.run_action("capture", (1000000, 4096))
        for _ in range(5):
            if self.worker.capture_session is not None:
                self.worker.poll()
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].data, self.device.data)
        self.assertEqual(states, [True, False])
        self.assertFalse(self.worker.timer.isActive())

    def test_cancel_keeps_uart_polling_alive(self):
        self.worker.active = True
        self.worker.run_action("capture", (2000, 262144))
        self.worker.run_action("cancel_capture", None)
        self.assertEqual(self.device.state, 3)
        self.assertIsNone(self.worker.capture_session)
        self.assertTrue(self.worker.timer.isActive())

    def test_disconnect_during_download_releases_usb(self):
        errors = []
        self.worker.failed.connect(errors.append)
        self.worker.run_action("capture", (2000, 4096))
        self.worker.poll()
        self.device.request = Mock(side_effect=OSError("USB removed"))
        self.worker.poll()
        self.assertEqual(errors, ["USB removed"])
        self.assertIsNone(self.worker.client)
        self.assertIsNone(self.worker.capture_session)
        self.assertFalse(self.worker.timer.isActive())
        self.device.transport.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
