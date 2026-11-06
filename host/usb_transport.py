"""按主机平台选择 USB 传输层；Windows 继续使用标准库 WinUSB。"""
import sys

if sys.platform == "win32":
    if __package__:
        from .winusb_transport import WinUSB as USB, list_devices
    else:
        from winusb_transport import WinUSB as USB, list_devices
elif sys.platform == "darwin":
    if __package__:
        from .macos_usb_transport import USB, list_devices
    else:
        from macos_usb_transport import USB, list_devices
else:
    def list_devices():
        raise OSError("当前 USB 传输层支持 Windows 和 macOS")

    class USB:
        def __init__(self, path):
            raise OSError("当前 USB 传输层支持 Windows 和 macOS")
