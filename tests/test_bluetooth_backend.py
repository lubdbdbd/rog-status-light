import asyncio
import json
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

WORK=Path(__file__).resolve().parent
sys.path[:0]=[str(WORK.parent/'src'),str(WORK.parents[1]/'work'/'deps')]
import bluetooth_backend as ble


class BluetoothValidation(unittest.TestCase):
    def test_row_scheduler_preserves_unsent_changes_and_settles_all_keys(self):
        scheduler=ble.RowScheduler((7,244,7))
        target=[(2,60,2)]*15
        for _ in range(4):
            chosen=scheduler.choose(target)
            self.assertLessEqual(len(chosen),4)
            scheduler.accept(chosen)
        self.assertEqual([scheduler.current[i] for i in ble.TOP_ROW],target)
        self.assertEqual(scheduler.choose(target),[])

    def test_prioritized_refresh_tracks_the_wave_more_closely_than_old_seven_fps_frames(self):
        from core import frame
        rgb=(7,244,7);scheduler=ble.RowScheduler(rgb);old=[rgb]*15
        old_deadline=new_deadline=0.;old_error=new_error=0.
        for sample in range(120*6):
            t=sample/120;desired=frame(rgb,68,list(range(15)),'working',t)[:15]
            if t>=old_deadline:old=list(desired);old_deadline+=1/7.43
            if t>=new_deadline:scheduler.accept(scheduler.choose(desired));new_deadline+=1/30
            old_error+=sum(abs(a[1]-b[1]) for a,b in zip(desired,old))
            new_error+=sum(abs(a[1]-scheduler.current[i][1]) for i,a in zip(ble.TOP_ROW,desired))
        self.assertLess(new_error,old_error*.85)

    def test_direct_packet_uses_the_hardware_matrix_and_four_lamps_per_ble_packet(self):
        entries=[(i,(7,244,7)) for i in ble.KEY_LEDS]
        packets=ble.direct_packets(entries)
        self.assertEqual(len(ble.KEY_LEDS),68)
        self.assertEqual(len(set(ble.KEY_LEDS)),68)
        self.assertEqual(ble.TOP_ROW,tuple(range(1,114,8)))
        self.assertEqual(len(packets),17)
        self.assertTrue(all(len(p)==20 and p[:4]==bytes((0xc0,0x81,4,0)) for p in packets))
        decoded=[(p[4+j*4],tuple(p[5+j*4:8+j*4])) for p in packets for j in range(p[2])]
        self.assertEqual(decoded,entries)
        with self.assertRaises(ValueError):ble.direct_packets([(14,(7,244,7))])

    def test_production_client_cannot_commit_settings_without_a_manual_test(self):
        with self.assertRaises(ValueError):asyncio.run(ble.BluetoothCommands().commit())

    def test_streaming_does_not_wait_for_a_vendor_reply_when_att_write_succeeds(self):
        client=ble.BluetoothCommands();written=[]
        async def write(buffer,option):
            written.append(bytes(buffer))
            return SimpleNamespace(status=0)
        client.command=SimpleNamespace(write_value_with_result_and_option_async=write)
        asyncio.run(client.direct([(1,(7,244,7)),(9,(2,70,2))]))
        self.assertEqual(len(written),1)
        self.assertEqual(written[0][:4],bytes((0xc0,0x81,2,0)))
        self.assertTrue(client.queue.empty())

    def test_fast_transport_remains_gated_until_explicit_validation(self):
        with self.assertRaises(ValueError):
            asyncio.run(ble.BluetoothCommands().direct([(1,(7,244,7))],without_response=True))

    def test_effect_format_matches_observed_armoury_packets(self):
        self.assertEqual(ble.effect_packet([7,244,7],100).hex(),'512c0000ff6400ffff07f4070000000000000000')
        self.assertEqual(ble.effect_packet([0,255,0],50,True).hex(),'512c01001e3200ffff00ff000000000000000000')
        for rgb,brightness in (([0,0,0],100),([0,255,0],0),([0,256,0],100),([True,1,1],100)):
            with self.assertRaises(ValueError):ble.effect_packet(rgb,brightness)

    def test_restore_snapshot_cannot_contain_an_arbitrary_command(self):
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)/'restore.json'
            p.write_text(json.dumps({'packet_hex':ble.effect_packet([7,244,7],100).hex()}),encoding='utf-8')
            self.assertEqual(ble.load_restore(p),([7,244,7],100))
            p.write_text(json.dumps({'packet_hex':('5055'+'00'*18)}),encoding='utf-8')
            with self.assertRaises(ValueError):ble.load_restore(p)

    def run_sequence(self,cancel_during_breath=False,fail_during_breath=False):
        clock=SimpleNamespace(now=100.)
        cancel=threading.Event()
        states=[]
        class Client:
            sent=[]
            mode=0
            staged=0
            closed=False
            async def connect(self):pass
            async def effect(self,rgb,brightness,breathing=False):
                self.staged=int(breathing)
                self.sent.append(('effect',self.staged))
                if breathing and fail_during_breath:raise TimeoutError('simulated lost reply')
            async def commit(self):
                self.mode=self.staged
                self.sent.append(('commit',self.mode))
            async def profile(self):return {'effect':self.mode,'profile':1}
            def close(self):self.closed=True
        client=Client()
        def tick():
            if cancel_during_breath and clock.now>=104.1:cancel.set()
        async def sleep(delay):clock.now+=delay
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)
            (p/'bluetooth-lighting-restore.json').write_text(json.dumps({'packet_hex':ble.effect_packet([7,244,7],100).hex()}),encoding='utf-8')
            with patch('bluetooth_backend.time.perf_counter',side_effect=lambda:clock.now),\
                 patch('bluetooth_backend.asyncio.sleep',side_effect=sleep):
                result=asyncio.run(ble.run_manual_test(p,lambda **s:states.append(s),threading.Event(),cancel,tick,lambda:client))
            self.assertTrue((p/'bluetooth-test-result.json').exists())
        self.assertTrue(client.closed)
        self.assertEqual(client.mode,0)
        self.assertTrue(result['restored'])
        self.assertFalse(result['automatic_lighting_enabled'])
        self.assertEqual(client.sent[-2:],[('effect',0),('commit',0)])
        return result,client

    def test_manual_test_commits_and_restores_but_does_not_enable_automatic_changes(self):
        result,client=self.run_sequence()
        self.assertEqual(result['before']['effect'],0)
        self.assertEqual(result['after_set']['effect'],0)
        self.assertEqual(result['after_commit']['effect'],1)
        self.assertEqual(client.sent,[('effect',1),('commit',1),('effect',0),('commit',0)])

    def test_stop_during_breath_restores_the_original_static_state(self):
        result,client=self.run_sequence(cancel_during_breath=True)
        self.assertTrue(result['cancelled'])

    def test_lost_reply_still_attempts_restore(self):
        with self.assertLogs(level='ERROR'):
            result,client=self.run_sequence(fail_during_breath=True)
        self.assertIn('simulated',result['error'])

    def run_wave(self,reject=False,cancel_wave=False):
        clock=SimpleNamespace(now=100.)
        cancel=threading.Event();direct_writes=[]
        class Client:
            sent=[]
            mode=0
            closed=False
            async def connect(self):pass
            async def profile(self):return {'effect':self.mode,'profile':1}
            async def effect(self,rgb,brightness,breathing=False):
                self.mode=int(breathing);self.sent.append(('effect',self.mode))
            async def commit(self):self.sent.append(('commit',self.mode))
            def optimize_streaming(self):return {'fixture':True}
            async def direct(self,entries,tick=None):
                if reject:raise RuntimeError('direct rejected')
                entries=list(entries);direct_writes.append(entries);self.mode=0xf1
                for packet in ble.direct_packets(entries):
                    if tick:tick()
                    clock.now+=.03
            def close(self):self.closed=True
        client=Client()
        def tick():
            if cancel_wave and direct_writes:cancel.set()
        async def sleep(delay):clock.now+=delay
        with tempfile.TemporaryDirectory() as directory:
            data=Path(directory)
            (data/'bluetooth-lighting-restore.json').write_text(json.dumps({'packet_hex':ble.effect_packet([7,244,7],100).hex()}),encoding='utf-8')
            with patch('bluetooth_backend.time.perf_counter',side_effect=lambda:clock.now),\
                 patch('bluetooth_backend.asyncio.sleep',side_effect=sleep):
                result=asyncio.run(ble.run_wave_test(data,lambda **s:None,threading.Event(),cancel,tick,lambda:client))
            self.assertTrue((data/'bluetooth-wave-result.json').exists())
        self.assertTrue(client.closed)
        self.assertTrue(result['restored'])
        self.assertEqual(client.mode,0)
        self.assertEqual([x for x in client.sent if x[0]=='commit'],[('commit',0)])
        return result,direct_writes

    def test_wave_test_streams_without_commits_and_only_changes_the_number_row_after_seeding(self):
        result,writes=self.run_wave()
        self.assertEqual(writes[0],[(i,(7,244,7)) for i in ble.KEY_LEDS])
        self.assertTrue(all(tuple(i for i,_ in row)==ble.TOP_ROW for row in writes[1:]))
        self.assertEqual(result['commits_during_animation'],0)
        self.assertEqual(result['after_volatile_breath']['effect'],1)
        self.assertGreater(result['frames'],1)

    def test_rejected_direct_frame_restores_static(self):
        with self.assertLogs(level='ERROR'):result,writes=self.run_wave(reject=True)
        self.assertIn('direct rejected',result['error'])

    def test_cancelled_wave_restores_static(self):
        result,writes=self.run_wave(cancel_wave=True)
        self.assertTrue(result['cancelled'])

    def run_smooth(self,reject_fast=False):
        clock=SimpleNamespace(now=100.)
        class Client:
            sent=[]
            sent_total=0
            packet_seconds=None
            direct_replies=0
            closed=False
            commits=0
            async def connect(self):pass
            async def profile(self):return {'effect':0,'profile':1}
            async def effect(self,rgb,brightness,breathing=False):pass
            async def commit(self):self.commits+=1
            def optimize_streaming(self):return {'fixture':True}
            async def direct(self,entries,tick=None,without_response=False):
                self.assert_no_commit()
                if without_response and reject_fast:raise RuntimeError('fast transport unavailable')
                packets=ble.direct_packets(entries)
                self.packet_seconds=.002 if without_response else .03
                for packet in packets:
                    if tick:tick()
                    clock.now+=self.packet_seconds
                    self.sent.append({'command':'c081'});self.sent_total+=1
                    if not without_response:self.direct_replies+=1
            def assert_no_commit(self):
                if self.commits:raise AssertionError('Unexpected persistent commit during animation')
            def close(self):self.closed=True
        client=Client()
        async def async_sleep(delay):clock.now+=delay
        def highres_sleep(delay):clock.now+=delay
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)
            (p/'bluetooth-lighting-restore.json').write_text(json.dumps({'packet_hex':ble.effect_packet([7,244,7],100).hex()}),encoding='utf-8')
            with patch('bluetooth_backend.time.perf_counter',side_effect=lambda:clock.now),\
                 patch('bluetooth_backend.asyncio.sleep',side_effect=async_sleep),\
                 patch('bluetooth_backend.time.sleep',side_effect=highres_sleep):
                result=asyncio.run(ble.run_smooth_test(p,lambda **s:None,threading.Event(),threading.Event(),lambda:None,lambda:client))
            self.assertTrue((p/'bluetooth-smooth-result.json').exists())
        self.assertTrue(client.closed)
        self.assertEqual(client.commits,1)
        self.assertTrue(result['restored'])
        return result

    def test_smooth_test_adapts_packet_budget_without_commits_during_animation(self):
        result=self.run_smooth()
        fast,compatible=result['candidates']
        self.assertGreater(fast['lamp_updates'],compatible['lamp_updates'])
        self.assertGreater(fast['updates_per_second'],27)
        self.assertGreater(compatible['updates_per_second'],27)
        self.assertEqual(result['commits_during_animation'],0)

    def test_unsupported_fast_transport_still_runs_compatible_candidate_and_restores(self):
        with self.assertLogs(level='WARNING'):result=self.run_smooth(reject_fast=True)
        self.assertIn('unavailable',result['candidates'][0]['error'])
        self.assertGreater(result['candidates'][1]['packets'],100)


if __name__=='__main__':unittest.main()
