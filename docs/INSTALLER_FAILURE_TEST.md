# INSTALLER_FAILURE_TEST.md

第 17 阶段 RC —— **17.1 安装器 shutdown 故障注入**实录。

原则（文档第十一条）：故障条件必须真实，**不对 MsgBox 做任何 mock**。本轮所有
"shutdown 失败"都由一个真实的假实例造成：它占住端口、能让程序判断"已有实例"，
但故意不响应 `SHUTDOWN`。

---

## 1. 假实例如何构造

新增 `tools/fake_runtime_lock.py`（源码开发目录工具，**不随安装包分发**）：

| 项 | 做法 |
| --- | --- |
| 占用端口 | 绑定单实例锁 `127.0.0.1:18721`（`listen(1)`）与 IPC `127.0.0.1:18722`（`listen(5)`） |
| 让程序以为"已有实例" | 18721 被占 → 新启动的 `TodoApp.exe` 判定已有实例 |
| 故意不响应 SHUTDOWN | IPC 循环 `recv` 到命令后**只记日志**：不退出、不显示窗口、不断开监听 |
| 持续时间 | `--seconds`（默认 300 秒），本轮实测用 180 秒 |
| 正常结束 | 出现 `--stop-file` 指定文件即优雅关闭端口并退出；演练驱动脚本用它收尾 |
| 用户数据 | **完全不接触**：脚本只做 socket 操作，从不打开 `%LOCALAPPDATA%\TodoApp` |

复现命令：

```powershell
.venv\Scripts\python.exe tools\fake_runtime_lock.py --seconds 180 `
    --log build_logs\fake_runtime.log --stop-file build_logs\fake.stop
```

---

## 2. `--shutdown` timeout 实际秒数

**实测 10.69 秒**（规定 timeout 为 10 秒，允许调度抖动）。

命令与观测：`dist\TodoApp\TodoApp.exe --shutdown`，假实例保持占用。

## 3. timeout exit code

**`exit = 1`**（非 0 ✔）。

同时验证的三条"不能"：

| 不允许 | 实测 |
| --- | --- |
| 返回 0 | exit = 1 ✔ |
| 启动新的 Todo 主窗口 | 全程采样 `IsWindowVisible`，未出现任何可见窗口；采样结束后 TodoApp 进程数 = 0 ✔ |
| 杀死假实例 | 假实例仍存活、18722 仍被占用 ✔ |

假实例侧日志证明命令确实到达且被故意忽略：

```
[14:59:19] 收到命令 b'SHUTDOWN' —— 故意不响应（不退出、不显示窗口）
```

## 3b. 正常 shutdown 不受影响

真实实例启动后执行 `--shutdown`：exit **0**，主实例 / Streamlit child / worker / tray
全部优雅退出，18721、18722、8501 三个端口全部释放、无残留进程（前序阶段已多次实测，
本轮 A5 的"释放占用后"同样复现）。

---

## 4. Setup failure 时实际提示

**静默分支（已实测，真实失败条件）**：安装器 `PrepareToInstall` 拿到 `--shutdown`
的非 0 退出码后拒绝继续，日志原文：

```
2026-10-05 14:59:54.121   PrepareToInstall failed: 安装已取消：无法确认 Todo App 已退出（静默模式不做强制结束）。
```

安装器进程退出码 **7**（非 0 ✔），**没有继续覆盖任何文件**（见第 9 条）。

**交互分支的对话框文案（代码为准，待人工点一次确认）**：

```
Todo App 当前仍在运行。
请退出 Todo App 后继续安装。
                                     [重试] [取消]
