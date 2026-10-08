# Todo App — Windows 安装包（Inno Setup 6）

本目录是第 17 阶段的安装器配置。构建产物在仓库根目录的 `release/`。

```
installer/
├── TodoApp.iss     Inno Setup 脚本（唯一安装定义）
├── version.iss     由 build_installer.py 生成（版本唯一来源是 version.py）
└── README.md       本文件
```

## 构建

```powershell
# 只构建安装包（dist/ 已是最新时）
.venv\Scripts\python.exe build_installer.py

# 从零一键：PyInstaller → 冒烟自检 → 敏感扫描 → 安装包 → SHA256
.venv\Scripts\python.exe build_release.py
```

输出：

```
release/
├── TodoApp-Setup-0.1.0.exe
└── TodoApp-Setup-0.1.0.exe.sha256
```

### 需要 Inno Setup 6（**只支持 6**）

Inno Setup 5 的 fallback 已在 RC 中移除：两代编译器行为不同，允许 5 会产出不可预期的
安装包。找到编译器后还会用 PE 版本资源校验其主版本号必须为 6。

`ISCC.exe` 的查找顺序：

1. `--iscc <路径>`（同样校验版本）
2. 环境变量 `INNO_SETUP_COMPILER`
3. `%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe`（winget 默认的按用户安装位置）
4. `C:\Program Files (x86)\Inno Setup 6\ISCC.exe`
5. `C:\Program Files\Inno Setup 6\ISCC.exe`

都不匹配时明确报「Inno Setup 6 未安装」或「不支持的编译器版本」，并给出下载地址：
<https://jrsoftware.org/isdl.php>

**单元测试不需要 Inno Setup**（`test_installer_config.py`、`test_release_build.py`
只做配置与脚本层校验，编译属于 integration test）。

## 关键约定

| 项 | 值 | 说明 |
| --- | --- | --- |
| AppId | `{{8E2F4C1B-7A3D-4E62-9B5F-2C7A1D0E4F83}}` | **固定不变**；改动会让 Windows 把新版本当另一个软件 |
| AUMID | `TodoApp.Desktop` | Windows 通知用，与上面的 AppId 是两回事 |
| 安装范围 | 当前用户 | `PrivilegesRequired=lowest`，不弹 UAC |
| 默认目录 | `{localappdata}\Programs\TodoApp` | 不在 `Program Files`，不需要管理员 |
| 版本来源 | `version.py` | `.iss` / 构建脚本 / About 都不硬编码 |
| 压缩 | `lzma2/max` + Solid | 只压传输体积，运行时 EXE 不变；**不用 UPX** |
| 架构 | `x64compatible` / Windows 10+ | 不声称支持 32 位 |
| 开机启动 | `{userstartup}\Todo App.lnk` + `--autostart` | 与应用内 StartupManager 完全同名同参数 |

## 安装包内容

只包含：

```
TodoApp.exe
_internal\...
RELEASE_NOTES.md
THIRD_PARTY_NOTICES.txt
```

**绝不包含**：`todo.db`、`config/`、`logs/`、`backups/`、`state/`、`.env`、任何 API Key、
开发脚本（`start_todo.bat`、`start_todo_silent.vbs`、`*.py`）。

`build_installer.py` 会在编译前扫描这些；`test_installer_config.py` 也会断言。

## 用户数据与升级/卸载

- 数据始终在 `%LOCALAPPDATA%\TodoApp`，安装器从不读写它。
- API Key 在 Windows 凭据管理器（service `TodoApp-AI`），安装器不接触。
- **升级**：同一个 AppId，直接运行新版 Setup 覆盖程序文件；旧实例由
  `TodoApp.exe --shutdown` 优雅退出后再覆盖，不需要先卸载。
- **卸载**：删除程序文件、桌面/开始菜单/开机启动快捷方式，
  **保留** `%LOCALAPPDATA%\TodoApp`（重装后任务与 Key 仍在）。
  `[UninstallDelete]` 里**永远不要**出现 `{localappdata}\TodoApp`。

## WebView2

安装到"附加任务"页时会检测 Microsoft Edge WebView2 Runtime（注册表 + 安装目录兜底）。
**只在缺失时**提示并给出官方下载地址，不阻断安装、也不内置庞大的 Evergreen Runtime。

## 已知限制

- 没有代码签名证书 → SmartScreen 可能提示"未知发布者"（预期行为，不绕过系统安全设置）。
- 不加壳、不用 UPX、不做混淆，保持标准 PyInstaller + 标准 Inno Setup。

## 人工验收清单（需要真机执行）

1. **Fresh Install**：无旧安装 → 运行 Setup → 默认目录正确、桌面/开始菜单快捷方式生成、
   能启动、**无控制台窗口**、Calendar / AI / 通知正常。
2. **Existing Data Install**：已有 `%LOCALAPPDATA%\TodoApp` 数据时安装，旧任务全部还在。
3. **Upgrade**：改动 `version.py` 后重建，运行中的旧版被 `--shutdown` 优雅关闭 →
   覆盖 → 新版启动 → 任务/设置/API Key 全部保留。
4. **Running-app Upgrade**：`CloseApplications=no`，靠 `--shutdown` 退出；
   10 秒内没退干净会提示「Todo App 当前仍在运行」，提供重试/取消，**不 taskkill**。
5. **Reinstall**：同版本重复运行 → 修复程序文件，数据不变。
6. **Uninstall**：安装目录、三种快捷方式被删除，`%LOCALAPPDATA%\TodoApp\todo.db` 仍在。
7. **Reinstall after uninstall**：重装后旧任务重新出现，AI Key 仍可用。
8. **Startup**：勾选"随 Windows 启动" → Startup 快捷方式存在 →
   `TodoApp.exe --autostart` 只起 worker + tray，**不自动打开主窗口**；
   应用内关闭后该快捷方式消失。
9. **中文/空格路径**：把 Setup 放到 `桌面\待办软件\` 之类路径下运行，仍正常。
