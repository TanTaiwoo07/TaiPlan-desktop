# UI 真机冒烟清单（13.2 起）

unittest 只能证明「代码没抛异常」，证明不了「看起来对不对」。
每次动 UI 之后，请照这份清单在 **真实 pywebview 窗口** 里过一遍。

启动方式（任选其一，效果一致）：

```
双击桌面快捷方式 Todo App.lnk
或
cd <PROJECT_ROOT>  &&  <PROJECT_ROOT>\.venv\Scripts\python.exe <PROJECT_ROOT>\desktop_runtime.py --debug
```

侧栏只有 **三种视觉状态**，只有一个持久状态 `sidebar_pinned`：

| 状态 | 宽度 | 主内容 |
|---|---|---|
| PINNED（`sidebar_pinned=True`） | 240px，占据布局 | 从侧栏右边开始 |
| RAIL（`sidebar_pinned=False`） | 72px，fixed 定位 | 固定留 72px |
| RAIL_HOVERED（鼠标移入） | 244px，覆盖在主内容上方 | **完全不动** |

---

## A. PINNED（默认）

- [ ] 侧栏宽约 240px
- [ ] 顶部是「✓ + Todo App」，一行，不换行
- [ ] 顶部右侧只有 **一个** 控制按钮，`‹` 形状（left_panel_close）
- [ ] **没有第二个箭头**（Streamlit 原生折叠箭头已隐藏）
- [ ] 导航项没有 radio 圆点
- [ ] 导航文字完整：今日 / 待办中转站 / 日历 / 后续安排 / 已完成 / 提醒中心 / 设置
- [ ] 当前页是 accent 浅底 + accent 文字
- [ ] 底部显示 `10月4日 · 周日`
- [ ] 侧栏与主内容之间只有一条细分割线，没有拖拽手柄

## B. RAIL（点一次那个控制）

- [ ] 侧栏明显变窄，约 72px
- [ ] 只显示图标；**没有任何中文文字、没有 Todo App 文字、没有底部日期**
- [ ] 没有文字残片、没有换行、没有横向滚动条
- [ ] 图标纵向居中排列
- [ ] 当前页面的图标有 accent 底色（不靠文字也能辨认）
- [ ] **不再显示第二个常驻展开箭头**
- [ ] 主内容整体左移到位，没有 244px 巨空白

## C. RAIL_HOVERED（鼠标移入侧栏）

- [ ] hover 后侧栏平滑展开到约 240px
- [ ] **主内容完全不动**（这是核心：harness overlay，不是推挤）
- [ ] 展开的侧栏覆盖在主内容之上，边缘有阴影
- [ ] 文字恢复显示（Logo / 导航 / 日期）
- [ ] 同一个控制槽位出现 pin 按钮（left_panel_open），点击可重新固定展开
- [ ] 鼠标移出 → 自动收回 72px
- [ ] 来回 hover **30 次**：不闪烁、不重载、不跑马灯

## D. 交互正确性

- [ ] 在日历点收起：仍然停在日历
- [ ] hover 展开 / 移出：仍然停在日历（不触发导航）
- [ ] hover 过程中**不出现 Streamlit running 状态 / 页面重载**
- [ ] 折叠状态下点击图标仍能正常切换页面

## E. 主题

- [ ] Light：pinned / rail / hover 三态都正常
- [ ] Dark：pinned / rail / hover 三态都正常（不是纯黑底 + 纯白字）
- [ ] 切换主题后侧栏视觉正常，无需重启

## F. 窗口尺寸

- [ ] 1200px 宽：pinned 240px 不挤爆主内容
- [ ] 900px 宽：自动退化为 rail（≤1050px），仍可 hover 展开
- [ ] 最小窗口 900×650 下仍可正常操作
- [ ] hover 展开时不会让主内容产生横向滚动条

## G. 无 hover 环境兜底

- [ ] 触屏 / 无鼠标环境（`@media (hover: none)`）：pin 按钮始终可见可点
- [ ] 能通过该按钮在 pinned / rail 之间切换，不会卡死

## H. 关键页面各切换一次

- [ ] Today / Inbox / Calendar / Upcoming / Completed / Reminder Center / Settings
- [ ] Settings 六个分区：外观 / AI / 提醒 / 日历 / 桌面 / 关于
- [ ] 打开一个 Dialog（新建 / 编辑），圆角与按钮位置正常

---

## 已知的「不是 bug」

