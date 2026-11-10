import sys
import unittest
from types import SimpleNamespace
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from lamp_devices import is_target_lamp, connection_label, device_order


class LampIdentity(unittest.TestCase):
    def test_receiver_with_no_falchion_in_name_is_recognized(self):
        receiver=SimpleNamespace(name='ROG OMNI RECEIVER',id=r'\\?\HID#VID_0B05&PID_1ACE&MI_04#device')
        self.assertTrue(is_target_lamp(receiver.id))
        self.assertIn('2.4GHz',connection_label(receiver.id))

    def test_wired_keyboard_is_recognized_without_name_dependency(self):
        self.assertTrue(is_target_lamp(r'\\?\hid#vid_0b05&pid_1b04&mi_04#device'))

    def test_other_devices_are_not_controlled(self):
        self.assertFalse(is_target_lamp('HID#VID_0B05&PID_1A94'))
        self.assertFalse(is_target_lamp('HID#VID_1234&PID_1ACE'))

    def test_wired_connection_is_preferred_over_paired_receiver(self):
        receiver=SimpleNamespace(id='HID#VID_0B05&PID_1ACE')
        wired=SimpleNamespace(id='HID#VID_0B05&PID_1B04')
        self.assertEqual(sorted([receiver,wired],key=device_order),[wired,receiver])


if __name__=='__main__':unittest.main()
