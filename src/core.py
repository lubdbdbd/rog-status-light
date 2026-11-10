"""Read-only Codex state and RGB effect calculations."""
from __future__ import annotations

import json
import math
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Status:
    mode: str = 'idle'
    reason: str = '空闲'
    active: int = 0


def frame(rgb, count, row, mode, elapsed, step=.14, flash=.55):
    colors = [tuple(rgb)] * count
    if mode == 'attention':
        # A strong breath followed by a softer echo; no on/off steps.
        phase = elapsed / max(1.2, flash * 3.6) % 1.0
        first_distance = (phase - .10 + .5) % 1.0 - .5
        echo_distance = (phase - .38 + .5) % 1.0 - .5
        first = math.exp(-.5 * (first_distance / .10) ** 2)
        echo = math.exp(-.5 * (echo_distance / .085) ** 2)
        pulse = 1 - (1 - first) * (1 - .68 * echo)
        scale = .06 + .94 * pulse
        color = tuple(round(channel * scale) for channel in rgb)
        return [color] * count
    if mode == 'working' and row:
        cycle = max(.4, max(.035, step) * len(row))
        head = elapsed / cycle * len(row)
        breath = .79 + .21 * math.cos(math.tau * elapsed / (cycle * 1.6))
        for position, lamp in enumerate(row):
            angle = math.tau * (head - position) / len(row)
            crest = (.5 + .5 * math.cos(angle)) ** 4
            wake = (.5 + .5 * math.cos(angle - math.tau * 3 / len(row))) ** 3
            wave = crest + .24 * wake * (1 - crest)
            scale = .025 + .975 * breath * wave
            colors[lamp] = tuple(round(channel * scale) for channel in rgb)
    return colors


def blend_frames(previous, current, progress):
    if previous is None or len(previous) != len(current) or progress >= 1:
        return current
    progress = max(0, progress)
    weight = progress * progress * (3 - 2 * progress)
    return [tuple(round(a + (b-a)*weight) for a, b in zip(old, new))
            for old, new in zip(previous, current)]


def pending_questions(items):
    pending = set()
    freeform_replies = set()
    for index, item in enumerate(items):
        kind = item.get('type')
        if kind == 'userMessage':
            text = json.dumps(item, ensure_ascii=False)
            matched = {identifier for identifier in pending if identifier in text}
            pending.difference_update(matched)
            freeform_replies.difference_update(matched)
            explicit_reply = 'questionItemId' in text or 'send_user_message_question_reply' in text
            if not explicit_reply:
                # A typed answer or screenshot has no questionItemId. Wait for
                # the agent to resume before considering the question handled.
                freeform_replies.update(pending)
        elif kind in ('agentMessage', 'commandExecution', 'fileChange'):
            pending.difference_update(freeform_replies)
            freeform_replies.clear()
            if kind == 'agentMessage' and item.get('questions'):
                pending.add(item.get('id') or f'question-without-id-{index}')
    return bool(pending)


