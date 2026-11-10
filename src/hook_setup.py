"""Merge the app's own hooks and trust their exact definitions through Codex config RPC."""
from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import threading
import time
from runtime_paths import DATA


def main(app_root):
    codex_home = Path(os.environ.get('CODEX_HOME', str(Path.home()/'.codex')))
    data = DATA
    backups = data/'backups'/time.strftime('%Y%m%d-%H%M%S')
    backups.mkdir(parents=True,exist_ok=True)
    path = codex_home/'hooks.json'
    config = codex_home/'config.toml'
    original = json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else {'hooks':{}}
    for source in (path, config):
        if source.exists():
            shutil.copy2(source, backups/source.name)
    command = '"' + str(Path(app_root)/'ROGStatusLight.exe') + '" --hook'
    hooks = original.setdefault('hooks',{})
    definitions = {
        'UserPromptSubmit': None,
        'PermissionRequest': None,
        'PreToolUse': '.*request_user_input.*',
        'PostToolUse': None,
        'PostModelRequest': None,
        'Stop': None,
        'Interrupt': None,
        'SessionEnd': None,
    }
    for event, matcher in definitions.items():
        existing = hooks.setdefault(event,[])
        existing[:] = [entry for entry in existing if not any(
            'ROGStatusLight.exe' in handler.get('command','')
            for handler in entry.get('hooks',[]))]
        handler = {'type':'command','command':command,'timeout':3,'async':True,
                   'statusMessage':'ROG 状态灯'}
        group = {'hooks':[handler]}
        if matcher:
            group['matcher'] = matcher
        existing.append(group)
    temporary = path.with_suffix('.rog-tmp')
    temporary.write_text(json.dumps(original,ensure_ascii=False,indent=2),encoding='utf-8')
    temporary.replace(path)
    candidates = list((Path(os.environ['LOCALAPPDATA'])/'OpenAI'/'Codex'/'bin').glob('*/codex.exe'))
    if not candidates:
        print('HOOKS_WRITTEN; Codex executable not found; exact hook review remains pending')
        return 2
    codex = max(candidates,key=lambda p:p.stat().st_mtime)
    process = subprocess.Popen([str(codex),'app-server','--stdio'],stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,encoding='utf-8',
        creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    messages = queue.Queue()
    def receive():
        for line in process.stdout:
            try:
                messages.put(json.loads(line))
            except ValueError:
                pass
    threading.Thread(target=receive,daemon=True).start()
    counter = 0
    def rpc(method,params):
        nonlocal counter
        counter += 1
        identifier = counter
        process.stdin.write(json.dumps({'id':identifier,'method':method,'params':params})+'\n')
        process.stdin.flush()
        deadline = time.monotonic()+20
        while time.monotonic()<deadline:
            message = messages.get(timeout=max(.1,deadline-time.monotonic()))
            if message.get('id')==identifier:
                if 'error' in message:
                    raise RuntimeError(f'{method}: {message["error"].get("message","request failed")}')
                return message.get('result',{})
        raise TimeoutError(method)
    try:
        rpc('initialize',{'clientInfo':{'name':'rog-status-light-setup','version':'1.0.0'},
                          'capabilities':{'experimentalApi':True}})
        process.stdin.write(json.dumps({'method':'initialized'})+'\n')
        process.stdin.flush()
        result = rpc('hooks/list',{'cwds':[str(app_root)]})
        owned = []
        for entry in result.get('data',[]):
            for hook in entry.get('hooks',[]):
                if 'ROGStatusLight.exe' in json.dumps(hook,ensure_ascii=False):
                    owned.append(hook)
        if len(owned) != len(definitions):
            raise RuntimeError(f'Expected {len(definitions)} app hooks; found {len(owned)}')
        edits = []
        for hook in owned:
            prefix = 'hooks.state.'+json.dumps(hook['key'])
            edits.extend([
                {'keyPath':prefix+'.enabled','value':True,'mergeStrategy':'upsert'},
                {'keyPath':prefix+'.trusted_hash','value':hook['currentHash'],'mergeStrategy':'upsert'}])
        rpc('config/batchWrite',{'edits':edits,'filePath':None,'expectedVersion':None,'reloadUserConfig':True})
        checked = rpc('hooks/list',{'cwds':[str(app_root)]})
        trusted = [hook for entry in checked.get('data',[]) for hook in entry.get('hooks',[])
                   if 'ROGStatusLight.exe' in json.dumps(hook,ensure_ascii=False)]
        if any(hook.get('trustStatus')!='trusted' for hook in trusted):
            raise RuntimeError('Hook trust verification did not pass')
        (data/'hooks-installed.json').write_text(json.dumps(
            {'keys':[h['key'] for h in trusted],'backup':str(backups),'installed_at':time.time()},
            ensure_ascii=False,indent=2),encoding='utf-8')
        print('HOOKS_INSTALLED_AND_VERIFIED',len(trusted))
        return 0
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()


if __name__=='__main__':
    raise SystemExit(main(Path(sys.argv[1])))
