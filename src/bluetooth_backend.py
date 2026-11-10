"""ROG Bluetooth lighting using the physically verified Armoury format.

Only FFF2 command replies are subscribed to. FFF4 keyboard state is not used.
Automatic streaming requires a local record of physical validation.
"""
from __future__ import annotations

import asyncio
from collections import deque
import json
import logging
from pathlib import Path
import time

# M603 hardware LED matrix, distinct from Windows LampArray indices 14..28.
TOP_ROW=tuple(range(1,0x72,8))
KEY_LEDS=(
    *TOP_ROW,*range(2,0x73,8),
    3,11,19,27,35,43,51,59,67,75,83,91,107,115,
    4,20,28,36,44,52,60,68,76,84,92,100,108,116,
    5,13,21,53,77,85,93,101,109,117)


class RowScheduler:
    """Spend each packet on the largest visible errors, retaining all others."""
    def __init__(self,initial):
        self.current={i:tuple(initial) for i in TOP_ROW}
        self.last_sent={i:0 for i in TOP_ROW}
        self.sequence=0

    def choose(self,desired,budget=4):
        if len(desired)!=len(TOP_ROW):raise ValueError('Expected the complete number row')
        candidates=[]
        for index,color in zip(TOP_ROW,desired):
            color=tuple(color)
            error=max(abs(a-b) for a,b in zip(color,self.current[index]))
            if error:candidates.append((error,-self.last_sent[index],index,color))
        candidates.sort(reverse=True)
        return [(index,color) for _,_,index,color in candidates[:budget]]

    def accept(self,entries):
        self.sequence+=1
        for index,color in entries:
            self.current[index]=tuple(color)
            self.last_sent[index]=self.sequence


def direct_packets(entries):
    """Candidate 20-byte radio frames; enabled only in a manual hardware test."""
    entries=list(entries)
    if not entries or len({i for i,_ in entries})!=len(entries):
        raise ValueError('LED entries must be nonempty and unique')
    for index,rgb in entries:
        if index not in KEY_LEDS or len(rgb)!=3 or any(type(v) is not int or not 0<=v<=255 for v in rgb):
            raise ValueError('Invalid M603 lamp or colour')
    packets=[]
    for start in range(0,len(entries),4):
        chunk=entries[start:start+4]
        packet=bytearray(20)
        packet[:4]=bytes((0xc0,0x81,len(chunk),0))
        for j,(index,rgb) in enumerate(chunk):packet[4+j*4:8+j*4]=bytes((index,*rgb))
        packets.append(bytes(packet))
    return packets


def effect_packet(rgb, brightness, breathing=False):
    if len(rgb)!=3 or any(type(v) is not int or not 0<=v<=255 for v in rgb):
        raise ValueError('Invalid RGB colour')
    if not any(rgb) or type(brightness) is not int or not 1<=brightness<=100:
        raise ValueError('A visible restore colour and brightness are required')
    packet=bytearray(20)
    packet[:9]=bytes((0x51,0x2c,1 if breathing else 0,0,30 if breathing else 255,
                      brightness,0,255,255))
    packet[9:12]=bytes(rgb)
    return bytes(packet)


def load_restore(path):
    data=json.loads(Path(path).read_text(encoding='utf-8-sig'))
    packet=bytes.fromhex(data['packet_hex'])
    if len(packet)!=20 or packet!=effect_packet(list(packet[9:12]),packet[5]):
        raise ValueError('The original static lighting snapshot is not valid')
    return list(packet[9:12]),packet[5]


def automatic_settings(data):
    """Use only the transport the owner has actually observed working."""
    data=Path(data)
    try:
        validation=json.loads((data/'bluetooth-validation.json').read_text(encoding='utf-8-sig'))
        if (validation.get('automatic_enabled') is not True or
            validation.get('volatile_effects')!='passed' or
            validation.get('per_key_stream')!='passed' or
            validation.get('smooth_transport') not in ('fast','compatible')):
            return None
        rgb,brightness=load_restore(data/'bluetooth-lighting-restore.json')
        return rgb,brightness,validation['smooth_transport']
    except (OSError,ValueError,KeyError,IndexError,TypeError):
        return None


