# PyInstaller 打包准备清单

本阶段（第 14 阶段）**不执行 PyInstaller**。这份清单记录下一阶段打包时必须处理的点，
以及本阶段已经为之铺好的路。

## 一、目录语义（已就绪）

| 类别 | 源码模式 | frozen 模式 | 谁负责 |
|---|---|---|---|
| 只读资源 | `<项目根>/assets` | `sys._MEIPASS/assets` | `app_paths.get_resource_dir()` / `resource_path()` |
| 可写数据 | `%LOCALAPPDATA%\TodoApp` | 同左（不变） | `app_paths.get_user_data_dir()` |

打包后**绝对不能**要求安装目录可写（`Program Files` 不可写）。
本阶段已经把数据、配置、日志、备份、状态全部搬到 `%LOCALAPPDATA%\TodoApp`，
并有测试模拟「程序目录只读」。

## 二、需要收集的资源

- `assets/app_icon.ico` —— 窗口图标 / 快捷方式 / 通知
- `assets/app_icon.png` —— 托盘图标 / 通知 IconUri
- `tools/toast.ps1` —— PowerShell WinRT 通知脚本（运行时被调用，必须随包）
- `streamlit` 的静态前端资源（`streamlit/static/**`）
- `streamlit_calendar` 的前端 bundle（`streamlit_calendar/frontend/**`）
- `pywebview` 的 JS 注入文件（`webview/js/*.js`，见 `webview` 包）
- `pystray` / `Pillow` 的二进制依赖（一般由 PyInstaller hook 处理）

PyInstaller 参数示意（下一阶段再用）：

```
--add-data "assets;assets"
--add-data "tools;tools"
--collect-all streamlit
--collect-all streamlit_calendar
--collect-data webview
--collect-all pystray
--icon assets/app_icon.ico
--name TodoApp
```

## 三、入口点

打包后不再需要 `.venv` / `wscript.exe` / VBS 三层包装：

- `TodoApp.exe` 直接作为入口，内部调用 `launcher.py` 的逻辑或直接进 `desktop_runtime.py`
- `launcher.py` 里对 `.venv\Scripts\pythonw.exe` 的依赖需要改为：frozen 时用
  `sys.executable`，源码模式才找 venv（`launcher.pythonw_path()` 已按此顺序回落）
- `start_todo.bat` / `start_todo_silent.vbs` 在 EXE 版本中不再需要
- `create_shortcut.py` 的目标改为 `TodoApp.exe`（`IconLocation` 用 `assets/app_icon.ico`）

## 四、Windows 通知身份（不要改）

- App User Model ID 固定为 `TodoApp.Desktop`（`app_metadata.APP_ID`）
- 注册入口：`windows_app_registration.register_app_id()`
- 打包后**必须继续用同一个 AUMID**，否则：
  - 通知会显示成 `Python` 或完全不出现在通知中心
  - 用户已有的通知权限设置失效
- 同理，AI API Key 的 keyring service 固定为 `TodoApp-AI`，
  凭据 id 只由 `api_type | base_url | model_id` 决定，与安装路径无关
  （`ai_settings.make_credential_id()`；有测试守着这一条）

## 五、隐藏导入 / 动态导入

项目里存在**函数内延迟导入**，PyInstaller 静态分析可能漏掉，需要显式 `--hidden-import`：

- `keyring.backends.Windows`
- `webview.platforms.winforms`
- `pystray._win32`
- `winotify`
- `PIL._tkinter_finder`（Pillow 常见坑）
- `streamlit_calendar`
- `winreg` / `ctypes`（标准库，一般无事）

## 六、Streamlit 子进程

当前 `desktop_runtime` 用 `sys.executable -m streamlit run app.py` 启动 Streamlit。
frozen 模式下 `sys.executable` 是 `TodoApp.exe`，不能再当 Python 用，需要：

1. 打包一个独立的 `TodoAppWeb.exe`（只跑 streamlit），或
2. 在同一个 EXE 里用 `--streamlit` 子命令自调用（推荐，注意 PyInstaller 的
   `multiprocessing.freeze_support()` 风格处理）

另外本阶段已实现：

- **动态端口**：不再固定 8501，在 8501~8510 内选空闲端口
- 选定端口写入 `state/runtime_port.json`，托盘 / Worker 都读同一个值
- 健康检查用 `/_stcore/health`

## 七、数据与日志

- 用户数据：`%LOCALAPPDATA%\TodoApp\{todo.db, config, logs, backups, state}`
- 日志用 `logging_config.setup_logging(component)`，RotatingFileHandler
- 首次启动 `first_run.run_first_run()`：建目录 → 迁移旧数据 → 写默认配置 →
  注册 AUMID → 写 `state/first_run.json`，全程不联网
- 数据库迁移前自动备份到 `backups/`（保留最近 10 个）

## 八、打包后必须回归

