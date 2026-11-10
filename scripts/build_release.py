"""Build a Windows release with the existing signed sparse identity package."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def source_version():
    return re.search(r"^VERSION = '([0-9]+\.[0-9]+\.[0-9]+)'", (ROOT/'src/app.py').read_text(encoding='utf-8'), re.M)[1]


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--version',default=source_version())
    args=parser.parse_args()
    if not re.fullmatch(r'\d+\.\d+\.\d+',args.version):raise ValueError('Expected a semantic version')
    generated=ROOT/'build/generated-src'
    generated.mkdir(parents=True,exist_ok=True)
    for path in (ROOT/'src').glob('*.py'):shutil.copy2(path,generated/path.name)
    app=generated/'app.py'
    app.write_text(re.sub(r"^VERSION = '[^']+'",f"VERSION = '{args.version}'",app.read_text(encoding='utf-8'),count=1,flags=re.M),encoding='utf-8')
    env=os.environ|{'ROG_BUILD_SOURCE':str(generated)}
    subprocess.run([sys.executable,'-m','PyInstaller','--noconfirm','--clean',
                    '--distpath',str(ROOT/'dist'),'--workpath',str(ROOT/'build/pyinstaller'),
                    str(ROOT/'ROGStatusLight.spec')],cwd=ROOT,env=env,check=True)
    output=ROOT/'release';output.mkdir(exist_ok=True)
    staging=ROOT/'build/package'/args.version/'ROG状态灯'
    staging.mkdir(parents=True,exist_ok=True)
    for path in (ROOT/'packaging').iterdir():
        if path.name=='rog-app.manifest':continue
        if path.is_dir():shutil.copytree(path,staging/path.name,dirs_exist_ok=True)
        else:shutil.copy2(path,staging/path.name)
    shutil.copytree(ROOT/'dist/ROGStatusLight',staging/'Program',dirs_exist_ok=True)
    for name in ('Assets','Public'):
        if (ROOT/'packaging'/name).exists():
            shutil.copytree(ROOT/'packaging'/name,staging/'Program'/name,dirs_exist_ok=True)
    for path in generated.glob('*.py'):shutil.copy2(path,staging/path.name)
    guide=staging/'使用说明.txt'
    guide.write_text(re.sub(r'^ROG 状态灯 \d+\.\d+\.\d+',f'ROG 状态灯 {args.version}',guide.read_text(encoding='utf-8-sig'),count=1),encoding='utf-8-sig')
    exe=staging/'Program/ROGStatusLight.exe'
    manifest={'version':args.version,'commit':os.environ.get('GITHUB_SHA','local'),
              'executable_sha256':hashlib.sha256(exe.read_bytes()).hexdigest(),
              'source_sha256':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(generated.glob('*.py'))}}
    (staging/'release.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
    archive=output/f'ROGStatusLight-v{args.version}-Windows-x64.zip'
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as zipped:
        for path in sorted(staging.rglob('*')):
            if path.is_file() and '__pycache__' not in path.parts:zipped.write(path,Path('ROG状态灯')/path.relative_to(staging))
    with zipfile.ZipFile(archive) as zipped:
        assert zipped.testzip() is None
        assert zipped.read('ROG状态灯/Program/ROGStatusLight.exe')==exe.read_bytes()
    checksum=hashlib.sha256(archive.read_bytes()).hexdigest()
    (output/'SHA256SUMS.txt').write_text(f'{checksum}  {archive.name}\n',encoding='utf-8')
    print(json.dumps({'archive':str(archive),'sha256':checksum,'version':args.version}))


if __name__=='__main__':main()