- rail 状态下文字仍在 DOM 里，只是被 CSS 隐藏（opacity/max-width），这是 hover 能瞬时展开、且不触发 rerun 的前提。
- 任务卡里的圆圈是 checkbox 的刻意样式，不是 radio 圆点。
- 设置 → 外观 里的「主题 / 界面密度」是真正的 radio，圆点应当存在。
- ≤1050px 宽时即使 pinned 也会退化成 rail，属于预期。

## 自动化能守住的部分

`python -m unittest test_ui_sidebar`：

- 界面无 `:material/xxx:` 裸文本、源码无错误写法
- 侧栏无 radio，导航是按钮
- **只有一个** sidebar control；原生 collapse / expand / resize 控件均已隐藏
- pinned = 240px；rail = 72px + `position: fixed` + `z-index: 1000` + 主内容 `margin-left: 72px`
- hover = 244px + 阴影；transition `.18s ease`；无 `animation`
- 文字只在 `:not(:hover)` 时隐藏
- `@media (hover: none)` 与 `@media (max-width: 1050px)` 兜底存在
- 只有一个持久状态 `sidebar_pinned`；`nav_collapsed` 可迁移；没有多余 hover 状态
- 源码里没有 hover 回调（保证 hover 不 rerun）
- 20 次切换稳定；7 页面 × 2 状态、6 设置分区 × 2 状态全部无异常
- 不依赖 `streamlit-rail-nav`（第三方库缺失也能跑）

---

## 追加：侧栏 rail 的「Logo 与导航错位」修复（2026-10-04）

### 现象
rail（收起）状态下，Logo 的 ✓ 图标看起来只剩右半边、像一个 "D" 字，
且与下方导航图标不在同一条中线上。

### 根因（已在真实 Chromium 中实测确认）
1. Logo 被放在 `st.columns([3.2, 1.0])` 的第 0 列里。rail 下侧栏内容区
   只有约 32px（72px 减去两侧各 ~20px 内边距），分成两列后该列只剩 **12px**；
   而 `.app-logo` 内容宽 30px（22px 图标 + 8px gap），加上 `overflow: hidden`
   → 图标被**从左侧裁掉约 7px**，剩下的右半部分就是那个 "D" 形。
2. 同时该列只有 12px，`justify-content: center` 也无法把 22px 图标排到
   侧栏中线（中线 36，实测图标中心只有 24）。
3. 附带发现一条更隐蔽的问题：`ui/theme.py` 里同时存在 f-string 模板
   （要把 CSS 花括号写成 `{{ }}`）和普通字符串模板（必须写单花括号）。
   有个补丁把 `{{ }}` 写进了普通字符串，浏览器收到 `{{ ... }}` 后
   **整条规则的声明块被解析器丢弃**——规则看着存在、实际完全没生效
   （`.app-logo` 居中、两条 `pointer-events` 共 3 条规则静默失效）。

### 修复
- `ui/layout.py`：Logo 不再放进 `st.columns`，直接占满整行；唯一的 pin 控制
  改为绝对定位在侧栏右上角同一个槽位（收起态 hover 才显示）。
- `ui/theme.py`：`.app-logo { width: 100%; padding-right: 32px }`；
  rail 未 hover 时 `justify-content: center; gap: 0`（gap 归零，否则隐藏的
  文字仍占 8px 导致偏左 5px）；不可见时 `pointer-events: none`；
  修掉 3 处双花括号泄漏；删掉死代码 `.app-logo-collapsed`、13.1 遗留的
  无 `!important` 的 `min-width: 244px`，并给 ≤1050px 回退补 `box-shadow: none`。
- 新增 `test_ui_css.py`（5 项）：断言生成的 CSS 不含 `{{`/`}}`、没有空声明块、
  花括号平衡、rail 关键规则带声明。

### 实测验收（无头 Edge + CDP，窗口 1300×880）

| 状态 | 侧栏 | position | main.x | Logo 图标 x | 导航按钮 x | 图标中心 vs 导航中心 |
|---|---|---|---|---|---|---|
| rail | 72px | fixed | 72 | 25..47 | 20..52 | 36 = 36 ✔ 对齐 |
| rail + hover | 244px | fixed | **72（未移动）** | 20..42 | 20..220 | — |
| pinned | 240px | relative | 240 | 20..42 | 20..220 | — |

其它确认项：`stSidebarHeader` 高 0、原生折叠/展开按钮与 resize 手柄均为 0 尺寸。

### 注意（踩坑）
- **改了 `ui/*.py` 必须重启应用**。Streamlit 的 rerun 只重跑 `app.py`，
  已导入的 `ui.*` 模块留在 `sys.modules` 里，不重启就看不到改动。
