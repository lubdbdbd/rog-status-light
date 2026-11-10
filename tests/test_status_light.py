import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
import unittest
from contextlib import closing

SOURCE = Path(__file__).resolve().parents[1]/'src'
sys.path.insert(0,str(SOURCE))
from core import StatusReader, frame, blend_frames, pending_questions


class Effects(unittest.TestCase):
    def test_working_changes_only_top_row_and_reaches_insert(self):
        rgb=(8,124,19)
        row=list(range(14,29))
        first=frame(rgb,82,row,'working',.01)
        last=frame(rgb,82,row,'working',14*.14+.01)
        self.assertEqual(max(row,key=lambda i:sum(first[i])),14)
        self.assertEqual(max(row,key=lambda i:sum(last[i])),28)
        self.assertEqual(first[40],rgb)
        self.assertEqual(first[3],rgb)
        self.assertLess(sum(first[28]),sum(rgb))

    def test_attention_breathes_smoothly_in_original_hue(self):
        rgb=(3,122,67)
        samples=[frame(rgb,82,[], 'attention',i/120)[0] for i in range(240)]
        self.assertGreater(len(set(samples)),30)
        self.assertGreater(min(c[1] for c in samples),0)
        self.assertEqual(max(c[1] for c in samples),rgb[1])
        self.assertLessEqual(max(abs(b[1]-a[1]) for a,b in zip(samples,samples[1:])),6)
        for color in samples:
            for channel,source in zip(color,rgb):
                self.assertLessEqual(abs(channel-source*color[1]/rgb[1]),1)
        self.assertEqual(len(set(frame(rgb,82,[], 'attention',.35))),1)

    def test_wave_has_wide_gradient_and_breathing_crest(self):
        row=list(range(14,29));rgb=(0,124,0)
        first=frame(rgb,82,row,'working',0)
        one_lap=frame(rgb,82,row,'working',len(row)*.14)
        self.assertGreaterEqual(len({first[i][1] for i in row}),7)
        self.assertGreaterEqual(sum(first[i][1]>=62 for i in row),3)
        self.assertLess(max(one_lap[i][1] for i in row),max(first[i][1] for i in row)*.8)

    def test_wave_has_no_step_jump_or_wrap_jump(self):
        row=list(range(14,29));rgb=(0,124,0)
        samples=[frame(rgb,82,row,'working',i/60) for i in range(260)]
        largest=max(abs(a[i][1]-b[i][1]) for a,b in zip(samples,samples[1:]) for i in row)
        self.assertLessEqual(largest,14)
        before=frame(rgb,82,row,'working',15*.14-.0001)
        after=frame(rgb,82,row,'working',15*.14+.0001)
        self.assertLessEqual(max(abs(a[1]-b[1]) for a,b in zip(before,after)),1)

    def test_wave_never_changes_keys_outside_number_row(self):
        row=list(range(14,29));rgb=(17,124,41)
        for elapsed in (0,.17,.53,1.4,2.1,4.8):
            colors=frame(rgb,82,row,'working',elapsed)
            self.assertTrue(all(color==rgb for i,color in enumerate(colors) if i not in row))

    def test_mode_transition_is_continuous_and_reaches_target(self):
        before=[(0,8,0),(0,124,0)];after=[(0,124,0)]*2
        self.assertEqual(blend_frames(before,after,0),before)
        self.assertEqual(blend_frames(before,after,1),after)
        self.assertLess(blend_frames(before,after,.01)[0][1],10)
        self.assertGreater(blend_frames(before,after,.99)[0][1],122)

    def test_idle_original_color(self):
        self.assertEqual(frame((0,124,0),82,[], 'idle', 50),[(0,124,0)]*82)

    def test_question_is_cleared_by_its_specific_reply(self):
        question={'type':'agentMessage','id':'call_123','questions':[{'title':'test'}]}
        self.assertTrue(pending_questions([question]))
        self.assertTrue(pending_questions([question,{'type':'userMessage','content':'unrelated'}]))
        reply={'type':'userMessage','content':'questionItemId: request_user_input_async,call_123,0'}
        self.assertFalse(pending_questions([question,reply]))

    def test_freeform_or_screenshot_reply_clears_when_agent_resumes(self):
        question={'type':'agentMessage','id':'call_official_control','questions':[{'title':'Can it change brightness?'}]}
        screenshot={'type':'userMessage','content':[{'type':'image','path':'screenshot.png'}]}
        resumed={'type':'agentMessage','phase':'commentary','text':'I will use the Windows interface.'}
        self.assertTrue(pending_questions([question,screenshot]))
        self.assertFalse(pending_questions([question,screenshot,resumed]))

    def test_answering_one_question_does_not_clear_another(self):
        first={'type':'agentMessage','id':'call_first','questions':[{'title':'first'}]}
        second={'type':'agentMessage','id':'call_second','questions':[{'title':'second'}]}
        reply={'type':'userMessage','content':'questionItemId: request_user_input_async,call_second,0'}
        resumed={'type':'commandExecution','status':'completed'}
        self.assertTrue(pending_questions([first,second,reply,resumed]))

    def test_independent_agent_work_keeps_unanswered_question_pending(self):
        question={'type':'agentMessage','id':'call_pending','questions':[{'title':'pending'}]}
        work={'type':'commandExecution','status':'completed'}
        self.assertTrue(pending_questions([question,work]))

    def test_reply_to_old_question_does_not_answer_a_new_question(self):
        first={'type':'agentMessage','id':'call_first','questions':[{'title':'first'}]}
        second={'type':'agentMessage','id':'call_second','questions':[{'title':'second'}]}
        reply={'type':'userMessage','content':'typed response'}
        self.assertTrue(pending_questions([first,reply,second]))


