# -*- coding: utf-8 -*-
"""0.1.1：.ics 导入的回归测试。

覆盖：
  * 文本层：折行、转义、引号内冒号、UTF-8/GBK
  * 值层：DATE 全天、Z→本地、TZID 原样、VTIMEZONE/VALARM 不污染
  * 组件层：VEVENT / VTODO（只有 DUE 时用 DUE）、CANCELLED 跳过
  * 重复规则映射：daily/workday/weekend/weekly/monthly/quarterly/yearly/hourly、
    UNTIL、COUNT、以及"表达不了就降级为单次 + 记 note"
  * 导入：建任务、幂等、自动备份、dry_run、绝不写 SQL
  * 界面：设置页出现「数据管理」分区与 .ics 区块

全部 hermetic：数据目录走 TODO_APP_DATA_DIR 临时目录，绝不碰真实 %LOCALAPPDATA%。
"""
import io
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from datetime import date as D
from datetime import datetime, time as T, timezone
from pathlib import Path

from tests import tests_env  # noqa: F401

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from taiplan import ics_import as ics  # noqa: E402

LABELS = {
    "untitled": "未命名事件", "location": "地点", "invalid_url": "链接已忽略",
    "duration_ignored": "时长已忽略", "multiday_allday": "跨天全天事件按开始日导入",
    "recurrence_simplified": "已按单次任务导入", "cancelled": "已取消的事件已跳过",
}

SINGLE = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:one
SUMMARY:一次会议
DTSTART:20261007T013000Z
DTEND:20261007T023000Z
END:VEVENT
END:VCALENDAR
"""


class IcsBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ics_test_"))
        self._env = os.environ.get("TODO_APP_DATA_DIR")
        os.environ["TODO_APP_DATA_DIR"] = str(self.tmp / "TaiPlan")
        (self.tmp / "TaiPlan").mkdir(parents=True, exist_ok=True)
        from taiplan import database

        self.database = database
        self._db_path = database.DB_PATH
        database.DB_PATH = str(self.tmp / "TaiPlan" / "todo.db")
        database.init_database()

    def tearDown(self):
        self.database.DB_PATH = self._db_path
        if self._env is None:
            os.environ.pop("TODO_APP_DATA_DIR", None)
        else:
            os.environ["TODO_APP_DATA_DIR"] = self._env
        shutil.rmtree(self.tmp, ignore_errors=True)


# =============================================================== 文本层
class TextLayerTest(IcsBase):
    def test_folded_lines_are_unfolded(self):
        raw = "SUMMARY:折行的\r\n 标题尾巴\r\n"
        self.assertEqual(ics.unfold_lines(raw)[0], "SUMMARY:折行的标题尾巴")

    def test_escapes_are_unescaped(self):
        self.assertEqual(ics.unescape(r"a\,b\;c\nd\\e"), "a,b;c\nd\\e")

    def test_split_property_keeps_colon_inside_quotes(self):
        name, params, value = ics.split_property(
            'DTSTART;TZID="Asia/Shanghai":20261007T090000')
        self.assertEqual(name, "DTSTART")
        self.assertEqual(params["TZID"], "Asia/Shanghai")
        self.assertEqual(value, "20261007T090000")

    def test_utf8_and_gbk_both_decode(self):
        text = "SUMMARY:中文标题"
        self.assertIn("中文标题", ics.decode_bytes(text.encode("utf-8")))
        self.assertIn("中文标题", ics.decode_bytes(text.encode("gbk")))


# =============================================================== 值层
class ValueLayerTest(IcsBase):
    def test_date_value_is_all_day(self):
        d, t, all_day = ics.parse_datetime("20261008", {"VALUE": "DATE"})
        self.assertEqual((d, t, all_day), (D(2026, 10, 8), None, True))

    def test_utc_is_converted_to_local(self):
        d, t, all_day = ics.parse_datetime("20261007T010000Z", {})
        expected = datetime(2026, 10, 7, 1, 0, tzinfo=timezone.utc).astimezone()
        self.assertEqual((d, t), (expected.date(), expected.time().replace(
            second=0, microsecond=0)))
        self.assertFalse(all_day)

    def test_tzid_is_used_as_local_wall_clock(self):
        d, t, all_day = ics.parse_datetime("20261007T090000", {"TZID": "Asia/Shanghai"})
        self.assertEqual((d, t, all_day), (D(2026, 10, 7), T(9, 0), False))

    def test_alarm_and_timezone_do_not_pollute_event(self):
        text = """BEGIN:VCALENDAR