- 验证侧栏几何不要用窗口截图（本机 `SetCursorPos` 触发不了 CSS `:hover`，
  且抓屏常抓到别的窗口）。用无头 Edge + CDP 查询
  `getBoundingClientRect()` / `getComputedStyle()`，并用
  `Input.dispatchMouseEvent` 模拟 hover，结果可重复。

### 追加修复：收起后无法恢复（2026-10-04）

**现象**：rail 收起后点不到 pin 按钮，无法再展开。

**根因（实测）**：Streamlit 顶部 header 是一条
`position:absolute`、**全宽 1270px、高 60px、`pointer-events:auto`、
`z-index:999990`** 的层。13.2 的 rail 规则把侧栏写成 `z-index:1000`，
于是侧栏顶部这条 0~60px 的带子被 header 完全覆盖：
- 该带子上的 hover 命中 header 而不是侧栏 → 不触发展开；
- 绝对定位在 `top:6px`（y 6~46）的 pin 按钮点不到 → 收起后无法恢复。

pinned 态之所以正常，是因为那里没有覆盖 z-index，用的是 Streamlit 自己的
`999991`（高于 header 的 999990），所以能收起、不能恢复——现象完全对得上。

**修复**：rail 与 ≤1050px 回退的 `z-index: 1000` → `999999`
（高于 header 的 999990，低于 Streamlit 弹窗/提示层的 9999999）。

**实测验收（真实浏览器 CDP 点击，非模拟）**：

| 步骤 | 侧栏 w | position | z-index | 结果 |
|---|---|---|---|---|
| 初始 | 72 | fixed | 999999 | rail |
| 点 pin（211,26） | 240 | relative | 999991 | 展开 ✔ |
| 再点 pin（207,26） | 72 | fixed | 999999 | 收起 ✔ |

修复前同样的点击毫无反应。另确认 `elementFromPoint(36, y)` 在 y=10/26/40/60/100
全部命中侧栏自身内容（修复前命中 header）。

**回归测试**：`test_ui_css.py` 新增 2 项，断言 rail 的 z-index > 999990、
且 `:hover` 展开规则含真实声明。

---

## 追加：重复任务「仅修改这一次」（2026-10-05）

**问题**：重复任务点「编辑」永远只能改整个系列（弹窗里那句"改的是整个系列"），
用户没法只改某一天的那一次。

**改法**（不动数据库结构，只用已有的 `task_occurrence_overrides`）：

1. `edit_session` 新增 `ACTIVE_OCCURRENCE_KEY`；`prepare()` 在 `mode="event"` 时把
   这一次已有的 override 叠加到日期/时间/时长控件上（弹窗里看到的是**这一次**的实际值，
   而不是系列值）；新增 `apply_occurrence_override()` 与 `active_occurrence_key()`。
2. `app.py::_edit_item()`：重复任务的某一次改传 `mode="event"`。
3. `app.py::edit_dialog()`：
   - 从某一次打开时多一个单选 **「修改范围：仅修改这一次 / 修改整个系列」**（默认仅这一次）；
   - 选「仅修改这一次」时：标题/描述/URL/优先级**禁用**，重复规则**不显示**，
     只留日期/时间/时长可改，并给出说明文案（含这一次的日期时间）；
   - 保存走 `services.set_occurrence_override(...)`，**完全不碰系列行**；
     取消勾选某项 = 不覆盖（回退到系列值）；三项都空时 services 会删除该覆盖。
   - 选「修改整个系列」时 = 原来的行为。
4. `calendar_view.py::_start_edit_series()`：带 `occurrence_key` 时也改传
   `mode="event"`（日历里点某一次同样能只改这一次）；顺带修掉日历弹窗里
   「编辑系列」按钮把 `occurrence_key` 丢掉的问题。

**语义边界**：标题/描述/URL/优先级没有按次字段（`task_occurrence_overrides` 只有
`override_date / override_time / override_duration_minutes`），所以这三类按次编辑
需要加列，本次未做。日期/时间/时长按次改已完整可用。

**测试**：`test_edit_session.py` 新增 `OccurrenceEditTest`（6 项）——默认进「这一次」、
保存只写 override 不动系列、切到系列范围仍改系列、已有 override 会预填、
取消勾选回退系列值、任务卡菜单同样走 event 模式；原 `test_03`/`test_09` 按新行为更新。
全量 **525 项 OK**。
