; MediaGrab 安装包脚本（Inno Setup 6）
; 编译：ISCC.exe installer.iss
; 产物：installer\MediaGrab-2.1.2-安装包.exe

#define AppName "MediaGrab"
#define AppVersion "2.1.2"
#define AppExe "MediaGrab.exe"

[Setup]
; AppId 决定"同一个程序"，升级时会自动覆盖旧版而不是装两份，别乱改
AppId={{8F3C2A64-5B7D-4E19-9A2C-6D1F0B7E4C31}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=MediaGrab
VersionInfoVersion=2.1.2.0
VersionInfoCompany=MediaGrab
VersionInfoDescription=MediaGrab 安装程序
VersionInfoProductName=MediaGrab
VersionInfoProductVersion=2.1.2.0
DefaultDirName={autopf}\MediaGrab
DefaultGroupName=MediaGrab
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\{#AppExe}
OutputDir=installer
OutputBaseFilename=MediaGrab-{#AppVersion}-安装包
SetupIconFile=app.ico
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
; 装到当前用户，不弹 UAC；{autopf} 会落到 %LOCALAPPDATA%\Programs
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesInstallIn64BitMode=x64compatible
InfoBeforeFile=说明.txt

[Languages]
Name: "chinesesimplified"; MessagesFile: "installer\lang\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务："

[Files]
Source: "MediaGrab.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "app.ico"; DestDir: "{app}"; Flags: ignoreversion
Source: "说明.txt"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\MediaGrab"; Filename: "{app}\{#AppExe}"; IconFilename: "{app}\app.ico"
Name: "{group}\使用说明"; Filename: "{app}\说明.txt"
Name: "{group}\卸载 MediaGrab"; Filename: "{uninstallexe}"
Name: "{autodesktop}\MediaGrab"; Filename: "{app}\{#AppExe}"; IconFilename: "{app}\app.ico"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "立即启动 MediaGrab"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; 只清日志和空目录；config.json / history.json / 下载产物属于用户数据，保留
Type: filesandordirs; Name: "{app}\logs"
