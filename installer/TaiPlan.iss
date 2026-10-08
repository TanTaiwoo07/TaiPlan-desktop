; ============================================================================
; Todo App — Inno Setup 6 安装脚本
;
; 编译：build_installer.py 调用 ISCC.exe（不要手改下面的版本号，见 version.iss）
;
; 关键约定
;   * AppId 是一个**固定 GUID**：生成一次后永不改变，
;     否则 Windows 会把每个版本当成不同软件（升级会变成并存两套）。
;   * 默认按用户安装（{localappdata}\Programs\TaiPlan），不需要管理员权限。
;   * 只安装程序文件；**绝不**包含或触碰用户数据
;     （当前 %LOCALAPPDATA%\TaiPlan；legacy %LOCALAPPDATA%\TodoApp 同样永久保留）。
;   * 安装版不依赖 cmd / PowerShell / bat / vbs 启动。
; ============================================================================

#include "version.iss"

#define MyAppName "TaiPlan"
#define MyAppExeName "TaiPlan.exe"
; Publisher 取自 app_metadata.PUBLISHER（不虚构公司名）
#define MyAppPublisher "TaiWoo_Chen"
; 固定 AppId（由 build_installer.py 校验其不变；勿改）
#define MyAppId "{{8E2F4C1B-7A3D-4E62-9B5F-2C7A1D0E4F83}"
; [Setup] 里 AppId 用 "{{GUID}" 这种转义写法；
; 但 [Code] 段里的字符串**不会**被折叠，{#MyAppId} 会原样插入双左花括号，
; 导致注册表卸载键变成 {{GUID}_is1 → InstalledVersion() 永远读不到已安装版本
; → 降级检测静默失效（实测确认过）。所以 [Code] 用下面这个单花括号常量。
#define MyAppIdCode "{8E2F4C1B-7A3D-4E62-9B5F-2C7A1D0E4F83}"
; 开机启动项名称必须与应用内 StartupManager 完全一致（同一个位置、同一个参数）
#define StartupShortcutName "TaiPlan"
; 旧身份名称仅用于「精确清理我们当年创建的旧快捷方式/启动项」
#define LegacyAppName "Todo App"

[Setup]
AppId={#MyAppId}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
VersionInfoVersion={#MyAppVersion}
VersionInfoProductName={#MyAppName}
VersionInfoDescription={#MyAppName} Setup
VersionInfoProductVersion={#MyAppVersion}

; 默认当前用户安装（无需 UAC）
; W4：升级安装时必须落到新目录，不能沿用上一次的 Programs\TodoApp
; （Inno 默认 UsePreviousAppDir=yes 会复用旧安装目录，因此显式关闭）
UsePreviousAppDir=no
DefaultDirName={localappdata}\Programs\TaiPlan
DefaultGroupName={#MyAppName}
PrivilegesRequired=lowest
DisableProgramGroupPage=yes

; 只支持 64 位兼容的 Windows 10 / 11
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0

; 压缩传输体积（运行时 EXE 不变；不使用 UPX）
Compression=lzma2/max
SolidCompression=yes

WizardStyle=modern
OutputDir=..\release
OutputBaseFilename=TaiPlan-Setup-{#MyAppVersion}
SetupIconFile=..\assets\app_icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}
AllowNoIcons=yes

; 关掉 Inno 自带的 RestartManager 强关：我们自己用 --shutdown 优雅退出，
; 避免出现"程序还在跑但文件被换掉"的混合版本。
CloseApplications=no
RestartApplications=no

[Languages]
; 英文为默认与 fallback（Inno Setup 6.7.3 官方发行包**不含**中文语言包，
; 实测其 Languages 目录为空，因此这里不启用中文——否则编译会直接失败）。
; 若自备 ChineseSimplified.isl（放到编译器 Languages 目录），可启用下面一行：
Name: "english"; MessagesFile: "compiler:Default.isl"
; Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: checkedonce
Name: "startupicon"; Description: "随 Windows 启动 {#MyAppName}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; 只安装 onedir 产物；不安装源码、VBS、BAT、任何用户数据
Source: "..\dist\TaiPlan\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\TaiPlan\_internal\*"; DestDir: "{app}\_internal"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\docs\RELEASE_NOTES.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\docs\THIRD_PARTY_NOTICES.txt"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{userprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\{#MyAppExeName}"
Name: "{userdesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\{#MyAppExeName}"; Tasks: desktopicon
; 与应用内 StartupManager 完全同名的启动项（同一路径、同一参数）
Name: "{userstartup}\{#StartupShortcutName}"; Filename: "{app}\{#MyAppExeName}"; Parameters: "--autostart"; WorkingDir: "{app}"; IconFilename: "{app}\{#MyAppExeName}"; Tasks: startupicon

[InstallDelete]
; 升级时先清掉旧的 _internal，避免新旧 DLL/pyd 混在一起
Type: filesandordirs; Name: "{app}\_internal"

[Run]
; 完成页勾选即启动（直接跑 exe，不经 cmd / PowerShell / bat / vbs）
Filename: "{app}\{#MyAppExeName}"; Description: "启动 {#MyAppName}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
; 卸载前先让运行中的实例优雅退出（停 worker / tray / pywebview / Streamlit child / 释放锁）
Filename: "{app}\{#MyAppExeName}"; Parameters: "--shutdown"; Flags: runhidden waituntilterminated; RunOnceId: "StopTaiPlanBeforeUninstall"

[InstallDelete]
; W4 升级清理：只删除我们自己当年创建的固定名字的旧快捷方式/启动项，
; 只做精确路径匹配，绝不做通配或模糊删除；旧安装目录与 legacy 数据目录都不动。
Type: files; Name: "{userdesktop}\{#LegacyAppName}.lnk"
Type: files; Name: "{userprograms}\{#LegacyAppName}.lnk"
Type: files; Name: "{userstartup}\{#LegacyAppName}.lnk"

[InstallDelete]
; W4 升级清理：只删除我们自己当年创建的固定名字的旧快捷方式/启动项，
; 只做精确路径匹配，绝不做通配或模糊删除；旧安装目录与 legacy 数据目录都不动。
Type: files; Name: "{userdesktop}\{#LegacyAppName}.lnk"
Type: files; Name: "{userprograms}\{#LegacyAppName}.lnk"
Type: files; Name: "{userstartup}\{#LegacyAppName}.lnk"

[UninstallDelete]
; 只删除安装器/应用创建的启动快捷方式。
; ★ 绝对不要在这里写 {localappdata}\TodoApp —— 那是用户数据，必须永久保留。
Type: files; Name: "{userstartup}\{#StartupShortcutName}.lnk"
Type: files; Name: "{app}\install.json"

[Code]
const
  // 注册表卸载键必须是 单左花括号 + GUID + 右花括号 + _is1（键里只能有一个左花括号）。
  // UninstallKeyOk() 会在安装/卸载时把校验结果写进日志：
  // 实测中它读出 0，直接暴露了"降级检测失效"这个 bug（当时键里是双左花括号）。
  // （注意：本段不能用花括号注释，否则注释会被第一个右花括号提前截断。）
  // 注意：这里必须用 {#MyAppIdCode}（单花括号），不能用 {#MyAppId}（双花括号）。
  UninstallKey = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{#MyAppIdCode}_is1';

function UninstallKeyOk(): Boolean;
begin
  Result := (Pos('{{', UninstallKey) = 0) and (Pos('{', UninstallKey) > 0)
            and (Pos('}', UninstallKey) > 0);
end;

{ ---- 版本比较：a > b 返回 1，相等 0，小于 -1 ---- }
function CompareVer(a, b: String): Integer;
var
  ia, ib, i, na, nb: Integer;
  sa, sb: String;
begin
  Result := 0;
  for i := 1 to 4 do
  begin
    sa := '';
    sb := '';
    na := 0;
    nb := 0;
    { 取第 i 段 }
    while (na < 3) and (a <> '') and (a[1] <> '.') do
    begin
      sa := sa + a[1];
      Delete(a, 1, 1);
      na := na + 1;
    end;
    if (a <> '') and (a[1] = '.') then
      Delete(a, 1, 1);
    while (nb < 3) and (b <> '') and (b[1] <> '.') do
    begin
      sb := sb + b[1];
      Delete(b, 1, 1);
      nb := nb + 1;
    end;
    if (b <> '') and (b[1] = '.') then
      Delete(b, 1, 1);
    if StrToIntDef(sa, 0) > StrToIntDef(sb, 0) then
    begin
      Result := 1;
      Exit;
    end;
    if StrToIntDef(sa, 0) < StrToIntDef(sb, 0) then
    begin
      Result := -1;
      Exit;
    end;
  end;
end;

function InstalledVersion(): String;
begin
  Result := '';
  if not RegQueryStringValue(HKCU, UninstallKey, 'DisplayVersion', Result) then
    Result := '';
end;

{ ---- 让正在运行的实例优雅退出；返回 True 表示可以继续安装 ---- }
function StopRunningTodo(): Boolean;
var
  ExePath: String;
  ResultCode: Integer;
begin
  Result := True;
  ExePath := ExpandConstant('{app}\{#MyAppExeName}');
  if not FileExists(ExePath) then
    Exit;

  { --shutdown 内部走单实例 IPC：实例退出返回 0，超时返回 1 }
  if Exec(ExePath, '--shutdown', ExpandConstant('{app}'), SW_HIDE,
          ewWaitUntilTerminated, ResultCode) then
  begin
    if ResultCode <> 0 then
    begin
      Log('StopRunningTodo: --shutdown 超时，实例可能仍在运行。');
      Result := False;
    end;
  end
  else
  begin
    { ★ RC 修复：Exec 本身失败（无法启动 exe）时，原来 Result 会保持 True，
      于是安装器会继续覆盖正在运行的文件。这里必须判为"未确认退出"。 }
    Log('StopRunningTodo: 无法执行 --shutdown（Exec 失败），判为未确认退出。');
    Result := False;
  end;
end;

procedure LogVersionState(installed: String);
begin
  { 这两行是降级检测的实测依据：日志里能直接看到键名与读到的版本 }
  Log('卸载注册表键 = ' + UninstallKey);
  Log('UninstallKey 合法（单个左花括号）= ' + IntToStr(Ord(UninstallKeyOk())));
  if installed = '' then
    Log('已安装版本 = （无，视为全新安装）')
  else
    Log('已安装版本 = ' + installed + '，本次 Setup 版本 = ' + '{#MyAppVersion}');
end;

function InitializeSetup(): Boolean;
var
  installed: String;
  answer: Integer;
begin
  Result := True;
  installed := InstalledVersion();
  LogVersionState(installed);

  if (installed <> '') and (CompareVer(installed, '{#MyAppVersion}') > 0) then
  begin
    Log('检测到降级：已安装 ' + installed + ' > 本次 ' + '{#MyAppVersion}');
    if WizardSilent() then
    begin
      { 静默模式问不了用户：不允许悄悄降级，直接中止 }
      Log('静默模式检测到降级 → 中止安装（不会悄悄降级）。');
      Result := False;
      Exit;
    end;
    { 已安装更新的版本：明确提示，由用户决定是否继续 }
    answer := MsgBox('本机已安装更新的版本 ' + installed + '。' + #13#10 +
                     '继续安装 ' + '{#MyAppVersion}' + ' 会降级（程序文件会被替换，用户数据不受影响）。' + #13#10 + #13#10 +
                     '要继续吗？', mbConfirmation, MB_YESNO);
    Result := (answer = IDYES);
  end;
end;

procedure WriteInstallMarker();
var
  lines: TArrayOfString;
begin
  { 只写安装版标记：version / install_type / installed_at（文档第 31 条），
    绝不写入用户数据库。 }
  SetArrayLength(lines, 5);
  lines[0] := '{';
  lines[1] := '  "version": "' + '{#MyAppVersion}' + '",';
  lines[2] := '  "install_type": "inno",';
  lines[3] := '  "installed_at": "' + GetDateTimeString('yyyy-mm-dd hh:nn:ss', '-', ':') + '"';
  lines[4] := '}';
  SaveStringsToUTF8File(ExpandConstant('{app}\install.json'), lines, False);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  attempts: Integer;
begin
  Result := '';
  attempts := 0;
  while not StopRunningTodo() do
  begin
    attempts := attempts + 1;
    Log(Format('安装前 shutdown 失败（第 %d 次）。', [attempts]));
    { ★ 静默安装下 MsgBox 会被抑制、默认回 Retry → 会死循环，必须直接中止 }
    if WizardSilent() then
    begin
      Result := '安装已取消：无法确认 Todo App 已退出（静默模式不做强制结束）。';
      Exit;
    end;
    if MsgBox('Todo App 当前仍在运行。' + #13#10 +
              '请退出 Todo App 后继续安装。', mbError, MB_RETRYCANCEL) = IDCANCEL then
    begin
      Result := '安装已取消：Todo App 仍在运行。';
      Exit;
    end;
  end;
end;

{ ---- 卸载前优雅退出（★ 不能只依赖 [UninstallRun]）----
  InitializeUninstall 在删除任何文件/注册表项之前执行，
  返回 False 会**中止卸载**，因此"shutdown 失败还要继续删文件"在结构上不可能发生。
  只调用应用自己的 --shutdown（单实例 IPC），绝不 taskkill /F。 }
function InitializeUninstall(): Boolean;
var
  attempts: Integer;
begin
  Result := True;
  attempts := 0;
  while not StopRunningTodo() do
  begin
    attempts := attempts + 1;
    Log(Format('卸载前 shutdown 失败（第 %d 次）：Todo App 可能仍在运行。', [attempts]));
    if UninstallSilent() then
    begin
      { 静默卸载无从询问用户：中止，绝不删除正在运行的文件的目录 }
      Log('静默卸载中止：无法确认 Todo App 已退出。');
      Result := False;
      Exit;
    end;
    if MsgBox('Todo App 当前仍在运行。' + #13#10 +
              '请退出 Todo App 后继续卸载。' + #13#10 + #13#10 +
              '重试 = 再尝试让它退出；取消 = 中止卸载（不会删除任何文件）',
              mbError, MB_RETRYCANCEL) = IDCANCEL then
    begin
      Log('用户取消卸载：Todo App 仍在运行。');
      Result := False;
      Exit;
    end;
  end;
end;

{ ---- WebView2 Runtime 检测（仅提示，不阻断安装） ---- }
function WebView2Installed(): Boolean;
var
  ver: String;
begin
  { Evergreen Runtime 的 EdgeUpdate 客户端键（HKLM 32/64 位视图） }
  Result :=
    RegQueryStringValue(HKLM, 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', ver)
    or RegQueryStringValue(HKLM, 'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', ver)
    or RegQueryStringValue(HKCU, 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', ver);
  { 兜底：看安装目录里有没有 msedgewebview2.exe }
  if not Result then
    Result := FileExists(ExpandConstant('{commonpf32}\Microsoft\EdgeWebView\Application\msedgewebview2.exe'))
      or FileExists(ExpandConstant('{commonpf}\Microsoft\EdgeWebView\Application\msedgewebview2.exe'));
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if CurPageID = wpSelectTasks then
  begin
    if not WebView2Installed() then
    begin
      if MsgBox('未检测到 Microsoft Edge WebView2 Runtime。' + #13#10 + #13#10 +
                'Todo App 的桌面窗口依赖它，缺少时无法打开主界面。' + #13#10 +
                '请到 Microsoft 官方页面安装后再启动：' + #13#10 +
                'https://developer.microsoft.com/microsoft-edge/webview2/' + #13#10 + #13#10 +
                '现在继续安装吗？', mbConfirmation, MB_YESNO) = IDNO then
        Result := False;
    end;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    WriteInstallMarker();
end;
