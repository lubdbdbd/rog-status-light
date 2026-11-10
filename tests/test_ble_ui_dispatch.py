import asyncio
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

WORK=Path(__file__).resolve().parent
sys.path[:0]=[str(WORK.parent/'src'),str(WORK.parents[1]/'work'/'deps')]
import app


class BluetoothUiDispatch(unittest.TestCase):
    def run_open(self,fail=False):
        with patch('app.load_config',return_value={'paused':False}):controller=app.Controller()
        ui_thread=threading.current_thread()
        outcome={}
        def from_id(device_id):
            self.assertIs(threading.current_thread(),ui_thread)
            self.assertEqual(device_id,'paired-device')
            if fail:raise PermissionError('Bluetooth access denied')
            async def operation():return 'opened-device'
            return operation()
        def worker():
            try:outcome['result']=asyncio.run(controller.open_bluetooth_device('paired-device'))
            except Exception as error:outcome['error']=error
        fake=SimpleNamespace(BluetoothLEDevice=SimpleNamespace(from_id_async=from_id))
        with patch.dict(sys.modules,{'winrt.windows.devices.bluetooth':fake}),\
             patch('app.ctypes.windll.combase.RoInitialize',return_value=0):
            thread=threading.Thread(target=worker,daemon=True)
            thread.start()
            controller.ui_operations.get(timeout=2)()
            thread.join(timeout=2)
            self.assertFalse(thread.is_alive())
        return outcome

    def test_device_object_is_created_on_ui_thread_and_returned_to_worker(self):
        self.assertEqual(self.run_open()['result'],'opened-device')

    def test_ui_access_failure_reaches_worker_without_hanging(self):
        self.assertIsInstance(self.run_open(fail=True)['error'],PermissionError)


if __name__=='__main__':unittest.main()
