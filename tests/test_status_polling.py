import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

WORK = Path(__file__).resolve().parent
sys.path[:0] = [str(WORK.parent/'src'), str(WORK.parents[1]/'work'/'deps')]
import app
from core import Status


class NonblockingStatusRead(unittest.TestCase):
    def test_slow_state_read_does_not_block_animation_or_handle_ack_on_render_thread(self):
        entered=threading.Event()
        release=threading.Event()
        acknowledged=threading.Event()
        render_thread=threading.current_thread()

        class Reader:
            def snapshot(self):
                if threading.current_thread() is render_thread:
                    raise AssertionError('State scan blocked the render thread')
                entered.set()
                release.wait(2)
                return Status('working','working',1)
            def acknowledge(self):
                if threading.current_thread() is render_thread:
                    raise AssertionError('Reader mutation crossed thread boundary')
                acknowledged.set()

        config={'paused':False,'poll_seconds':.2,'scroll_step_seconds':.14,'flash_half_period_seconds':.55}
        with patch('app.load_config',return_value=config),patch('app.save_config'):
            controller=app.Controller()
            controller.reader=Reader()
            worker=threading.Thread(target=controller.status_worker,daemon=True)
            worker.start()
            try:
                self.assertTrue(entered.wait(1))
                for _ in range(60):
                    controller.desired()
                controller.command('ack')
                controller.process_commands()
                self.assertFalse(acknowledged.is_set())
                release.set()
                self.assertTrue(acknowledged.wait(1))
                self.assertEqual(controller.desired(),('working','working',1))
            finally:
                release.set()
                controller.stop.set()
                worker.join(timeout=1)
            self.assertFalse(worker.is_alive())


if __name__=='__main__':unittest.main()