async def connected_keyboards():
    from winrt.windows.devices.enumeration import DeviceInformation
    from winrt.windows.devices.bluetooth import BluetoothLEDevice,BluetoothConnectionStatus
    selector=BluetoothLEDevice.get_device_selector_from_connection_status(BluetoothConnectionStatus.CONNECTED)
    devices=await asyncio.wait_for(DeviceInformation.find_all_async_aqs_filter(selector),timeout=4)
    return [d for d in devices if ''.join(c for c in d.name.upper() if c.isalnum())=='ROGFALCHIONRXLOWPROFILE']


class BluetoothCommands:
    def __init__(self):
        self.device=None
        self.service=None
        self.command=None
        self.reply=None
        self.token=None
        self.queue=asyncio.Queue()
        self.sent=deque(maxlen=512)
        self.sent_total=0
        self.open_device=None
        self.closing=False
        self.allow_persistent_commit=False
        self.throughput_request=None
        self.direct_rejected=False
        self.direct_replies=0
        self.allow_unconfirmed_stream=False
        self.packet_seconds=None

    async def connect(self):
        from winrt.windows.devices.bluetooth import BluetoothLEDevice,BluetoothCacheMode
        from winrt.windows.devices.bluetooth.genericattributeprofile import GattSharingMode,GattClientCharacteristicConfigurationDescriptorValue
        devices=await connected_keyboards()
        if not devices:raise RuntimeError('未找到已连接的蓝牙键盘')
        self.device=(await self.open_device(devices[0].id) if self.open_device is not None else
                     await asyncio.wait_for(BluetoothLEDevice.from_id_async(devices[0].id),timeout=4))
        result=await asyncio.wait_for(self.device.get_gatt_services_with_cache_mode_async(BluetoothCacheMode.CACHED),timeout=4)
        self.service=next((s for s in result.services if str(s.uuid).startswith('0000fff0')),None)
        if self.service is None:raise RuntimeError('蓝牙控制服务不可用')
        await asyncio.wait_for(self.service.open_async(GattSharingMode.SHARED_READ_AND_WRITE),timeout=4)
        result=await asyncio.wait_for(self.service.get_characteristics_with_cache_mode_async(BluetoothCacheMode.CACHED),timeout=4)
        self.command=next(c for c in result.characteristics if str(c.uuid).startswith('0000fff1'))
        self.reply=next(c for c in result.characteristics if str(c.uuid).startswith('0000fff2'))
        loop=asyncio.get_running_loop()
        def callback(sender,args):
            if self.closing:return
            value=bytes(args.characteristic_value)
            if value[:2]==b'\xc0\x81':
                self.direct_replies+=1
                if len(value)>2 and value[2] in (0xfa,0xff):self.direct_rejected=True
                return  # Streaming replies never accumulate in a control queue.
            # Drop unrelated responses before they enter this client's queue.
            if value[:2] in (b'\x51\x2c',b'\x50\x55',b'\x12\x00'):
                loop.call_soon_threadsafe(self.queue.put_nowait,value)
        self.token=self.reply.add_value_changed(callback)
        status=await asyncio.wait_for(self.reply.write_client_characteristic_configuration_descriptor_async(
            GattClientCharacteristicConfigurationDescriptorValue.NOTIFY),timeout=4)
        if int(status)!=0:raise RuntimeError('蓝牙命令回复订阅失败')

    async def exchange(self,packet):
        from winrt.windows.devices.bluetooth.genericattributeprofile import GattWriteOption
        from winrt.windows.storage.streams import DataWriter
        if packet[:2] not in (b'\x51\x2c',b'\x50\x55',b'\x12\x00',b'\xc0\x81') or len(packet)!=20:
            raise ValueError('Unsupported Bluetooth command')
        if packet[:2]==b'\x50\x55' and not self.allow_persistent_commit:
            raise ValueError('Settings commits are limited to an explicit manual test')
        while not self.queue.empty():self.queue.get_nowait()
        writer=DataWriter();writer.write_bytes(packet)
        started=time.perf_counter()
        result=await asyncio.wait_for(self.command.write_value_with_result_and_option_async(
            writer.detach_buffer(),GattWriteOption.WRITE_WITH_RESPONSE),timeout=3)
        if int(result.status)!=0:raise RuntimeError(f'蓝牙发送失败：{result.status}')
        deadline=time.perf_counter()+3
        while True:
            remaining=deadline-time.perf_counter()
            if remaining<=0:raise TimeoutError('未收到匹配的蓝牙命令回复')
            reply=await asyncio.wait_for(self.queue.get(),timeout=remaining)
            if reply[:2]!=packet[:2]:continue
            if packet[:2]==b'\x51\x2c' and reply[2:12]!=packet[2:12]:continue
            if packet[:2]==b'\xc0\x81' and reply[2:4]!=packet[2:4]:
                raise RuntimeError('键盘未确认逐键灯光帧')
            if b'\xff\xaa'==reply[4:6]:raise RuntimeError('键盘拒绝了灯光命令')
            self.sent.append({'command':packet[:2].hex(),'effect':packet[2] if packet[:2]==b'\x51\x2c' else None,
                              'seconds':round(time.perf_counter()-started,4)})
            self.sent_total+=1
            return reply

    async def effect(self,rgb,brightness,breathing=False):
        return await self.exchange(effect_packet(rgb,brightness,breathing))

    async def commit(self):
        # One explicit settings commit per manual test phase, never per frame.
        if not self.allow_persistent_commit:
            raise ValueError('Settings commits are limited to an explicit manual test')
        return await self.exchange(b'\x50\x55'+bytes(18))

    async def direct(self,entries,tick=None,without_response=False):
        from winrt.windows.devices.bluetooth.genericattributeprofile import GattWriteOption
        from winrt.windows.storage.streams import DataWriter
        if without_response and not self.allow_unconfirmed_stream:
            raise ValueError('The fast transport must be physically validated first')
        for packet in direct_packets(entries):
            if tick is not None:tick()
            if self.direct_rejected:raise RuntimeError('键盘拒绝了逐键灯光命令')
            writer=DataWriter();writer.write_bytes(packet)
            started=time.perf_counter()
            result=await asyncio.wait_for(self.command.write_value_with_result_and_option_async(
                writer.detach_buffer(),GattWriteOption.WRITE_WITHOUT_RESPONSE if without_response else GattWriteOption.WRITE_WITH_RESPONSE),timeout=3)
            if int(result.status)!=0:raise RuntimeError(f'蓝牙逐键帧发送失败：{result.status}')
            elapsed=time.perf_counter()-started
            self.packet_seconds=elapsed if self.packet_seconds is None else self.packet_seconds*.8+elapsed*.2
            # Streaming does not require an additional vendor reply. Some
            # firmware only acknowledges the ATT write for direct frames.
            self.sent.append({'command':'c081','effect':None,'seconds':round(time.perf_counter()-started,4)})
            self.sent_total+=1

    def optimize_streaming(self):
        result={}
        try:
            from winrt.windows.devices.bluetooth import BluetoothLEPreferredConnectionParameters
            result['interval_before']=self.device.get_connection_parameters().connection_interval
            self.throughput_request=self.device.request_preferred_connection_parameters(
                BluetoothLEPreferredConnectionParameters.throughput_optimized)
            result['request_status']=int(self.throughput_request.status)
        except Exception as error:
            result['unavailable']=str(error)
        return result

    async def profile(self):
        reply=await self.exchange(b'\x12\x00'+bytes(18))
        return {'effect':reply[9],'profile':reply[10]}

    def close(self):
        self.closing=True
        if self.throughput_request is not None:
            try:self.throughput_request.close()
            except Exception:logging.exception('Could not release temporary Bluetooth throughput request')
        if self.reply is not None and self.token is not None:
            try:self.reply.remove_value_changed(self.token)
            except Exception:logging.exception('Could not detach Bluetooth reply listener')
        if self.service is not None:self.service.close()
        if self.device is not None:self.device.close()


