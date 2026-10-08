"""灵活的日期 / 时间本地解析器（纯本地、确定性，不调用网络）。

用于 Detail Mode 的「粘贴日期 / 粘贴时间」智能识别，
也作为 AI fallback 之前的本地第一层解析。

不写数据库、不创建任务。
"""

import re
from datetime import date as _date, datetime, timedelta
from typing import Optional


class DateTimeParseError(ValueError):
    """日期 / 时间解析失败。"""


# ---------- 统一的日期时间结果 ----------

class ParsedDateTime:
    """统一的日期时间解析结果。"""

    def __init__(self, date=None, time=None, date_recognized=False,
                 time_recognized=False, time_ambiguous=False, source="local"):
        self.date = date
        self.time = time
        self.date_recognized = date_recognized
        self.time_recognized = time_recognized
        self.time_ambiguous = time_ambiguous
        self.source = source

    def __repr__(self):
        return (f"ParsedDateTime(date={self.date!r}, time={self.time!r}, "
                f"date_recognized={self.date_recognized}, "
                f"time_recognized={self.time_recognized}, "
                f"time_ambiguous={self.time_ambiguous}, source={self.source!r})")


# ---------- 日期 ----------

_RELATIVE_DATE = {
    "今天": 0,
    "今日": 0,
    "明天": 1,
    "明日": 1,
    "后天": 2,
}

_CN_DATE_RE = re.compile(r"(\d{1,4})年(\d{1,2})月(\d{1,2})[日号]")
_SEP_DATE_RE = re.compile(r"(\d{1,4})[-/.\s](\d{1,2})[-/.\s](\d{1,2})")
_MONTH_DAY_RE = re.compile(r"(\d{1,2})月(\d{1,2})[日号]")


def _normalize_text(text):
    """归一化全角字符和空格。

    - 全角空格 → 半角空格
    - 全角斜杠 ／ → /
    - 全角点号 ． → .
    - 全角冒号 ： → :
    - 去除日期/时间分隔符（/ . -）前后的空白
    - 折叠多余空白
    """
    if not isinstance(text, str):
        raise DateTimeParseError("无法识别有效日期。")
    s = text.strip()
    # 全角空格 → 半角
    s = s.replace("\u3000", " ")
    # 全角斜杠 → 半角
    s = s.replace("／", "/")
    # 全角点号 → 半角
    s = s.replace("．", ".")
    # 全角冒号 → 半角（时间用）
    s = s.replace("：", ":")
    # 去除分隔符（/ . -）前后的空白，例如 "2026 / 10 / 03" → "2026/10/03"
    s = re.sub(r"\s*([/.\-])\s*", r"\1", s)
    # 折叠多余空白
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def _parse_year_month_day(y, mo, d, default_year=None):
    """构造合法日期；非法抛错。"""
    year = y if y else (default_year or datetime.now().year)
    try:
        return _date(year, mo, d).isoformat()
    except ValueError:
        raise DateTimeParseError("无法识别有效日期。")


def parse_flexible_date(text, now=None):
    """解析灵活日期文本，返回 YYYY-MM-DD 或抛 DateTimeParseError。

    支持：
      YYYY-MM-DD、YYYY/MM/D、YYYY.M.D、YYYY MM DD
      YYYY年M月D日/号
      M月D日/号（缺少年份 → 当前年份）
      M-D、M/D、M.D（缺少年份 → 当前年份）
      今天 / 明天 / 后天
    """
    now = now or datetime.now()
    s = _normalize_text(text)

    # 相对日期
    if s in _RELATIVE_DATE:
        return (now.date() + timedelta(days=_RELATIVE_DATE[s])).isoformat()

    # 中文全格式 YYYY年M月D日
    m = _CN_DATE_RE.fullmatch(s)
    if m:
        return _parse_year_month_day(int(m.group(1)), int(m.group(2)), int(m.group(3)))

    # 分隔格式 YYYY-MM-DD / YYYY/MM/D / YYYY.MM.D / YYYY MM DD
    m = _SEP_DATE_RE.fullmatch(s)
    if m:
        y = int(m.group(1)) if len(m.group(1)) == 4 else None
        return _parse_year_month_day(y, int(m.group(2)), int(m.group(3)), now.year)

    # 月日格式 M月D日 / M月D号
    m = _MONTH_DAY_RE.fullmatch(s)
    if m:
        return _parse_year_month_day(None, int(m.group(1)), int(m.group(2)), now.year)

    # 短格式 M-D / M/D / M.D
    m = re.fullmatch(r"(\d{1,2})[-/.](\d{1,2})", s)
    if m:
        return _parse_year_month_day(None, int(m.group(1)), int(m.group(2)), now.year)

    raise DateTimeParseError("无法识别有效日期。")


