"""Quick Add 文本解析器（完全本地、确定性，不调用任何第三方 API）。

职责：把 Quick Mode 输入的自然语言文本解析成结构化字段
（title / date / time / priority / recurrence）。

不写数据库、不执行 SQL、不创建任务；创建仍走 services.create_task()。
"""

import re
from dataclasses import dataclass, field
from datetime import datetime, date as _date, timedelta
from typing import List, Optional


class QuickParseError(ValueError):
    """解析错误（冲突、非法时间、标题为空等）。"""


@dataclass
class ParsedQuickTask:
    title: str
    date: Optional[str] = None      # YYYY-MM-DD
    time: Optional[str] = None      # HH:MM
    priority: str = "normal"        # low / normal / urgent
    recognized_tokens: List[str] = field(default_factory=list)
    # 8B 新增：重复任务
    is_recurring: bool = False
    recurrence_frequency: Optional[str] = None
    recurrence_interval: int = 1
    recurrence_end_type: Optional[str] = None
    recurrence_end_date: Optional[str] = None
    # 12 阶段新增：时间块时长（分钟）
    duration_minutes: Optional[int] = None


# 相对日期词 → 天数偏移（含时段语义的复合词）
_RELATIVE_DAYS = {
    "今晚": 0,
    "今天": 0,
    "今日": 0,
    "明早": 1,
    "明晚": 1,
    "明天": 1,
    "明日": 1,
    "后天": 2,
}

# 时段词 → (是否 PM)
_PERIODS = {
    "早上": False,
    "上午": False,
    "中午": True,
    "下午": True,
    "晚上": True,
}

# 优先级标记
_PRIORITY_TOKENS = {
    "!紧急": "urgent",
    "!普通": "normal",
    "!低": "low",
}

_DATE_RE = re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})")
_CN_DATE_RE = re.compile(r"(\d{1,2})月(\d{1,2})[日号]")
_HHMM_RE = re.compile(r"(?<!\d)(\d{1,2}):(\d{2})(?!\d)")
_CN_TIME_RE = re.compile(r"(\d{1,2})点(半|(\d{1,2})分?)?")

# 星期几映射（中文数字 → Python weekday 0-6）
_WEEKDAY_MAP = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}

# 星期几：周X / 星期X（X 为 一~日/天）
_WEEKDAY_ONLY_RE = re.compile(r"(?:星期|周)([一二三四五六日天])")

# 每N天 / 每N周 / 每N个月 / 每N年 / 每N小时
_NUM_INTERVAL_RE = re.compile(r"每(\d+)(天|周|个月|年|小时)")

# 结束日期
_END_DATE_RE = re.compile(r"(?:到|截止)\s*(\d{4})-(\d{1,2})-(\d{1,2})")
_END_CN_DATE_RE = re.compile(r"(?:到|截止)\s*(\d{1,2})月(\d{1,2})[日号]")

# 12 阶段：时间区间「X点到Y点(半/N分)」
_TIME_RANGE_RE = re.compile(
    r"(\d{1,2})\s*点\s*(半|(\d{1,2})\s*分)?\s*(?:到|至|-|~|—)\s*"
    r"(\d{1,2})\s*点\s*(半|(\d{1,2})\s*分)?"
)

# 12 阶段：时长关键词
_DURATION_HOUR_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:个)?\s*(?:小时|钟头)")
_DURATION_MIN_RE = re.compile(r"(\d+)\s*(?:分钟)")
_DURATION_HALF_RE = re.compile(r"(?:半个?|一个半)\s*小时")

MIN_DURATION_MINUTES = 5
MAX_DURATION_MINUTES = 1440


def _clamp_duration(minutes):
    """把分钟数收敛到 5～1440。"""
    if minutes is None:
        return None
    minutes = int(round(minutes))
    if minutes < MIN_DURATION_MINUTES:
        return None
    if minutes > MAX_DURATION_MINUTES:
        return MAX_DURATION_MINUTES
    return minutes


