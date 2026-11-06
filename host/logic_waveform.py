"""四路数字波形，概览使用逐像素高低包络，保留窄脉冲。"""
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import QWidget

if __package__:
    from .logic_capture import unpack_samples
else:
    from logic_capture import unpack_samples


class LogicWaveform(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(260)
        self.samples = b""
        self.rate = 1
        self.start = 0
        self.count = 1024

    def set_capture(self, result):
        self.samples = unpack_samples(result.data)
        self.rate = result.capture["actual_rate"]
        self.start = 0
        self.count = len(self.samples)
        self.update()

    def set_view(self, start, count):
        self.start = min(start, max(0, len(self.samples) - 1))
        self.count = min(count, len(self.samples) - self.start)
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#111c2c"))
        if not self.samples:
            p.setPen(QColor("#a7bbd3"))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "采集完成后显示 GP16～GP19 四路波形")
            return
        left, top = 80, 36
        width = max(1, self.width() - left - 20)
        height = max(40, (self.height() - 68) // 4)
        count = self.count
        p.setPen(QColor("#34465e"))
        for tick in range(6):
            x = left + tick * width // 5
            p.drawLine(x, top, x, top + height * 4)
            p.setPen(QColor("#a7bbd3"))
            seconds = (self.start + count * tick / 5) / self.rate
            label = f"{seconds * 1000:.4g} ms"
            label_width = p.fontMetrics().horizontalAdvance(label)
            label_x = max(4, min(x - label_width // 2, self.width() - label_width - 6))
            p.drawText(label_x, 24, label)
            p.setPen(QColor("#34465e"))
        colors = ("#65d9ba", "#66b7ff", "#ffc776", "#d39bff")
        # 每个像素桶同时保留 AND/OR，概览不会把窄脉冲当成稳定电平。
        buckets = []
        if count > width:
            for x in range(width):
                lo = self.start + x * count // width
                hi = self.start + (x + 1) * count // width
                low, high = 15, 0
                for sample in self.samples[lo:hi]:
                    low &= sample
                    high |= sample
                buckets.append((low, high))
        for channel, color in enumerate(colors):
            high_y = top + channel * height + 8
            low_y = high_y + height - 24
            p.setPen(QColor("#a7bbd3"))
            p.drawText(12, high_y + 15, f"GP{16 + channel}")
            p.drawText(62, high_y + 4, "1")
            p.drawText(62, low_y + 4, "0")
            p.setPen(QPen(QColor(color), 1.5))
            if buckets:
                previous = None
                for x, (low, high) in enumerate(buckets):
                    y = high_y if high & (1 << channel) else low_y
                    y2 = high_y if low & (1 << channel) else low_y
                    p.drawLine(left + x, y, left + x + 1, y)
                    p.drawLine(left + x, y, left + x, y2)
                    if previous is not None:
                        p.drawLine(left + x, previous, left + x, y)
                    previous = y2
            else:
                previous = None
                for index in range(count):
                    x = left + index * width // count
                    end = left + (index + 1) * width // count
                    y = high_y if self.samples[self.start + index] & (1 << channel) else low_y
                    if previous is not None:
                        p.drawLine(x, previous, x, y)
                    p.drawLine(x, y, end, y)
                    previous = y
        p.end()