# ---------- 时间 ----------

_HHMM_COLON_RE = re.compile(r"^(\d{1,2})[:.](\d{2})$")
_HHMM_4DIGIT_RE = re.compile(r"^(\d{2})(\d{2})$")
_HHMM_SPACE_RE = re.compile(r"^(\d{1,2})\s+(\d{2})$")
_CN_TIME_RE = re.compile(r"^(\d{1,2})点(半|(\d{1,2})分?)?$")

# 中文数字 → 阿拉伯数字（时间用）
_CN_DIGITS = {
    "零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
    "十一": 11, "十二": 12, "十三": 13, "十四": 14, "十五": 15,
    "十六": 16, "十七": 17, "十八": 18, "十九": 19, "二十": 20,
    "二十一": 21, "二十二": 22, "二十三": 23,
}
_CN_TIME_CN_RE = re.compile(r"^([零一二两三四五六七八九十]+)点(半|([零一二两三四五六七八九十]+)分?)?$")

_PERIOD_AM = {"早上", "上午"}
_PERIOD_PM = {"中午", "下午", "晚上", "晚"}


def _cn_digit_to_int(s):
    """中文数字转整数；不支持返回 None。"""
    if s in _CN_DIGITS:
        return _CN_DIGITS[s]
    # 处理「X十Y」形式，如「二十五」
    if "十" in s:
        parts = s.split("十")
        if len(parts) == 2:
            tens = _CN_DIGITS.get(parts[0], 1) if parts[0] else 1
            ones = _CN_DIGITS.get(parts[1], 0) if parts[1] else 0
            return tens * 10 + ones
    return None



def parse_flexible_time(text, now=None):
    """解析灵活时间文本，返回 HH:MM 或抛 DateTimeParseError。

    支持：
      18:30、18：30、18.30、18 30、1830
      6:30 PM / 6:30PM / 6:30 pm
      下午6:30、下午6点30、下午6点半
      晚上8点、晚8点、晚上8点半
      上午9点、早上9点
      9点、9点30、9点半
    """
    s = _normalize_text(text)
    if not s:
        raise DateTimeParseError("无法识别有效时间。")

    # 处理 PM/AM 后缀
    period_am = None
    period_pm = None
    m = re.search(r"^(.*?)\s*(AM|PM|am|pm)$", s)
    if m:
        base = m.group(1).strip()
        suffix = m.group(2).upper()
        period_pm = (suffix == "PM")
        period_am = (suffix == "AM")
        s = base

    # 中文时段前缀
    if period_pm is None:
        for p in _PERIOD_AM:
            if s.startswith(p):
                period_am = True
                s = s[len(p):].strip()
                break
        if period_am is None:
            for p in sorted(_PERIOD_PM, key=len, reverse=True):
                if s.startswith(p):
                    period_pm = True
                    s = s[len(p):].strip()
                    break

    # 提取 hh 和 mm
    hh = None
    mm = 0

    # 18:30 / 18.30
    m = _HHMM_COLON_RE.match(s)
    if m:
        hh, mm = int(m.group(1)), int(m.group(2))

    # 18 30
    if hh is None:
        m = _HHMM_SPACE_RE.match(s)
        if m:
            hh, mm = int(m.group(1)), int(m.group(2))

    # 1830
    if hh is None:
        m = _HHMM_4DIGIT_RE.match(s)
        if m:
            hh, mm = int(m.group(1)), int(m.group(2))

    # 中文 9点 / 9点30 / 9点半
    if hh is None:
        m = _CN_TIME_RE.match(s)
        if m:
            hh = int(m.group(1))
            if m.group(2) == "半":
                mm = 30
            elif m.group(3) is not None:
                mm = int(m.group(3))

    # 中文数字时间：八点半 / 二十一点
    if hh is None:
        m = _CN_TIME_CN_RE.match(s)
        if m:
            hh = _cn_digit_to_int(m.group(1))
            if hh is None:
                raise DateTimeParseError("无法识别有效时间。")
            if m.group(2) == "半":
                mm = 30
            elif m.group(3) is not None:
                mm = _cn_digit_to_int(m.group(3))
                if mm is None:
                    mm = 0

    if hh is None:
        raise DateTimeParseError("无法识别有效时间。")

    # 12/24 小时转换
    if period_pm is True:
        if hh < 12:
            hh += 12
    elif period_am is True:
        if hh == 12:
            hh = 0

    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        raise DateTimeParseError("无法识别有效时间。")

    return f"{hh:02d}:{mm:02d}"


