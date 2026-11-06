"""Windows 自带 WinUSB API 的最小封装，不需要 libusb 或 PyUSB。"""
import ctypes as C
from ctypes import wintypes as W
import os
import uuid

INTERFACE_GUID = "7C9B140E-5C85-4783-8B8A-F0BC97913546"


class GUID(C.Structure):
    _fields_ = [("data1", W.DWORD), ("data2", W.WORD), ("data3", W.WORD),
                ("data4", C.c_ubyte * 8)]


class InterfaceData(C.Structure):
    _fields_ = [("cbSize", W.DWORD), ("guid", GUID), ("flags", W.DWORD),
                ("reserved", C.c_size_t)]


def _libraries():
    if os.name != "nt":
        raise OSError("该传输层仅用于 Windows")
    setup = C.WinDLL("setupapi", use_last_error=True)
    kernel = C.WinDLL("kernel32", use_last_error=True)
    usb = C.WinDLL("winusb", use_last_error=True)
    setup.SetupDiGetClassDevsW.argtypes = [C.POINTER(GUID), W.LPCWSTR, W.HWND, W.DWORD]
    setup.SetupDiGetClassDevsW.restype = W.HANDLE
    setup.SetupDiEnumDeviceInterfaces.argtypes = [W.HANDLE, C.c_void_p, C.POINTER(GUID), W.DWORD, C.POINTER(InterfaceData)]
    setup.SetupDiEnumDeviceInterfaces.restype = W.BOOL
    setup.SetupDiGetDeviceInterfaceDetailW.argtypes = [W.HANDLE, C.POINTER(InterfaceData), C.c_void_p, W.DWORD, C.POINTER(W.DWORD), C.c_void_p]
    setup.SetupDiGetDeviceInterfaceDetailW.restype = W.BOOL
    setup.SetupDiDestroyDeviceInfoList.argtypes = [W.HANDLE]
    setup.SetupDiDestroyDeviceInfoList.restype = W.BOOL
    kernel.CreateFileW.argtypes = [W.LPCWSTR, W.DWORD, W.DWORD, C.c_void_p, W.DWORD, W.DWORD, W.HANDLE]
    kernel.CreateFileW.restype = W.HANDLE
    kernel.CloseHandle.argtypes = [W.HANDLE]
    kernel.CloseHandle.restype = W.BOOL
    usb.WinUsb_Initialize.argtypes = [W.HANDLE, C.POINTER(C.c_void_p)]
    usb.WinUsb_Initialize.restype = W.BOOL
    usb.WinUsb_Free.argtypes = [C.c_void_p]
    usb.WinUsb_Free.restype = W.BOOL
    usb.WinUsb_SetPipePolicy.argtypes = [C.c_void_p, C.c_ubyte, W.ULONG, W.ULONG, C.c_void_p]
    usb.WinUsb_SetPipePolicy.restype = W.BOOL
    for name in ("WinUsb_ReadPipe", "WinUsb_WritePipe"):
        method = getattr(usb, name)
        method.argtypes = [C.c_void_p, C.c_ubyte, C.c_void_p, W.ULONG, C.POINTER(W.ULONG), C.c_void_p]
        method.restype = W.BOOL
    return setup, kernel, usb


def _check(ok):
    if not ok:
        raise C.WinError(C.get_last_error())


def list_devices():
    setup, _, _ = _libraries()
    guid = GUID.from_buffer_copy(uuid.UUID(INTERFACE_GUID).bytes_le)
    devices = setup.SetupDiGetClassDevsW(C.byref(guid), None, None, 0x12)
    if devices == C.c_void_p(-1).value:
        raise C.WinError(C.get_last_error())
    paths = []
    try:
        index = 0
        while True:
            item = InterfaceData()
            item.cbSize = C.sizeof(item)
            if not setup.SetupDiEnumDeviceInterfaces(devices, None, C.byref(guid), index, C.byref(item)):
                if C.get_last_error() == 259:
                    break
                raise C.WinError(C.get_last_error())
            required = W.DWORD()
            setup.SetupDiGetDeviceInterfaceDetailW(devices, C.byref(item), None, 0, C.byref(required), None)
            if C.get_last_error() != 122 or required.value < 8:
                raise C.WinError(C.get_last_error())
            detail = C.create_string_buffer(required.value)
            C.cast(detail, C.POINTER(W.DWORD))[0] = 8 if C.sizeof(C.c_void_p) == 8 else 6
            _check(setup.SetupDiGetDeviceInterfaceDetailW(devices, C.byref(item), detail, required, None, None))
            paths.append(C.wstring_at(C.addressof(detail) + 4))
            index += 1
    finally:
        setup.SetupDiDestroyDeviceInfoList(devices)
    return paths


class WinUSB:
    def __init__(self, path):
        _, self.kernel, self.usb = _libraries()
        self.file = None
        self.interface = C.c_void_p()
        self.file = self.kernel.CreateFileW(path, 0xC0000000, 3, None, 3, 0x40000000, None)
        if self.file == C.c_void_p(-1).value:
            self.file = None
            raise C.WinError(C.get_last_error())
        try:
            _check(self.usb.WinUsb_Initialize(self.file, C.byref(self.interface)))
            for endpoint in (0x01, 0x81):
                timeout_ms = W.ULONG(200)
                _check(self.usb.WinUsb_SetPipePolicy(self.interface, endpoint, 3, C.sizeof(timeout_ms), C.byref(timeout_ms)))
        except Exception:
            self.close()
            raise

    def write(self, data):
        # Bulk OUT 0x01 从主机到设备，data 已由上层编码。
        buffer = C.create_string_buffer(data)
        count = W.ULONG()
        _check(self.usb.WinUsb_WritePipe(self.interface, 0x01, buffer, len(data), C.byref(count), None))
        if count.value != len(data):
            raise OSError("USB 写入不完整，需重新建立会话")

    def read(self):
        # Bulk IN 0x81 从设备到主机，按实际读取长度返回一段字节流。
        buffer = C.create_string_buffer(4096)
        count = W.ULONG()
        if not self.usb.WinUsb_ReadPipe(self.interface, 0x81, buffer, len(buffer), C.byref(count), None):
            error = C.get_last_error()
            if error == 121:
                return b""
            raise C.WinError(error)
        return buffer.raw[:count.value]

    def close(self):
        if self.interface.value:
            self.usb.WinUsb_Free(self.interface)
            self.interface = C.c_void_p()
        if self.file is not None:
            self.kernel.CloseHandle(self.file)
            self.file = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
