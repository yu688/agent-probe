"""Agent Probe 的 USB 设备信息和串口接收测试界面。"""
import codecs
import sys
import time

from PyQt6.QtCore import QObject, QThread, QTimer, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QFontDatabase, QTextCursor
from PyQt6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFormLayout,
                             QGroupBox, QHBoxLayout, QLabel, QMainWindow,
                             QPlainTextEdit, QPushButton, QSpinBox, QVBoxLayout, QWidget)

if __package__:
    from .probe_client import Client, decode_uart_event
    from .usb_transport import USB, list_devices
else:
    from probe_client import Client, decode_uart_event
    from usb_transport import USB, list_devices


class DeviceWorker(QObject):
    devices = pyqtSignal(list)
    info = pyqtSignal(object)
    status = pyqtSignal(dict)
    streaming = pyqtSignal(bool)
    log_bytes = pyqtSignal(bytes)
    failed = pyqtSignal(str)
    done = pyqtSignal()
    finished = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.client = None
        self.active = False
        self.pending = bytearray()
        self.next_status = 0.0
        self.timer = QTimer(self)
        self.timer.setInterval(20)
        self.timer.timeout.connect(self.poll)

    @pyqtSlot(str, object)
    def run_action(self, action, argument):
        try:
            if action == "scan":
                self.devices.emit(list_devices())
            elif action == "connect":
                self.client = Client(USB(argument), self.collect)
                self.refresh()
            elif action == "refresh":
                self.refresh()
            elif action == "configure":
                self.client.configure_uart(0, True, argument)
                self.status.emit(self.client.status())
            elif action == "stream":
                self.set_stream(argument)
            elif action == "disconnect":
                self.disconnect()
        except (OSError, RuntimeError, ValueError) as error:
            self.disconnect(stop_stream=False)
            self.failed.emit(str(error))
        finally:
            self.done.emit()

    def refresh(self):
        self.info.emit(self.client.info())
        self.status.emit(self.client.status())

    def set_stream(self, enabled):
        if enabled and not self.client.status()["uart"][0]["enabled"]:
            self.failed.emit("通道 0 未启用，请先应用波特率。")
            return
        self.client.set_log_stream(enabled)
        self.flush_logs()
        self.active = enabled
        self.streaming.emit(enabled)
        if enabled:
            self.next_status = time.monotonic() + 1
            self.timer.start()
        else:
            self.timer.stop()
            self.status.emit(self.client.status())

    def collect(self, frame):
        if frame.channel == 0:
            _, data = decode_uart_event(frame)
            self.pending.extend(data)

    def flush_logs(self):
        if self.pending:
            self.log_bytes.emit(bytes(self.pending))
            self.pending.clear()

    @pyqtSlot()
    def poll(self):
        # 唯一持有 USB 连接的线程在轮询、状态查询和按钮操作之间串行切换。
        try:
            if time.monotonic() >= self.next_status:
                self.status.emit(self.client.status())
                self.next_status = time.monotonic() + 1
            else:
                self.client.poll_events()
            self.flush_logs()
        except (OSError, RuntimeError, ValueError) as error:
            self.disconnect(stop_stream=False)
            self.failed.emit(str(error))

    def disconnect(self, stop_stream=True):
        self.timer.stop()
        if self.client is not None:
            try:
                if self.active and stop_stream:
                    self.client.set_log_stream(False)
            finally:
                self.flush_logs()
                self.client.transport.close()
                self.client = None
                self.active = False
                self.streaming.emit(False)
                self.info.emit(None)

    @pyqtSlot()
    def shutdown(self):
        try:
            self.disconnect()
        except (OSError, RuntimeError, ValueError) as error:
            self.failed.emit(str(error))
        finally:
            self.finished.emit()


