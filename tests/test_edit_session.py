"""Calendar 编辑状态生命周期测试（第 12 阶段 Bug 修复）。

重点：不再出现 StreamlitWidgetAlreadyInstantiatedError；
编辑 widget state 只在「widget 创建之前」被写入。

使用 AppTest + 临时数据库，不碰真实 todo.db。
"""

import os
import tempfile
import unittest
from pathlib import Path

import database
import edit_session
import services
import ui.icons as icons

from streamlit.testing.v1 import AppTest

from tests import tests_env  # noqa: F401  必须早于项目模块导入（隔离数据目录）

PROJECT_DIR = Path(__file__).resolve().parent.parent
from datetime import date as _date
from datetime import timedelta
APP_PATH = PROJECT_DIR / "app.py"

# 固定日期基准：重复系列的 occurrence 断言全部基于这一天；
# 与真实 today 解耦，避免日期翻转后变成假失败。
# 时间基准：D0 = 夹具当天，D1 = 次日。断言全部相对它们计算，
# 与真实日期解耦（日期翻转不会再产生假失败）。
D0 = _date.today().isoformat()
D1 = (_date.today() + timedelta(days=1)).isoformat()
FIXTURE_DATE = D0

CAL_KEY = "todo_interactive_calendar"


class _Base(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mktemp(suffix=".db")
        database.DB_PATH = self.tmp
        database.init_database()
        from datetime import date as _date
        today = _date.today().isoformat()
        services.create_task("高数", date=today, time="14:00", duration_minutes=90)
        services.create_task("每天吃药", date=D0, time="09:00", duration_minutes=30,
                             is_recurring=True, recurrence_frequency="daily",
                             recurrence_interval=1)
        self.plain_id = [t["id"] for t in services.get_tasks() if t["title"] == "高数"][0]
        self.rec_id = [t["id"] for t in services.get_tasks() if t["title"] == "每天吃药"][0]

    def tearDown(self):
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(self.tmp + suffix)
            except OSError:
                pass

    # ---- helpers ----
    def cal_key(self, at):
        """当前组件 key（由内容派生，不再固定）。"""
        return at.session_state.get("_cal_last_component_key") or CAL_KEY

    def calendar_app(self):
        """启动 app 并切到日历视图（Sidebar 导航）。"""
        at = AppTest.from_file(str(APP_PATH), default_timeout=60)
        at.run()
        at.session_state["nav"] = icons.NAV_CALENDAR
        at.run()
        return at

    def click_event(self, at, event_id):
        at.session_state[self.cal_key(at)] = {
            "callback": "eventClick",
            "eventClick": {"event": {"id": event_id}},
        }
        at.run()
        return at

    def drag_event(self, at, event_id, start, end, all_day=False):
        at.session_state[self.cal_key(at)] = {
            "callback": "eventChange",
            "eventChange": {"id": event_id, "start": start, "end": end, "allDay": all_day},
        }
        at.run()
        return at

    def click_blank(self, at, date_iso):
        at.session_state[self.cal_key(at)] = {
            "callback": "dateClick",
            "dateClick": {"allDay": False, "date": date_iso},
        }
        at.run()
        return at

    def drag_select(self, at, start_iso, end_iso):
        at.session_state[self.cal_key(at)] = {
            "callback": "select",
            "select": {"allDay": False, "start": start_iso, "end": end_iso},
        }
        at.run()
        return at

    def assert_no_exception(self, at):
        self.assertEqual([str(e.value) for e in at.exception], [],
                         msg=f"unexpected exception(s): {[e.type for e in at.exception]}")

    def assert_no_instantiation_error(self, at):
        for e in at.exception:
            self.assertNotEqual(e.type, "StreamlitWidgetAlreadyInstantiatedError",
                                msg=str(e.value))


class CalendarEditOpenTest(_Base):

    def test_01_click_plain_task_opens_dialog(self):
        at = self.calendar_app()
        self.click_event(at, f"task:{self.plain_id}")
        self.assert_no_exception(at)
        self.assertEqual(at.session_state.get("editing_task_id"), self.plain_id)
        self.assertEqual(at.session_state.get(f"edit_title_{self.plain_id}"), "高数")
        # pending 请求已被消费
        self.assertIsNone(at.session_state.get(edit_session.PENDING_KEY))

    def test_02_close_then_click_same_task_again(self):
        at = self.calendar_app()
        self.click_event(at, f"task:{self.plain_id}")
        self.assertEqual(at.session_state.get("editing_task_id"), self.plain_id)

        # 取消关闭
        at.button(key=f"edit_cancel_{self.plain_id}").click().run()
        self.assert_no_exception(at)
        self.assertIsNone(at.session_state.get("editing_task_id"))
        self.assertNotIn(f"edit_title_{self.plain_id}", at.session_state)

        # 再次点击同一任务 → 必须仍能打开
        self.click_event(at, f"task:{self.plain_id}")
        self.assert_no_exception(at)
        self.assertEqual(at.session_state.get("editing_task_id"), self.plain_id)
        self.assertEqual(at.session_state.get(f"edit_title_{self.plain_id}"), "高数")

    def test_03_click_recurring_occurrence_opens_occurrence_edit(self):
        at = self.calendar_app()
        self.click_event(at, f"occurrence:{self.rec_id}:{D0}T09:00")
        self.assert_no_exception(at)
        self.assertEqual(at.session_state.get("editing_task_id"), self.rec_id)
        self.assertEqual(at.session_state.get(f"edit_has_recurring_{self.rec_id}"), True)
        self.assertEqual(at.session_state.get(f"edit_recurrence_frequency_{self.rec_id}"), "每天")
        # 现在是「这一次」模式：弹窗里默认「仅修改这一次」
        self.assertEqual(at.session_state.get(edit_session.ACTIVE_MODE_KEY), "event")
        self.assertEqual(
            at.session_state.get(edit_session.ACTIVE_OCCURRENCE_KEY),
            f"{D0}T09:00",
        )

    def test_04_rerun_after_click_does_not_reinit(self):
        """eventClick 回放在后续 rerun 中不应重复初始化。"""
        at = self.calendar_app()
        calls = []
        original = edit_session.request_edit
        edit_session.request_edit = lambda *a, **k: (calls.append(a), original(*a, **k))[1]
        try:
            self.click_event(at, f"task:{self.plain_id}")
            self.assertEqual(len(calls), 1, "一次点击只应产生一次编辑请求")
            # 再来一次普通 rerun
            at.run()
            self.assertEqual(len(calls), 1, "rerun 不应重复产生编辑请求")
        finally:
            edit_session.request_edit = original

    def test_05_payload_is_consumed_and_not_replayed(self):
        """点击被消费后组件状态被清空，后续 rerun 不再重复处理。"""
        at = self.calendar_app()
        calls = []
        original = edit_session.request_edit
        edit_session.request_edit = lambda *a, **k: (calls.append(a), original(*a, **k))[1]
        try:
            self.click_event(at, f"task:{self.plain_id}")
            self.assertEqual(len(calls), 1)
            at.run()
            at.run()
            self.assertEqual(len(calls), 1, "组件状态已清空，不应重复处理")
            # 组件状态被清空（值为 None / {}），确认旧回调已不再投递
            self.assertIn(at.session_state.get(self.cal_key(at)), (None, {}),
                          "回调应被消费并清空")
        finally:
            edit_session.request_edit = original

    def test_05b_replayed_payload_ignored_by_fingerprint(self):
        """守卫：即使组件重复投递同一 payload，也不会再次打开编辑器。"""
        at = self.calendar_app()
        calls = []
        original = edit_session.request_edit
        edit_session.request_edit = lambda *a, **k: (calls.append(a), original(*a, **k))[1]
        try:
            at.session_state["_cal_consumed_click"] = f"eventClick|{self.plain_id}|None"
            at.session_state["_clear_calendar_callback"] = False
            at.session_state[self.cal_key(at)] = {
                "callback": "eventClick",
                "eventClick": {"event": {"id": f"task:{self.plain_id}"}},
            }
            at.run()
            self.assertEqual(len(calls), 0, "重复投递不应再次打开编辑器")
        finally:
            edit_session.request_edit = original

    def test_06_no_widget_already_instantiated_error(self):
        at = self.calendar_app()
        self.click_event(at, f"task:{self.plain_id}")
        self.assert_no_instantiation_error(at)
        at.run()
        self.assert_no_instantiation_error(at)
        at.button(key=f"edit_cancel_{self.plain_id}").click().run()
        self.assert_no_instantiation_error(at)


class CalendarEditSaveTest(_Base):

    def test_07_save_updates_database(self):
        at = self.calendar_app()
        self.click_event(at, f"task:{self.plain_id}")
        at.text_input(key=f"edit_title_{self.plain_id}").set_value("高等数学")
        at.button(key=f"edit_save_{self.plain_id}").click().run()
        self.assert_no_exception(at)
        self.assertEqual(services.get_task(self.plain_id)["title"], "高等数学")
        self.assertIsNone(at.session_state.get("editing_task_id"))
        self.assertNotIn(f"edit_title_{self.plain_id}", at.session_state)

    def test_08_cancel_does_not_change_database(self):
        at = self.calendar_app()
        self.click_event(at, f"task:{self.plain_id}")
        at.text_input(key=f"edit_title_{self.plain_id}").set_value("不应保存")
        at.button(key=f"edit_cancel_{self.plain_id}").click().run()
        self.assert_no_exception(at)
        self.assertEqual(services.get_task(self.plain_id)["title"], "高数")

    def test_09_edit_recurring_series_save_reflects_in_calendar(self):
        at = self.calendar_app()
        self.click_event(at, f"occurrence:{self.rec_id}:{D0}T09:00")
        # 现在默认进「仅修改这一次」，改标题属于系列操作，先切到整个系列
        at.radio(key=f"edit_scope_{self.rec_id}").set_value("修改整个系列").run()
        at.text_input(key=f"edit_title_{self.rec_id}").set_value("每天吃药(改)")
        at.button(key=f"edit_save_{self.rec_id}").click().run()
        self.assert_no_exception(at)
        task = services.get_task(self.rec_id)
        self.assertEqual(task["title"], "每天吃药(改)")
        self.assertTrue(task["is_recurring"])
        items = services.get_items_for_range(D1, D1)
        self.assertTrue(any(i.title == "每天吃药(改)" for i in items))

    def test_10_today_card_edit_still_works(self):
        """今日任务卡的 ⋯ 菜单 → 编辑。"""
        at = AppTest.from_file(str(APP_PATH), default_timeout=60)
        at.run()
        keys = [b.key for b in at.button
                if b.key and b.key.startswith("m_edit_today_")]
        self.assertTrue(keys, "未找到今日任务卡的编辑菜单项")
        at.button(key=keys[0]).click().run()
        self.assert_no_exception(at)
        tid = at.session_state.get("editing_task_id")
        self.assertIsNotNone(tid)
        self.assertTrue(at.session_state.get(f"edit_title_{tid}"))


class CalendarDragOnceTest(_Base):

    def test_11_drag_saves_once(self):
        at = self.calendar_app()
        calls = []
        original = services.move_plain_task
        services.move_plain_task = lambda *a, **k: (calls.append((a, k)), original(*a, **k))[1]
        try:
            self.drag_event(at, f"task:{self.plain_id}",
                            f"{D0}T16:00:00", f"{D0}T17:00:00")
            self.assertEqual(services.get_task(self.plain_id)["time"], "16:00")
            self.assertEqual(len(calls), 1, "一次拖动只应写库一次")
            # 再次 rerun，不应重复写库
            at.run()
            self.assertEqual(len(calls), 1, "rerun 不应重复写库")
        finally:
            services.move_plain_task = original

    def test_12_resize_saves_once(self):
        at = self.calendar_app()
        calls = []
        original = services.move_plain_task
        services.move_plain_task = lambda *a, **k: (calls.append((a, k)), original(*a, **k))[1]
        try:
            self.drag_event(at, f"task:{self.plain_id}",
                            f"{D0}T14:00:00", f"{D0}T18:00:00")
            self.assertEqual(services.get_task(self.plain_id)["duration_minutes"], 240)
            self.assertEqual(len(calls), 1)
        finally:
            services.move_plain_task = original

    def test_13_recurring_drag_creates_pending_change_not_db_write(self):
        at = self.calendar_app()
        self.drag_event(at, f"occurrence:{self.rec_id}:{D0}T09:00",
                        f"{D1}T14:00:00", f"{D1}T15:00:00")
        self.assert_no_exception(at)
        # 未弹窗确认前不写 override
        self.assertIsNone(services.get_occurrence_override(self.rec_id, f"{D0}T09:00"))
        # master 未被修改
        self.assertEqual(services.get_task(self.rec_id)["time"], "09:00")


class CalendarRefreshTest(_Base):
    """组件 key 必须随数据/视图/日期变化，从而触发日历重新初始化。"""

    def test_key_stable_when_nothing_changes(self):
        at = self.calendar_app()
        k1 = at.session_state.get("_cal_last_component_key")
        self.assertIsNotNone(k1)
        at.run()
        self.assertEqual(at.session_state.get("_cal_last_component_key"), k1,
                         "无关 rerun 不应更换 key（避免视图/滚动被重置）")

    def test_key_changes_when_events_change(self):
        at = self.calendar_app()
        k1 = at.session_state.get("_cal_last_component_key")
        services.create_task("新任务", date=(_date.today() + timedelta(days=4)).isoformat(), time="10:00",
                             duration_minutes=30)
        at.run()
        k2 = at.session_state.get("_cal_last_component_key")
        self.assertNotEqual(k1, k2, "数据变化必须更换 key，否则日历不会刷新")

    def test_key_changes_when_view_changes(self):
        at = self.calendar_app()
        k1 = at.session_state.get("_cal_last_component_key")
        at.segmented_control(key="cal_view_seg").set_value("周").run()
        k2 = at.session_state.get("_cal_last_component_key")
        self.assertNotEqual(k1, k2, "切换视图必须更换 key")

    def test_key_changes_when_date_changes(self):
        at = self.calendar_app()
        k1 = at.session_state.get("_cal_last_component_key")
        at.button(key="cal_next").click().run()
        k2 = at.session_state.get("_cal_last_component_key")
        self.assertNotEqual(k1, k2, "翻页必须更换 key")

    def test_edit_save_changes_key(self):
        """编辑保存后 key 变化 → 日历重新初始化并显示新数据。"""
        at = self.calendar_app()
        k1 = at.session_state.get("_cal_last_component_key")
        self.click_event(at, f"task:{self.plain_id}")
        at.text_input(key=f"edit_title_{self.plain_id}").set_value("改过的标题")
        at.button(key=f"edit_save_{self.plain_id}").click().run()
        k2 = at.session_state.get("_cal_last_component_key")
        self.assertNotEqual(k1, k2)


class BlankClickActionTest(_Base):

    def setUp(self):
        super().setUp()
        import calendar_settings
        from unittest import mock
        self._cfg_tmp = tempfile.mktemp(suffix=".json")
        self._patcher = mock.patch.object(calendar_settings, "CONFIG_PATH",
                                          Path(self._cfg_tmp))
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        try:
            os.remove(self._cfg_tmp)
        except OSError:
            pass
        super().tearDown()

    def _set(self, **kw):
        import calendar_settings
        cfg = calendar_settings.load_calendar_config()
        cfg.update(kw)
        calendar_settings.save_calendar_config(cfg)

    def test_blank_click_default_is_ignored(self):
        """默认：单击空白不弹窗（避免误触）。"""
        at = self.calendar_app()
        calls = []
        import app as app_module
        original = app_module.create_dialog
        app_module.create_dialog = lambda *a, **k: calls.append(1)
        try:
            self.click_blank(at, f"{D0}T15:00:00")
            self.assertEqual(calls, [], "默认不应因单击空白而弹窗")
        finally:
            app_module.create_dialog = original

    def test_blank_click_creates_when_enabled(self):
        self._set(blank_click_action="create")
        at = self.calendar_app()
        self.click_blank(at, f"{D0}T15:00:00")
        self.assert_no_exception(at)
        self.assertEqual(at.session_state.get("create_mode"), "详细模式")
        self.assertEqual(at.session_state.get("detail_time").strftime("%H:%M"), "15:00")
        # 弹窗内应标明来源，避免与「编辑任务」混淆
        self.assertIn("来自日历", str(at.session_state.get("_calendar_create_origin")))

    def test_drag_select_creates_by_default(self):
        at = self.calendar_app()
        self.drag_select(at, f"{D0}T20:00:00", f"{D0}T21:30:00")
        self.assert_no_exception(at)
        self.assertEqual(at.session_state.get("create_mode"), "详细模式")
        self.assertEqual(at.session_state.get("detail_time").strftime("%H:%M"), "20:00")
        self.assertIn("90 分钟", str(at.session_state.get("_calendar_create_origin")))

    def test_drag_select_can_be_disabled(self):
        self._set(drag_select_action="ignore")
        at = self.calendar_app()
        calls = []
        import app as app_module
        original = app_module.create_dialog
        app_module.create_dialog = lambda *a, **k: calls.append(1)
        try:
            self.drag_select(at, f"{D0}T20:00:00", f"{D0}T21:30:00")
            self.assertEqual(calls, [], "关闭拖选后不应弹窗")
        finally:
            app_module.create_dialog = original

    def test_last_callback_recorded(self):
        at = self.calendar_app()
        self.click_blank(at, f"{D0}T15:00:00")
        self.assertIn("dateClick", str(at.session_state.get("_cal_last_callback")))


class EditSessionUnitTest(_Base):
    """edit_session 状态机的纯逻辑测试（不依赖 widget 实例化）。"""

    def test_widget_keys_cover_all_edit_fields(self):
        keys = edit_session.widget_keys(21)
        for expected in ["edit_title_21", "edit_description_21", "edit_url_21",
                         "edit_has_date_21", "edit_date_21", "edit_has_time_21",
                         "edit_time_21", "edit_duration_21_minutes", "edit_priority_21",
                         "edit_has_recurring_21", "edit_recurrence_frequency_21",
                         "edit_recurrence_interval_21", "edit_recurrence_end_type_21",
                         "edit_recurrence_end_date_21"]:
            self.assertIn(expected, keys)

    def test_priority_reverse_matches_app(self):
        import app
        self.assertEqual(edit_session.PRIORITY_REVERSE, app.PRIORITY_REVERSE)

    def test_recurrence_labels_match_app_options(self):
        import app
        for label in edit_session.RECURRENCE_LABELS.values():
            self.assertIn(label, app.RECURRENCE_OPTIONS)


class OccurrenceEditTest(_Base):
    """「仅修改这一次」：只写 occurrence override，绝不动系列行。"""

    def _occ_key(self):
        from datetime import date as _date
        return f"{D0}T09:00"

    def _open_occurrence(self, at=None):
        at = at or self.calendar_app()
        self.click_event(at, f"occurrence:{self.rec_id}:{self._occ_key()}")
        self.assert_no_exception(at)
        return at

    def test_20_defaults_to_this_occurrence_only(self):
        at = self._open_occurrence()
        self.assertEqual(at.session_state.get(edit_session.ACTIVE_MODE_KEY), "event")
        self.assertEqual(at.session_state.get(edit_session.ACTIVE_OCCURRENCE_KEY),
                         self._occ_key())
        self.assertEqual(at.radio(key=f"edit_scope_{self.rec_id}").value, "仅修改这一次")
        # 系列级字段在「仅改这一次」下不可编辑
        self.assertTrue(at.text_input(key=f"edit_title_{self.rec_id}").disabled)
        self.assertTrue(at.text_input(key=f"edit_url_{self.rec_id}").disabled)
        # 日期 / 时间 / 时长可改
        self.assertFalse(at.time_input(key=f"edit_time_{self.rec_id}").disabled)

    def test_21_save_writes_override_and_leaves_series_untouched(self):
        at = self._open_occurrence()
        from datetime import time as _time
        at.time_input(key=f"edit_time_{self.rec_id}").set_value(_time(10, 30))
        at.button(key=f"edit_save_{self.rec_id}").click().run()
        self.assert_no_exception(at)

        ov = services.get_occurrence_override(self.rec_id, self._occ_key())
        self.assertIsNotNone(ov, "应当写入一条 occurrence override")
        self.assertEqual(ov["override_time"], "10:30")

        series = services.get_task(self.rec_id)
        self.assertEqual(series["time"], "09:00", "系列开始时间不应被改动")
        self.assertEqual(series["title"], "每天吃药", "系列标题不应被改动")
        self.assertIsNone(at.session_state.get("editing_task_id"))

    def test_22_switching_to_series_scope_still_edits_series(self):
        at = self._open_occurrence()
        at.radio(key=f"edit_scope_{self.rec_id}").set_value("修改整个系列").run()
        self.assert_no_exception(at)
        at.text_input(key=f"edit_title_{self.rec_id}").set_value("每天吃药（新）")
        at.button(key=f"edit_save_{self.rec_id}").click().run()
        self.assert_no_exception(at)

        self.assertEqual(services.get_task(self.rec_id)["title"], "每天吃药（新）")
        self.assertIsNone(services.get_occurrence_override(self.rec_id, self._occ_key()),
                          "改整个系列时不应写 occurrence override")

    def test_23_existing_override_is_loaded_into_dialog(self):
        services.set_occurrence_override(self.rec_id, self._occ_key(), time="11:15")
        at = self._open_occurrence()
        shown = at.time_input(key=f"edit_time_{self.rec_id}").value
        self.assertEqual(shown.strftime("%H:%M"), "11:15",
                         "弹窗里应显示这一次的实际时间，而不是系列时间")

    def test_24_clearing_override_falls_back_to_series(self):
        services.set_occurrence_override(self.rec_id, self._occ_key(), time="11:15")
        at = self._open_occurrence()
        at.checkbox(key=f"edit_has_time_{self.rec_id}").set_value(False)
        at.button(key=f"edit_save_{self.rec_id}").click().run()
        self.assert_no_exception(at)

        # 取消勾选「设置时间」= 这一次不再覆盖时间，回退到系列值
        ov = services.get_occurrence_override(self.rec_id, self._occ_key())
        if ov is not None:
            self.assertIsNone(ov["override_time"], "取消勾选后不应残留时间覆盖")
        self.assertEqual(services.get_task(self.rec_id)["time"], "09:00",
                         "系列时间不应被改动")

        # 与 _occ_key() 用同一个日期基准（重复系列建在 FIXTURE_DATE）
        mine = [i for i in services.get_items_for_date(FIXTURE_DATE)
                if i.task_id == self.rec_id and i.occurrence_key == self._occ_key()]
        self.assertTrue(mine, "应在系列的这一天找到这一次")
        self.assertEqual(mine[0].time, "09:00", "这一次的时间应回退到系列值")

    def test_25_task_card_edit_uses_event_mode_for_occurrence(self):
        """任务卡 ⋯ → 编辑 也必须进「这一次」模式（不只是日历那条路）。"""
        at = AppTest.from_file(str(APP_PATH), default_timeout=60)
        at.run()
        at.session_state["nav"] = icons.NAV_TODAY
        at.run()
        self.assert_no_exception(at)

        keys = [k for k in at.session_state.keys()
                if k.startswith("m_edit_") and str(self.rec_id) in k]
        self.assertTrue(
            keys,
            "没找到重复任务卡片的编辑按钮，现有 m_edit_* key: "
            + str([k for k in at.session_state.keys() if k.startswith("m_edit_")]),
        )
        at.button(key=keys[0]).click().run()
        self.assert_no_exception(at)
        self.assertEqual(at.session_state.get(edit_session.ACTIVE_MODE_KEY), "event")
        self.assertIsNotNone(at.session_state.get(edit_session.ACTIVE_OCCURRENCE_KEY))


if __name__ == "__main__":
    unittest.main()