class BluetoothLighting:
    """One volatile lighting session, shared by automatic and panel effects."""
    def __init__(self,settings,open_device=None,client_factory=BluetoothCommands):
        self.rgb,self.brightness,self.transport=settings
        self.base=tuple(round(c*self.brightness/100) for c in self.rgb)
        self.client=client_factory()
        self.client.open_device=open_device
        self.client.allow_unconfirmed_stream=self.transport=='fast'
        self.client.allow_persistent_commit=False
        self.mode=None
        self.scheduler=None
        self.changed=False
        self.connected_at=0

    @property
    def connected(self):
        return self.client.device is not None and int(self.client.device.connection_status)==1

    async def connect(self):
        await self.client.connect()
        self.client.optimize_streaming()
        self.connected_at=time.perf_counter()

    async def render(self,mode,elapsed,step=.14,flash=.55,tick=None):
        from core import frame
        if mode not in ('idle','working','attention'):raise ValueError('Unknown lighting mode')
        if mode!=self.mode:
            self.changed=True
            await self.client.effect(self.rgb,self.brightness,mode=='attention')
            if mode=='working':
                self.client.direct_rejected=False
                # Entering host mode needs one complete base frame. Later
                # frames touch only the number row, so the body stays steady.
                await self.client.direct(((i,self.base) for i in KEY_LEDS),tick)
                self.scheduler=RowScheduler(self.base)
                self.client.packet_seconds=None
            else:self.scheduler=None
            self.mode=mode
        if mode!='working':return False
        desired=frame(self.base,len(KEY_LEDS),list(range(15)),mode,elapsed,step,flash)[:15]
        seconds=self.client.packet_seconds
        budget=(15 if self.transport=='fast' else 4) if seconds is None else max(4,min(15,4*int((1/30-.001)/max(.001,seconds))))
        selected=self.scheduler.choose(desired,budget)
        if selected:
            await self.client.direct(selected,tick,without_response=self.transport=='fast')
            self.scheduler.accept(selected)
        return bool(selected)

    async def close(self,restore=True):
        try:
            if restore and self.changed:
                await self.client.effect(self.rgb,self.brightness)
        finally:
            self.client.close()


