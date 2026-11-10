"""Session-local commands that work across packaged filesystem views."""
import ctypes
from ctypes import wintypes

COMMANDS = ('quit', 'pause', 'ack', 'idle', 'working', 'attention', 'demo', 'auto', 'show', 'ble-test', 'ble-wave', 'ble-smooth', 'ble-cancel')
PREFIX = 'Local\\ROGStatusLight-v1-'


def kernel():
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    api.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
    api.CreateEventW.restype = wintypes.HANDLE
    api.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    api.OpenEventW.restype = wintypes.HANDLE
    api.SetEvent.argtypes = [wintypes.HANDLE]
    api.SetEvent.restype = wintypes.BOOL
    api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    api.WaitForSingleObject.restype = wintypes.DWORD
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    api.CloseHandle.restype = wintypes.BOOL
    return api


def send(command, prefix=PREFIX):
    if command not in COMMANDS:
        raise ValueError('Unknown command')
    api = kernel()
    handle = api.OpenEventW(0x0002, False, prefix + command)
    if not handle:
        return False
    try:
        if not api.SetEvent(handle):
            raise ctypes.WinError(ctypes.get_last_error())
        return True
    finally:
        api.CloseHandle(handle)


class SignalInbox:
    def __init__(self, prefix=PREFIX):
        self.api = kernel()
        self.handles = {}
        try:
            for command in COMMANDS:
                handle = self.api.CreateEventW(None, False, False, prefix + command)
                if not handle:
                    raise ctypes.WinError(ctypes.get_last_error())
                self.handles[command] = handle
        except Exception:
            self.close()
            raise

    def poll(self):
        return [command for command, handle in self.handles.items()
                if self.api.WaitForSingleObject(handle, 0) == 0]

    def close(self):
        for handle in self.handles.values():
            self.api.CloseHandle(handle)
        self.handles.clear()
