# TaiPlan

**Tasks into time.**

本地优先的任务与时间规划桌面应用（Windows）。
设计并开发：**TaiWoo_Chen**

版本 0.1.1 · MIT License

[English README](README.md)

---

## TaiPlan 是什么

TaiPlan 把"一件事"变成"一天里的一段时间"。它用轻量的待办中转站收纳想法，再用真正的
时间块日历把它们安排进日程，所以任务不只是"记着要做"，而是"已经占好了位置"。

程序运行在原生桌面窗口里：没有浏览器地址栏，不需要一直开着某个本地网页，也不需要注册账号。

## 功能

### Today（今日）
只显示属于今天的内容：按时间排序的定时任务、全天事项，以及已经过期的任务。
这是可以长期开着的那一屏。

### Inbox（中转站）
存放还没定日期的任务。先记下来，之后再安排。

### Calendar 与 Time Blocking（时间块）
- 月 / 周 / 日视图，自带导航栏。
- 拖动任务到别的日期或时间格即可改期。
- 拖动任务底边可以改变持续时间。
- 单击空白时间格，或在时间上拖选一段范围，可以直接在日历上新建任务。
- 点击已有任务就地编辑。
- 当前时间指示线，以及可配置的时间网格（15 / 30 / 60 分钟）。
- 全天事项与定时任务可以互相拖动转换。
- 时间冲突会提示但允许保存，不会静默丢弃任何任务。

### Recurrence（重复）
- 每天 / 每周 / 每月 / 自定义间隔（含"仅工作日"）。
- 可以修改整个系列，也可以只改其中一次。
- 单次 occurrence 可独立移动、改时间、标记完成。
- 删除系列时可以自行选择保留或一并处理过去与未来的 occurrence。

### Reminders（提醒）
- 基于时间的任务提醒，程序运行期间持续检查。
- Windows 系统通知，可在应用内开关。
- 提醒中心，列出最近触发与即将到期的提醒。

### AI 解析（可选）
- 输入一句话（例如"明天下午三点开会 1 小时"），让 AI 解析成结构化任务
  （日期、时间、时长、优先级、重复）。
- 完全可选：关闭 AI 时所有功能照常可用。
- 由你自己配置 Provider、接口地址、模型与你自己的 API Key。

### 界面
- 中文（zh-CN）与英文（en-US），默认跟随系统语言。
- 浅色 / 深色外观，可折叠侧边栏，Fluent 风格。
- Settings 为一个连续滚动页，页面内自带目录导航。

### 本地优先的数据
- 任务数据保存在你自己用户目录下的本地数据库中。
- 无登录、无同步服务、无遥测。
- 支持手动与自动备份，以及安全的导入/恢复。

## 数据与隐私

核心任务数据保存在本地。AI 功能是可选的。
当启用 AI 时，相关输入可能会发送给你所配置的 AI 服务提供方。

更具体地说：

| 内容 | 位置 |
| --- | --- |
| 任务、提醒、设置 | `%LOCALAPPDATA%\TaiPlan` |
| AI API Key | Windows 凭据管理器（不写入任何文件） |

- **关闭** AI（默认）时，任何任务内容都不会离开这台电脑。
- **启用** AI 时，你提交用于解析的文本会发送给你配置的服务提供方，适用该方的条款；
  除此之外不会因为其他原因发送任务数据。
- API Key 保存在 Windows 凭据管理器中，服务名为 `TaiPlan-AI`；不写入日志、不写入配置文件、
  也不包含在备份里。

## Windows 安装

1. 运行 `TaiPlan-Setup-0.1.1.exe`。
2. 按**当前用户**安装，不需要管理员权限，默认目录：

   ```
   %LOCALAPPDATA%\Programs\TaiPlan
   ```

3. 开始菜单会创建名为 `TaiPlan` 的入口。
4. 从该入口启动即可，不需要安装 Python 或任何命令行工具。

系统要求：Windows 10 / 11（64 位），以及 Microsoft Edge WebView2 运行时
（Windows 10/11 通常已自带；缺失时安装程序会提示下载地址）。

升级：直接运行新版本安装包覆盖安装即可。程序文件会被替换，任务、设置与 API Key 全部保留。
如果你此前使用的是旧产品名下的版本，TaiPlan 会在首次启动时迁移数据与已保存的 Key，
同时完整保留旧数据目录作为回退，并清理旧快捷方式与旧启动项。

卸载：只删除程序文件与快捷方式，**不会删除** `%LOCALAPPDATA%\TaiPlan` 里的任务数据。

## 从源码运行（开发）

```bat
:: 1. 创建环境
py -3.14 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt

:: 2. 浏览器模式运行（最快）
.venv\Scripts\python.exe -m streamlit run app.py

:: 3. 原生桌面窗口（无控制台）
.venv\Scripts\python.exe desktop_runtime.py

:: 4. 原生桌面窗口 + 控制台日志
.venv\Scripts\python.exe desktop_runtime.py --debug

:: 5. 环境自检（不启动应用）
.venv\Scripts\python.exe launcher.py --check
```

测试：

```bat
.venv\Scripts\python.exe -m unittest discover -p "test_*.py"
```

构建可执行文件与安装包：

```bat
.venv\Scripts\python.exe build.py            :: onedir 构建 -> dist\TaiPlan
.venv\Scripts\python.exe build_installer.py  :: 安装包 -> release\TaiPlan-Setup-<version>.exe
.venv\Scripts\python.exe build_release.py    :: 完整发布流水线
```

开发约定、目录结构与打包说明见 [CONTRIBUTING.md](CONTRIBUTING.md)；第三方组件与许可证见
[THIRD_PARTY_NOTICES.txt](THIRD_PARTY_NOTICES.txt)。

## 目录结构

```
app.py                 Streamlit 界面入口（页面本体）
main.py                所有运行模式的唯一入口
desktop_runtime.py     原生窗口、托盘、单实例锁、IPC、看门狗
services.py            业务逻辑（UI 只调用这一层）
database.py            唯一写 SQL 的地方
ui/                    主题、布局、通用组件、图标
i18n/                  zh-CN / en-US 文案与日期本地化
calendar_view.py       日历页与时间块
recurrence.py          重复展开与 occurrence 处理
notification_service.py 提醒判定与投递
ai_client.py           可选的 AI 服务客户端
data_dir_migration.py  数据目录安全迁移
packaging_tools.py     版本资源与打包卫生检查
```

## 许可证

MIT License，见 [LICENSE](LICENSE)。

Copyright (c) 2026 TaiWoo_Chen
