import base64
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

WORK=Path(__file__).resolve().parent
sys.path[:0]=[str(WORK.parent/'src'),str(WORK.parents[1]/'work'/'deps')]
from cloud_status import CloudMonitor,CloudRPC,CloudReadError,CloudSnapshot,parse_tasks
from core import Status,StatusReader


def raw(identifier='cloud-task',kind='active',flags=(),updated=10,source='user'):
    return {'id':identifier,'status':{kind:{'activeFlags':{'values':list(flags)}}},
            'updatedAt':str(updated),'threadSource':source,'title':'private title','messages':['private text']}


class CloudStatusTests(unittest.TestCase):
    def monitor(self,rows):
        clock=SimpleNamespace(now=100.)
        rpc=SimpleNamespace(fetch=lambda:('signed-in-owner',parse_tasks(rows)))
        monitor=CloudMonitor(Path('.'),rpc,lambda:clock.now)
        return monitor,rpc,clock

    def test_actual_desktop_wire_shape_and_waiting_flags(self):
        rows=parse_tasks([raw(flags=['THREAD_ACTIVE_FLAG_WAITING_ON_USER_INPUT']),raw('child',source='aeon_child'),raw('old','idle')])
        self.assertEqual(len(rows),2)
        self.assertTrue(rows[0]['active'] and rows[0]['pending'])
        self.assertNotIn('private',json.dumps(rows))
        self.assertTrue(parse_tasks([raw(flags=['waitingOnApproval'])])[0]['pending'])

    def test_non_loaded_threads_are_not_running(self):
        monitor,_,_=self.monitor([raw(kind='notLoaded')]);monitor.poll()
        self.assertFalse(monitor.snapshot().active_ids)

    def test_numeric_waiting_flags_are_supported_too(self):
        self.assertTrue(parse_tasks([raw(flags=[2])])[0]['pending'])

    def test_startup_does_not_replay_old_completion(self):
        monitor,_,_=self.monitor([raw(kind='idle')]);monitor.poll()
        self.assertEqual(monitor.snapshot().completed_at,0)

    def test_cloud_completion_and_acknowledgement(self):
        rows=[raw()];monitor,rpc,clock=self.monitor(rows);monitor.poll()
        rows[0]=raw(kind='idle',updated=20);clock.now=105.;monitor.poll()
        self.assertEqual(monitor.snapshot().completed_at,105.)
        self.assertFalse(monitor.snapshot().active_ids)
        monitor.acknowledge();self.assertEqual(monitor.snapshot().completed_at,0)

    def test_account_switch_clears_previous_tasks_and_completions(self):
        monitor,rpc,clock=self.monitor([raw()]);monitor.poll()
        rpc.fetch=lambda:('different-owner',parse_tasks([raw(kind='idle',updated=20)]))
        clock.now+=1;monitor.poll()
        self.assertEqual(monitor.snapshot().completed_at,0)
        self.assertFalse(monitor.snapshot().active_ids)

    def test_cloud_error_acknowledgement_does_not_hide_a_new_error(self):
        rows=[raw(kind='systemError')];monitor,rpc,clock=self.monitor(rows);monitor.poll()
        self.assertTrue(monitor.snapshot().error)
        monitor.acknowledge();self.assertFalse(monitor.snapshot().error)
        rows[0]=raw(kind='systemError',updated=30);monitor.poll()
        self.assertTrue(monitor.snapshot().error)

    def test_disconnection_expires_cached_activity_instead_of_scrolling_forever(self):
        monitor,_,clock=self.monitor([raw()]);monitor.poll();monitor.last_error='network'
        self.assertTrue(monitor.snapshot().active_ids)
        clock.now+=31
        self.assertFalse(monitor.snapshot().active_ids)
        self.assertFalse(monitor.snapshot().pending)

    def test_slow_cloud_fetch_never_blocks_a_lighting_state_snapshot(self):
        entered=threading.Event();finish=threading.Event()
        def fetch():entered.set();finish.wait(2);return 'owner',[]
        monitor=CloudMonitor(Path('.'),SimpleNamespace(fetch=fetch))
        worker=threading.Thread(target=monitor.poll)
        worker.start();self.assertTrue(entered.wait(1))
        try:
            began=time.perf_counter();monitor.snapshot()
            self.assertLess(time.perf_counter()-began,.1)
        finally:finish.set();worker.join(1)

    def test_rpc_is_read_only_paginates_and_keeps_only_status_metadata(self):
        pages=[{'data':{'values':[raw()]},'nextCursor':'page-two'},
               {'data':{'values':[raw('second','idle')]}}]
        requests=[]
        def open_request(request,timeout):
            requests.append(json.loads(request.data));self.assertEqual(request.full_url,'https://codex-cloud-backend.chatgpt.com/grpc')
            return io.BytesIO(json.dumps(pages.pop(0)).encode())
        rpc=CloudRPC(Path('.'));rpc.opener=SimpleNamespace(open=open_request)
        with patch('cloud_status.credentials',return_value=('session-token','account','owner')):
            identity,rows=rpc.fetch()
        self.assertEqual(len(rows),2);self.assertEqual(identity,'owner')
        self.assertTrue(all(r['method'].endswith('/ListThreads') and r['request']['archived'] is False for r in requests))
        self.assertEqual(requests[1]['request']['cursor'],'page-two')
        self.assertNotIn('private',json.dumps(rows))

    def reader(self,local,remote,ids=()):
        cloud=SimpleNamespace(snapshot=lambda:remote,acknowledge=lambda:None)
        reader=StatusReader(Path('.'),Path('.'),cloud)
        def local_snapshot():reader.local_active_ids=set(ids);return local
        reader.local_snapshot=local_snapshot
        return reader

    def test_cloud_work_is_detected_even_with_no_local_task(self):
        reader=self.reader(Status(),CloudSnapshot(frozenset({'remote'}),available=True))
        self.assertEqual(reader.snapshot(),Status('working','1 个任务运行中（本地 0 / 云端 1）',1))

    def test_local_and_cloud_tasks_are_combined_without_double_counting(self):
        reader=self.reader(Status('working','local',1),CloudSnapshot(frozenset({'same','remote'})),{'same'})
        self.assertEqual(reader.snapshot().active,2)
        self.assertEqual(reader.cloud_active,1)

    def test_cloud_question_takes_priority_over_local_work(self):
        reader=self.reader(Status('working','local',1),CloudSnapshot(frozenset({'remote'}),pending=True))
        self.assertEqual(reader.snapshot(),Status('attention','云端任务等待回答或确认',2))

    def test_cloud_question_clears_as_soon_as_its_flag_is_removed(self):
        cloud=SimpleNamespace(value=CloudSnapshot(frozenset({'remote'}),pending=True))
        source=SimpleNamespace(snapshot=lambda:cloud.value,acknowledge=lambda:None)
        reader=StatusReader(Path('.'),Path('.'),source);reader.local_snapshot=lambda:Status()
        self.assertEqual(reader.snapshot().mode,'attention')
        cloud.value=CloudSnapshot(frozenset({'remote'}))
        self.assertEqual(reader.snapshot().mode,'working')

    def test_local_detection_survives_cloud_network_failure(self):
        reader=self.reader(Status('working','local',1),CloudSnapshot(hint='云端连接暂时中断'))
        self.assertEqual(reader.snapshot(),Status('working','1 个任务运行中',1))

    def test_manual_cloud_test_uses_only_cloud_activity(self):
        import app
        with patch('app.load_config',return_value=app.DEFAULTS.copy()),patch('app.save_config'):
            controller=app.Controller()
            controller.latest_status=Status('attention','local question',1)
            cloud=SimpleNamespace(value=CloudSnapshot())
            controller.cloud_monitor=SimpleNamespace(snapshot=lambda:cloud.value)
            controller.command('cloud-test');controller.process_commands()
            self.assertEqual(controller.desired()[0],'idle')
            cloud.value=CloudSnapshot(frozenset({'remote'}),available=True)
            self.assertEqual(controller.desired()[0],'working')
            self.assertEqual(controller.desired()[2],1)


if __name__=='__main__':unittest.main()
