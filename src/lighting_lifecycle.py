"""Release Windows lighting after restoring the last original colour."""
import logging


def release_lamps(entries, color_factory):
    for entry in entries:
        lamp = entry['array']
        if 'availability_token' in entry:
            try:
                lamp.remove_availability_changed(entry['availability_token'])
            except Exception:
                logging.exception('Could not detach lighting availability notification')
        try:
            if lamp.is_connected:
                r, g, b = entry['rgb']
                lamp.brightness_level = entry['brightness']
                lamp.set_color(color_factory(a=255, r=r, g=g, b=b))
        except Exception:
            logging.exception('Could not restore original colour before releasing lighting')
        finally:
            try:
                lamp.is_enabled = False
            except Exception:
                logging.exception('Could not release disconnected lighting interface')
