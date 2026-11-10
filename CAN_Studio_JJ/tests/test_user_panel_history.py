import copy
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt5.QtCore import Qt, QEvent
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication
from src.user_panel_v2.window import UserPanelWindow
from src.user_panel_v2.config_dialog import WidgetConfigDialog


class HistoryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.panel = UserPanelWindow(SimpleNamespace(), {})
        self.panel._load_panel_data(dict(widgets=[dict(
            id=str(i), title=f"Tool {i}", widget_type="label", behavior="rx",
            row=i * 3, col=0, row_span=3, col_span=5, binding={}) for i in range(2)]))

    def tearDown(self):
        self.panel.close()
        self.panel.deleteLater()
        self.app.sendPostedEvents(None, QEvent.DeferredDelete)

    def test_multi_delete_undo_redo_with_keyboard(self):
        p = self.panel
        p.show()
        p.activateWindow()
        p.list_tools.item(1).setSelected(True)
        original = copy.deepcopy(p.widgets_config)
        p.list_tools.setFocus()
        self.app.processEvents()
        QTest.keyClick(p.list_tools, Qt.Key_Delete)
        self.assertEqual(p.widgets_config, [])
        QTest.keyClick(p.list_tools, Qt.Key_Z, Qt.ControlModifier)
        self.assertEqual(p.widgets_config, original)
        self.assertEqual(p.selected_widget_ids, {"0", "1"})
        QTest.keyClick(p.list_tools, Qt.Key_Y, Qt.ControlModifier)
        self.assertEqual(p.widgets_config, [])

    def test_batch_is_one_step_and_new_edit_clears_redo(self):
        p = self.panel
        p.list_tools.item(1).setSelected(True)
        original = copy.deepcopy(p.widgets_config)
        prop = p.properties
        prop.checks["col_span"].setChecked(True)
        prop.fields["col_span"].setValue(7)
        prop.apply_checked()
        self.assertEqual(len(p._undo_stack), 1)
        p.undo_edit()
        self.assertEqual(p.widgets_config, original)
        selected_id = p.selected_widget_id
        p.nudge_selected(1, 0)
        self.assertFalse(p._redo_stack)
        p.redo_edit()
        self.assertEqual(p._cfg_by_id(selected_id)["col"], 1)

    def test_limit_noop_mode_and_load(self):
        p = self.panel
        for i in range(55):
            p.widgets_config[0]["title"] = f"Revision {i}"
            p.rebuild_grid()
        self.assertEqual(len(p._undo_stack), 50)
        p.rebuild_grid()
        self.assertEqual(len(p._undo_stack), 50)
        p.mode = "run"
        p.undo_edit()
        self.assertEqual(p.widgets_config[0]["title"], "Revision 54")
        p.mode = "edit"
        for _ in range(60):
            p.undo_edit()
        self.assertEqual(p.widgets_config[0]["title"], "Revision 4")
        self.assertEqual(len(p._redo_stack), 50)
        for _ in range(60):
            p.redo_edit()
        self.assertEqual(p.widgets_config[0]["title"], "Revision 54")
        p._load_panel_data(copy.deepcopy(p._panel_data()))
        self.assertFalse(p._undo_stack)
        self.assertFalse(p._redo_stack)

    def test_creation_preview_is_one_step_cancel_is_none(self):
        p = self.panel
        original = copy.deepcopy(p.widgets_config)
        for accepted in (False, True):
            def simulate(dialog):
                cfg = dialog.get_config()
                cfg["title"] = "Preview"
                dialog.config_changed.emit(cfg)
                cfg = copy.deepcopy(cfg)
                cfg["title"] = "Preview 2"
                dialog.config_changed.emit(cfg)
                return dialog.Accepted if accepted else dialog.Rejected
            with patch.object(WidgetConfigDialog, "exec_", simulate):
                p.add_widget("rx")
            self.assertEqual(len(p._undo_stack), int(accepted))
        p.undo_edit()
        self.assertEqual(p.widgets_config, original)

    def test_text_undo_stays_in_editor_and_speed_is_not_restored(self):
        p = self.panel
        p.nudge_selected(1, 0)
        p.show()
        p.activateWindow()
        text = p.properties.editor.edit_title
        text.setFocus()
        self.app.processEvents()
        text.selectAll()
        QTest.keyClicks(text, "Draft")
        QTest.keyClick(text, Qt.Key_Z, Qt.ControlModifier)
        self.assertEqual(p.widgets_config[0]["col"], 1)
        self.assertNotEqual(text.text(), "Draft")
        p.channel_settings = {"1": {"bitrate": 250000}}
        p.undo_edit()
        self.assertEqual(p.channel_settings["1"]["bitrate"], 250000)