1. 全新机器（无 `%LOCALAPPDATA%\TodoApp`）首次启动能建库并正常用
2. 从 `C:\` 等任意工作目录启动都正常
3. 安装到 `C:\Program Files\...` 后仍能创建任务 / 存主题 / 写日志
4. 通知身份仍是 `TodoApp.Desktop`
5. 升级老用户：`todo.db` 自动复制到新目录，旧任务全部还在
6. 卸载不删用户数据

---

# 第 15 阶段：PyInstaller onedir 发行版（已实施）

## 构建方式

```powershell
cd <PROJECT_ROOT>
.venv\Scripts\python.exe build.py            # Release：dist\TodoApp\TodoApp.exe（无控制台）
.venv\Scripts\python.exe build.py --debug    # Debug  ：dist\TodoApp-Debug\TodoApp-Debug.exe（带控制台）
```

依赖：`pyinstaller 6.22.3`（含 `pyinstaller-hooks-contrib 2026.8`），装在项目 venv 里。
构建产物 206 MB / 2105 个文件（Streamlit 前端静态资源 21.4 MB + pandas/pyarrow/numpy 为主）。

## 入口与三种模式（main.py）

| 命令行 | 行为 |
|---|---|
| `TodoApp.exe` | 桌面运行时（pywebview + Tray + Worker + 单实例） |
| `TodoApp.exe --streamlit-child --port N` | 只跑 Streamlit server |
| `TodoApp.exe --smoke-test` | 打包自检，不开 UI，成功返回 0 |

`TODO_APP_STREAMLIT_CHILD=1` 作为子进程标记；`desktop_runtime.main()` 见到它就拒绝启动，
避免子进程又起一套桌面运行时。

## Frozen 下绝不能 `-m streamlit`

`sys.executable` 在 frozen 下是 `TodoApp.exe`，`TodoApp.exe -m streamlit ...` 无意义。
所以 `streamlit_runner.streamlit_command()` 分两支：

- 源码：`[python, "-m", "streamlit", "run", app.py, --server.headless true, ...]`
- frozen：`[TodoApp.exe, "--streamlit-child", "--port", N]`

## 子模式实际怎么起 Streamlit（1.64.0 实测）

照 `streamlit/web/cli.py::_main_run` 的做法，不走命令行：

```python
from streamlit import config as st_config
st_config._main_script_path = os.path.abspath(script)   # 必须在 load_config_options 之前
from streamlit.web import bootstrap
bootstrap.load_config_options(flag_options=opts)
bootstrap.run(script, False, [], opts)
```

`flag_options` 用配置项名作键：

```python
{
    "global.developmentMode": False,   # ← 必须，见下
    "server.headless": True,
    "server.port": int(port),
    "browser.gatherUsageStats": False,
}
```

### 踩坑：developmentMode 会被误判成 True

`streamlit/config.py`：

```python
def _global_development_mode() -> bool:
    return (not env_util.is_pex()
            and "site-packages" not in __file__      # ← 打包后 __file__ 在 _internal 下
            and "dist-packages" not in __file__
            and "__pypackages__" not in __file__)
```

打包后 `streamlit/__file__` 不含 `site-packages` → 判成开发模式；
而 `config._check_conflicts()` 里 developmentMode 与 `server.port` 互斥，直接抛：

```
RuntimeError: server.port does not work when global.developmentMode is true.
```

表现是「主窗口起不来、child 退出码非 0、logs/streamlit.log 里有 traceback、
runtime.log 里 `streamlit start failed: timeout`」。
**必须显式把 `global.developmentMode` 置 False。**

## 资源打包要点

- `app.py` 必须作为**真实文件**放进 bundle 根（Streamlit 当脚本执行它），
  与 `app_paths.resource_path()` 语义一致（onedir 下 = `_internal/`）。
- `assets/`、`tools/` 整目录进 datas。`tools/toast.ps1` 是 PowerShell 通知的后端，
  漏了通知会静默失效（`PowerShellToastNotifier` 会报 "toast.ps1 missing"）。
- `collect_data_files("streamlit")` + `collect_data_files("streamlit_calendar")`
  —— 后者带上 `frontend/build/**`（index.html + main.*.js ~850KB + css），
  漏掉就是日历白屏 / Component Error。
- `copy_metadata`：streamlit / streamlit_calendar / pywebview / pystray / pillow /
  keyring / winotify。
- 官方 hook 已覆盖 webview、pystray、keyring、PIL、clr；
  **没有** streamlit 与 winotify 的 hook，需要自己收集。
- 显式 hiddenimports：`keyring.backends.Windows`、`win32ctypes.core`、
  `webview.platforms.winforms`、`pystray._win32`、`winotify`、
  以及**全部自家模块**（app.py 由 Streamlit 执行，静态分析推不到它的依赖）。

## 安全

- `packaging_tools.scan_sensitive_files()` 在构建第 8 步扫描 dist：
  `todo.db*` / `.env` / 真实 config / `logs|backups|state` 目录 / 密钥形态内容，
  并用**真实凭据原文**逐字节比对（凭据不会被打印）。
- 第三方包目录（Streamlit 自带文档、streamlit_calendar 的 `.env`）按
  `BUNDLED_PACKAGE_PREFIXES` 白名单降级，只做强检查，避免误报。
- `build.py` 删除任何路径前都过 `_assert_inside_project()`，用户数据目录永不进入删除范围。
