# 一键重新打包：找解释器 -> 查版本号 -> pyinstaller -> 拷回根目录 -> 清中间产物
# 用法：
#   powershell -ExecutionPolicy Bypass -File rebuild.ps1
#   powershell -ExecutionPolicy Bypass -File rebuild.ps1 -All     # 顺便生成绿色包和安装包
#
# 两个坑记在这里：
# 1. PATH 上的 python 可能是别的、缺依赖的解释器（这台机器上 D:\py\python.exe 就是），
#    所以脚本自己探测一个同时装有 PyInstaller + yt_dlp + gallery_dl 的，路径记进 build_python.txt。
# 2. 外部命令的 stderr 会把 PowerShell 的 Stop 模式变成"终止错误"，
#    所以这里统一用 Continue，靠 $LASTEXITCODE 判断成败。

param([switch]$All)

$ErrorActionPreference = 'Continue'
$proj = $PSScriptRoot
Set-Location $proj

# ---------- 0. 程序还在跑就别打包 ----------
# 2026-10-21 踩过：两个 MediaGrab 进程占着根目录的 EXE，Copy-Item 静默失败，
# 结果新版 EXE 没拷回来，校验报"版本对不上"，看着像打包坏了。
$running = @(Get-Process MediaGrab -ErrorAction SilentlyContinue)
if ($running.Count -gt 0) {
    throw "还有 $($running.Count) 个 MediaGrab 进程在运行（PID: $(($running | ForEach-Object Id) -join ', ')）。先关掉程序再打包，否则新的 EXE 拷不回去。"
}

# ---------- 1. 找可用的 Python ----------
$candidates = @()
if (Test-Path 'build_python.txt') { $candidates += (Get-Content 'build_python.txt' -Raw).Trim() }
$onPath = (Get-Command python -ErrorAction SilentlyContinue).Source
if ($onPath) { $candidates += $onPath }
$candidates += 'D:\Python3.14.7\python\python.exe'

$python = $null
foreach ($c in $candidates) {
    if (-not $c -or -not (Test-Path $c)) { continue }
    & cmd /c "`"$c`" -c `"import PyInstaller, yt_dlp, gallery_dl`" >nul 2>nul"
    if ($LASTEXITCODE -eq 0) { $python = $c; break }
    "跳过（缺 PyInstaller / yt_dlp / gallery_dl）：$c"
}
if (-not $python) {
    throw "找不到同时装了 PyInstaller + yt_dlp + gallery_dl 的 Python。把正确的 python.exe 绝对路径写进 build_python.txt，再跑一次。"
}
Set-Content 'build_python.txt' $python -Encoding UTF8
"解释器：$python"
""

# ---------- 2. 版本号三处对齐 ----------
$py  = (Select-String -Path 'MediaGrab.py'     -Pattern 'APP_VERSION = "([\d.]+)"').Matches[0].Groups[1].Value
$vi  = (Select-String -Path 'version_info.txt' -Pattern "FileVersion', '([\d.]+)'").Matches[0].Groups[1].Value
$iss = (Select-String -Path 'installer.iss'    -Pattern 'AppVersion "([\d.]+)"').Matches[0].Groups[1].Value
$isv = (Select-String -Path 'installer.iss'    -Pattern 'VersionInfoProductVersion=([\d.]+)').Matches[0].Groups[1].Value

function Normalize([string]$v) {
    $parts = $v.Split('.')
    while ($parts.Count -lt 3) { $parts += '0' }
    ($parts[0..2]) -join '.'
}
$npy = Normalize $py; $nvi = Normalize $vi; $niss = Normalize $iss; $nisv = Normalize $isv

"版本号自检："
"  MediaGrab.py      APP_VERSION = $py"
"  version_info.txt  FileVersion = $vi"
"  installer.iss     AppVersion  = $iss"
"  installer.iss     VersionInfoProductVersion = $isv"
if ($npy -ne $nvi -or $npy -ne $niss -or $npy -ne $nisv) {
    throw "版本号对不上（$npy / $nvi / $niss / $nisv）。先改成一致再打包，否则发出去的几份程序版本会互相矛盾。"
}
"  一致：$npy"
""

# ---------- 3. 打包 ----------
"打包中……"
$log = & $python -m PyInstaller MediaGrab.spec --noconfirm --distpath dist --workpath build 2>&1
$log | Select-String -Pattern 'ERROR|Traceback|Build complete' | ForEach-Object { "  " + $_.Line }
if ($LASTEXITCODE -ne 0) { throw "PyInstaller 退出码 $LASTEXITCODE，打包失败" }

if (-not (Test-Path 'dist\MediaGrab.exe')) { throw "没找到 dist\MediaGrab.exe，打包失败了" }

Copy-Item 'dist\MediaGrab.exe' 'MediaGrab.exe' -Force
$exe = Get-Item 'MediaGrab.exe'
$ver = $exe.VersionInfo.FileVersion
"已拷回根目录：MediaGrab.exe  {0:N1} MB  版本 {1}" -f ($exe.Length / 1MB), $ver

if ($ver -ne $vi) {
    throw "拷回来的 EXE 版本是 $ver，和 version_info.txt 的 $vi 对不上——检查 spec 里的 version= 是否挂上了"
}

Remove-Item 'dist', 'build' -Recurse -Force -ErrorAction SilentlyContinue
"已清掉 dist 和 build"

# ---------- 4. 可选：出成品 ----------
if ($All) {
    ""
    "生成绿色版……"
    $out = & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $proj 'make_share_package.ps1') 2>&1
    $out | Where-Object { $_ -match '分享包已生成|MB' } | ForEach-Object { "  " + $_ }

    ""
    "生成安装包……"
    $iscc = Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe'
    if (-not (Test-Path $iscc)) { throw "找不到 ISCC.exe，Inno Setup 6 没装？" }
    $isetup = & $iscc (Join-Path $proj 'installer.iss') 2>&1
    $isetup | Where-Object { $_ -match 'Successful|Error' } | ForEach-Object { "  " + $_ }
    if ($LASTEXITCODE -ne 0) { throw "Inno Setup 编译失败，退出码 $LASTEXITCODE" }
}

""
"完成。"