def _strip_span(text, start, end):
    return text[:start] + " " + text[end:]


def _to_date(value):
    if isinstance(value, _date):
        return value
    if isinstance(value, str):
        try:
            return _date.fromisoformat(value)
        except ValueError:
            return None
    return None


def _next_weekday(from_date, target_weekday):
    """从 from_date（含今天）开始找最近的目标星期几。"""
    days_ahead = (target_weekday - from_date.weekday()) % 7
    return from_date + timedelta(days=days_ahead)


def _next_workday(from_date):
    """今天若为工作日则今天，否则下一个周一。"""
    if from_date.weekday() < 5:
        return from_date
    days_ahead = (0 - from_date.weekday()) % 7
    return from_date + timedelta(days=days_ahead)


def _next_weekend(from_date):
    """今天若为周末则今天，否则下一个周六。"""
    if from_date.weekday() in (5, 6):
        return from_date
    days_ahead = (5 - from_date.weekday()) % 7
    return from_date + timedelta(days=days_ahead)


def _clean_title(text, remove_control_words=False):
    """清理标题：去除控制 token 后的多余空格和首尾标点。"""
    title = text.strip()
    title = title.strip("，。！？、；:：,.!?; ")
    if remove_control_words:
        title = title.replace("开始", "").replace("从", "")
    title = re.sub(r"\s+", " ", title)
    title = title.strip("，。！？、；:：,.!?; ")
    return title.strip()


