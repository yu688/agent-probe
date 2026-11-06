"""macOS 的厂商自定义 Bulk USB 接口，不改变固件及 Windows 驱动。"""
import os


def _usb():
    # 延迟导入，使协议测试和 Windows 入口不需要 macOS 的可选依赖。
    try:
        import usb.core
        import usb.util
        import libusb_package
    except ImportError as error:
        raise OSError("请在项目虚拟环境安装依赖：pip install -r host/requirements-macos.txt") from error
    backend = libusb_package.get_libusb1_backend()
    if backend is None:
        raise OSError("无法加载 libusb；请重新安装 host/requirements-macos.txt")
    return usb.core, usb.util, backend


def _find(core, backend):
    return core.find(find_all=True, backend=backend,
                     idVendor=int(os.environ.get("AGENT_USB_VID", "0xCAFE"), 0),
                     idProduct=int(os.environ.get("AGENT_USB_PID", "0x40F0"), 0))


def _path(device):
    return f"libusb:{device.bus}:{device.address}"


def list_devices():
    core, _, backend = _usb()
    return sorted(_path(device) for device in _find(core, backend))


class USB:
    def __init__(self, path):
        self.core, self.util, backend = _usb()
        self.device = None
        self.claimed = False
        for device in _find(self.core, backend):
            if _path(device) == path:
                self.device = device
                break
        if self.device is None:
            raise OSError("设备已断开或地址改变，请重新扫描")
        try:
            config = self.device.get_active_configuration()
            if config.bConfigurationValue != 1:
                self.device.set_configuration(1)
                config = self.device.get_active_configuration()
            interface = config[(0, 0)]
            endpoints = {ep.bEndpointAddress: ep for ep in interface}
            if (interface.bInterfaceClass != 0xFF or
                    any(address not in endpoints or endpoints[address].bmAttributes & 3 != 2
                        for address in (0x01, 0x81))):
                raise OSError("设备接口与 Agent Probe Bulk 协议不匹配")
            self.util.claim_interface(self.device, 0)
            self.claimed = True
        except Exception:
            self.close()
            raise

    def write(self, data):
        if self.device is None:
            raise OSError("USB 会话已关闭")
        count = self.device.write(0x01, data, timeout=200)
        if count != len(data):
            raise OSError("USB 写入不完整，需重新建立会话")

    def read(self):
        if self.device is None:
            raise OSError("USB 会话已关闭")
        try:
            return bytes(self.device.read(0x81, 4096, timeout=200))
        except self.core.USBTimeoutError:
            return b""

    def close(self):
        device, self.device = self.device, None
        if device is not None:
            try:
                if self.claimed:
                    self.util.release_interface(device, 0)
            finally:
                self.claimed = False
                self.util.dispose_resources(device)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
