# 生成分享包：只装 exe + 图标 + 说明，绝不带 cookies、历史、下载产物
# 用法：在 D:\MediaGrab 里右键「使用 PowerShell 运行」，或  powershell -File make_share_package.ps1
$ErrorActionPreference = 'Stop'
$proj = $PSScriptRoot

$exe = Join-Path $proj 'MediaGrab.exe'
if (-not (Test-Path $exe)) {
    throw "找不到 MediaGrab.exe。先打包：python -m PyInstaller MediaGrab.spec --noconfirm"
}

$share = Join-Path $proj 'share'
$stage = Join-Path $share 'MediaGrab'
if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
New-Item -ItemType Directory -Path $stage -Force | Out-Null

Copy-Item $exe                    $stage
Copy-Item (Join-Path $proj 'app.ico')   $stage
Copy-Item (Join-Path $proj '说明.txt')  $stage

$zip = Join-Path $share 'MediaGrab-分享版.zip'
if (Test-Path $zip) { Remove-Item $zip -Force }
Compress-Archive -Path (Join-Path $stage '*') -DestinationPath $zip -CompressionLevel Optimal

$mb = [math]::Round((Get-Item $zip).Length / 1MB, 1)
"分享包已生成：$zip  ($mb MB)"
"包内文件："
Get-ChildItem $stage | ForEach-Object { "  " + $_.Name }
""
"已明确排除：config.json / history.json / instagram_cookies.txt / downloads / downloads_images / logs"
