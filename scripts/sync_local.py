"""Publish only a completed, source-matching local keyboard bundle."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time
import zipfile

SOURCES=('app.py','background_access.py','bluetooth_backend.py','cloud_status.py','core.py','event_hook.py',
         'hook_setup.py','lamp_devices.py','lighting_lifecycle.py','native_signals.py','runtime_paths.py')
PACKAGING=('安装.cmd','打开控制面板.cmd','退出程序.cmd','卸载.cmd','Install.ps1','Start.ps1',
           'Stop.ps1','Uninstall.ps1','使用说明.txt','AppxManifest.xml','ROGStatusLight.cer',
           'ROGStatusLight.msix','config.json','bluetooth-lighting-restore.json','bluetooth-validation.json',
           'Assets/Square150.png','Assets/Square44.png','Assets/StoreLogo.png')


def guard_text(data,name):
    if Path(name).suffix not in ('.py','.ps1','.cmd','.txt','.json','.xml'):return
    text=data.decode('utf-8-sig')
    if re.search(r'gh[pousr]_[A-Za-z0-9]{20,}|-----BEGIN .*PRIVATE KEY-----|C:[\\/]+Users[\\/]+[^%{]',text):
        raise ValueError('Local paths or credentials must be removed before publishing: '+name)


def snapshot(workspace,now=None):
    workspace=Path(workspace);source=workspace/'outputs/ROG状态灯'
    bundle=workspace/'outputs/ROG状态灯-Windows.zip'
    if (time.time() if now is None else now)-bundle.stat().st_mtime<120:
        return None
    selected={}
    with zipfile.ZipFile(bundle) as archive:
        for name in (*SOURCES,*PACKAGING):
            data=(source/name).read_bytes()
            if archive.read('ROG状态灯/'+name)!=data:return None
            guard_text(data,name)
            if name=='bluetooth-validation.json':
                value=json.loads(data.decode('utf-8-sig'))
                value.pop('owner_feedback',None);value.pop('verified_at',None)
                data=(json.dumps(value,ensure_ascii=False,indent=2)+'\n').encode('utf-8')
            selected[('src/' if name in SOURCES else 'packaging/')+name]=data
    for path in sorted((workspace/'work').glob('test_*.py')):
        text=path.read_text(encoding='utf-8-sig')
        text=re.sub(r"/'outputs'\s*/\s*'ROG状态灯'", "/'src'",text)
        text=text.replace(" / 'outputs' / 'ROG状态灯'", " / 'src'")
        # The repository's CI uses installed dependencies; this optional local
        # dependency path is never required by the release build.
        text=text.replace("str(WORK/'deps')", "str(WORK.parents[1]/'work'/'deps')")
        data=text.encode('utf-8');guard_text(data,path.name)
        selected['tests/'+path.name]=data
    fingerprint=hashlib.sha256(b''.join(k.encode()+b'\0'+v for k,v in sorted(selected.items()))).hexdigest()
    return selected,fingerprint


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--config',required=True)
    parser.add_argument('--initialize',action='store_true');parser.add_argument('--dry-run',action='store_true')
    args=parser.parse_args();config_path=Path(args.config)
    config=json.loads(config_path.read_text(encoding='utf-8-sig'))
    root=Path(config['repository']).resolve();workspace=Path(config['workspace']).resolve()
    if root!=Path(__file__).resolve().parents[1]:raise ValueError('Repository path does not match this publisher')
    if not (root/'.git').is_dir():raise ValueError('An isolated repository is required')
    folder=config_path.parent;state_path=folder/'state.json';status_path=folder/'status.json'
    state=json.loads(state_path.read_text(encoding='utf-8')) if state_path.exists() else {}
    def save_status(status,**details):
        status_path.write_text(json.dumps({'status':status,'time':time.time(),**details},indent=2),encoding='utf-8')
    def run(*parts):
        return subprocess.check_output(list(parts),cwd=root,stderr=subprocess.STDOUT,
            text=True,encoding='utf-8',creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),timeout=180).strip()
    try:
        bundle=workspace/'outputs/ROG状态灯-Windows.zip'
        stat=bundle.stat();stamp=[stat.st_size,stat.st_mtime_ns]
        if not args.initialize and state.get('bundle_stamp')==stamp:
            # Retry a failed network push without adding another commit.
            if state.get('pending_push'):
                run(config['git'],'push','origin','HEAD:main')
                state['pending_push']=False
                state_path.write_text(json.dumps(state,indent=2),encoding='utf-8')
                save_status('published',commit=state['commit'])
            return
        result=snapshot(workspace)
        if result is None:save_status('waiting_for_complete_bundle');return
        files,fingerprint=result
        if args.dry_run:
            save_status('dry_run_ready',files=len(files),fingerprint=fingerprint);print('Ready; no commit or push performed.');return
        if not args.initialize and fingerprint!=state.get('fingerprint'):
            if run(config['git'],'status','--porcelain'):
                save_status('waiting_for_clean_repository');return
            run(config['git'],'fetch','origin','main','--tags')
            if run(config['git'],'rev-parse','HEAD')!=run(config['git'],'rev-parse','origin/main'):
                save_status('remote_changed_update_checkout_first');return
            checked=subprocess.run([config['python'],'-m','unittest','discover','-s','work','-p','test_*.py','-q'],
                cwd=workspace,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf-8',
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),timeout=120)
            if checked.returncode:
                save_status('tests_failed',output=checked.stdout[-3000:]);return
            for name,data in files.items():
                target=root/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
            run(config['git'],'add','--',*sorted(files))
            if run(config['git'],'diff','--cached','--name-only'):
                version=re.search(rb"^VERSION = '([^']+)'",files['src/app.py'],re.M)[1].decode()
                run(config['git'],'commit','-m',f'Update ROG status lighting {version}')
                state['pending_push']=True
        state.update(bundle_stamp=stamp,fingerprint=fingerprint,commit=run(config['git'],'rev-parse','HEAD'))
        state_path.write_text(json.dumps(state,indent=2),encoding='utf-8')
        if state.get('pending_push'):
            run(config['git'],'push','origin','HEAD:main')
            state['pending_push']=False
            state_path.write_text(json.dumps(state,indent=2),encoding='utf-8')
        save_status('initialized' if args.initialize else 'published',commit=state['commit'])
    except Exception as error:
        save_status('error',error=str(error)[:2000]);raise


if __name__=='__main__':main()
