# TaiPlan 0.1.2

**Tasks into time.**

本地优先的任务与时间规划桌面应用（Windows）。把一件事变成一天里的一段时间：先用中转站收纳想法，再用时间块日历把它们安排进日程。

本次更新修复了一个 0.1.1 用户报告的显示问题，并带来一点新东西。

---

## 下载

| 文件 | 大小 | 说明 |
| --- | --- | --- |
| `TaiPlan-Setup-0.1.2.exe` | 57.1 MB | Windows 安装包（64 位） |
| `TaiPlan-Setup-0.1.2.exe.sha256` | — | 下载完整性校验值 |

两个文件都在本 Release 的 **Assets** 里。

### 校验下载（推荐）

安装前核对一下哈希，确认文件在传输过程中没有损坏或被替换。

**CMD：**

```
certutil -hashfile TaiPlan-Setup-0.1.2.exe SHA256
```

**PowerShell：**

```powershell
Get-FileHash TaiPlan-Setup-0.1.2.exe -Algorithm SHA256
```

输出应等于：

```
16e5ca5313fd59e06930ff814cf44019f31ad698edebb9b6a22a5f895e7054d5
```

---

## 本次更新（0.1.2）

- **修复：深色系统下切换浅色主题的残留。** 如果 Windows 是深色模式，之前在应用里切到「浅色」后，标题栏、部分按钮和日历区域仍是深色。现在应用会把主题选择传递给界面基座（包括日历），桌面窗口底色也会跟随；保存主题后如需完全切换，会出现「立即重启 TaiPlan」按钮，一键完成。
- **新增：键盘快捷键。** 在今日页按 `Ctrl+N`（Mac 上 `Cmd+N`）直接新建任务。
- 0.1.1 的修复与新功能（导入 .ics 日历、全新电脑首次启动修复、侧栏悬停提示残留修复、数据管理入口修复）全部包含在本版中。

---

## 安装

1. 双击 `TaiPlan-Setup-0.1.2.exe`。
2. 按**当前用户**安装，不需要管理员权限，默认目录 `%LOCALAPPDATA%\Programs\TaiPlan`。
3. 安装完成后会创建开始菜单快捷方式「TaiPlan」，安装结束即可直接启动。

安装后不再需要 Python、虚拟环境或任何命令行工具。

**升级**：直接运行新版本安装包覆盖即可，任务与设置全部保留。此前使用旧产品名的版本会自动迁移数据与已保存的 Key，旧目录完整保留作为回退。

**卸载**：只删除程序文件和快捷方式，**不会删除** `%LOCALAPPDATA%\TaiPlan` 里的任务数据；重新安装后任务与 API Key 仍然可用。

## 你的数据在哪

| 内容 | 位置 |
| --- | --- |
| 任务数据与设置 | `%LOCALAPPDATA%\TaiPlan` |
| AI API Key | Windows 凭据管理器（不写入文件） |

---

## 系统要求

- Windows 10 / 11（64 位）
- Microsoft Edge WebView2 运行时（Windows 10/11 通常已自带；缺失时安装程序会提示下载地址）

## 已知说明

- 本版本**没有**代码签名证书，Windows SmartScreen 可能提示「未知发布者」。选择「更多信息 → 仍要运行」即可，这属于预期行为。
- AI 解析质量取决于你自己配置的 provider 与模型。

## 关于 AI 与隐私

核心任务数据保存在本地，AI 功能可选。关闭 AI 时不会有任何任务内容离开本机；启用 AI 时，你提交用于解析的文本会发送给你自己配置的 AI 服务提供方。

---

## English summary

TaiPlan 0.1.2 — a local-first task and time-blocking desktop app for Windows.

**Fixed:** choosing Light in the app while Windows is in dark mode left the title bar, some
native widgets and the calendar dark. The theme choice is now propagated to the UI base
(calendar included) and the desktop window background; a "Restart TaiPlan now" button
completes the switch.

**Added:** `Ctrl+N` / `Cmd+N` opens the new-task dialog from the Today page.

Includes everything from 0.1.1 (.ics calendar import, fresh-machine first-launch fix,
sidebar tooltip fix, data-management entry fix).

Install: run `TaiPlan-Setup-0.1.2.exe` (per-user, no admin rights). Data lives in
`%LOCALAPPDATA%\TaiPlan`; AI keys stay in Windows Credential Manager. Requires Windows
10/11 64-bit and the Edge WebView2 runtime. Not code-signed — SmartScreen may warn
"unknown publisher"; choose "More info → Run anyway".

Verify your download with `certutil -hashfile TaiPlan-Setup-0.1.2.exe SHA256`; it must
match `16e5ca5313fd59e06930ff814cf44019f31ad698edebb9b6a22a5f895e7054d5`.

---

MIT License · © 2026 TaiWoo_Chen