# ---------- 统一的日期时间解析 ----------

# 疑似日期/时间的自然语言特征（用于判断是否需要 AI fallback）
_DATE_HINT_RE = re.compile(r"(?:下|本|这)?(?:个)?(?:周|星期|礼拜|月|年)|\d{4}年|\d{1,2}月\d{1,2}|\d{1,2}[日号]")
_TIME_HINT_RE = re.compile(r"(?:早上|上午|中午|下午|晚上|晚|凌晨|\d{1,2}点|\d{1,2}[:.]\d{2}|\d{1,2}时)")


def parse_flexible_datetime(text, now=None):
    """统一的日期时间解析（从同一段文本中同时提取日期和时间）。

    返回 ParsedDateTime。

    策略：
    - 先尝试整体解析
    - 日期和时间各自独立识别，允许同段文本同时包含两者
    """
    now = now or datetime.now()
    if not isinstance(text, str):
        raise DateTimeParseError("无法识别有效日期时间。")
    s = _normalize_text(text).strip()

    date = None
    time = None
    date_recognized = False
    time_recognized = False
    time_ambiguous = False

    # 先分离：尝试找日期部分和时间部分
    # 简单策略：整段尝试日期；整段尝试时间；再尝试按空白/中文切分

    # 1) 整段作为日期
    try:
        date = parse_flexible_date(s, now)
        date_recognized = True
    except DateTimeParseError:
        pass

    # 2) 整段作为时间
    if not date_recognized:
        try:
            time = parse_flexible_time(s, now)
            time_recognized = True
        except DateTimeParseError:
            pass

    # 3) 如果整段都不是，尝试提取其中的日期和时间片段
    if not date_recognized or not time_recognized:
        # 在文本中搜索日期候选
        date_candidate = None
        time_candidate = None

        # 尝试提取日期：找 YYYY-MM-DD / 中文日期 / 相对日期
        for m in re.finditer(r"\d{4}[-/.\s]\d{1,2}[-/.\s]\d{1,2}|\d{4}年\d{1,2}月\d{1,2}[日号]|\d{1,2}月\d{1,2}[日号]|今天|明天|后天|今日|明日", s):
            cand = m.group(0)
            try:
                date_candidate = parse_flexible_date(cand, now)
                if not date_recognized:
                    date = date_candidate
                    date_recognized = True
                break
            except DateTimeParseError:
                continue

        # 尝试提取时间：找 HH:MM / 中文时间 / 中文数字时间
        for m in re.finditer(r"\d{1,2}[:.]\d{2}|(?:早上|上午|中午|下午|晚上|晚)?\s*\d{1,2}点(?:半|\d{1,2}分?)?|(?:早上|上午|中午|下午|晚上|晚)?\s*[零一二两三四五六七八九十]+点(?:半|[零一二两三四五六七八九十]+分?)?", s):
            cand = m.group(0).strip()
            try:
                time_candidate = parse_flexible_time(cand, now)
                if not time_recognized:
                    time = time_candidate
                    time_recognized = True
                break
            except DateTimeParseError:
                continue

    return ParsedDateTime(
        date=date,
        time=time,
        date_recognized=date_recognized,
        time_recognized=time_recognized,
        time_ambiguous=time_ambiguous,
        source="local",
    )
