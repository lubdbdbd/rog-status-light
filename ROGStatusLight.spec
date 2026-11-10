from pathlib import Path
import os
from PyInstaller.utils.hooks import collect_all

root = Path(SPECPATH)
source = Path(os.environ.get('ROG_BUILD_SOURCE', root / 'src'))
datas, binaries, hiddenimports = collect_all('winrt')
hiddenimports += ['pystray._win32', 'hook_setup', 'event_hook']
a = Analysis([str(source / 'app.py')], pathex=[str(source)],
             binaries=binaries, datas=datas, hiddenimports=hiddenimports,
             hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=[],
             noarchive=False, optimize=0)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='ROGStatusLight',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
          console=False, disable_windowed_traceback=False,
          manifest=str(root / 'packaging' / 'rog-app.manifest'))
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='ROGStatusLight')