def parse_quick_task(text, now=None):
    """解析 Quick Mode 文本。

    text：用户输入字符串
    now：可选 datetime，用于相对日期计算（不传则用本地 now）

    返回 ParsedQuickTask；解析失败抛 QuickParseError。
    """
    if now is None:
        now = datetime.now()
    if not isinstance(text, str):
        raise QuickParseError("请输入任务内容。")

    tokens_used = []
    date = None
    time = None
    priority = "normal"
    period_is_pm = None

    # recurrence 状态
    is_recurring = False
    frequency = None
    interval = 1
    weekday_target = None
    end_type = None
    end_date = None

    # 12 阶段：时间块时长
    duration_minutes = None

    # ---------- 1. 优先级标记 ----------
    for token, prio in _PRIORITY_TOKENS.items():
        if token in text:
            priority = prio
            text = text.replace(token, " ")
            tokens_used.append(token)

    # ---------- 2. 结束日期（先于其他日期解析） ----------
    m = _END_DATE_RE.search(text)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            end_date = _date(y, mo, d).isoformat()
        except ValueError:
            raise QuickParseError("结束日期格式无效。")
        end_type = "on_date"
        text = _strip_span(text, m.start(), m.end())
        tokens_used.append(m.group(0))
    else:
        m = _END_CN_DATE_RE.search(text)
        if m:
            mo, d = int(m.group(1)), int(m.group(2))
            try:
                end_date = _date(now.year, mo, d).isoformat()
            except ValueError:
                raise QuickParseError("结束日期格式无效。")
            end_type = "on_date"
            text = _strip_span(text, m.start(), m.end())
            tokens_used.append(m.group(0))

    if end_type == "on_date":
        text = text.replace("结束", " ")
        tokens_used.append("结束")

    # ---------- 3. 明确日期 ----------
    explicit_date = None

    # 处理「从X开始」：X 作为 anchor 日期，但「从/开始」是控制词
    from_start_date = None
    m = re.search(r"从\s*(\d{4})-(\d{1,2})-(\d{1,2})\s*开始", text)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            from_start_date = _date(y, mo, d).isoformat()
        except ValueError:
            raise QuickParseError("日期格式无效。")
        text = _strip_span(text, m.start(), m.end())
        tokens_used.append(m.group(0))

    # 「从明天开始」等相对日期作为 anchor（非明确日期冲突源）
    relative_anchor = None
    m = re.search(r"从\s*(明天|今天|后天|今日|明日)\s*开始", text)
    if m:
        word = m.group(1)
        relative_anchor = _RELATIVE_DAYS.get(word, 1)
        text = _strip_span(text, m.start(), m.end())
        tokens_used.append(m.group(0))

    m = _DATE_RE.search(text)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            explicit_date = _date(y, mo, d).isoformat()
        except ValueError:
            raise QuickParseError("日期格式无效。")
        text = _strip_span(text, m.start(), m.end())
        tokens_used.append(m.group(0))

    m = _CN_DATE_RE.search(text)
    if m:
        mo, d = int(m.group(1)), int(m.group(2))
        try:
            explicit_date = _date(now.year, mo, d).isoformat()
        except ValueError:
            raise QuickParseError("日期格式无效。")
        text = _strip_span(text, m.start(), m.end())
        tokens_used.append(m.group(0))

    # ---------- 4. recurrence 表达解析 ----------
    # 4a. 带数字间隔：每N天/周/个月/年/小时（先于每周一处理）
    if frequency is None:
        m = _NUM_INTERVAL_RE.search(text)
        if m:
            n = int(m.group(1))
            unit = m.group(2)
            if n < 1:
                raise QuickParseError("重复间隔必须 >= 1")
            unit_map = {"天": "daily", "周": "weekly", "个月": "monthly",
                        "年": "yearly", "小时": "hourly"}
            frequency = unit_map[unit]
            interval = n
            is_recurring = True
            text = _strip_span(text, m.start(), m.end())
            tokens_used.append(m.group(0))

    # 4b. 星期几（周X / 星期X），若尚未确定 frequency，默认 weekly interval=1
    if frequency is None or frequency == "weekly":
        wm = _WEEKDAY_ONLY_RE.search(text)
        if wm:
            if frequency is None:
                frequency = "weekly"
                interval = 1
                is_recurring = True
            weekday_target = _WEEKDAY_MAP[wm.group(1)]
            text = _strip_span(text, wm.start(), wm.end())
            tokens_used.append(wm.group(0))
            # 移除紧邻的「每」字（如「每周一」中的「每」）
            if text[:wm.start()].rstrip().endswith("每"):
                pos = text[:wm.start()].rfind("每")
                text = text[:pos] + " " + text[wm.start():]
                tokens_used.append("每")

    # 4c. 明确无数字频率词（长的优先）
    if frequency is None:
        for phrase, freq in [
            ("每个工作日", "workday"), ("每工作日", "workday"), ("工作日每天", "workday"),
            ("每个周末", "weekend"), ("每周末", "weekend"),
            ("每季度", "quarterly"),
        ]:
            if phrase in text:
                frequency = freq
                interval = 1
                is_recurring = True
                text = text.replace(phrase, " ")
                tokens_used.append(phrase)
                break

    if frequency is None:
        if "每3个月" in text:
            frequency = "quarterly"
            interval = 1
            is_recurring = True
            text = text.replace("每3个月", " ")
            tokens_used.append("每3个月")

    if frequency is None:
        for phrase, freq in [
            ("每星期", "weekly"),
            ("每周", "weekly"),
            ("每天", "daily"), ("每日", "daily"),
            ("每月", "monthly"),
            ("每年", "yearly"),
            ("每小时", "hourly"),
        ]:
            if phrase in text:
                frequency = freq
                interval = 1
                is_recurring = True
                text = text.replace(phrase, " ")
                tokens_used.append(phrase)
                break

    # ---------- 5. 相对日期 + 时段 ----------
    date_offset = None
    compound_period = None

    for word, offset in [("今晚", 0), ("明早", 1), ("明晚", 1)]:
        if word in text:
            if explicit_date is not None:
                raise QuickParseError("检测到多个日期，请只保留一个日期表达。")
            date_offset = offset
            text = text.replace(word, " ")
            tokens_used.append(word)
            compound_period = True if word in ("今晚", "明晚") else False
            break

    if compound_period is not None:
        period_is_pm = compound_period
    else:
        for p in _PERIODS:
            if p in text:
                period_is_pm = _PERIODS[p]
                text = text.replace(p, " ")
                tokens_used.append(p)
                break

    if date_offset is None:
        for word in sorted(_RELATIVE_DAYS, key=len, reverse=True):
            if word in text:
                # 检测第二个相对日期词
                rest = text.replace(word, "", 1)
                for other in sorted(_RELATIVE_DAYS, key=len, reverse=True):
                    if other != word and other in rest:
                        raise QuickParseError("检测到多个日期，请只保留一个日期表达。")
                if explicit_date is not None:
                    raise QuickParseError("检测到多个日期，请只保留一个日期表达。")
                date_offset = _RELATIVE_DAYS[word]
                text = text.replace(word, " ")
                tokens_used.append(word)
                break

    # ---------- 6. 时间解析 ----------
    # 6a. 先处理时间段「9点到10点」，它同时确定 time 与 duration
    m = _TIME_RANGE_RE.search(text)
    if m:
        sh = int(m.group(1))
        if m.group(2) == "半":
            sm = 30
        elif m.group(3):
            sm = int(m.group(3))
        else:
            sm = 0
        eh = int(m.group(4))
        if m.group(5) == "半":
            em = 30
        elif m.group(6):
            em = int(m.group(6))
        else:
            em = 0

        if period_is_pm is True:
            if sh < 12:
                sh += 12
            if eh < 12:
                eh += 12
        elif period_is_pm is False:
            if sh == 12:
                sh = 0
            if eh == 12:
                eh = 0

        if not (0 <= sh <= 23 and 0 <= eh <= 23):
            raise QuickParseError("时间格式无效。")
        start_min = sh * 60 + sm
        end_min = eh * 60 + em
        if end_min <= start_min:
            end_min += 24 * 60   # 跨午夜：例如 23点到1点
        duration_minutes = _clamp_duration(end_min - start_min)
        time = f"{sh:02d}:{sm:02d}"
        text = _strip_span(text, m.start(), m.end())
        tokens_used.append(m.group(0))

    if time is None:
        # 6b. 先尝试时长关键词（2小时 / 90分钟 / 半小时），
        # 以免「3点半小时」被当成 3:30
        if duration_minutes is None:
            m = _DURATION_HALF_RE.search(text)
            if m:
                matched = m.group(0)
                duration_minutes = 90 if "一个半" in matched else 30
                text = _strip_span(text, m.start(), m.end())
                tokens_used.append(matched)
            else:
                m = _DURATION_HOUR_RE.search(text)
                if m:
                    duration_minutes = _clamp_duration(float(m.group(1)) * 60)
                    text = _strip_span(text, m.start(), m.end())
                    tokens_used.append(m.group(0))
                else:
                    m = _DURATION_MIN_RE.search(text)
                    if m:
                        duration_minutes = _clamp_duration(int(m.group(1)))
                        text = _strip_span(text, m.start(), m.end())
                        tokens_used.append(m.group(0))

        m = _HHMM_RE.search(text)
        if m:
            hh, mm = int(m.group(1)), int(m.group(2))
            if not (0 <= hh <= 23 and 0 <= mm <= 59):
                raise QuickParseError("时间格式无效。")
            time = f"{hh:02d}:{mm:02d}"
            text = _strip_span(text, m.start(), m.end())
            tokens_used.append(m.group(0))

    if time is None:
        m = _CN_TIME_RE.search(text)
        if m:
            hh = int(m.group(1))
            half = m.group(2) == "半"
            minutes = 30 if half else 0
            if not half and m.group(3) is not None:
                minutes = int(m.group(3))
            if not (0 <= hh <= 23):
                raise QuickParseError("时间格式无效。")
            if not (0 <= minutes <= 59):
                raise QuickParseError("时间格式无效。")

            if period_is_pm is True:
                if hh < 12:
                    hh += 12
            elif period_is_pm is False:
                if hh == 12:
                    hh = 0

            if not (0 <= hh <= 23):
                raise QuickParseError("时间格式无效。")
            time = f"{hh:02d}:{minutes:02d}"
            text = _strip_span(text, m.start(), m.end())
            tokens_used.append(m.group(0))

    # 6c. 若仍未得到时长，再尝试一次关键词（例如时间写在时长之后）
    if duration_minutes is None and time is not None:
        m = _DURATION_HALF_RE.search(text)
        if m:
            matched = m.group(0)
            duration_minutes = 90 if "一个半" in matched else 30
            text = _strip_span(text, m.start(), m.end())
            tokens_used.append(matched)
        else:
            m = _DURATION_HOUR_RE.search(text)
            if m:
                duration_minutes = _clamp_duration(float(m.group(1)) * 60)
                text = _strip_span(text, m.start(), m.end())
                tokens_used.append(m.group(0))
            else:
                m = _DURATION_MIN_RE.search(text)
                if m:
                    duration_minutes = _clamp_duration(int(m.group(1)))
                    text = _strip_span(text, m.start(), m.end())
                    tokens_used.append(m.group(0))

    # ---------- 7. 冲突检测：残留数字时间/日期 ----------
    if _HHMM_RE.search(text) or _CN_TIME_RE.search(text):
        raise QuickParseError("检测到多个时间，请只保留一个时间表达。")
    if _DATE_RE.search(text) or _CN_DATE_RE.search(text):
        raise QuickParseError("检测到多个日期，请只保留一个日期表达。")

    # ---------- 8. anchor date 计算 ----------
    if is_recurring:
        if weekday_target is not None:
            # 星期几：明确日期（explicit_date）需一致；
            # 「从X开始」的 X 视为控制词，不算明确日期冲突
            if explicit_date is not None:
                if _to_date(explicit_date).weekday() != weekday_target:
                    raise QuickParseError("开始日期与重复星期不一致。")
                date = explicit_date
            else:
                date = _next_weekday(now.date(), weekday_target).isoformat()
        else:
            # 非星期几规则：优先级 from_start_date > relative_anchor > explicit_date > 默认
            if from_start_date is not None:
                date = from_start_date
            elif relative_anchor is not None:
                date = (now.date() + timedelta(days=relative_anchor)).isoformat()
            elif explicit_date is not None:
                date = explicit_date
            else:
                if frequency == "workday":
                    date = _next_workday(now.date()).isoformat()
                elif frequency == "weekend":
                    date = _next_weekend(now.date()).isoformat()
                else:
                    date = now.date().isoformat()

        # hourly 必须有具体时间
        if frequency == "hourly" and time is None:
            raise QuickParseError(
                "每小时重复任务需要指定首次时间，例如：上午9点开始每2小时喝水。"
            )

        # 结束日期验证：不能早于开始日期
        if end_type == "on_date" and end_date is not None and date is not None:
            if end_date < date:
                raise QuickParseError("结束日期不能早于开始日期。")
    else:
        # 非重复任务：explicit_date 或相对日期
        if explicit_date is not None:
            date = explicit_date
        elif date_offset is not None:
            date = (now.date() + timedelta(days=date_offset)).isoformat()
        elif from_start_date is not None:
            date = from_start_date
        elif relative_anchor is not None:
            date = (now.date() + timedelta(days=relative_anchor)).isoformat()

    # ---------- 9. 清理标题 ----------
    title = _clean_title(text, remove_control_words=is_recurring)
    if not title:
        raise QuickParseError("请输入任务内容。")

    return ParsedQuickTask(
        title=title,
        date=date,
        time=time,
        priority=priority,
        recognized_tokens=tokens_used,
        is_recurring=is_recurring,
        recurrence_frequency=frequency,
        recurrence_interval=interval,
        recurrence_end_type=end_type,
        recurrence_end_date=end_date,
        duration_minutes=duration_minutes if time is not None else None,
    )
