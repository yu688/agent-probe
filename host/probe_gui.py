"""Agent Probe 的 USB、串口接收和四路逻辑采集界面。"""
import codecs
import sys
import time

from PyQt6.QtCore import QObject, QThread, QTimer, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QFontDatabase, QTextCursor
from PyQt6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFileDialog, QFormLayout,
                             QGroupBox, QHBoxLayout, QLabel, QMainWindow,
                             QPlainTextEdit, QProgressBar, QPushButton, QSpinBox, QTabWidget, QVBoxLayout, QWidget)

if __package__:
    from .probe_client import Client, decode_uart_event
    from .usb_transport import USB, list_devices
    from .logic_capture import CaptureSession, save_capture
    from .logic_waveform import LogicWaveform
else:
    from probe_client import Client, decode_uart_event
    from usb_transport import USB, list_devices
    from logic_capture import CaptureSession, save_capture
    from logic_waveform import LogicWaveform


class DeviceWorker(QObject):
    devices = pyqtSignal(list)
    info = pyqtSignal(object)
    status = pyqtSignal(dict)
    streaming = pyqtSignal(bool)
    log_bytes = pyqtSignal(bytes)
    capturing = pyqtSignal(bool)
    capture_progress = pyqtSignal(int, str)
    capture_ready = pyqtSignal(object)
    failed = pyqtSignal(str)
    done = pyqtSignal()
    finished = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.client = None
        self.active = False
        self.pending = bytearray()
        self.next_status = 0.0
        self.capture_session = None
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
            elif action == "capture":
                self.capture_session = CaptureSession(self.client, *argument)
                self.capturing.emit(True)
                self.capture_progress.emit(0, "正在采集 GP16～GP19…")
                self.timer.start()
            elif action == "cancel_capture":
                self.cancel_capture()
        except (OSError, RuntimeError, ValueError) as error:
            self.fail(error)
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
            if self.capture_session is None:
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
            if self.capture_session is not None:
                session = self.capture_session
                result = session.step()
                if result is not None:
                    self.capture_session = None
                    self.capture_ready.emit(result)
                    self.capturing.emit(False)
                    if not self.active:
                        self.timer.stop()
                elif session.current["state"] == 2:
                    progress = len(session.data) * 100 // session.current["bytes"]
                    self.capture_progress.emit(progress, f"读取数据 {len(session.data):,} / {session.current['bytes']:,} B")
                self.flush_logs()
                return
            if time.monotonic() >= self.next_status:
                self.status.emit(self.client.status())
                self.next_status = time.monotonic() + 1
            else:
                self.client.poll_events()
            self.flush_logs()
        except (OSError, RuntimeError, ValueError) as error:
            self.fail(error)

    def fail(self, error):
        try:
            self.disconnect(stop_stream=False)
        except (OSError, RuntimeError, ValueError):
            pass  # USB 已断开时取消命令也可能失败，仍需报告原始错误。
        self.failed.emit(str(error))

    def cancel_capture(self):
        session, self.capture_session = self.capture_session, None
        try:
            if session is not None:
                session.cancel()
        finally:
            self.capturing.emit(False)
            self.capture_progress.emit(0, "已取消，本次不保留部分数据")
            if not self.active:
                self.timer.stop()

    def disconnect(self, stop_stream=True):
        self.timer.stop()
        if self.client is not None:
            try:
                if self.capture_session is not None:
                    self.cancel_capture()
                if self.active and stop_stream:
                    self.client.set_log_stream(False)
            finally:
                self.flush_logs()
                try:
                    self.client.transport.close()
                finally:
                    self.client = None
                    self.active = False
                    self.capture_session = None
                    self.capturing.emit(False)
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
        self.setWindowTitle("Agent Probe · 串口与逻辑采集")
        self.resize(1000, 780)
        self.connected = self.active = self.busy = self.closing = False
        self.capturing = False
        self.capture_supported = False
        self.capture_result = None
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
        tabs = QTabWidget()
        tabs.addTab(uart_group, "串口日志")
        logic = QWidget()
        logic_layout = QVBoxLayout(logic)
        hint = QLabel("GP16～GP19 · 四路输入 · 定长即时采集 · 与目标共地，输入电压需符合核心板 IO 规格")
        hint.setWordWrap(True)
        logic_layout.addWidget(hint)
        row = QHBoxLayout()
        self.capture_rate = QSpinBox()
        self.capture_rate.setRange(2000, 25000000)
        self.capture_rate.setValue(1000000)
        self.capture_rate.setSuffix(" S/s")
        self.capture_samples = QSpinBox()
        self.capture_samples.setRange(8, 262144)
        self.capture_samples.setSingleStep(8)
        self.capture_samples.setValue(65536)
        self.capture_button = QPushButton("开始采集")
        self.cancel_capture_button = QPushButton("取消采集")
        for widget in (QLabel("采样率"), self.capture_rate, QLabel("点数"), self.capture_samples,
                       self.capture_button, self.cancel_capture_button):
            row.addWidget(widget)
        logic_layout.addLayout(row)
        self.capture_message = QLabel("等待采集；点数必须为 8 的倍数。")
        self.capture_message.setWordWrap(True)
        logic_layout.addWidget(self.capture_message)
        self.capture_progress = QProgressBar()
        self.capture_progress.setValue(0)
        logic_layout.addWidget(self.capture_progress)
        self.waveform = LogicWaveform()
        logic_layout.addWidget(self.waveform, 1)
        view_row = QHBoxLayout()
        self.view_start = QSpinBox()
        self.view_start.setRange(0, 0)
        self.view_count = QSpinBox()
        self.view_count.setRange(1, 262144)
        self.view_count.setValue(65536)
        self.save_bin = QPushButton("保存 BIN + 元数据")
        self.save_csv = QPushButton("导出 CSV")
        for widget in (QLabel("起点"), self.view_start, QLabel("显示点数"), self.view_count,
                       self.save_bin, self.save_csv):
            view_row.addWidget(widget)
        logic_layout.addLayout(view_row)
        logic_layout.addWidget(QLabel("减小显示点数可放大波形；时间轴按实际平均采样率计算，概览保留高低电平包络。"))
        tabs.addTab(logic, "逻辑 IO 采集")
        layout.addWidget(tabs, 1)

        self.scan_button.clicked.connect(lambda: self.dispatch("scan"))
        self.connect_button.clicked.connect(self.toggle_connection)
        self.refresh_button.clicked.connect(lambda: self.dispatch("refresh"))
        self.configure_button.clicked.connect(lambda: self.dispatch("configure", self.baud.value()))
        self.stream_button.clicked.connect(self.toggle_stream)
        self.device_picker.currentIndexChanged.connect(self.update_controls)
        clear.clicked.connect(self.log.clear)
        self.capture_button.clicked.connect(self.start_capture)
        self.cancel_capture_button.clicked.connect(lambda: self.dispatch("cancel_capture"))
        self.view_start.valueChanged.connect(self.update_waveform)
        self.view_count.valueChanged.connect(self.update_waveform)
        self.save_bin.clicked.connect(lambda: self.save_result(False))
        self.save_csv.clicked.connect(lambda: self.save_result(True))

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
        self.worker.capturing.connect(self.show_capturing)
        self.worker.capture_progress.connect(self.show_capture_progress)
        self.worker.capture_ready.connect(self.show_capture)
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
        self.baud.setEnabled(ready and self.connected and not self.active and not self.capturing)
        self.configure_button.setEnabled(self.baud.isEnabled())
        self.hex_mode.setEnabled(ready and not self.active)
        self.stream_button.setEnabled(ready and self.connected)
        self.connect_button.setText("断开" if self.connected else "连接")
        self.stream_button.setText("停止接收" if self.active else "开始接收")
        can_capture = ready and self.connected and self.capture_supported and not self.capturing
        self.capture_button.setEnabled(can_capture)
        self.capture_rate.setEnabled(can_capture)
        self.capture_samples.setEnabled(can_capture)
        self.cancel_capture_button.setEnabled(ready and self.capturing)
        for button in (self.save_bin, self.save_csv):
            button.setEnabled(ready and self.capture_result is not None and not self.capturing)

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
        self.capture_supported = bool(info and info["capabilities"] & 2)
        if info:
            self.capture_rate.setMaximum(info["max_rate"])
            self.capture_samples.setMaximum(info["max_samples"])
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
        self.capture_message.setText(message)

    def start_capture(self):
        if self.capture_samples.value() % 8:
            self.capture_message.setText("点数必须是 8 的倍数，请调整后重试。")
            return
        self.dispatch("capture", (self.capture_rate.value(), self.capture_samples.value()))

    def show_capturing(self, active):
        self.capturing = active
        self.update_controls()

    def show_capture_progress(self, value, message):
        self.capture_progress.setValue(value)
        self.capture_message.setText(message)

    def show_capture(self, result):
        self.capture_result = result
        c = result.capture
        self.waveform.set_capture(result)
        self.view_start.setRange(0, c["samples"] - 1)
        self.view_start.setValue(0)
        self.view_count.setMaximum(c["samples"])
        self.view_count.setValue(c["samples"])
        self.update_waveform()
        self.show_capture_progress(100, f'采集 #{c["capture_id"]} 完成 · {c["samples"]:,} 点 · '
                                   f'实际 {c["actual_rate"]:,} S/s · '
                                   f'{c["samples"] / c["actual_rate"] * 1000:.3f} ms')

    def update_waveform(self):
        if self.capture_result is not None:
            self.view_count.setMaximum(self.capture_result.capture["samples"] - self.view_start.value())
            self.waveform.set_view(self.view_start.value(), self.view_count.value())

    def save_result(self, as_csv):
        suffix = "csv" if as_csv else "bin"
        path, _ = QFileDialog.getSaveFileName(self, "保存逻辑采集", f"capture.{suffix}",
                                            f"采集数据 (*.{suffix})")
        if path:
            try:
                save_capture(self.capture_result, path, as_csv)
                self.statusBar().showMessage(f"已保存：{path} 及 .json 元数据")
            except OSError as error:
                self.show_error(str(error))

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