async def run_manual_test(data,update,stop,cancel,tick,client_factory=BluetoothCommands,open_device=None):
    data=Path(data)
    report={'started_at':time.time(),'automatic_lighting_enabled':False,'steps':[],
            'restored':False,'manual_start':True}
    client=client_factory()
    client.open_device=open_device
    client.allow_persistent_commit=True
    changed=False
    rgb,brightness=load_restore(data/'bluetooth-lighting-restore.json')
    async def wait(seconds):
        until=time.perf_counter()+seconds
        while time.perf_counter()<until:
            tick()
            if stop.is_set() or cancel.is_set():raise asyncio.CancelledError()
            await asyncio.sleep(.1)
    def phase(text):
        update(device='ROG 魔导士 · 蓝牙',mode='idle',reason=text,
               bluetooth_test_status=text,background_hint='测试结束后恢复原色常亮',available=False)
        logging.info('Bluetooth manual test: %s',text)
    try:
        phase('蓝牙测试 1/3 · 原色常亮（4 秒）')
        await client.connect()
        report['before']=await client.profile()
        # The owner already chose this captured static state in Armoury Crate.
        await wait(4)
        phase('蓝牙测试 2/3 · 原色呼吸（8 秒）')
        changed=True
        await client.effect(rgb,brightness,True)
        report['after_set']=await client.profile()
        await client.commit()
        report['after_commit']=await client.profile()
        await wait(8)
    except asyncio.CancelledError:
        report['cancelled']=True
    except Exception as error:
        report['error']=str(error)
        logging.exception('Bluetooth manual test failed')
    finally:
        if changed:
            phase('蓝牙测试 3/3 · 正在恢复原色常亮')
            try:
                await client.effect(rgb,brightness)
                await client.commit()
                report['after_restore']=await client.profile()
                report['restored']=True
            except Exception as error:
                report['restore_error']=str(error)
                logging.exception('Could not restore Bluetooth static lighting')
        client.close()
        report['steps']=list(client.sent)
        report['finished_at']=time.time()
        (data/'bluetooth-test-result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        if report.get('restore_error'):
            result='蓝牙测试恢复失败 · 请在奥创选择原色常亮'
        elif report.get('error'):
            result='蓝牙测试未完成 · '+report['error']
        elif report.get('cancelled'):
            result='蓝牙测试已停止 · 原色常亮已恢复'
        else:
            result='蓝牙测试已结束 · 请反馈是否看见呼吸'
        update(bluetooth_test_status=result,reason=result,background_hint=result,available=False)
    return report


async def run_wave_test(data,update,stop,cancel,tick,client_factory=BluetoothCommands,open_device=None):
    from core import frame
    data=Path(data)
    rgb,brightness=load_restore(data/'bluetooth-lighting-restore.json')
    client=client_factory()
    client.open_device=open_device
    client.allow_persistent_commit=True  # Used once in the final restore only.
    report={'started_at':time.time(),'manual_start':True,'restored':False,'commits_during_animation':0}
    changed=False
    def check():
        tick()
        if stop.is_set() or cancel.is_set():raise asyncio.CancelledError()
    async def wait(seconds):
        until=time.perf_counter()+seconds
        while time.perf_counter()<until:
            check();await asyncio.sleep(.05)
    def phase(text):
        logging.info('Bluetooth wave test: %s',text)
        update(device='ROG 魔导士 · 蓝牙',mode='idle',reason=text,available=False,
               bluetooth_test_status=text,background_hint='测试结束后自动恢复原色常亮')
    try:
        phase('蓝牙海浪测试 1/3 · 原色呼吸（8 秒）')
        await client.connect()
        report['before']=await client.profile()
        changed=True
        # Deliberately no settings commit here: verify transient switching.
        await client.effect(rgb,brightness,True)
        report['after_volatile_breath']=await client.profile()
        await wait(8)
        await client.effect(rgb,brightness)
        phase('蓝牙海浪测试 2/3 · 正在准备逐键灯光')
        report['throughput']=client.optimize_streaming()
        base=tuple(round(v*brightness/100) for v in rgb)
        await client.direct(((i,base) for i in KEY_LEDS),check)
        report['after_seed']=await client.profile()
        phase('蓝牙海浪测试 2/3 · Esc → Ins 海浪（12 秒）')
        started=time.perf_counter();frames=0
        while time.perf_counter()-started<12:
            check()
            began=time.perf_counter()
            colors=frame(base,len(KEY_LEDS),list(range(15)),'working',began-started)
            await client.direct(zip(TOP_ROW,colors[:15]),check)
            frames+=1
            update(bluetooth_test_status='蓝牙海浪测试 · 请观察数字行，结束后恢复常亮',
                   render_fps=round(frames/max(.01,time.perf_counter()-started),1))
            await asyncio.sleep(max(.001,1/30-(time.perf_counter()-began)))
        report.update(frames=frames,stream_fps=round(frames/(time.perf_counter()-started),2))
    except asyncio.CancelledError:
        report['cancelled']=True
    except Exception as error:
        report['error']=str(error)
        logging.exception('Bluetooth wave validation failed')
    finally:
        if changed:
            phase('蓝牙海浪测试 3/3 · 正在恢复原色常亮')
            try:
                await client.effect(rgb,brightness)
                await client.commit()
                report['after_restore']=await client.profile()
                report['restored']=True
            except Exception as error:
                report['restore_error']=str(error)
                logging.exception('Could not restore Bluetooth lighting after wave test')
        client.close()
        report['steps']=list(client.sent)
        report['finished_at']=time.time()
        (data/'bluetooth-wave-result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        if report.get('restore_error'):text='蓝牙测试恢复失败 · 请在奥创选择原色常亮'
        elif report.get('error'):text='蓝牙海浪测试未完成 · '+report['error']
        elif report.get('cancelled'):text='蓝牙海浪测试已停止 · 已恢复原色常亮'
        else:text='蓝牙海浪测试结束 · 请反馈呼吸和数字行效果'
        update(bluetooth_test_status=text,reason=text,background_hint=text,available=False)
    return report


async def run_smooth_test(data,update,stop,cancel,tick,client_factory=BluetoothCommands,open_device=None):
    from core import frame
    data=Path(data)
    rgb,brightness=load_restore(data/'bluetooth-lighting-restore.json')
    base=tuple(round(c*brightness/100) for c in rgb)
    client=client_factory();client.open_device=open_device
    client.allow_persistent_commit=True
    client.allow_unconfirmed_stream=True  # Explicitly selected manual validation.
    report={'started_at':time.time(),'manual_start':True,'candidates':[],'restored':False,
            'commits_during_animation':0,'scheduler':'largest_visible_error'}
    changed=False
    def check():
        tick()
        if stop.is_set() or cancel.is_set():raise asyncio.CancelledError()
    def phase(text):
        logging.info('Bluetooth smoothness test: %s',text)
        update(device='ROG 魔导士 · 蓝牙',mode='idle',reason=text,available=False,
               bluetooth_test_status=text,background_hint='两段测试结束后恢复原色常亮')
    try:
        phase('蓝牙流畅测试 · 正在连接')
        await client.connect();changed=True
        report['throughput']=client.optimize_streaming()
        for number,transport in enumerate(('fast','compatible'),1):
            check()
            phase(f'蓝牙流畅测试 · 第 {number} 段准备中')
            await client.effect(rgb,brightness)
            client.direct_rejected=False
            await client.direct(((i,base) for i in KEY_LEDS),check)
            scheduler=RowScheduler(base)
            candidate={'transport':transport,'packets':0,'lamp_updates':0}
            report['candidates'].append(candidate)
            started=time.perf_counter();frames=0
            client.packet_seconds=None
            replies_before=getattr(client,'direct_replies',0)
            phase(f'蓝牙流畅测试 · 第 {number} 段（10 秒）')
            try:
                while time.perf_counter()-started<10:
                    check();began=time.perf_counter()
                    desired=frame(base,len(KEY_LEDS),list(range(15)),'working',began-started)[:15]
                    packet_seconds=getattr(client,'packet_seconds',None)
                    budget=(15 if transport=='fast' else 4) if packet_seconds is None else max(4,min(15,4*int((1/30-.001)/max(.001,packet_seconds))))
                    selected=scheduler.choose(desired,budget)
                    if selected:
                        await client.direct(selected,check,without_response=transport=='fast')
                        scheduler.accept(selected)
                        candidate['packets']+=(len(selected)+3)//4;candidate['lamp_updates']+=len(selected)
                    frames+=1
                    update(render_fps=round(frames/max(.01,time.perf_counter()-started),1))
                    await asyncio.sleep(0)
                    # The dedicated lighting thread uses the same high-
                    # resolution pacing as USB/RF. Asyncio's timed sleeps
                    # otherwise add another Windows timer tick per packet.
                    delay=1/30-(time.perf_counter()-began)
                    if delay>0:time.sleep(delay)
            except asyncio.CancelledError:raise
            except Exception as error:
                candidate['error']=str(error)
                logging.warning('Bluetooth transport %s unavailable: %s',transport,error)
            candidate['seconds']=round(time.perf_counter()-started,3)
            candidate['updates_per_second']=round(frames/max(.01,candidate['seconds']),2)
            candidate['vendor_replies']=getattr(client,'direct_replies',0)-replies_before
    except asyncio.CancelledError:report['cancelled']=True
    except Exception as error:
        report['error']=str(error);logging.exception('Bluetooth smoothness test failed')
    finally:
        if changed:
            phase('蓝牙流畅测试 · 正在恢复原色常亮')
            try:
                await client.effect(rgb,brightness);await client.commit()
                report['after_restore']=await client.profile();report['restored']=True
            except Exception as error:
                report['restore_error']=str(error);logging.exception('Bluetooth smoothness restore failed')
        client.close()
        report['steps']=list(client.sent);report['finished_at']=time.time()
        report['trace_tail_limit']=512
        report['sent_total']=getattr(client,'sent_total',len(client.sent))
        (data/'bluetooth-smooth-result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        if report.get('restore_error'):text='流畅测试恢复失败 · 请在奥创选择原色常亮'
        elif report.get('error'):text='流畅测试未完成 · '+report['error']
        elif report.get('cancelled'):text='流畅测试已停止 · 已恢复原色常亮'
        else:text='流畅测试结束 · 请反馈哪一段更顺滑'
        update(bluetooth_test_status=text,reason=text,background_hint=text,available=False)
    return report
