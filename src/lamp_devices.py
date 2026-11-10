"""Match the keyboard's USB and receiver identities independently of names."""


def is_target_lamp(device_id):
    identifier = device_id.upper()
    return any(identity in identifier for identity in
               ('VID_0B05&PID_1B04', 'VID_0B05&PID_1ACE'))


def connection_label(device_id):
    return 'ROG 魔导士 · 2.4GHz' if 'PID_1ACE' in device_id.upper() else 'ROG 魔导士 · USB'


def device_order(device):
    return 0 if 'PID_1B04' in device.id.upper() else 1
