import asyncio
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

WORK=Path(__file__).resolve().parent
sys.path[:0]=[str(WORK.parent/'src'),str(WORK.parents[1]/'work'/'deps')]
import bluetooth_backend as ble
import app


class Wire:
    def __init__(self):
        self.device=SimpleNamespace(connection_status=1)
        self.calls=[]
        self.packet_seconds=.001
        self.closed=False

    async def connect(self):self.calls.append(('connect',))
    def optimize_streaming(self):return {}
    async def effect(self,rgb,brightness,breathing=False):
        self.calls.append(('effect',tuple(rgb),brightness,breathing))
    async def direct(self,entries,tick=None,without_response=False):
        entries=list(entries)
        self.calls.append(('direct',entries,without_response))
        self.packet_seconds=.001
        if tick:tick()
    def close(self):self.closed=True


class AutomaticBluetooth(unittest.TestCase):
    def settings(self,folder,**overrides):
        (folder/'bluetooth-lighting-restore.json').write_text(json.dumps({
            'packet_hex':ble.effect_packet([7,244,7],100).hex()}),encoding='utf-8')
        (folder/'bluetooth-validation.json').write_text(json.dumps({
            'automatic_enabled':True,'volatile_effects':'passed','per_key_stream':'passed',
            'smooth_transport':'fast',**overrides}),encoding='utf-8')

    def test_automatic_lighting_requires_actual_validation_record(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory)
            self.assertIsNone(ble.automatic_settings(folder))
            for override in ({'smooth_transport':'pending'},{'automatic_enabled':False},{'per_key_stream':'pending'}):
                self.settings(folder,**override)
                self.assertIsNone(ble.automatic_settings(folder))
            self.settings(folder)
            self.assertEqual(ble.automatic_settings(folder),([7,244,7],100,'fast'))

    def test_working_preserves_body_and_uses_verified_fast_transport(self):
        wire=Wire();session=ble.BluetoothLighting(([7,244,7],100,'fast'),client_factory=lambda:wire)
        async def run():
            await session.connect()
            for t in (0,.1,.2):await session.render('working',t)
            await session.close()
        asyncio.run(run())
        streams=[c for c in wire.calls if c[0]=='direct']
        self.assertEqual([i for i,_ in streams[0][1]],list(ble.KEY_LEDS))
        self.assertFalse(streams[0][2])
        self.assertTrue(all(c[2] for c in streams[1:]))
        self.assertTrue(all(color==(7,244,7) for c in streams[1:] for index,color in c[1] if index not in ble.TOP_ROW))
        self.assertTrue(wire.closed)
        self.assertFalse(wire.allow_persistent_commit)
        self.assertEqual(wire.calls[-1],('effect',(7,244,7),100,False))

    def test_body_lighting_is_repaired_after_a_sleep_buffer_loss_without_full_frame_stalls(self):
        wire=Wire();session=ble.BluetoothLighting(([7,244,7],100,'fast'),client_factory=lambda:wire)
        clock=SimpleNamespace(now=100.)
        async def run():
            await session.connect();await session.render('working',0)
            wire.calls.clear()  # Simulate the body losing its lighting buffer.
            for tick in range(100):
                clock.now+=.034
                await session.render('working',tick*.034)
        with patch('bluetooth_backend.time.perf_counter',side_effect=lambda:clock.now):asyncio.run(run())
        writes=[call for call in wire.calls if call[0]=='direct']
        body={i for call in writes for i,color in call[1] if i in ble.BODY_LEDS and color==(7,244,7)}
        self.assertEqual(body,set(ble.BODY_LEDS))
        self.assertTrue(all(len(call[1])<=15 and call[2] for call in writes))

    def test_attention_uses_firmware_breath_and_does_not_restart_it_every_frame(self):
        wire=Wire();session=ble.BluetoothLighting(([7,244,7],100,'fast'),client_factory=lambda:wire)
        async def run():
            await session.connect()
            await session.render('working',0)
            for t in (0,.1,2,5):await session.render('attention',t)
            await session.render('idle',0)
            await session.close()
        asyncio.run(run())
        effects=[c for c in wire.calls if c[0]=='effect']
        self.assertEqual([c[3] for c in effects],[False,True,False,False])

    def test_restore_failure_still_releases_bluetooth_handles(self):
        wire=Wire();session=ble.BluetoothLighting(([7,244,7],100,'compatible'),client_factory=lambda:wire)
        async def fail(*args,**kwargs):raise TimeoutError('radio disconnected')
        async def run():
            await session.render('working',0)
            wire.effect=fail
            with self.assertRaises(TimeoutError):await session.close()
        asyncio.run(run())
        self.assertTrue(wire.closed)
        self.assertTrue(all(not c[2] for c in wire.calls if c[0]=='direct'))

    def test_controller_runs_bluetooth_without_any_panel_and_restores_on_pause(self):
        with patch('app.load_config',return_value=app.DEFAULTS|{'paused':False}):controller=app.Controller()
        controller.latest_status=app.Status('working','task',1)
        wire=Wire();sessions=[];snapshots=[];session_type=ble.BluetoothLighting
        def factory(settings,opener):
            session=session_type(settings,opener,lambda:wire)
            sessions.append(session)
            return session
        original=controller.process_commands
        def tick():
            original()
            snapshots.append(controller.snapshot())
            if len(snapshots)==5:controller.commands.put('pause')
            if len(snapshots)>=8:controller.stop.set()
        controller.process_commands=tick
        async def devices(selector):return []
        modules={
            'winrt.windows.devices.enumeration':SimpleNamespace(DeviceInformation=SimpleNamespace(find_all_async_aqs_filter=devices)),
            'winrt.windows.devices.lights':SimpleNamespace(LampArray=SimpleNamespace(get_device_selector=lambda:'lamp')),
            'winrt.windows.system':SimpleNamespace(VirtualKey=None),
            'winrt.windows.ui':SimpleNamespace(Color=None),
            'winrt.system':SimpleNamespace(Array=None)}
        with patch.dict(sys.modules,modules),patch('bluetooth_backend.automatic_settings',return_value=([7,244,7],100,'fast')),\
             patch('bluetooth_backend.connected_keyboards',return_value=['keyboard']),\
             patch('bluetooth_backend.BluetoothLighting',side_effect=factory),\
             patch('app.save_config'),patch('app.time.sleep'):
            asyncio.run(controller.lighting())
        self.assertEqual(len(sessions),1)
        self.assertTrue(any(s['available'] and '蓝牙' in s['device'] for s in snapshots))
        self.assertTrue(controller.config['paused'])
        self.assertTrue(wire.closed)
        self.assertEqual(wire.calls[-1],('effect',(7,244,7),100,False))


if __name__=='__main__':unittest.main()
