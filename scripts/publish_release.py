"""Publish build assets as a draft first, then expose the complete release."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser();parser.add_argument('version');args=parser.parse_args()
    tag='v'+args.version
    command=['gh','release','view',tag,'--json','isDraft,targetCommitish']
    existing=subprocess.run(command,cwd=ROOT,capture_output=True,text=True)
    if existing.returncode and 'release not found' not in existing.stderr.lower():
        raise RuntimeError(existing.stderr)
    if existing.returncode:
        notes=ROOT/'build/release-notes.md'
        notes.write_text(f'ROG 状态灯 {tag}\n\n'
            '下载 `ROGStatusLight-*-Windows-x64.zip`，完整解压后运行“安装.cmd”。\n\n'
            '支持 ROG 魔导士 RX Low Profile 的 USB、Omni 2.4GHz 和蓝牙状态灯联动。\n'
            '软件更新自动完成检查、Windows 打包和发布。校验值见 SHA256SUMS.txt。\n',encoding='utf-8')
        subprocess.run(['gh','release','create',tag,'--draft','--target',os.environ['GITHUB_SHA'],
                        '--title',f'ROG 状态灯 {tag}','--notes-file',str(notes)],cwd=ROOT,check=True)
    else:
        release=json.loads(existing.stdout)
        if not release['isDraft']:
            print(f'{tag} already published; nothing to replace.');return
    assets=[ROOT/'release'/f'ROGStatusLight-{tag}-Windows-x64.zip',ROOT/'release/SHA256SUMS.txt']
    subprocess.run(['gh','release','upload',tag,*map(str,assets),'--clobber'],cwd=ROOT,check=True)
    subprocess.run(['gh','release','edit',tag,'--draft=false','--latest'],cwd=ROOT,check=True)


if __name__=='__main__':main()
