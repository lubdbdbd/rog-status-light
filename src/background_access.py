"""Inspect the keyboard lighting policy for the currently connected USB port."""
from __future__ import annotations

import ctypes
import winreg

from lamp_devices import is_target_lamp

LIGHTING = r'Software\Microsoft\Lighting'
PROVIDER = 'Local.ROGStatusLight_k2je547w4p4nm'


def device_path(device_id):
    if not is_target_lamp(device_id):
        raise ValueError('Not a supported keyboard lighting interface')
    identifier = device_id[4:] if device_id.startswith('\\\\?\\') else device_id
    if '\\' in identifier or not identifier:
        raise ValueError('Invalid device identifier')
    return LIGHTING + '\\Devices\\' + identifier


def values(path):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
            result = {}
            for i in range(winreg.QueryInfoKey(key)[1]):
                name = winreg.EnumValue(key, i)[0]
                value, kind = winreg.QueryValueEx(key, name)
                result[name] = {'value': value, 'kind': kind}
            return result
    except FileNotFoundError:
        return {}


def ordered_providers(settings):
    return [item['value'] for name, item in sorted(
        settings.items(), key=lambda item: int(item[0]) if item[0].isdecimal() else 2**31)
        if name.isdecimal() and isinstance(item['value'], str)]


def policy(device_id):
    path = device_path(device_id)
    global_settings = values(LIGHTING)
    settings = global_settings | values(path)
    providers = ordered_providers(values(path + '\\Providers'))
    if not providers:
        providers = ordered_providers(values(LIGHTING + '\\Providers'))
    return {'providers': providers,
            'preferred': bool(providers and providers[0] == PROVIDER),
            'foreground_override': settings.get('ControlledByForegroundApp', {'value': 1})['value'],
            'dynamic_lighting': settings.get('AmbientLightingEnabled', {'value': 1})['value']}


def package_identity():
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    result = {}
    for name in ('GetCurrentPackageFullName', 'GetCurrentApplicationUserModelId'):
        function = getattr(kernel, name)
        function.argtypes = [ctypes.POINTER(ctypes.c_uint32), ctypes.c_wchar_p]
        function.restype = ctypes.c_long
        size = ctypes.c_uint32(1024)
        buffer = ctypes.create_unicode_buffer(size.value)
        error = function(ctypes.byref(size), buffer)
        result[name] = buffer.value if error == 0 else f'error:{error}'
    return result



def access_hint(settings, available):
    if available:
        return ''
    if not settings.get('dynamic_lighting', 1):
        return '请开启 Windows 动态光效。'
    if not settings.get('preferred', False):
        return '请打开当前接收器或键盘的设备卡片，将“ROG 状态灯”排到背景光控第一位。'
    if settings.get('foreground_override', 1):
        return '前台灯光应用可能正在占用；可关闭此设备的“前台中的兼容应用始终控制光效”。'
    return '正在等待 Windows 释放灯光控制权。'
