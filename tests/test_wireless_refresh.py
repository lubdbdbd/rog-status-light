"""A live receiver can outlast the keyboard's volatile lighting state."""
import asyncio
import datetime
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

WORK=Path(__file__).resolve().parent
sys.path[:0]=[str(WORK.parent/'src'),str(WORK.parents[1]/'work'/'deps')]
import app


class WirelessRefresh(unittest.TestCase):
    def verify_mode(self,mode):
        clock=SimpleNamespace(now=100.)
        config={'paused':False,'poll_seconds':.6,'scroll_step_seconds':.14,'flash_half_period_seconds':.55}
        with patch('app.load_config',return_value=config):
            controller=app.Controller()
        controller.desired=lambda:(mode,'test',0)
        writes=[]
        brightness_writes=[]

        class Lamp:
            is_connected=True
            is_enabled=False
            lamp_count=82
            min_update_interval=datetime.timedelta(milliseconds=33)
            gain=1
            lost_state=False
            colors=[(0,0,0)]*82
            @property
            def is_available(self):
                if clock.now>=101 and not self.lost_state:
                    # Radio receiver remains present and available: no control
                    # transition tells the application to repaint the keyboard.
                    self.colors=[(0,0,0)]*82
                    self.gain=0
                    self.lost_state=True
                if clock.now>=104.3:
                    controller.stop.set()
                return True
            @property
            def brightness_level(self):return self.gain
            @brightness_level.setter
            def brightness_level(self,value):
                self.gain=value
                brightness_writes.append(clock.now)
            def get_indices_for_key(self,key):return [14 if key=='esc' else 28]
            def get_lamp_info(self,index):
                return SimpleNamespace(position=SimpleNamespace(x=index,y=0 if 14<=index<=28 else 1))
            def add_availability_changed(self,callback):return 'token'
            def remove_availability_changed(self,token):pass
            def set_color(self,color):self.set_colors_for_indices([color]*82,list(range(82)))
            def set_colors_for_indices(self,colors,indices):
                for color,index in zip(colors,indices):
                    self.colors[index]=(color['r'],color['g'],color['b'])
                writes.append({'time':clock.now,'indices':list(indices),
                               'body':self.colors[40],'gain':self.gain})

        lamp=Lamp()
        device=SimpleNamespace(id=r'\\?\HID#VID_0B05&PID_1ACE&MI_04#test',name='ROG OMNI RECEIVER')
        async def devices(_):return [device]
        async def from_id(_):return lamp
        modules={
            'winrt.windows.devices.enumeration':SimpleNamespace(DeviceInformation=SimpleNamespace(find_all_async_aqs_filter=devices)),
            'winrt.windows.devices.lights':SimpleNamespace(LampArray=SimpleNamespace(get_device_selector=lambda:'selector',from_id_async=from_id)),
            'winrt.windows.system':SimpleNamespace(VirtualKey=SimpleNamespace(ESCAPE='esc',INSERT='ins')),
            'winrt.windows.ui':SimpleNamespace(Color=lambda **kwargs:kwargs),
            'winrt.system':SimpleNamespace(Array=lambda kind,data:data),
        }
        def sleep(delay):clock.now+=delay
        with patch.dict(sys.modules,modules),patch('app.original_palette',return_value=((0,124,0),1)),\
             patch('app.policy',return_value={'preferred':True,'dynamic_lighting':1}),\
             patch('app.time.perf_counter',side_effect=lambda:clock.now),patch('app.time.sleep',side_effect=sleep):
            asyncio.run(controller.lighting())
        before_stop=[w for w in writes if w['time']<104.3]
        recovered=[w for w in before_stop if 102<=w['time']<102.11 and len(w['indices'])==82]
        self.assertTrue(recovered,'Full keyboard must be resent even without a disconnect or control change')
        self.assertEqual(recovered[0]['gain'],1,'The same brightness value must be resent after state loss')
        self.assertTrue(any(102<=value<102.11 for value in brightness_writes))
        self.assertEqual(controller.snapshot()['full_refreshes'],3)
        if mode in ('idle','working'):
            self.assertEqual(recovered[0]['body'],(0,124,0))
        if mode=='working':
            self.assertTrue(any(len(w['indices'])==15 for w in before_stop))

    def test_static_idle_recovers_without_an_effect_change(self):self.verify_mode('idle')
    def test_scrolling_refreshes_nonanimated_keys_too(self):self.verify_mode('working')
    def test_attention_refreshes_brightness_while_colours_change(self):self.verify_mode('attention')


if __name__=='__main__':unittest.main()