BEGIN:VTIMEZONE
TZID:Asia/Shanghai
BEGIN:STANDARD
DTSTART:19700101T000000
TZOFFSETFROM:+0800
TZOFFSETTO:+0800
END:STANDARD
END:VTIMEZONE
BEGIN:VEVENT
UID:x
SUMMARY:不要被污染
DTSTART:20261007T090000
BEGIN:VALARM
ACTION:DISPLAY
DESCRIPTION:闹钟说明不该变成事件描述
TRIGGER:-PT15M
END:VALARM
END:VEVENT
END:VCALENDAR
"""
        events, _w = ics.parse_ics(text)
        self.assertEqual(len(events), 1)
        self.assertIsNone(events[0].description)
        self.assertEqual(events[0].start_date, D(2026, 10, 7))


# =============================================================== 组件层
class ComponentLayerTest(IcsBase):
    def test_vtodo_without_dtstart_uses_due(self):
        text = """BEGIN:VCALENDAR
BEGIN:VTODO
UID:todo
SUMMARY:交材料
DUE:20261010T180000
END:VTODO
END:VCALENDAR
"""
        events, warnings = ics.parse_ics(text)
        self.assertEqual(len(events), 1, f"VTODO 不该被丢掉: {warnings}")
        self.assertEqual(events[0].start_date, D(2026, 10, 10))

    def test_cancelled_event_is_marked(self):
        text = SINGLE.replace("SUMMARY:一次会议", "SUMMARY:取消掉\nSTATUS:CANCELLED")
        events, _w = ics.parse_ics(text)
        self.assertTrue(events[0].cancelled)

    def test_event_without_dtstart_is_reported_not_silently_dropped(self):
        text = "BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:y\nSUMMARY:没有时间\nEND:VEVENT\nEND:VCALENDAR\n"
        events, warnings = ics.parse_ics(text)
        self.assertEqual(events, [])
        self.assertTrue(any("DTSTART" in w for w in warnings), warnings)


# =============================================================== 重复规则
class RruleTest(unittest.TestCase):
    def _map(self, rule, start=D(2026, 10, 7)):
        return ics.map_recurrence(ics.parse_rrule(rule), start)

    def test_simple_frequencies(self):
        self.assertEqual(self._map("FREQ=DAILY")[0], "daily")
        self.assertEqual(self._map("FREQ=DAILY;INTERVAL=2")[1], 2)
        self.assertEqual(self._map("FREQ=WEEKLY;BYDAY=WE")[0], "weekly")
        self.assertEqual(self._map("FREQ=MONTHLY")[0], "monthly")
        self.assertEqual(self._map("FREQ=MONTHLY;INTERVAL=3")[0], "quarterly")
        self.assertEqual(self._map("FREQ=YEARLY")[0], "yearly")
        self.assertEqual(self._map("FREQ=HOURLY")[0], "hourly")

    def test_workday_and_weekend(self):
        self.assertEqual(self._map("FREQ=DAILY;BYDAY=MO,TU,WE,TH,FR")[0], "workday")
        self.assertEqual(self._map("FREQ=DAILY;BYDAY=SA,SU")[0], "weekend")

    def test_weekly_on_other_weekday_is_downgraded_with_note(self):
        freq, _i, _et, _ed, note = self._map("FREQ=WEEKLY;BYDAY=MO")
        self.assertIsNone(freq)
        self.assertIn("weekday", note)

    def test_until_becomes_end_date(self):
        freq, _i, end_type, end_date, _n = self._map("FREQ=WEEKLY;UNTIL=20261130T000000Z")
        self.assertEqual(freq, "weekly")
        self.assertEqual(end_type, "on_date")
        self.assertEqual(end_date, D(2026, 11, 30))

    def test_count_becomes_end_date(self):
        self.assertEqual(self._map("FREQ=DAILY;COUNT=5")[3], D(2026, 10, 11))
        self.assertEqual(self._map("FREQ=MONTHLY;COUNT=4", D(2026, 10, 31))[3], D(2027, 1, 31))

    def test_unsupported_is_downgraded_with_reason(self):
        freq, _i, _et, _ed, note = self._map("FREQ=MONTHLY;BYDAY=3FR")
        self.assertIsNone(freq)
        self.assertTrue(note)
        self.assertIsNone(self._map("FREQ=MINUTELY")[0])
        self.assertIsNone(self._map("FREQ=DAILY;BYMONTHDAY=16")[0])
        # RDATE/EXDATE 不影响是否重复，只记 note
        self.assertIsNotNone(self._map("FREQ=DAILY")[0])


# =============================================================== 参数映射
class BuildArgsTest(unittest.TestCase):
    def _event(self, **kw):
        base = dict(uid="u", summary="标题", start_date=D(2026, 10, 7),
                    start_time=T(9, 0))
        base.update(kw)
        return ics.Event(**base)

    def test_priority_mapping(self):
        self.assertEqual(ics.build_task_args(self._event(priority=1), LABELS)[0]["priority"],
                         "urgent")
        self.assertEqual(ics.build_task_args(self._event(priority=5), LABELS)[0]["priority"],
                         "normal")
        self.assertEqual(ics.build_task_args(self._event(priority=9), LABELS)[0]["priority"],
                         "low")

    def test_duration_from_dtend(self):
        args, _n = ics.build_task_args(
            self._event(end_date=D(2026, 10, 7), end_time=T(10, 30)), LABELS)
        self.assertEqual(args["duration_minutes"], 90)

    def test_all_day_has_no_time(self):
        args, _n = ics.build_task_args(
            self._event(start_time=None, all_day=True), LABELS)
        self.assertIsNone(args["time"])
        self.assertIsNone(args["duration_minutes"])

    def test_invalid_url_is_dropped_with_note(self):
        args, notes = ics.build_task_args(self._event(url="ftp://x/y"), LABELS)
        self.assertIsNone(args["url"])
        self.assertIn(LABELS["invalid_url"], notes)

    def test_location_and_description_are_merged(self):
        args, _n = ics.build_task_args(
            self._event(location="会议室 A", description="带材料"), LABELS)
        self.assertIn("会议室 A", args["description"])
        self.assertIn("带材料", args["description"])

    def test_unsupported_rrule_sets_single_task_and_note(self):
        args, notes = ics.build_task_args(
            self._event(rrule="FREQ=MONTHLY;BYDAY=3FR"), LABELS)
        self.assertFalse(args["is_recurring"])
        self.assertIn(LABELS["recurrence_simplified"], notes)


# =============================================================== 导入
class ImportTest(IcsBase):
    def _events(self, text):
        return ics.parse_ics(text)[0]

    def test_import_creates_tasks_and_backup(self):
        from taiplan import services

        report = ics.import_events(self._events(SINGLE), LABELS)
        self.assertEqual(report.created, 1)
        self.assertEqual(len(services.get_tasks()), 1)
        self.assertIsNotNone(report.backup, "导入前必须自动备份")
        self.assertTrue((Path(os.environ["TODO_APP_DATA_DIR"]) / "backups").is_dir())

    def test_second_import_is_idempotent(self):
        events = self._events(SINGLE)
        first = ics.import_events(events, LABELS)
        second = ics.import_events(events, LABELS)
        self.assertEqual(first.created, 1)
        self.assertEqual(second.created, 0, "同一份 .ics 重复导入不应再建任务")
        self.assertEqual(second.duplicates, 1)

    def test_same_content_different_uid_is_deduped_by_content(self):
        events = self._events(SINGLE)
        ics.import_events(events, LABELS)
        renamed = SINGLE.replace("UID:one", "UID:another")
        report = ics.import_events(self._events(renamed), LABELS)
        self.assertEqual(report.created, 0)
        self.assertEqual(report.duplicates, 1)

    def test_dry_run_writes_nothing(self):
        from taiplan import services

        report = ics.import_events(self._events(SINGLE), LABELS, dry_run=True)
        self.assertEqual(report.created, 1)
        self.assertEqual(len(services.get_tasks()), 0)
        self.assertIsNone(report.backup)

    def test_cancelled_events_are_skipped(self):
        text = SINGLE.replace("SUMMARY:一次会议", "SUMMARY:取消\nSTATUS:CANCELLED")
        report = ics.import_events(self._events(text), LABELS)
        self.assertEqual(report.created, 0)
        self.assertIn(LABELS["cancelled"], report.notes)

    def test_imported_task_keeps_recurrence(self):
        from taiplan import services

        text = SINGLE.replace("END:VEVENT", "RRULE:FREQ=WEEKLY;BYDAY=WE;UNTIL=20261231T000000Z\nEND:VEVENT")
        report = ics.import_events(self._events(text), LABELS)
        self.assertEqual(report.created, 1)
        task = services.get_tasks()[0]
        self.assertEqual(task["is_recurring"], 1)
        self.assertEqual(task["recurrence_frequency"], "weekly")
        self.assertEqual(task["recurrence_end_type"], "on_date")
        self.assertEqual(task["recurrence_end_date"], "2026-12-31")


# =============================================================== 结构不变式
class InvariantTest(unittest.TestCase):
    def test_no_sql_and_no_new_third_party_deps(self):
        src = io.open(ROOT / "taiplan/ics_import.py", encoding="utf-8-sig").read()
        self.assertNotIn("import sqlite3", src, "SQL 只能出现在 database.py")
        self.assertNotIn("execute(", src)
        for forbidden in ("import icalendar", "import vobject", "from dateutil",
                          "import requests", "import pandas"):
            self.assertNotIn(forbidden, src, f"不允许引入新依赖: {forbidden}")

    def test_no_hardcoded_ui_text_in_module(self):
        """导入器不产出用户可见文案：所有措辞由调用方通过 labels 传入。"""
        src = io.open(ROOT / "taiplan/ics_import.py", encoding="utf-8-sig").read()
        self.assertIn("labels.get", src)
        self.assertNotIn("from i18n", src)


# =============================================================== 界面
class SettingsUiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ics_ui_"))
        self._env = os.environ.get("TODO_APP_DATA_DIR")
        os.environ["TODO_APP_DATA_DIR"] = str(self.tmp / "TaiPlan")
        from taiplan import database

        self.database = database
        self._db = database.DB_PATH
        database.DB_PATH = str(self.tmp / "TaiPlan" / "todo.db")
        (self.tmp / "TaiPlan").mkdir(parents=True, exist_ok=True)
        database.init_database()

    def tearDown(self):
        self.database.DB_PATH = self._db
        if self._env is None:
            os.environ.pop("TODO_APP_DATA_DIR", None)
        else:
            os.environ["TODO_APP_DATA_DIR"] = self._env
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _settings(self):
        from streamlit.testing.v1 import AppTest

        at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=120)
        at.session_state["nav"] = "settings"
        at.run()
        return at

    def test_data_section_and_ics_block_render(self):
        at = self._settings()
        self.assertEqual([str(e.value) for e in at.exception], [], "不应有 st.exception")
        text = " ".join(str(m.value) for m in at.markdown)
        self.assertIn("数据管理", text, "设置页应出现数据管理分区")
        self.assertIn("导入日历", text, "设置页应出现 .ics 导入区块")

    def test_no_error_widgets(self):
        at = self._settings()
        self.assertEqual([str(e.value) for e in at.error], [], "不应有 st.error")


if __name__ == "__main__":
    unittest.main(verbosity=2)
