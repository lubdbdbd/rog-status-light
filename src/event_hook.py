"""Lifecycle bridge. Persist only event metadata; produce no model output."""
import json
import os
import sys
import time
import uuid
import ctypes
import msvcrt
from pathlib import Path
from runtime_paths import DATA


def main():
    try:
        source = sys.stdin
        if source is None:
            ctypes.windll.kernel32.GetStdHandle.restype = ctypes.c_void_p
            handle = ctypes.windll.kernel32.GetStdHandle(-10)
            if handle in (None, 0, ctypes.c_void_p(-1).value):
                return 0
            descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY)
            source = os.fdopen(descriptor, 'r', encoding='utf-8')
        payload = json.load(source)
        event = payload.get('hook_event_name', '')
        tool = payload.get('tool_name', '')
        error = payload.get('error') or payload.get('error_info') or {}
        outcome = payload.get('outcome')
        if isinstance(outcome, dict):
            error = error or outcome.get('error', {})
        text = json.dumps(error, ensure_ascii=False).lower()
        quota = bool(error) and any(word in text for word in
            ('usagelimitexceeded', 'ratelimitexceeded', 'sessionbudgetexceeded',
             'insufficient_quota', 'insufficient quota', 'usage limit', 'quota exceeded',
             'rate limit', 'too many requests', 'hit your limit', '额度', '余额不足'))
        record = {'event': event, 'tool': tool, 'quota': quota, 'time': time.time(),
                  'session_id': payload.get('session_id', ''), 'turn_id': payload.get('turn_id', ''),
                  'model_status': payload.get('status', '') if event == 'PostModelRequest' else ''}
        directory = DATA / 'events'
        directory.mkdir(parents=True, exist_ok=True)
        name = f'{time.time_ns()}-{uuid.uuid4().hex}'
        temporary = directory / (name + '.tmp')
        temporary.write_text(json.dumps(record, ensure_ascii=False), encoding='utf-8')
        temporary.replace(directory / (name + '.json'))
    except Exception:
        pass
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
