# -*- mode: python ; coding: utf-8 -*-
import os
from PyInstaller.utils.hooks import collect_all

# 所有路径相对 spec 所在目录，整个文件夹搬走也能直接打包
HERE = SPECPATH

datas = []
binaries = []
_deno = os.path.join(HERE, 'deno.exe')
# deno 占了 93 MB，是包里最大的东西。Gitee 单个附件上限 100 MB，
# 完整包会超限；需要小体积时设 MG_SLIM=1 打不带 deno 的版本。
# 代价：YouTube 解析需要 JS 运行时，精简版要用户自己装 deno。
if os.path.isfile(_deno) and os.environ.get('MG_SLIM') != '1':
    binaries.append((_deno, '.'))

hiddenimports = []
# yt_dlp_ejs 是 yt-dlp 的「JS 挑战求解脚本」（2026 版起必需）。
# 不打包它，YouTube 的签名和 n 挑战解不出来 → 视频格式全被跳过，
# 表现为 "Requested format is not available"（2026-10-28 实测踩过）。
for _pkg in ('yt_dlp', 'yt_dlp_ejs', 'imageio_ffmpeg', 'gallery_dl'):
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