```

> 说明：静默模式下 MsgBox 会被抑制、默认回退到"重试"，若沿用同一分支会**死循环**，
> 因此 RC 中已改为 `WizardSilent()` 时直接中止 —— 这也是上面日志出现"静默模式不做
> 强制结束"的原因。交互模式才弹出上述对话框，由用户决定重试或取消。

## 5. Retry 结果

**语义路径（已实测）**：停掉假实例释放端口后再次安装 → `exit = 0`，安装目录摘要发生变化
（说明文件确实被更新），程序正常启动。

```
[PASS] 重试（释放后）安装成功 :: exit=0
[PASS] 安装目录已更新
```

**GUI 上的【重试】按钮**：对话框是模态的、等待用户点击，无法可靠自动化，按文档
第十一条改为人工点击（见文末演练命令）。

## 6. Cancel 结果

**语义路径（已实测）**：假实例保持占用时安装 → `exit = 7`，安装目录**完全未被触碰**
（见第 9 条），即"完整中止"。

**GUI 上的【取消】按钮**：同上，人工点击（见文末演练命令）。

---

## 7. Uninstaller failure path

**已实测（真实失败条件）**：假实例占用时运行卸载程序：

```
2026-10-05 15:00:06.119   静默卸载中止：无法确认 Todo App 已退出。
```

卸载器 `exit = 1`（非 0 ✔），并且结构性保证不会走到删除动作：
`InitializeUninstall()` 在**删除任何文件/注册表项之前**执行，返回 `False` 即整体中止；
静默卸载无处询问用户 → 直接中止。全程**没有 taskkill**（测试会剥掉 Pascal 注释后
断言全文不含 `taskkill` / `TerminateProcess` / `WM_CLOSE` / `Stop-Process`）。

## 8. Uninstaller Cancel

**已实测（中止后状态）**：

| 检查 | 结果 |
| --- | --- |
| `TodoApp.exe` 仍存在 | ✔ |
| 安装目录完整 | 714 个文件，摘要 `2e030aa424abb041` 与中止前**完全一致** ✔ |
| Windows「应用」列表条目仍在 | ✔（注册表卸载项可查） |
| 半卸载状态 | **未出现** ✔ |

## 9. 是否发生部分覆盖

**没有。**

| 时点 | 文件数 | 安装目录内容摘要 |
| --- | --- | --- |
| 安装失败前 | 714 | `2e030aa424abb041` |
| 安装被中止后 | 714 | `2e030aa424abb041` |

`PrepareToInstall` 在 `[InstallDelete]`（清理旧 `_internal`）与 `[Files]` 之前执行，
中止即意味着一个字节都没动。

## 10. 是否发生部分卸载

**没有。** 见第 8 条：卸载被中止后安装目录文件数与摘要不变、`TodoApp.exe` 在、
注册表条目在、无半卸载残留。

## 11. 用户数据是否保持完整

**始终完整。** 全流程（含最终正常卸载）之后：

```
★ 用户数据完好 :: %LOCALAPPDATA%\TodoApp\todo.db  53248 bytes
```

卸载器 `[UninstallDelete]` 里只允许删除 `{userstartup}\Todo App.lnk` 与
`{app}\install.json`，`{localappdata}\TodoApp` 是构建期就会被拒绝的写法（有断言守）。

---

## 汇总

| # | 完成标准（文档第十二条） | 结果 |
| --- | --- | --- |
| 1 | 正常 shutdown：exit 0 | ✔ 已实测 |
| 2 | 假实例 timeout：非 0 | ✔ 已实测（exit=1，10.69 秒） |
| 3 | 安装 shutdown 失败：不能覆盖 | ✔ 已实测（714/714，摘要一致） |
| 4 | 安装 Cancel：完整中止 | ✔ 语义已实测；GUI 按钮待人工 |
| 5 | 安装 Retry：释放占用后正常继续 | ✔ 语义已实测；GUI 按钮待人工 |
| 6 | 卸载 shutdown 失败：不能删除 | ✔ 已实测（文件与注册表条目均在） |
| 7 | 卸载 Cancel：完整保留程序 | ✔ 已实测（无半卸载） |
| 8 | 卸载 Retry：释放占用后正常卸载 | ✔ 已实测（exit 0，目录与条目移除） |
| 9 | 用户数据始终不受影响 | ✔ 已实测（53,248 字节） |

自动部分复跑命令：

```powershell
.venv\Scripts\python.exe verify_installer_failure.py
```

最近一次：**27/27 通过**。

---

## 待人工确认（文档第十一条允许人工点击）

对话框是模态的、等待真人点击，无法可靠自动化。为此提供演练驱动
`tools/failure_gui_drill.py`：它负责摆好**真实故障条件**（起假实例）并打开**真实
向导/卸载程序**，你只需点按钮。四种演练：

```powershell
# 安装 - 取消（预期：Setup 中止，不覆盖任何文件）
.venv\Scripts\python.exe tools\failure_gui_drill.py install-cancel

# 安装 - 重试（脚本 60 秒后自动停掉假实例，此时点【重试】→ 安装继续）
.venv\Scripts\python.exe tools\failure_gui_drill.py install-retry

# 卸载 - 取消（预期：完整保留，无半卸载）
.venv\Scripts\python.exe tools\failure_gui_drill.py uninstall-cancel

# 卸载 - 重试（脚本 60 秒后停掉假实例，此时点【重试】→ 卸载继续，用户数据保留）
.venv\Scripts\python.exe tools\failure_gui_drill.py uninstall-retry
```

每次演练都会把向导日志写到 `build_logs/drill_<动作>.log` 并打印退出码、安装目录文件数、
`TodoApp.exe` 是否存在、`todo.db` 字节数 —— 跑完把输出贴回来即可归档。

> 注意：演练前请先退出正在运行的 Todo App（真实实例会占用同样的端口，导致假实例
> 起不来，脚本会直接报错提示）。