class ProbeWindow(QMainWindow):
    action = pyqtSignal(str, object)
    shutdown = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Agent Probe · USB 与串口测试")
        self.resize(820, 620)
        self.connected = self.active = self.busy = self.closing = False
        self.decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        device_row = QHBoxLayout()
        self.device_picker = QComboBox()
        self.scan_button = QPushButton("扫描设备")
        self.connect_button = QPushButton("连接")
        self.refresh_button = QPushButton("刷新信息")
        device_row.addWidget(self.device_picker, 1)
        for button in (self.scan_button, self.connect_button, self.refresh_button):
            device_row.addWidget(button)
        layout.addLayout(device_row)

        info_group = QGroupBox("USB 设备信息")
        info_form = QFormLayout(info_group)
        self.info_labels = {}
        for key, label in (("serial", "序列号"), ("firmware", "固件版本"), ("sys_hz", "系统时钟"),
                           ("capabilities", "能力位"), ("uart_a_rx", "UART RX 引脚")):
            field = QLabel("—")
            self.info_labels[key] = field
            info_form.addRow(label, field)
        layout.addWidget(info_group)

        uart_group = QGroupBox("串口接收测试 · 通道 0 · 8N1")
        uart_layout = QVBoxLayout(uart_group)
        uart_row = QHBoxLayout()
        self.baud = QSpinBox()
        self.baud.setRange(1200, 1000000)
        self.baud.setValue(100000)
        self.configure_button = QPushButton("应用波特率")
        self.stream_button = QPushButton("开始接收")
        self.hex_mode = QCheckBox("十六进制")
        clear = QPushButton("清空显示")
        uart_row.addWidget(QLabel("波特率"))
        for widget in (self.baud, self.configure_button, self.stream_button, self.hex_mode, clear):
            uart_row.addWidget(widget)
        uart_row.addStretch()
        uart_layout.addLayout(uart_row)
        self.counters = QLabel("等待连接")
        uart_layout.addWidget(self.counters)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.log.setPlaceholderText("开始接收后，目标串口日志会显示在这里。")
        uart_layout.addWidget(self.log, 1)
        layout.addWidget(uart_group, 1)

        self.scan_button.clicked.connect(lambda: self.dispatch("scan"))
        self.connect_button.clicked.connect(self.toggle_connection)
        self.refresh_button.clicked.connect(lambda: self.dispatch("refresh"))
        self.configure_button.clicked.connect(lambda: self.dispatch("configure", self.baud.value()))
        self.stream_button.clicked.connect(self.toggle_stream)
        self.device_picker.currentIndexChanged.connect(self.update_controls)
        clear.clicked.connect(self.log.clear)

        self.thread = QThread(self)
        self.worker = DeviceWorker()
        self.worker.moveToThread(self.thread)
        self.action.connect(self.worker.run_action)
        self.shutdown.connect(self.worker.shutdown)
        self.worker.devices.connect(self.show_devices)
        self.worker.info.connect(self.show_info)
        self.worker.status.connect(self.show_status)
        self.worker.streaming.connect(self.show_streaming)
        self.worker.log_bytes.connect(self.append_log)
        self.worker.failed.connect(self.show_error)
        self.worker.done.connect(self.complete_action)
        self.worker.finished.connect(self.thread.quit)
        self.thread.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.close)
        self.thread.start()
        self.dispatch("scan")

    def dispatch(self, action, argument=None):
        self.busy = True
        self.update_controls()
        self.action.emit(action, argument)

    def update_controls(self):
        ready = not (self.busy or self.closing)
        self.device_picker.setEnabled(ready and not self.connected)
        self.scan_button.setEnabled(ready and not self.connected)
        self.connect_button.setEnabled(ready and (self.connected or self.device_picker.currentIndex() >= 0))
        self.refresh_button.setEnabled(ready and self.connected)
        self.baud.setEnabled(ready and self.connected and not self.active)
        self.configure_button.setEnabled(self.baud.isEnabled())
        self.hex_mode.setEnabled(ready and not self.active)
        self.stream_button.setEnabled(ready and self.connected)
        self.connect_button.setText("断开" if self.connected else "连接")
        self.stream_button.setText("停止接收" if self.active else "开始接收")

    def toggle_connection(self):
        if self.connected:
            self.dispatch("disconnect")
        else:
            self.dispatch("connect", self.device_picker.currentData())

    def toggle_stream(self):
        if not self.active:
            self.decoder.reset()
            self.log.clear()
        self.dispatch("stream", not self.active)

    def show_devices(self, paths):
        self.device_picker.clear()
        for index, path in enumerate(paths):
            self.device_picker.addItem(f"Agent Probe {index}", path)
        self.statusBar().showMessage(f"发现 {len(paths)} 台设备" if paths else "未发现 Agent Probe USB 设备")

    def show_info(self, info):
        self.connected = info is not None
        for key, label in self.info_labels.items():
            value = info[key] if self.connected else "—"
            if key == "capabilities" and self.connected:
                value = f"0x{value:08X}"
            elif key == "sys_hz" and self.connected:
                value = f"{value:,} Hz"
            elif key == "uart_a_rx" and self.connected:
                value = f"GP{value}"
            label.setText(str(value))
        if not self.connected:
            self.counters.setText("等待连接")
        self.statusBar().showMessage("已连接" if self.connected else "已断开")
        self.update_controls()

    def show_status(self, status):
        uart = status["uart"][0]
        enabled = f'{uart["baud"]} baud' if uart["enabled"] else "未启用"
        self.counters.setText(f'{enabled}    接收 {uart["received"]} B    丢弃 {uart["dropped"]} B'
                              f'    UART 错误 {uart["errors"]}    缓冲 {uart["buffered"]} B')

    def show_streaming(self, enabled):
        if self.active and not enabled and not self.hex_mode.isChecked():
            self.insert_log(self.decoder.decode(b"", final=True))
        self.active = enabled
        self.update_controls()

    def append_log(self, data):
        text = data.hex(" ").upper() + " " if self.hex_mode.isChecked() else self.decoder.decode(data)
        self.insert_log(text)

    def insert_log(self, text):
        cursor = QTextCursor(self.log.document())
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(text)
        if cursor.position() > 100000:
            # 限制显示缓存，包括长时间没有换行的串口输出。
            cursor.setPosition(0)
            cursor.movePosition(QTextCursor.MoveOperation.NextCharacter,
                                QTextCursor.MoveMode.KeepAnchor, cursor.document().characterCount() - 90001)
            cursor.removeSelectedText()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.log.setTextCursor(cursor)

    def complete_action(self):
        self.busy = False
        self.update_controls()

    def show_error(self, message):
        self.statusBar().showMessage(message)

    def closeEvent(self, event):
        if self.thread.isRunning():
            event.ignore()
            if not self.closing:
                self.closing = True
                self.update_controls()
                self.shutdown.emit()
        else:
            event.accept()


def main():
    app = QApplication(sys.argv)
    window = ProbeWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
