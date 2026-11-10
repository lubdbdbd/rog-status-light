"""Choose the declared version, or advance the latest patch automatically."""
from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess

ROOT=Path(__file__).resolve().parents[1]


def choose_version(base,tags,at_head):
    parse=lambda value:tuple(map(int,value.lstrip('v').split('.')))
    valid=lambda value:bool(re.fullmatch(r'v\d+\.\d+\.\d+',value))
    current=[parse(t) for t in at_head if valid(t)]
    if current:return '.'.join(map(str,max(current)))
    previous=[parse(t) for t in tags if valid(t)]
    if not previous or parse(base)>max(previous):return base
    major,minor,patch=max(previous)
    return f'{major}.{minor}.{patch+1}'


def main():
    source=(ROOT/'src/app.py').read_text(encoding='utf-8')
    base=re.search(r"^VERSION = '([0-9]+\.[0-9]+\.[0-9]+)'",source,re.M)[1]
    tags=subprocess.check_output(['git','tag','--list'],cwd=ROOT,text=True).splitlines()
    at_head=subprocess.check_output(['git','tag','--points-at','HEAD'],cwd=ROOT,text=True).splitlines()
    version=choose_version(base,tags,at_head)
    if os.environ.get('GITHUB_EVENT_NAME')=='pull_request':version=base
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'],'a',encoding='utf-8') as output:output.write(f'version={version}\n')
    print(version)


if __name__=='__main__':main()