class StateReader(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory()
        self.home=Path(self.temporary.name)
        self.data=self.home/'app'
        self.data.mkdir()
        self.now=int(time.time())
        with closing(sqlite3.connect(self.home/'state_5.sqlite')) as c:
            c.execute('CREATE TABLE threads(id TEXT PRIMARY KEY, archived INT, thread_source TEXT, agent_path TEXT)')
        with closing(sqlite3.connect(self.home/'thread_history_1.sqlite')) as c:
            c.execute('CREATE TABLE thread_turns(thread_id TEXT,turn_id TEXT,rollout_ordinal INT,status TEXT,started_at INT,completed_at INT,error_json TEXT)')
            c.execute('CREATE TABLE thread_items(thread_id TEXT,turn_id TEXT,rollout_ordinal INT,item_type TEXT,item_json TEXT)')
        self.reader=StatusReader(self.home,self.data)
        self.reader.app_running=True
        self.reader.process_floor=self.now-100
        self.reader.processes=lambda:None

    def tearDown(self):
        self.temporary.cleanup()

    def add(self,name,status='inProgress',started=None,error=None,source='user',path=None):
        with closing(sqlite3.connect(self.home/'state_5.sqlite')) as c:
            c.execute('INSERT INTO threads VALUES(?,0,?,?)',(name,source,path))
            c.commit()
        with closing(sqlite3.connect(self.home/'thread_history_1.sqlite')) as c:
            c.execute('INSERT INTO thread_turns VALUES(?,?,0,?,?,?,?)',
                (name,'turn-'+name,status,started or self.now,self.now if status!='inProgress' else None,error))
            c.commit()

    def test_multiple_tasks(self):
        self.add('one');self.add('two')
        state=self.reader.snapshot()
        self.assertEqual((state.mode,state.active),('working',2))

    def test_scheduled_local_runs_are_excluded_without_hiding_manual_work(self):
        self.add('manual');self.add('cron',source='cron');self.add('heartbeat',source='heartbeat')
        state=self.reader.snapshot()
        self.assertEqual((state.mode,state.active),('working',1))

    def test_heartbeat_turn_is_ignored_but_manual_message_in_the_same_chat_counts(self):
        self.add('one')
        item={'type':'userMessage','content':[{'type':'text','text':'<heartbeat>\n<instructions>Check progress</instructions>\n</heartbeat>'}]}
        with closing(sqlite3.connect(self.home/'thread_history_1.sqlite')) as db:
            db.execute('INSERT INTO thread_items VALUES(?,?,0,?,?)',('one','turn-one','userMessage',json.dumps(item)))
            db.commit()
        self.assertEqual(self.reader.snapshot().mode,'idle')
        item['content']=[{'type':'text','text':'Help me build a scheduler'}]
        with closing(sqlite3.connect(self.home/'thread_history_1.sqlite')) as db:
            db.execute('UPDATE thread_items SET item_json=?',(json.dumps(item),));db.commit()
        self.assertEqual(self.reader.snapshot().active,1)

    def test_automation_run_registry_excludes_runs_with_a_user_source(self):
        self.add('one');(self.home/'sqlite').mkdir()
        with closing(sqlite3.connect(self.home/'sqlite/codex.db')) as db:
            db.execute('CREATE TABLE automation_runs(thread_id TEXT)')
            db.execute("INSERT INTO automation_runs VALUES ('one')")
            db.execute('CREATE TABLE inbox_items(id TEXT,read_at INT,created_at INT)');db.commit()
        self.assertEqual(self.reader.snapshot().mode,'idle')

    def test_closed_app_is_idle(self):
        self.add('one')
        self.reader.app_running=False
        self.assertEqual(self.reader.snapshot().mode,'idle')

    def test_stale_task_from_previous_process_is_ignored(self):
        self.add('old',started=self.now-10000)
        self.assertEqual(self.reader.snapshot().mode,'idle')

    def test_child_task_not_counted_twice(self):
        self.add('root');self.add('child',source='subagent',path='/root/child')
        self.assertEqual(self.reader.snapshot().active,1)

    def test_question_overrides_working(self):
        self.add('one')
        item={'type':'agentMessage','id':'ask-1','questions':[{'title':'test'}]}
        with closing(sqlite3.connect(self.home/'thread_history_1.sqlite')) as c:
            c.execute('INSERT INTO thread_items VALUES(?,?,0,?,?)',('one','turn-one','agentMessage',json.dumps(item)))
            c.commit()
        self.assertEqual(self.reader.snapshot().mode,'attention')

    def test_quota_error_latches_until_acknowledged(self):
        self.add('one','failed',error=json.dumps({'codexErrorInfo':'usageLimitExceeded'}))
        self.assertEqual(self.reader.snapshot().mode,'attention')
        self.reader.acknowledge()
        self.assertEqual(self.reader.snapshot().mode,'idle')

    def test_permission_hook_resolves_when_tool_returns(self):
        self.add('one')
        folder=self.data/'events';folder.mkdir()
        event={'event':'PermissionRequest','session_id':'one','tool':'Bash','time':time.time()}
        (folder/'1.json').write_text(json.dumps(event))
        self.assertEqual(self.reader.snapshot().mode,'attention')
        event['event']='PostToolUse'
        (folder/'2.json').write_text(json.dumps(event))
        self.assertEqual(self.reader.snapshot().mode,'working')

    def test_completed_task_triggers_short_notification(self):
        self.add('one');self.reader.snapshot()
        with closing(sqlite3.connect(self.home/'thread_history_1.sqlite')) as c:
            c.execute("UPDATE thread_turns SET status='completed',completed_at=?",(time.time(),))
            c.commit()
        self.assertEqual(self.reader.snapshot().mode,'attention')
        self.reader.acknowledge()
        self.assertEqual(self.reader.snapshot().mode,'idle')

    def test_successful_model_retry_clears_quota_hook(self):
        self.add('one')
        folder=self.data/'events';folder.mkdir()
        event={'event':'PostModelRequest','session_id':'one','tool':'','quota':True,
               'model_status':'failed','time':time.time()}
        (folder/'1.json').write_text(json.dumps(event))
        self.assertEqual(self.reader.snapshot().mode,'attention')
        event.update(quota=False,model_status='completed')
        (folder/'2.json').write_text(json.dumps(event))
        self.assertEqual(self.reader.snapshot().mode,'working')


if __name__=='__main__':
    unittest.main(verbosity=2)
