"""Quick Add 自然语言解析器单元测试。

运行方式：
    cd <PROJECT_ROOT>
    .venv\\Scripts\\python -m unittest test_nlp_parser
"""

import unittest
from datetime import datetime

from taiplan.nlp_parser import parse_quick_task, QuickParseError

NOW = datetime(2026, 10, 2, 16, 0)


def parse(text):
    return parse_quick_task(text, now=NOW)


class NlpParserTest(unittest.TestCase):

    def test_plain_title(self):
        r = parse("买水")
        self.assertEqual(r.title, "买水")
        self.assertIsNone(r.date)
        self.assertIsNone(r.time)
        self.assertEqual(r.priority, "normal")

    def test_today(self):
        r = parse("今天写报告")
        self.assertEqual(r.date, "2026-10-02")
        self.assertEqual(r.title, "写报告")

    def test_tomorrow(self):
        r = parse("明天写报告")
        self.assertEqual(r.date, "2026-10-03")
        self.assertEqual(r.title, "写报告")

    def test_day_after_tomorrow(self):
        r = parse("后天写报告")
        self.assertEqual(r.date, "2026-10-04")
        self.assertEqual(r.title, "写报告")

    def test_mingzao_9(self):
        r = parse("明早9点开会")
        self.assertEqual(r.date, "2026-10-03")
        self.assertEqual(r.time, "09:00")
        self.assertEqual(r.title, "开会")

    def test_mingwan_8(self):
        r = parse("明晚8点吃饭")
        self.assertEqual(r.date, "2026-10-03")
        self.assertEqual(r.time, "20:00")
        self.assertEqual(r.title, "吃饭")

    def test_afternoon_3(self):
        r = parse("下午3点写报告")
        self.assertEqual(r.time, "15:00")
        self.assertEqual(r.title, "写报告")

    def test_evening_9_half(self):
        r = parse("晚上9点半看电影")
        self.assertEqual(r.time, "21:30")
        self.assertEqual(r.title, "看电影")

    def test_hhmm(self):
        r = parse("18:30 吃饭")
        self.assertEqual(r.time, "18:30")
        self.assertEqual(r.title, "吃饭")

    def test_iso_date(self):
        r = parse("2026-10-15 交作业")
        self.assertEqual(r.date, "2026-10-15")
        self.assertEqual(r.title, "交作业")

    def test_cn_date(self):
        r = parse("10月15日交作业")
        self.assertEqual(r.date, "2026-10-15")
        self.assertEqual(r.title, "交作业")

    def test_priority_urgent(self):
        r = parse("明天晚上8点交作业 !紧急")
        self.assertEqual(r.date, "2026-10-03")
        self.assertEqual(r.time, "20:00")
        self.assertEqual(r.priority, "urgent")
        self.assertEqual(r.title, "交作业")

    def test_normal_urgent_word_not_stripped(self):
        r = parse("紧急通知需要整理")
        self.assertEqual(r.title, "紧急通知需要整理")
        self.assertEqual(r.priority, "normal")

    def test_multiple_dates_conflict(self):
        with self.assertRaises(QuickParseError):
            parse("今天明天开会")

    def test_multiple_times_conflict(self):
        with self.assertRaises(QuickParseError):
            parse("9点 10点 开会")

    def test_invalid_time(self):
        with self.assertRaises(QuickParseError):
            parse("明天 25:90 开会")

    def test_empty_title(self):
        with self.assertRaises(QuickParseError):
            parse("明天9点 !紧急")

    # 额外组合测试（需求第十节）
    def test_today_afternoon_3(self):
        r = parse("今天下午3点写报告")
        self.assertEqual(r.date, "2026-10-02")
        self.assertEqual(r.time, "15:00")
        self.assertEqual(r.title, "写报告")

    def test_tonight_8(self):
        r = parse("今晚8点吃饭")
        self.assertEqual(r.date, "2026-10-02")
        self.assertEqual(r.time, "20:00")
        self.assertEqual(r.title, "吃饭")

    def test_noon_12(self):
        r = parse("中午12点开会")
        self.assertEqual(r.time, "12:00")

    def test_recurrence_word_kept_in_title(self):
        # 8B 规则变更："每天" 现在识别为 recurrence
        r = parse("每天晚上8点吃饭")
        self.assertEqual(r.time, "20:00")
        self.assertTrue(r.is_recurring)
        self.assertEqual(r.recurrence_frequency, "daily")


