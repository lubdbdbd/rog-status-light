from __future__ import annotations

import sys

if '--hook' in sys.argv:
    from event_hook import main
    raise SystemExit(main())

import asyncio
import ctypes
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk
import winreg

import pystray
from PIL import Image, ImageDraw

from core import Status, StatusReader, frame, blend_frames
from lighting_lifecycle import release_lamps
from runtime_paths import DATA
from lamp_devices import is_target_lamp, connection_label, device_order
from background_access import package_identity, policy, access_hint

VERSION = '1.6.1'
ROOT = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).parent
DATA.mkdir(parents=True, exist_ok=True)
CONFIG_PATH = DATA / 'config.json'
DEFAULTS = {'paused': True, 'scroll_step_seconds': .14,
            'flash_half_period_seconds': .55, 'poll_seconds': .6}


def load_config():
    try:
        return DEFAULTS | json.loads(CONFIG_PATH.read_text(encoding='utf-8-sig'))
    except (OSError, ValueError):
        return DEFAULTS.copy()


def save_config(config):
    temporary = CONFIG_PATH.with_suffix('.tmp')
    temporary.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(CONFIG_PATH)


def registry_values(path):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
            result = {}
            for name in ('Color', 'Brightness', 'UseSystemAccentColor'):
                try:
                    result[name] = winreg.QueryValueEx(key, name)[0]
                except OSError:
                    pass
            return result
    except OSError:
        return {}


def original_palette(device_id):
    settings = registry_values(r'Software\Microsoft\Lighting')
    identifier = device_id[4:] if device_id.startswith('\\\\?\\') else device_id
    settings.update(registry_values('Software\\Microsoft\\Lighting\\Devices\\' + identifier))
    color = settings.get('Color', 0xFFFFFFFF)
    if settings.get('UseSystemAccentColor', 0):
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Windows\DWM') as key:
                color = winreg.QueryValueEx(key, 'AccentColor')[0]
        except OSError:
            pass
    rgb = (color & 255, color >> 8 & 255, color >> 16 & 255)
    brightness = max(0, min(1, settings.get('Brightness', 100) / 100))
    return rgb, brightness


