import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import uuid
import winreg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
import background_access as access


class BackgroundAccess(unittest.TestCase):
    def setUp(self):
        self.root = 'Software\\ROGStatusLight\\Tests\\' + uuid.uuid4().hex
        self.root_patch = patch.object(access, 'LIGHTING', self.root)
        self.root_patch.start()
        self.temporary = tempfile.TemporaryDirectory()
        self.device = r'\\?\HID#VID_0B05&PID_1ACE&MI_04#new-port'

    def tearDown(self):
        def remove(path):
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
                children = [winreg.EnumKey(key, i) for i in range(winreg.QueryInfoKey(key)[0])]
            for child in children:
                remove(path+'\\'+child)
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)
        try:
            remove(self.root)
        except FileNotFoundError:
            pass
        self.root_patch.stop()
        self.temporary.cleanup()

    def write(self, path, settings):
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_SET_VALUE) as key:
            for name, value in settings.items():
                winreg.SetValueEx(key, name, 0, winreg.REG_SZ if isinstance(value, str) else winreg.REG_DWORD, value)

    def test_new_port_order_overrides_the_global_order(self):
        self.write(self.root+'\\Providers', {'1':access.PROVIDER,'2':'WindowsLighting','3':'Vendor'})
        path = access.device_path(self.device)
        self.write(path+'\\Providers', {'1':'WindowsLighting','2':'Vendor','3':access.PROVIDER})
        before = access.values(path+'\\Providers')
        result = access.policy(self.device)
        self.assertFalse(result['preferred'])
        self.assertEqual(result['providers'],['WindowsLighting','Vendor',access.PROVIDER])
        self.assertIn('设备卡片',access.access_hint(result,False))
        self.assertEqual(access.values(path+'\\Providers'),before)

    def test_device_toggle_overrides_global_and_available_clears_warning(self):
        self.write(self.root, {'AmbientLightingEnabled':1,'ControlledByForegroundApp':1})
        self.write(self.root+'\\Providers', {'1':access.PROVIDER,'2':'WindowsLighting'})
        path = access.device_path(self.device)
        self.write(path, {'AmbientLightingEnabled':0,'ControlledByForegroundApp':0})
        result = access.policy(self.device)
        self.assertEqual(result['dynamic_lighting'],0)
        self.assertEqual(result['foreground_override'],0)
        self.assertIn('开启',access.access_hint(result,False))
        self.assertEqual(access.access_hint(result,True),'')

    def test_new_device_without_override_inherits_global_preference(self):
        self.write(self.root+'\\Providers', {'1':access.PROVIDER,'2':'Vendor'})
        self.assertTrue(access.policy(self.device)['preferred'])

    def test_unrelated_peripheral_or_path_is_rejected(self):
        for device in (r'\\?\HID#VID_1234&PID_5678',self.device+'\\Providers'):
            with self.assertRaises(ValueError):
                access.policy(device)


if __name__=='__main__':unittest.main()