class NlpParserRecurrenceTest(unittest.TestCase):
    """8B 阶段：重复任务解析。"""

    def test_daily_evening_8(self):
        r = parse("每天晚上8点吃饭")
        self.assertEqual(r.title, "吃饭")
        self.assertEqual(r.time, "20:00")
        self.assertTrue(r.is_recurring)
        self.assertEqual(r.recurrence_frequency, "daily")
        self.assertEqual(r.recurrence_interval, 1)
        self.assertIsNone(r.recurrence_end_type)

    def test_every_2_days(self):
        r = parse("每2天上午9点吃药")
        self.assertEqual(r.recurrence_frequency, "daily")
        self.assertEqual(r.recurrence_interval, 2)
        self.assertEqual(r.time, "09:00")
        self.assertEqual(r.title, "吃药")

    def test_weekly_monday(self):
        # now = 2026-10-02 周五；最近周一 = 10-05
        r = parse("每周一上午9点开会")
        self.assertEqual(r.recurrence_frequency, "weekly")
        self.assertEqual(r.recurrence_interval, 1)
        self.assertEqual(r.date, "2026-10-05")
        self.assertEqual(r.time, "09:00")
        self.assertEqual(r.title, "开会")

    def test_every_2_weeks_friday(self):
        # now = 2026-10-02 周五；最近周五 = 今天（含今天）
        r = parse("每2周周五下午3点汇报")
        self.assertEqual(r.recurrence_frequency, "weekly")
        self.assertEqual(r.recurrence_interval, 2)
        self.assertEqual(r.date, "2026-10-02")
        self.assertEqual(r.time, "15:00")
        self.assertEqual(r.title, "汇报")

    def test_workday(self):
        r = parse("每个工作日早上8点晨会")
        self.assertEqual(r.recurrence_frequency, "workday")
        self.assertEqual(r.recurrence_interval, 1)
        self.assertEqual(r.time, "08:00")

    def test_weekend(self):
        r = parse("每个周末上午10点运动")
        self.assertEqual(r.recurrence_frequency, "weekend")
        self.assertEqual(r.time, "10:00")

    def test_monthly(self):
        r = parse("每月写总结")
        self.assertEqual(r.recurrence_frequency, "monthly")
        self.assertEqual(r.recurrence_interval, 1)

    def test_every_2_months(self):
        r = parse("每2个月检查一次")
        self.assertEqual(r.recurrence_frequency, "monthly")
        self.assertEqual(r.recurrence_interval, 2)

    def test_quarterly(self):
        r = parse("每季度整理资料")
        self.assertEqual(r.recurrence_frequency, "quarterly")
        self.assertEqual(r.recurrence_interval, 1)

    def test_yearly(self):
        r = parse("每年10月15日纪念日")
        self.assertEqual(r.recurrence_frequency, "yearly")
        self.assertEqual(r.date, "2026-10-15")

    def test_hourly_with_time(self):
        r = parse("上午9点开始每2小时喝水")
        self.assertEqual(r.recurrence_frequency, "hourly")
        self.assertEqual(r.recurrence_interval, 2)
        self.assertEqual(r.time, "09:00")
        self.assertEqual(r.title, "喝水")

    def test_hourly_without_time_errors(self):
        with self.assertRaises(QuickParseError):
            parse("每2小时喝水")

    def test_end_date(self):
        r = parse("每天晚上8点吃药 到2026-12-31结束")
        self.assertEqual(r.recurrence_frequency, "daily")
        self.assertEqual(r.recurrence_end_type, "on_date")
        self.assertEqual(r.recurrence_end_date, "2026-12-31")

    def test_end_date_before_start_errors(self):
        with self.assertRaises(QuickParseError):
            parse("从2026-10-10开始 每天跑步 到2026-10-05结束")

    def test_date_weekday_conflict_errors(self):
        # 2026-10-03 是周六，但要求每周一
        with self.assertRaises(QuickParseError):
            parse_quick_task("2026-10-03 每周一上午9点开会", now=NOW)

    def test_priority_with_recurrence(self):
        r = parse("每周一上午9点开会 !紧急")
        self.assertEqual(r.priority, "urgent")
        self.assertEqual(r.recurrence_frequency, "weekly")
        self.assertEqual(r.title, "开会")

    def test_title_cleanup_with_control_words(self):
        r = parse("从明天开始每周一上午9点开会 !紧急 到2026-12-31结束")
        self.assertEqual(r.title, "开会")
        self.assertEqual(r.date, "2026-10-05")  # 最近周一
        self.assertEqual(r.recurrence_frequency, "weekly")
        self.assertEqual(r.priority, "urgent")
        self.assertEqual(r.recurrence_end_date, "2026-12-31")


if __name__ == "__main__":
    unittest.main()
