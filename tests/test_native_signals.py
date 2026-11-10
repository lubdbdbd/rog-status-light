import subprocess
import sys
import unittest
import uuid
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / 'src'
sys.path.insert(0, str(SOURCE))
from native_signals import SignalInbox, send


class NativeCommands(unittest.TestCase):
    def test_command_crosses_process_boundary_and_resets_after_read(self):
        prefix = 'Local\\ROGStatusLight-test-' + uuid.uuid4().hex + '-'
        inbox = SignalInbox(prefix)
        try:
            result = subprocess.run([sys.executable, '-c',
                'import sys;sys.path.insert(0,sys.argv[1]);from native_signals import send;'
                'sys.exit(0 if send("quit",sys.argv[2]) else 1)', str(SOURCE), prefix],
                capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(inbox.poll(), ['quit'])
            self.assertEqual(inbox.poll(), [])
        finally:
            inbox.close()
        self.assertFalse(send('quit', prefix))

    def test_reject_unknown_command(self):
        with self.assertRaises(ValueError):
            send('unknown')


if __name__ == '__main__':
    unittest.main()