class Controller:
    def __init__(self):
        self.config = load_config()
        self.stop = threading.Event()
        self.commands = queue.Queue()
        self.ui_commands = queue.Queue()
        self.ui_operations = queue.Queue()
        self.ui_ro_initialized = False
        self.status_commands = queue.Queue()
        self.lock = threading.Lock()
        self.latest_status = Status()
        self.state = {'mode': 'idle', 'reason': '已暂停 · 原色常亮', 'device': '正在查找键盘',
                      'available': False, 'active': 0, 'palette': '',
                      'background_hint': '',
                      'full_refreshes': 0,
                      'bluetooth_test_running': False, 'bluetooth_test_status': '蓝牙：尚未测试',
                      'hooks': (DATA/'hooks-installed.json').exists()}
        self.reader = StatusReader(Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))), DATA)
        self.test_mode = None
        self.test_until = 0
        self.test_started = 0
        self.bluetooth_test_requested = False
        self.bluetooth_cancel = threading.Event()

    def update(self, **values):
        with self.lock:
            self.state.update(values)

    def snapshot(self):
        with self.lock:
            return self.state.copy()

    def command(self, name):
        self.commands.put(name)

    async def open_bluetooth_device(self, device_id):
        # Windows requires creating the BLE object on the application's UI
        # thread; discovery and GATT exchanges continue on the worker thread.
        loop=asyncio.get_running_loop()
        ready=loop.create_future()
        def finish(operation,error):
            if ready.done():return
            if error is not None:ready.set_exception(error)
            else:ready.set_result(operation)
        def begin():
            try:
                if not self.ui_ro_initialized:
                    result=ctypes.windll.combase.RoInitialize(0)
                    self.ui_ro_initialized=result>=0
                from winrt.windows.devices.bluetooth import BluetoothLEDevice
                operation=BluetoothLEDevice.from_id_async(device_id)
                loop.call_soon_threadsafe(finish,operation,None)
            except Exception as error:
                loop.call_soon_threadsafe(finish,None,error)
        self.ui_operations.put(begin)
        operation=await asyncio.wait_for(ready,timeout=4)
        return await asyncio.wait_for(operation,timeout=4)

    def process_commands(self):
        while not self.commands.empty():
            name = self.commands.get_nowait()
            if name == 'auto':
                self.bluetooth_cancel.set()
                self.bluetooth_test_requested=False
                self.config['paused'] = False
                self.test_mode = None
            elif name == 'pause':
                self.bluetooth_cancel.set()
                self.bluetooth_test_requested=False
                self.config['paused'] = True
                self.test_mode = None
            elif name == 'ack':
                self.status_commands.put('ack')
            elif name == 'quit':
                self.stop.set()
                self.bluetooth_cancel.set()
                self.bluetooth_test_requested=False
            elif name in ('ble-test','ble-wave','ble-smooth'):
                if not self.snapshot()['bluetooth_test_running']:
                    self.config['paused']=True
                    self.test_mode=None
                    self.bluetooth_test_requested = name
            elif name == 'ble-cancel':
                self.bluetooth_cancel.set()
                self.bluetooth_test_requested=False
            elif name in ('idle', 'working', 'attention', 'demo'):
                self.test_mode = name
                self.test_started = time.perf_counter()
                self.test_until = self.test_started + (24 if name == 'demo' else 8)
            save_config(self.config)

    def desired(self):
        current = time.perf_counter()
        if self.test_mode and current < self.test_until:
            mode = self.test_mode
            if mode == 'demo':
                mode = ('idle', 'working', 'attention')[min(2, int((current-self.test_started)/8))]
            return mode, f'测试：{dict(idle="常亮",working="海浪呼吸",attention="呼吸提醒")[mode]}', 0
        self.test_mode = None
        if self.config['paused']:
            return 'idle', '已暂停 · 原色常亮', 0
        with self.lock:
            status = self.latest_status
        return status.mode, status.reason, status.active

    def status_worker(self):
        # SQLite and process scans can take longer than a lighting frame.
        # Keep the animation clock independent of those reads.
        while not self.stop.is_set():
            while not self.status_commands.empty():
                if self.status_commands.get_nowait() == 'ack':
                    self.reader.acknowledge()
            try:
                status = self.reader.snapshot()
            except Exception:
                logging.exception('Task state read failed; retrying')
                status = Status('idle', '状态读取待重试', 0)
            with self.lock:
                self.latest_status = status
            self.stop.wait(max(.2, self.config['poll_seconds']))

    async def lighting(self):
        from winrt.windows.devices.enumeration import DeviceInformation
        from winrt.windows.devices.lights import LampArray
        from winrt.windows.system import VirtualKey
        from winrt.windows.ui import Color
        from winrt.system import Array
        from bluetooth_backend import automatic_settings, BluetoothLighting
        bluetooth_settings=automatic_settings(DATA)
        bluetooth=None
        bluetooth_detected=False
        bluetooth_retry_after=0
        self.update(bluetooth_ready=bluetooth_settings is not None)
        if bluetooth_settings:self.update(bluetooth_test_status='蓝牙原色呼吸、数字行海浪及流畅度已验证')
        lamps = []
        last_scan = 0
        last_palette = 0
        last_mode = None
        mode_started = time.perf_counter()
        last_frame = None
        fps_started = mode_started
        rendered_frames = 0
        full_refreshes = 0
        try:
            while not self.stop.is_set():
                self.process_commands()
                now = time.perf_counter()
                if self.bluetooth_test_requested:
                    bluetooth_test_kind=self.bluetooth_test_requested
                    self.bluetooth_test_requested = False
                    if lamps:
                        self.update(bluetooth_test_status='请先将键盘切换到蓝牙模式，再启动蓝牙测试')
                    else:
                        if bluetooth is not None:
                            try:await bluetooth.close()
                            except Exception:logging.exception('Bluetooth restore before manual test failed')
                            bluetooth=None
                        from bluetooth_backend import run_manual_test,run_wave_test,run_smooth_test
                        self.bluetooth_cancel.clear()
                        self.update(bluetooth_test_running=True)
                        try:
                            test={'ble-wave':run_wave_test,'ble-smooth':run_smooth_test}.get(bluetooth_test_kind,run_manual_test)
                            await test(DATA,self.update,self.stop,self.bluetooth_cancel,self.process_commands,
                                       open_device=self.open_bluetooth_device)
                        except Exception as error:
                            logging.exception('Bluetooth test could not start')
                            self.update(bluetooth_test_status='蓝牙测试无法启动 · '+str(error))
                        finally:
                            self.update(bluetooth_test_running=False)
                        now=time.perf_counter()
                if now - last_scan > 3:
                    connected = [entry for entry in lamps if entry['array'].is_connected]
                    if len(connected) != len(lamps):
                        release_lamps([entry for entry in lamps if entry not in connected], Color)
                        last_frame = None
                    lamps = connected
                    if not lamps:
                        devices = await DeviceInformation.find_all_async_aqs_filter(LampArray.get_device_selector())
                        for device in sorted(devices, key=device_order):
                            if not is_target_lamp(device.id):
                                continue
                            lamp = await LampArray.from_id_async(device.id)
                            escape = list(lamp.get_indices_for_key(VirtualKey.ESCAPE))
                            insert = list(lamp.get_indices_for_key(VirtualKey.INSERT))
                            if not escape or not insert:
                                continue
                            row_y = lamp.get_lamp_info(escape[0]).position.y
                            row = [i for i in range(lamp.lamp_count)
                                   if abs(lamp.get_lamp_info(i).position.y - row_y) < .001]
                            row.sort(key=lambda i: lamp.get_lamp_info(i).position.x)
                            if len(row) != 15 or row[0] != escape[0] or row[-1] != insert[0]:
                                logging.warning('Unsupported top-row mapping: %s', len(row))
                                continue
                            def availability_changed(sender, args):
                                logging.info('Lighting availability changed: %s', sender.is_available)
                            token = lamp.add_availability_changed(availability_changed)
                            lamp.is_enabled = True
                            rgb, brightness = original_palette(device.id)
                            lamps.append({'array': lamp, 'id': device.id, 'name': device.name,
                                          'row': row, 'rgb': rgb, 'brightness': brightness,
                                          'availability_token': token})
                            logging.info('Lighting interface connected; %s lamps; top row verified', lamp.lamp_count)
                            break
                    if lamps and bluetooth is not None:
                        try:await bluetooth.close(restore=False)
                        finally:bluetooth=None
                    device_name = (connection_label(lamps[0]['id']) if lamps else '未连接灯控接口')
                    if not lamps:
                        try:
                            from bluetooth_backend import connected_keyboards
                            bluetooth_detected=bluetooth.connected if bluetooth is not None else bool(await connected_keyboards())
                            if bluetooth_detected:
                                device_name='ROG 魔导士 · 蓝牙'+('' if bluetooth_settings else '（联动待验证）')
                                self.update(background_hint=('蓝牙已就绪 · 点击“自动联动”开始' if bluetooth_settings else
                                            '可手动启动“蓝牙测试”，验证期间才会发送灯光命令'))
                            else:
                                self.update(background_hint='请连接键盘，或按键唤醒蓝牙键盘')
                        except Exception:
                            self.update(background_hint='蓝牙状态暂时不可用；USB 和原装接收器仍可自动联动')
                    self.update(device=device_name)
                    for entry in lamps:
                        entry['policy'] = policy(entry['id'])
                    last_scan = now
                if now - last_palette > 2:
                    for entry in lamps:
                        entry['rgb'], entry['brightness'] = original_palette(entry['id'])
                    last_palette = now
                mode, reason, active = self.desired()
                if mode != last_mode:
                    logging.info('Effect -> %s', mode)
                    mode_started = now
                    last_mode = mode
                    last_frame = None
                    for entry in lamps:
                        entry['transition_from'] = entry.get('last_colors')
                        entry['transition_done'] = False
                available = False
                for entry in lamps:
                    lamp = entry['array']
                    has_control = lamp.is_available
                    if has_control != entry.get('had_control'):
                        entry['policy'] = policy(entry['id'])
                        if not has_control:
                            logging.warning('Background lighting unavailable: %s',
                                            access_hint(entry['policy'], False))
                    self.update(background_hint=access_hint(entry.get('policy', {}), has_control))
                    available |= has_control
                    colors = frame(entry['rgb'], lamp.lamp_count, entry['row'], mode, now-mode_started,
                                   self.config['scroll_step_seconds'], self.config['flash_half_period_seconds'])
                    colors = blend_frames(entry.get('transition_from'), colors, (now-mode_started)/.24)
                    current_frame = (tuple(colors), entry['brightness'], has_control)
                    first_frame = not entry.get('had_control')
                    # The receiver stays connected while the wireless keyboard
                    # sleeps. Windows availability therefore cannot tell us if
                    # the keyboard has retained its last frame or gain setting.
                    # Periodically resend both, including steady idle frames.
                    resync = first_frame or now-entry.get('last_resync', 0) >= 2
                    if has_control and (resync or current_frame != last_frame):
                        if resync or entry.get('sent_brightness') != entry['brightness']:
                            lamp.brightness_level = entry['brightness']
                            entry['sent_brightness'] = entry['brightness']
                        if all(color == colors[0] for color in colors):
                            r, g, b = colors[0]
                            lamp.set_color(Color(a=255, r=r, g=g, b=b))
                        else:
                            row_only = (mode == 'working' and now-mode_started >= .24
                                        and entry.get('transition_done', False)
                                        and not resync and entry.get('sent_rgb') == entry['rgb'])
                            indices = entry['row'] if row_only else list(range(lamp.lamp_count))
                            lamp.set_colors_for_indices(
                                Array(Color, [Color(a=255,r=colors[i][0],g=colors[i][1],b=colors[i][2]) for i in indices]),
                                Array('i', indices))
                        entry['sent_rgb'] = entry['rgb']
                        entry['last_colors'] = colors
                        entry['transition_done'] = now-mode_started >= .24
                        if resync:
                            entry['last_resync'] = now
                            full_refreshes += 1
                            self.update(full_refreshes=full_refreshes, last_full_refresh_at=time.time())
                        rendered_frames += 1
                        last_frame = current_frame
                    entry['had_control'] = has_control
                    self.update(palette='#%02X%02X%02X' % entry['rgb'])
                if not lamps and bluetooth_settings:
                    should_run=not self.config['paused'] or self.test_mode is not None
                    try:
                        if bluetooth is not None and (not should_run or not bluetooth.connected):
                            try:await bluetooth.close(restore=bluetooth.connected)
                            finally:bluetooth=None
                        if bluetooth is None and should_run and bluetooth_detected and now>=bluetooth_retry_after:
                            bluetooth=BluetoothLighting(bluetooth_settings,self.open_bluetooth_device)
                            await bluetooth.connect()
                            logging.info('Bluetooth automatic lighting connected: %s',bluetooth.transport)
                        if bluetooth is not None:
                            sent=await bluetooth.render(mode,now-mode_started,
                                self.config['scroll_step_seconds'],self.config['flash_half_period_seconds'],self.process_commands)
                            available=True
                            rendered_frames+=int(sent)
                            self.update(device='ROG 魔导士 · 蓝牙',palette='#%02X%02X%02X' % tuple(bluetooth.rgb),
                                        background_hint='蓝牙后台联动 · 空闲常亮 / 数字行海浪 / 原色呼吸提醒')
                    except Exception as error:
                        logging.exception('Bluetooth automatic lighting reconnect scheduled')
                        if bluetooth is not None:
                            try:await bluetooth.close()
                            except Exception:logging.exception('Bluetooth reconnect restore failed')
                            bluetooth=None
                        bluetooth_retry_after=time.perf_counter()+5
                        self.update(background_hint='蓝牙灯控正在重连 · 可按键唤醒键盘')
                self.update(mode=mode, reason=reason, active=active, available=available)
                if now-fps_started >= 2:
                    self.update(render_fps=round(rendered_frames/(now-fps_started),1))
                    rendered_frames = 0
                    fps_started = now
                minimum = max((entry['array'].min_update_interval.total_seconds() for entry in lamps), default=.05)
                interval = max(1/60, minimum)
                if bluetooth is not None:interval=1/30
                if mode == 'idle' and now-mode_started >= .24:
                    interval = max(.1, interval)
                # This is the dedicated animation thread. Windows' high-resolution
                # sleep avoids the coarse IOCP timeout used by asyncio.sleep;
                # no other coroutine runs here between device discovery calls.
                delay = max(0, interval - (time.perf_counter()-now))
                if delay:
                    time.sleep(delay)
                await asyncio.sleep(0)
        finally:
            if bluetooth is not None:
                try:await bluetooth.close()
                except Exception:logging.exception('Bluetooth lighting restore on exit failed')
            release_lamps(lamps, Color)

    def worker(self):
        ctypes.windll.combase.RoInitialize(1)
        try:
            while not self.stop.is_set():
                try:
                    asyncio.run(self.lighting())
                    break
                except Exception as error:
                    logging.exception('Lighting reconnect scheduled')
                    self.update(device='灯光接口正在重连', available=False)
                    self.stop.wait(3)
        finally:
            ctypes.windll.combase.RoUninitialize()


