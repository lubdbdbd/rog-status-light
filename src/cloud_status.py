"""Read cloud task metadata through the signed-in Codex account.

The endpoint and method match the installed desktop client. Only task IDs,
status flags and timestamps survive parsing; credentials and chat text are
never persisted. This reader never starts, resumes or changes a cloud task.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
import json
import math
from pathlib import Path
import sqlite3
import threading
import time
import urllib.error
import urllib.request

ENDPOINT='https://codex-cloud-backend.chatgpt.com/grpc'
METHOD='/oaiproto.codex_app_server_backend.ThreadService/ListThreads'
ACTIVE_STALE_SECONDS=24*60*60


class CloudReadError(Exception):pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None


def credentials(home):
    try:
        auth=json.loads((Path(home)/'auth.json').read_text(encoding='utf-8-sig'))
        tokens=auth.get('tokens') or {}
        token=tokens.get('access_token')
        if not isinstance(token,str) or not token:raise CloudReadError('auth_missing')
        payload=token.split('.')[1]
        claims=json.loads(base64.urlsafe_b64decode(payload+'='*(-len(payload)%4)))
        if claims.get('exp',0)<=time.time()+10:raise CloudReadError('auth_expired')
        account=tokens.get('account_id') or ''
        identity=(account,claims.get('sub',''))
        return token,account,identity
    except CloudReadError:raise
    except (OSError,ValueError,KeyError,IndexError,TypeError):raise CloudReadError('auth_missing') from None


class CloudRPC:
    def __init__(self,home):
        self.home=Path(home)
        self.opener=urllib.request.build_opener(NoRedirect())

    def fetch(self):
        token,account,identity=credentials(self.home)
        headers={'Authorization':'Bearer '+token,'Content-Type':'application/json',
                 'Accept':'application/json','X-OpenAI-Product-Sku':'codex',
                 'User-Agent':'ROGStatusLight/1.7'}
        if account:headers['ChatGPT-Account-Id']=account
        rows=[];cursor=None;visited=set()
        for _ in range(20):
            arguments={'limit':100,'archived':False}
            if cursor:arguments['cursor']=cursor
            request=urllib.request.Request(ENDPOINT,
                data=json.dumps({'method':METHOD,'request':arguments}).encode('utf-8'),
                headers=headers,method='POST')
            try:
                with self.opener.open(request,timeout=6) as response:
                    raw=response.read(8*1024*1024+1)
                if len(raw)>8*1024*1024:raise CloudReadError('invalid_response')
                value=json.loads(raw)
                if not isinstance(value,dict) or 'data' not in value:raise CloudReadError('invalid_response')
                page=value.get('data',{})
                page=page.get('values',[]) if isinstance(page,dict) else page
                if not isinstance(page,list):raise CloudReadError('invalid_response')
                rows.extend(parse_tasks(page))
                cursor=value.get('nextCursor')
                if not cursor:return identity,rows
                if not isinstance(cursor,str) or cursor in visited:raise CloudReadError('invalid_response')
                visited.add(cursor)
            except urllib.error.HTTPError as error:
                raise CloudReadError({401:'auth_expired',403:'forbidden',429:'rate_limited'}.get(error.code,'network')) from None
            except (urllib.error.URLError,TimeoutError,OSError):raise CloudReadError('network') from None
            except (ValueError,AttributeError,TypeError):raise CloudReadError('invalid_response') from None
        raise CloudReadError('invalid_response')


def normalize(value):
    return ''.join(c for c in str(value).lower() if c.isalnum())


def is_scheduled_source(value):
    name=normalize(value)
    if name.startswith('threadsource'):name=name[len('threadsource'):]
    return name in ('heartbeat','cron','automation','scheduled','scheduledtask','scheduledautomation')


def is_scheduled_task(row):
    if not isinstance(row,dict):return False
    if is_scheduled_source(row.get('threadSource')):return True
    if any(row.get(k) for k in ('automationId','automation_id','scheduledTaskId','scheduled_task_id')):return True
    if any(is_scheduled_source(row.get(k)) for k in ('trigger','runTrigger','kind')):return True
    source=row.get('source',{})
    if isinstance(source,str):return is_scheduled_source(source)
    if isinstance(source,dict):
        if any(is_scheduled_source(k) for k in source):return True
        if any(is_scheduled_source(source.get(k)) for k in ('type','kind','trigger','runTrigger')):return True
        custom=source.get('customSessionSource',{})
        if isinstance(custom,dict) and is_scheduled_source(custom.get('custom')):return True
    return False


def scheduled_thread_ids(home):
    path=Path(home)/'sqlite'/'codex.db'
    if not path.exists():return set()
    try:
        connection=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=.15)
        try:return {r[0] for r in connection.execute('SELECT thread_id FROM automation_runs') if r[0]}
        finally:connection.close()
    except (sqlite3.Error,OSError):return set()


def scheduled_message(item):
    if not isinstance(item,dict) or item.get('type')!='userMessage':return False
    if is_scheduled_task(item):return True
    content=item.get('content',[])
    if isinstance(content,str):texts=[content]
    elif isinstance(content,list):texts=[v.get('text','') for v in content if isinstance(v,dict)]
    else:texts=[]
    return any(isinstance(text,str) and text.lstrip().startswith(('<heartbeat>','<scheduled_task>')) for text in texts)


def timestamp(value):
    try:
        result=float(value)
        if not math.isfinite(result) or result<=0:return 0
        return result/1000 if result>=1e12 else result
    except (ValueError,TypeError):return 0


def parse_tasks(rows):
    result=[]
    for row in rows:
        if not isinstance(row,dict):continue
        identifier=row.get('id')
        if not isinstance(identifier,str) or not identifier or len(identifier)>200:continue
        source_name=normalize(row.get('threadSource'))
        if source_name.removeprefix('threadsource') in ('subagent','aeonchild') or is_scheduled_task(row):continue
        source=row.get('source',{})
        if isinstance(source,dict) and any('subagent' in normalize(k) for k in source):continue
        status=row.get('status',{})
        if not isinstance(status,dict):continue
        kind=status.get('type')
        if not kind:kind=next((k for k in ('active','idle','notLoaded','systemError') if k in status),None)
        if kind not in ('active','idle','notLoaded','systemError'):continue
        active=status.get('active',{}) if 'active' in status else status
        flags=active.get('activeFlags',{}) if isinstance(active,dict) else {}
        flags=flags.get('values',[]) if isinstance(flags,dict) else flags
        if not isinstance(flags,list):flags=[]
        waiting=any((type(flag) is int and flag in (1,2)) or normalize(flag).endswith(
            ('waitingonapproval','waitingonuserinput','waitingforapproval','waitingforuserinput')) for flag in flags)
        updated=timestamp(row.get('updatedAt'))
        activity=max(updated,timestamp(row.get('lastActivityAt')))
        result.append({'id':identifier,'active':kind=='active','pending':kind=='active' and waiting,
                       'error':kind=='systemError','updated':updated,'activity':activity})
    return result


@dataclass(frozen=True)
class CloudSnapshot:
    active_ids:frozenset=frozenset()
    pending:bool=False
    error:bool=False
    completed_at:float=0
    available:bool=False
    hint:str='云端状态正在连接'


class CloudMonitor:
    def __init__(self,home,rpc=None,clock=time.time):
        self.home=Path(home)
        self.rpc=rpc or CloudRPC(home)
        self.clock=clock
        self.lock=threading.Lock()
        self.tasks={};self.identity=None;self.last_success=0
        self.completed_at=0;self.last_error='';self.ack_errors=set()

    def poll(self):
        identity,rows=self.rpc.fetch()
        ignored=scheduled_thread_ids(self.home)
        now=self.clock();current={row['id']:row for row in rows if row['id'] not in ignored}
        with self.lock:
            if identity!=self.identity:
                self.tasks={};self.completed_at=0;self.ack_errors.clear();self.identity=identity
            for identifier,old in self.tasks.items():
                new=current.get(identifier)
                if (old['active'] and now-old.get('activity',old['updated'])<=ACTIVE_STALE_SECONDS
                        and new is not None and not new['active'] and new['updated']>=old['updated']):
                    self.completed_at=now
            self.tasks=current;self.last_success=now;self.last_error=''
            self.ack_errors.intersection_update((k,v['updated']) for k,v in current.items() if v['error'])

    def acknowledge(self):
        with self.lock:
            self.completed_at=0
            self.ack_errors={(k,v['updated']) for k,v in self.tasks.items() if v['error']}

    def snapshot(self):
        with self.lock:
            now=self.clock();fresh=self.last_success>0 and now-self.last_success<=30
            hints={'auth_missing':'云端检测需要已登录的 Codex 账号',
                   'auth_expired':'云端登录状态待刷新', 'forbidden':'云端状态读取暂不可用',
                   'rate_limited':'云端状态读取稍后重试','network':'云端连接暂时中断',
                   'invalid_response':'云端状态格式待重试'}
            if not fresh:return CloudSnapshot(hint=hints.get(self.last_error,'云端状态正在连接'))
            active=frozenset(k for k,v in self.tasks.items() if v['active']
                             and 0<v.get('activity',v['updated'])<=now+300
                             and now-v.get('activity',v['updated'])<=ACTIVE_STALE_SECONDS)
            pending=any(v['pending'] for k,v in self.tasks.items() if k in active)
            error=any(v['error'] and (k,v['updated']) not in self.ack_errors for k,v in self.tasks.items())
            return CloudSnapshot(active,pending,error,self.completed_at,not bool(self.last_error),
                                 hints.get(self.last_error,'云端检测已连接'))

    def run(self,stop):
        failures=0
        while not stop.is_set():
            try:self.poll();failures=0
            except Exception as error:
                failures+=1
                code=str(error) if isinstance(error,CloudReadError) else 'network'
                with self.lock:self.last_error=code
            stop.wait(min(30,4*2**min(failures,3)))
