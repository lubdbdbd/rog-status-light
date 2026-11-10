import asyncio
import datetime
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

WORK = Path(__file__).resolve().parent
sys.path[:0] = [str(WORK.parent/'src'), str(WORK.parents[1]/'work'/'deps')]
import app


class ControlRecovery(unittest.TestCase):
    def test_idle_colour_is_resent_after_background_control_returns(self):
        config={'paused':True,'poll_seconds':.6,'scroll_step_seconds':.14,'flash_half_period_seconds':.55}
        with patch('app.load_config',return_value=config):
            controller=app.Controller()
        writes=[]
        removed=[]

        class Lamp:
            is_connected=True
            is_enabled=False
            lamp_count=82
            min_update_interval=datetime.timedelta(milliseconds=33)
            brightness_level=1
            checks=0

            @property
            def is_available(self):
                self.checks+=1
                if self.checks>=4:
                    controller.stop.set()
                return self.checks!=2

            def get_indices_for_key(self,key):
                return [14 if key=='esc' else 28]

            def get_lamp_info(self,index):
                return SimpleNamespace(position=SimpleNamespace(x=index,y=0 if 14<=index<=28 else 1))

            def add_availability_changed(self,callback):return 'token'
            def remove_availability_changed(self,token):removed.append(token)
            def set_color(self,color):writes.append((self.checks,color))

        lamp=Lamp()
        device=SimpleNamespace(id=r'\\?\HID#VID_0B05&PID_1ACE&MI_04#test',name='ROG OMNI RECEIVER')
        async def devices(_):return [device]
        async def from_id(_):return lamp
        fake_modules={
            'winrt.windows.devices.enumeration':SimpleNamespace(DeviceInformation=SimpleNamespace(find_all_async_aqs_filter=devices)),
            'winrt.windows.devices.lights':SimpleNamespace(LampArray=SimpleNamespace(get_device_selector=lambda:'selector',from_id_async=from_id)),
            'winrt.windows.system':SimpleNamespace(VirtualKey=SimpleNamespace(ESCAPE='esc',INSERT='ins')),
            'winrt.windows.ui':SimpleNamespace(Color=lambda **kwargs:kwargs),
            'winrt.system':SimpleNamespace(Array=lambda kind,data:data),
        }
        preferences={'preferred':True,'foreground_override':0,'dynamic_lighting':1}
        with patch.dict(sys.modules,fake_modules),patch('app.original_palette',return_value=((0,124,0),1)),\
             patch('app.policy',return_value=preferences),patch('app.time.sleep'):
            with self.assertLogs(level='WARNING'):
                asyncio.run(controller.lighting())
        # First render, control regained, and final restore before release.
        self.assertEqual([count for count,_ in writes],[1,3,4])
        self.assertEqual(removed,['token'])
        self.assertFalse(lamp.is_enabled)


if __name__=='__main__':unittest.main()