def icon_image():
    image = Image.new('RGBA', (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((3, 12, 61, 53), radius=8, fill='#20241f')
    for row in range(3):
        for column in range(6):
            x, y = 9+column*8, 19+row*9
            draw.rounded_rectangle((x,y,x+5,y+5), radius=1, fill='#bad7a9')
    return image


def main():
    from native_signals import SignalInbox, send
    if '--command' in sys.argv:
        position = sys.argv.index('--command') + 1
        if position >= len(sys.argv):
            return 2
        return 0 if send(sys.argv[position]) else 3
    if '--self-check' in sys.argv:
        from winrt.windows.devices.enumeration import DeviceInformation
        from winrt.windows.devices.lights import LampArray
        from winrt.windows.ui import Color
        from winrt.system import Array
        async def check():
            devices = await DeviceInformation.find_all_async_aqs_filter(LampArray.get_device_selector())
            results = []
            for device in devices:
                if is_target_lamp(device.id):
                    lamp = await LampArray.from_id_async(device.id)
                    results.append({'name':device.name,'id':device.id,'count':lamp.lamp_count,
                                    'supported':True,'available':lamp.is_available,
                                    'background_policy':policy(device.id),
                                    'minimum_update_ms':lamp.min_update_interval.total_seconds()*1000,
                                    'palette':original_palette(device.id)})
                else:
                    results.append({'name':device.name,'id':device.id,'supported':False})
            Array(Color,[Color(a=255,r=1,g=2,b=3)])
            target = Path(sys.argv[sys.argv.index('--report')+1]) if '--report' in sys.argv else DATA/'self-check.json'
            target.write_text(json.dumps({'ok':True,'version':VERSION,'data_dir':str(DATA),
                                         'identity':package_identity(),
                                         'devices':results},ensure_ascii=False,indent=2),encoding='utf-8')
        asyncio.run(check())
        return 0
    if '--configure-hooks' in sys.argv:
        from hook_setup import main as setup
        try:
            result = setup(ROOT)
            (DATA/'hooks-setup.log').write_text(f'Exit code: {result}\n',encoding='utf-8')
            return result
        except Exception as error:
            import traceback
            (DATA/'hooks-setup.log').write_text(traceback.format_exc(),encoding='utf-8')
            return 2
    ctypes.windll.kernel32.CreateMutexW.restype = ctypes.c_void_p
    ctypes.windll.kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    mutex = ctypes.windll.kernel32.CreateMutexW(None, False, 'Local\\ROGStatusLight-v1')
    if ctypes.windll.kernel32.GetLastError() == 183:
        if '--show' in sys.argv:
            send('show')
        ctypes.windll.kernel32.CloseHandle(mutex)
        return 0
    signals = SignalInbox()
    log_handler = RotatingFileHandler(DATA/'app.log', maxBytes=400_000, backupCount=2, encoding='utf-8')
    logging.basicConfig(level=logging.INFO, handlers=[log_handler], format='%(asctime)s %(levelname)s %(message)s')
    logging.info('Starting %s; identity=%s', VERSION, package_identity())
    controller = Controller()
    root = tk.Tk()
    root.title('ROG 状态灯')
    root.geometry('690x470')
    root.minsize(690,470)
    root.configure(bg='#f3f3ee')
    style = ttk.Style(root)
    style.theme_use('clam')
    style.configure('TButton', padding=(12,10), font=('Microsoft YaHei UI',10))
    status_text = tk.StringVar(value='正在连接')
    detail_text = tk.StringVar()
    bluetooth_text = tk.StringVar(value='蓝牙：尚未测试')
    tk.Label(root,text='ROG 状态灯',font=('Microsoft YaHei UI',23,'bold'),bg='#f3f3ee',fg='#252b23').pack(anchor='w',padx=28,pady=(22,8))
    tk.Label(root,textvariable=status_text,font=('Microsoft YaHei UI',15),bg='#f3f3ee',fg='#252b23').pack(anchor='w',padx=30,pady=6)
    tk.Label(root,textvariable=detail_text,font=('Microsoft YaHei UI',10),bg='#f3f3ee',fg='#62665f',justify='left',wraplength=570).pack(anchor='w',padx=30,pady=(0,16))
    buttons = tk.Frame(root,bg='#f3f3ee')
    buttons.pack(anchor='w',padx=28)
    for caption, command in [('自动联动','auto'),('暂停联动','pause'),('取消提醒','ack')]:
        ttk.Button(buttons,text=caption,command=lambda name=command:controller.command(name)).pack(side='left',padx=(0,8))
    ttk.Button(buttons,text='灯光设置',command=lambda:os.startfile('ms-settings:personalization-lighting')).pack(side='left',padx=(0,8))
    tests = tk.Frame(root,bg='#f3f3ee')
    tests.pack(anchor='w',padx=28,pady=12)
    for caption, command in [('常亮测试','idle'),('海浪呼吸','working'),('呼吸提醒','attention'),('完整测试','demo')]:
        ttk.Button(tests,text=caption,command=lambda name=command:controller.command(name)).pack(side='left',padx=(0,6))
    bluetooth_controls=tk.Frame(root,bg='#f3f3ee')
    bluetooth_controls.pack(anchor='w',padx=28,pady=(0,6))
    bluetooth_button=ttk.Button(bluetooth_controls,text='蓝牙测试',command=lambda:controller.command('ble-test'))
    bluetooth_button.pack(side='left',padx=(0,8))
    bluetooth_wave_button=ttk.Button(bluetooth_controls,text='蓝牙海浪测试',command=lambda:controller.command('ble-wave'))
    bluetooth_wave_button.pack(side='left',padx=(0,8))
    bluetooth_smooth_button=ttk.Button(bluetooth_controls,text='蓝牙流畅测试',command=lambda:controller.command('ble-smooth'))
    bluetooth_smooth_button.pack(side='left',padx=(0,8))
    ttk.Button(bluetooth_controls,text='停止测试',command=lambda:controller.command('ble-cancel')).pack(side='left')
    tk.Label(root,textvariable=bluetooth_text,font=('Microsoft YaHei UI',9),bg='#f3f3ee',fg='#62665f',wraplength=630,justify='left').pack(anchor='w',padx=30,pady=(0,4))
    tk.Label(root,text='关闭窗口后继续驻留托盘。退出请使用托盘菜单。',font=('Microsoft YaHei UI',9),bg='#f3f3ee',fg='#62665f').pack(anchor='w',padx=30,pady=8)
    root.protocol('WM_DELETE_WINDOW', root.withdraw)
    if '--show' not in sys.argv:
        root.withdraw()
    tray = pystray.Icon('ROGStatusLight',icon_image(),'ROG 状态灯', menu=pystray.Menu(
        pystray.MenuItem('打开控制面板',lambda:controller.ui_commands.put('show'),default=True),
        pystray.MenuItem('自动联动',lambda:controller.command('auto')),
        pystray.MenuItem('暂停 · 原色常亮',lambda:controller.command('pause')),
        pystray.MenuItem('取消提醒',lambda:controller.command('ack')),
        pystray.MenuItem('蓝牙测试',lambda:controller.command('ble-test')),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem('退出',lambda:controller.command('quit'))))
    tray.run_detached()
    status_thread = threading.Thread(target=controller.status_worker,daemon=True,name='task-state-reader')
    status_thread.start()
    thread = threading.Thread(target=controller.worker,daemon=True,name='lighting-controller')
    thread.start()
    previous_status = None
    last_status_write = 0

    def tick():
        nonlocal previous_status, last_status_write
        while not controller.ui_operations.empty():
            controller.ui_operations.get_nowait()()
        for command in signals.poll():
            if command == 'show':
                controller.ui_commands.put('show')
            else:
                controller.command(command)
        if (DATA/'stop-request').exists():
            (DATA/'stop-request').unlink(missing_ok=True)
            controller.stop.set()
        if (DATA/'show-panel').exists():
            (DATA/'show-panel').unlink(missing_ok=True)
            controller.ui_commands.put('show')
        while not controller.ui_commands.empty():
            controller.ui_commands.get_nowait()
            root.deiconify()
            root.lift()
        state = controller.snapshot()
        status_text.set(state['reason'])
        bluetooth_text.set(state['bluetooth_test_status'])
        bluetooth_button.configure(state='disabled' if state['bluetooth_test_running'] else 'normal')
        bluetooth_wave_button.configure(state='disabled' if state['bluetooth_test_running'] else 'normal')
        bluetooth_smooth_button.configure(state='disabled' if state['bluetooth_test_running'] else 'normal')
        permission = '已取得灯光控制权' if state['available'] else '当前未获得灯控权'
        if '蓝牙' in state['device'] and not state['available']:
            permission=('蓝牙测试进行中' if state['bluetooth_test_running'] else
                        '蓝牙已就绪 · 当前暂停' if state.get('bluetooth_ready') and controller.config['paused'] else
                        '蓝牙正在连接' if state.get('bluetooth_ready') else '蓝牙联动待验证')
        detail_text.set(f"{state['device']}\n{permission}  ·  原色 {state['palette'] or '读取中'}"
                        + ('\n'+state['background_hint'] if state['background_hint'] else ''))
        tray.title = ('ROG 状态灯 · '+(state['reason'] if state['available'] else permission))[:125]
        public = state | {'version':VERSION,'paused':controller.config['paused'],
                          'panel_visible':bool(root.winfo_viewable()),
                          'background_control':state['available'] and not bool(root.winfo_viewable()),
                          'updated_at':time.time(),'pid':os.getpid()}
        comparable = {k:v for k,v in public.items() if k != 'updated_at'}
        if comparable != previous_status or time.perf_counter()-last_status_write >= 2:
            temporary = DATA/'status.tmp'
            temporary.write_text(json.dumps(public,ensure_ascii=False,indent=2),encoding='utf-8')
            temporary.replace(DATA/'status.json')
            previous_status = comparable
            last_status_write = time.perf_counter()
        if controller.stop.is_set():
            tray.stop()
            root.destroy()
            return
        root.after(200,tick)

    root.after(200,tick)
    try:
        root.mainloop()
    finally:
        controller.stop.set()
        thread.join(timeout=12)
        status_thread.join(timeout=3)
        signals.close()
        if controller.ui_ro_initialized:ctypes.windll.combase.RoUninitialize()
        ctypes.windll.kernel32.CloseHandle(mutex)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception:
        import traceback
        (DATA/'startup-error.log').write_text(traceback.format_exc(),encoding='utf-8')
        raise SystemExit(1)
