"""灵活日期/时间本地解析器单元测试。

运行方式：
    cd <PROJECT_ROOT>
    .venv\\Scripts\\python -m unittest test_datetime_parser
"""

import unittest
from datetime import datetime

from datetime_parser import parse_flexible_date, parse_flexible_time, parse_flexible_datetime, DateTimeParseError

NOW = datetime(2026, 10, 3, 0, 15)


class DateParserTest(unittest.TestCase):

    def test_iso_date(self):
        self.assertEqual(parse_flexible_date("2026-10-03", NOW), "2026-10-03")

    def test_slash_date(self):
        self.assertEqual(parse_flexible_date("2026/10/3", NOW), "2026-10-03")

    def test_dot_date(self):
        self.assertEqual(parse_flexible_date("2026.10.3", NOW), "2026-10-03")

    def test_cn_full_date(self):
        self.assertEqual(parse_flexible_date("2026年10月3号", NOW), "2026-10-03")

    def test_cn_month_day(self):
        self.assertEqual(parse_flexible_date("10月3日", NOW), "2026-10-03")

    def test_short_slash(self):
        self.assertEqual(parse_flexible_date("10/3", NOW), "2026-10-03")

    def test_short_dot(self):
        self.assertEqual(parse_flexible_date("10.3", NOW), "2026-10-03")

    def test_fullwidth_slash_with_spaces(self):
        self.assertEqual(parse_flexible_date("2026 ／ 10 ／ 03", NOW), "2026-10-03")

    def test_fullwidth_slash_no_spaces(self):
        self.assertEqual(parse_flexible_date("2026／10／3", NOW), "2026-10-03")

    def test_halfwidth_slash_with_spaces(self):
        self.assertEqual(parse_flexible_date("2026 / 10 / 03", NOW), "2026-10-03")

    def test_dot_with_spaces(self):
        self.assertEqual(parse_flexible_date("2026 . 10 . 03", NOW), "2026-10-03")

    def test_today(self):
        self.assertEqual(parse_flexible_date("今天", NOW), "2026-10-03")

    def test_tomorrow(self):
        self.assertEqual(parse_flexible_date("明天", NOW), "2026-10-04")

    def test_day_after(self):
        self.assertEqual(parse_flexible_date("后天", NOW), "2026-10-05")

    def test_invalid_date(self):
        with self.assertRaises(DateTimeParseError):
            parse_flexible_date("2026-02-31", NOW)


class TimeParserTest(unittest.TestCase):

    def test_hhmm(self):
        self.assertEqual(parse_flexible_time("18:30"), "18:30")

    def test_fullwidth_colon(self):
        self.assertEqual(parse_flexible_time("18：30"), "18:30")

    def test_4digit(self):
        self.assertEqual(parse_flexible_time("1830"), "18:30")

    def test_dot_time(self):
        self.assertEqual(parse_flexible_time("18.30"), "18:30")

    def test_pm(self):
        self.assertEqual(parse_flexible_time("6:30 PM"), "18:30")

    def test_cn_pm(self):
        self.assertEqual(parse_flexible_time("下午6:30"), "18:30")

    def test_evening_8(self):
        self.assertEqual(parse_flexible_time("晚上8点"), "20:00")

    def test_evening_8_half(self):
        self.assertEqual(parse_flexible_time("晚上8点半"), "20:30")

    def test_morning_9(self):
        self.assertEqual(parse_flexible_time("上午9点"), "09:00")

    def test_9_half(self):
        self.assertEqual(parse_flexible_time("9点半"), "09:30")

    def test_invalid_time(self):
        with self.assertRaises(DateTimeParseError):
            parse_flexible_time("25:90")



class DateTimeCombineTest(unittest.TestCase):
    """统一的日期时间合并解析。"""

    def test_date_and_time(self):
        r = parse_flexible_datetime("2026 / 10 / 03 18:30", NOW)
        self.assertEqual(r.date, "2026-10-03")
        self.assertEqual(r.time, "18:30")
        self.assertTrue(r.date_recognized)
        self.assertTrue(r.time_recognized)

    def test_tomorrow_afternoon(self):
        r = parse_flexible_datetime("明天下午3点", NOW)
        self.assertEqual(r.date, "2026-10-04")
        self.assertEqual(r.time, "15:00")

    def test_cn_date_and_time(self):
        r = parse_flexible_datetime("10月15号晚上八点半", NOW)
        self.assertEqual(r.date, "2026-10-15")
        self.assertEqual(r.time, "20:30")

    def test_plain_text_no_datetime(self):
        r = parse_flexible_datetime("买书", NOW)
        self.assertFalse(r.date_recognized)
        self.assertFalse(r.time_recognized)


if __name__ == "__main__":
    unittest.main()
