"""重复规则计算引擎的单元测试。

运行方式：
    cd <PROJECT_ROOT>
    .venv\\Scripts\\python -m unittest test_recurrence.py
"""

import unittest
from datetime import date

import recurrence


def make_task(task_id=1, date=None, time=None, is_recurring=0,
              frequency=None, interval=1, end_type=None, end_date=None):
    """构造一个类似 sqlite3.Row 的任务对象（用 dict 即可，recurrence 用 .get 访问）。"""
    return {
        "id": task_id,
        "date": date,
        "time": time,
        "is_recurring": is_recurring,
        "recurrence_frequency": frequency,
        "recurrence_interval": interval,
        "recurrence_end_type": end_type,
        "recurrence_end_date": end_date,
    }


def dates(occurrences):
    return [o.date for o in occurrences]


class RecurrenceEngineTest(unittest.TestCase):

    def test_daily(self):
        t = make_task(date="2026-10-01", is_recurring=1, frequency="daily", interval=1)
        occ = recurrence.get_occurrences_for_range(t, "2026-10-01", "2026-10-07")
        self.assertEqual(dates(occ), [
            "2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04",
            "2026-10-05", "2026-10-06", "2026-10-07",
        ])

    def test_daily_every_2_days(self):
        t = make_task(date="2026-10-01", is_recurring=1, frequency="daily", interval=2)
        occ = recurrence.get_occurrences_for_range(t, "2026-10-01", "2026-10-07")
        self.assertEqual(dates(occ), ["2026-10-01", "2026-10-03", "2026-10-05", "2026-10-07"])

    def test_weekly(self):
        # 2026-10-05 是周一
        t = make_task(date="2026-10-05", is_recurring=1, frequency="weekly", interval=1)
        occ = recurrence.get_occurrences_for_range(t, "2026-10-05", "2026-10-31")
        self.assertEqual(dates(occ), ["2026-10-05", "2026-10-12", "2026-10-19", "2026-10-26"])

    def test_weekly_every_2_weeks(self):
        t = make_task(date="2026-10-05", is_recurring=1, frequency="weekly", interval=2)
        occ = recurrence.get_occurrences_for_range(t, "2026-10-05", "2026-10-31")
        self.assertEqual(dates(occ), ["2026-10-05", "2026-10-19"])

    def test_workday_skips_weekend(self):
        # 2026-10-02 是周五
        t = make_task(date="2026-10-02", is_recurring=1, frequency="workday", interval=1)
        occ = recurrence.get_occurrences_for_range(t, "2026-10-02", "2026-10-08")
        # 10-02 周五, 10-05 周一, 10-06 周二, 10-07 周三, 10-08 周四
        self.assertEqual(dates(occ), [
            "2026-10-02", "2026-10-05", "2026-10-06", "2026-10-07", "2026-10-08",
        ])

    def test_weekend(self):
        # 2026-10-03 周六
        t = make_task(date="2026-10-03", is_recurring=1, frequency="weekend", interval=1)
        occ = recurrence.get_occurrences_for_range(t, "2026-10-03", "2026-10-18")
        self.assertEqual(dates(occ), [
            "2026-10-03", "2026-10-04", "2026-10-10", "2026-10-11", "2026-10-17", "2026-10-18",
        ])

    def test_monthly_jan31(self):
        # 1月31日每月重复，月末安全，不漂移
        t = make_task(date="2026-01-31", is_recurring=1, frequency="monthly", interval=1)
        occ = recurrence.get_occurrences_for_range(t, "2026-01-01", "2026-04-30")
        self.assertEqual(dates(occ), ["2026-01-31", "2026-02-28", "2026-03-31", "2026-04-30"])

    def test_yearly_leap_day(self):
        # 2024-02-29 每年重复，非闰年落到 2/28
        t = make_task(date="2024-02-29", is_recurring=1, frequency="yearly", interval=1)
        occ = recurrence.get_occurrences_for_range(t, "2024-01-01", "2028-12-31")
        self.assertEqual(dates(occ), [
            "2024-02-29", "2025-02-28", "2026-02-28", "2027-02-28", "2028-02-29",
        ])

    def test_quarterly(self):
        t = make_task(date="2026-01-31", is_recurring=1, frequency="quarterly", interval=1)
        occ = recurrence.get_occurrences_for_range(t, "2026-01-01", "2026-12-31")
        self.assertEqual(dates(occ), ["2026-01-31", "2026-04-30", "2026-07-31", "2026-10-31"])

    def test_hourly(self):
        t = make_task(date="2026-10-02", time="09:30", is_recurring=1, frequency="hourly", interval=2)
        occ = recurrence.get_occurrences_for_range(t, "2026-10-02", "2026-10-02")
        times = [o.time for o in occ]
        self.assertIn("09:30", times)
        self.assertIn("11:30", times)
        self.assertIn("13:30", times)

    def test_hourly_cross_midnight(self):
        t = make_task(date="2026-10-02", time="23:30", is_recurring=1, frequency="hourly", interval=2)
        occ = recurrence.get_occurrences_for_range(t, "2026-10-02", "2026-10-03")
        # 23:30 -> 次日 01:30
        pairs = [(o.date, o.time) for o in occ]
        self.assertIn(("2026-10-02", "23:30"), pairs)
        self.assertIn(("2026-10-03", "01:30"), pairs)

    def test_end_date(self):
        t = make_task(date="2026-10-01", is_recurring=1, frequency="daily", interval=1,
                      end_type="on_date", end_date="2026-10-10")
        occ = recurrence.get_occurrences_for_range(t, "2026-10-01", "2026-10-20")
        self.assertEqual(dates(occ)[0], "2026-10-01")
        self.assertEqual(dates(occ)[-1], "2026-10-10")  # 结束日期包含当天
        self.assertEqual(len(occ), 10)

    def test_range_boundary(self):
        t = make_task(date="2026-10-01", is_recurring=1, frequency="daily", interval=1)
        # 只请求 10-05 到 10-08
        occ = recurrence.get_occurrences_for_range(t, "2026-10-05", "2026-10-08")
        self.assertEqual(dates(occ), ["2026-10-05", "2026-10-06", "2026-10-07", "2026-10-08"])

    def test_never_rule_only_requested_range(self):
        # never 规则不应无限生成，只生成请求范围
        t = make_task(date="2026-10-01", is_recurring=1, frequency="daily", interval=1,
                      end_type="never")
        occ = recurrence.get_occurrences_for_range(t, "2026-10-01", "2026-10-03")
        self.assertEqual(len(occ), 3)

    def test_non_recurring_single(self):
        t = make_task(date="2026-10-01", is_recurring=0)
        occ = recurrence.get_occurrences_for_range(t, "2026-10-01", "2026-10-31")
        self.assertEqual(len(occ), 1)
        self.assertEqual(occ[0].date, "2026-10-01")

    def test_non_recurring_out_of_range(self):
        t = make_task(date="2026-11-01", is_recurring=0)
        occ = recurrence.get_occurrences_for_range(t, "2026-10-01", "2026-10-31")
        self.assertEqual(len(occ), 0)


if __name__ == "__main__":
    unittest.main()
