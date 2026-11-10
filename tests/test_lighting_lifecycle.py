import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from lighting_lifecycle import release_lamps


class LightingRelease(unittest.TestCase):
    def test_restore_original_colour_before_release(self):
        calls = []
        class Lamp:
            is_connected = True
            brightness_level = 0
            def set_color(self, color):
                calls.append(('color', color, self.brightness_level))
            @property
            def is_enabled(self):
                return True
            @is_enabled.setter
            def is_enabled(self, enabled):
                calls.append(('enabled', enabled))
        release_lamps([{'array': Lamp(), 'rgb': (0, 124, 0), 'brightness': .8}], dict)
        self.assertEqual(calls, [('color', dict(a=255, r=0, g=124, b=0), .8), ('enabled', False)])

    def test_failed_restore_still_releases_every_device(self):
        first = Mock(is_connected=True)
        first.set_color.side_effect = OSError('disconnected during restore')
        second = Mock(is_connected=True)
        with self.assertLogs(level='ERROR'):
            release_lamps([{'array': x, 'rgb': (0, 124, 0), 'brightness': 1}
                           for x in (first, second)], dict)
        self.assertFalse(first.is_enabled)
        self.assertFalse(second.is_enabled)
        second.set_color.assert_called_once()

    def test_disconnected_array_is_not_written(self):
        lamp = Mock(is_connected=False)
        release_lamps([{'array': lamp, 'rgb': (0, 124, 0), 'brightness': 1}], dict)
        lamp.set_color.assert_not_called()
        self.assertFalse(lamp.is_enabled)


if __name__ == '__main__':
    unittest.main()