class StatusReader:
    def __init__(self, codex_home: Path, data_dir: Path):
        self.home = codex_home
        self.data = data_dir
        self.started = time.time()
        self.seen_turns = {}
        self.notifications = set()
        self.baseline = False
        self.flash_until = 0.0
        self.hook_waits = {}
        self.hook_errors = {}
        self.errors_seen = set()
        self.ack_time = 0.0
        self.last_error = ''
        self.last_process_check = 0.0
        self.process_floor = self.started
        self.app_running = False

    def acknowledge(self):
        self.flash_until = 0
        self.hook_errors.clear()
        self.ack_time = time.time()

    def processes(self):
        if time.monotonic() - self.last_process_check < 2:
            return
        import psutil
        starts = []
        for process in psutil.process_iter(['name', 'create_time']):
            if (process.info['name'] or '').lower() in ('chatgpt.exe', 'codex.exe'):
                if process.info['create_time']:
                    starts.append(process.info['create_time'])
        self.app_running = bool(starts)
        self.process_floor = min(starts) - 5 if starts else time.time()
        self.last_process_check = time.monotonic()

    @staticmethod
    def connect(path):
        connection = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=.15)
        connection.row_factory = sqlite3.Row
        return connection

    def events(self):
        folder = self.data / 'events'
        if not folder.exists():
            return
        for path in sorted(folder.glob('*.json'))[:250]:
            try:
                event = json.loads(path.read_text(encoding='utf-8'))
                age = time.time() - event.get('time', 0)
                if age > 86400:
                    path.unlink(missing_ok=True)
                    continue
                key = event.get('session_id', '')
                kind = event.get('event', '')
                if kind == 'UserPromptSubmit':
                    self.hook_waits.pop(key, None)
                    self.hook_errors.pop(key, None)
                elif kind == 'PermissionRequest':
                    self.hook_waits[key] = (event.get('tool', ''), '等待确认', event['time'])
                elif kind == 'PreToolUse' and 'request_user_input' in event.get('tool', ''):
                    self.hook_waits[key] = (event['tool'], '等待回答', event['time'])
                elif kind == 'PostToolUse':
                    previous = self.hook_waits.get(key)
                    if previous and previous[0] == event.get('tool'):
                        self.hook_waits.pop(key, None)
                elif kind in ('Stop', 'Interrupt', 'SessionEnd'):
                    self.hook_waits.pop(key, None)
                    if kind == 'Stop' and age < 10 and event['time'] > self.ack_time:
                        self.flash_until = max(self.flash_until, time.monotonic() + 4.4)
                elif kind == 'PostModelRequest' and event.get('model_status') == 'completed':
                    self.hook_errors.pop(key, None)
                if event.get('quota') and event['time'] > self.ack_time:
                    self.hook_errors[key] = '额度或频率限制'
                path.unlink(missing_ok=True)
            except (OSError, ValueError, KeyError):
                continue

    def snapshot(self):
        self.processes()
        self.events()
        if not self.app_running:
            self.hook_waits.clear()
            self.hook_errors.clear()
            return Status()
        active = 0
        pending = False
        persistent_error = False
        try:
            with closing(self.connect(self.home / 'state_5.sqlite')) as state:
                metadata = {r['id']: r for r in state.execute(
                    'SELECT id,archived,thread_source,agent_path FROM threads')}
            with closing(self.connect(self.home / 'thread_history_1.sqlite')) as history:
                turns = history.execute('''
                    SELECT t.* FROM thread_turns AS t JOIN
                    (SELECT thread_id,MAX(rollout_ordinal) AS last_ordinal
                     FROM thread_turns GROUP BY thread_id) AS newest
                    ON t.thread_id=newest.thread_id AND t.rollout_ordinal=newest.last_ordinal
                ''').fetchall()
                live_ids = set()
                for turn in turns:
                    key = turn['thread_id']
                    meta = metadata.get(key)
                    if not meta or meta['archived'] or meta['thread_source'] == 'subagent':
                        continue
                    if meta['agent_path'] not in (None, '', '/root'):
                        continue
                    started = turn['started_at'] or 0
                    if started < self.process_floor:
                        continue
                    live_ids.add(key)
                    previous = self.seen_turns.get(key)
                    current = (turn['turn_id'], turn['status'])
                    if previous and previous != current and turn['status'] == 'completed':
                        if (turn['completed_at'] or 0) > self.ack_time:
                            self.flash_until = max(self.flash_until, time.monotonic() + 4.4)
                    if previous and previous[0] != turn['turn_id']:
                        self.hook_errors.pop(key, None)
                        self.hook_waits.pop(key, None)
                    self.seen_turns[key] = current
                    if turn['status'] == 'inProgress':
                        active += 1
                        rows = history.execute('''SELECT item_json FROM thread_items
                            WHERE thread_id=? AND turn_id=? AND
                            item_type IN ('agentMessage','userMessage','commandExecution','fileChange')
                            ORDER BY rollout_ordinal''', (key, turn['turn_id']))
                        items = [json.loads(r[0]) for r in rows]
                        pending |= pending_questions(items)
                        pending |= any(item.get('status') in ('waitingForApproval', 'awaitingApproval',
                                                               'pendingApproval', 'waitingOnUserInput')
                                       for item in items)
                    elif turn['status'] == 'failed' and (turn['completed_at'] or 0) > self.ack_time:
                        code = (turn['error_json'] or '').lower()
                        persistent_error |= any(word in code for word in
                            ('usagelimitexceeded', 'ratelimitexceeded', 'sessionbudgetexceeded',
                             'insufficient_quota', 'usage limit', 'quota exceeded', '额度', '余额不足'))
                self.hook_waits = {k: v for k, v in self.hook_waits.items()
                                   if k in live_ids and time.time() - v[2] < 86400}
            db = self.home / 'sqlite' / 'codex.db'
            if db.exists():
                with closing(self.connect(db)) as desktop:
                    rows = desktop.execute('SELECT id,read_at,created_at FROM inbox_items').fetchall()
                    current_ids = {row['id'] for row in rows}
                    if self.baseline:
                        for row in rows:
                            if row['id'] not in self.notifications and row['read_at'] is None:
                                self.flash_until = max(self.flash_until, time.monotonic() + 6.6)
                    self.notifications = current_ids
            self.baseline = True
            self.last_error = ''
        except (sqlite3.Error, OSError, ValueError) as error:
            self.last_error = str(error)
            return Status('idle', '状态读取待重试', 0)
        if pending or self.hook_waits:
            return Status('attention', '等待回答或确认', active)
        if persistent_error or self.hook_errors:
            return Status('attention', '额度或频率限制', active)
        if time.monotonic() < self.flash_until:
            return Status('attention', '任务完成或新通知', active)
        return Status('working', f'{active} 个任务运行中', active) if active else Status()
