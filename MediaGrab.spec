# -*- mode: python ; coding: utf-8 -*-
import os
from PyInstaller.utils.hooks import collect_all

# 所有路径相对 spec 所在目录，整个文件夹搬走也能直接打包
HERE = SPECPATH

datas = []
binaries = []
_deno = os.path.join(HERE, 'deno.exe')
if os.path.isfile(_deno):
    binaries.append((_deno, '.'))

hiddenimports = []
for _pkg in ('yt_dlp', 'imageio_ffmpeg', 'gallery_dl'):
    _tmp = collect_all(_pkg)
    datas += _tmp[0]
    binaries += _tmp[1]
    hiddenimports += _tmp[2]


a = Analysis(
    [os.path.join(HERE, 'MediaGrab.py')],
    pathex=[HERE],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='MediaGrab',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=[os.path.join(HERE, 'app.ico')],
    version=os.path.join(HERE, 'version_info.txt'),
)
