import copy
import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt5.QtCore import QEvent, Qt
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QDialog

from src.user_panel_v2.color_picker import ColorPicker
from src.user_panel_v2.config_dialog import WidgetConfigDialog
from src.user_panel_v2.label_color_dialog import LabelColorRulesDialog
from src.user_panel_v2.window import UserPanelWindow


class ColorPreviewTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.addCleanup(lambda: self.app.sendPostedEvents(None, QEvent.DeferredDelete))

    def panel(self, kind='shape_rect', binding=None):
        panel = UserPanelWindow(SimpleNamespace(), {})
        self.addCleanup(panel.deleteLater)
        self.addCleanup(panel.close)
        panel._load_panel_data(dict(grid=dict(rows=24, cols=40), widgets=[
            dict(id='a', widget_type=kind, behavior='rx' if kind == 'label' else 'none',
                 title='Original', row_span=12, col_span=20, binding=copy.deepcopy(binding or {})),
            dict(id='b', widget_type='shape_rect', col=22, row_span=6, col_span=12)]))
        panel.selected_widget_id = 'a'
        panel.selected_widget_ids = {'a'}
        panel.properties.refresh(force=True)
        return panel

    def test_recommended_colors_show_names_and_custom_colors_show_hex(self):
        picker = ColorPicker('#e8f1ff')
        self.addCleanup(picker.deleteLater)
        self.assertEqual(picker.button.text(), '색 파란색')
        picker.recommended_buttons[3].click()
        self.assertEqual(picker.button.text(), '색 초록색')
        self.assertEqual(picker.text(), '#DCFCE7')
        picker.setText('#123456')
        self.assertEqual(picker.button.text(), '색 #123456')
        picker.setText('#ffedd5')
        self.assertEqual(picker.button.text(), '색 주황색')

    def test_preview_changes_rendering_but_not_saved_data_or_history(self):
        panel = self.panel()
        editor = panel.properties.editor
        before = copy.deepcopy(panel._panel_data())
        history = copy.deepcopy(panel._history_snapshot())
        undo = len(panel._undo_stack)
        editor.edit_fill_color.recommended_buttons[3].click()
        self.assertIn('#dcfce7', panel.widget_controls['a'].styleSheet())
        self.assertEqual(panel._panel_data(), before)
        self.assertEqual(panel._history_snapshot(), history)
        self.assertEqual(len(panel._undo_stack), undo)
        self.assertIs(panel.properties.editor, editor)
        panel.properties.refresh()  # Ordinary display refresh must keep the preview.
        self.assertIn('#dcfce7', panel.widget_controls['a'].styleSheet())

    def test_apply_commits_once_and_undo_restores_original(self):
        panel = self.panel()
        editor = panel.properties.editor
        before = copy.deepcopy(panel.widgets_config)
        undo = len(panel._undo_stack)
        editor.edit_fill_color.recommended_buttons[3].click()
        editor.edit_stroke_color.recommended_buttons[7].click()
        editor.accept()
        binding = panel._cfg_by_id('a')['binding']
        self.assertTrue(binding['fill'])
        self.assertEqual(binding['fill_color'], '#DCFCE7')
        self.assertEqual(binding['stroke_color'], '#EDE9FE')
        self.assertEqual(len(panel._undo_stack), undo + 1)
        self.assertIsNone(panel.properties._preview_id)
        panel.undo_edit()
        self.assertEqual(panel.widgets_config, before)
        self.assertIn('background:transparent', panel.widget_controls['a'].styleSheet())

    def test_selecting_another_tool_discards_preview(self):
        panel = self.panel()
        original = panel.widget_controls['a'].styleSheet()
        panel.properties.editor.edit_fill_color.recommended_buttons[3].click()
        panel.selected_widget_id = 'b'
        panel.selected_widget_ids = {'b'}
        panel._refresh_selection_ui()
        self.assertEqual(panel.widget_controls['a'].styleSheet(), original)
        self.assertFalse(panel._cfg_by_id('a')['binding']['fill'])
        self.assertIsNone(panel.properties._preview_id)

    def test_outside_click_reverts_and_palette_click_keeps_preview(self):
        panel = self.panel()
        panel.show()
        self.app.processEvents()
        editor = panel.properties.editor
        original = panel.widget_controls['a'].styleSheet()
        editor.edit_title.setText('Pending title')
        editor.edit_fill_color.recommended_buttons[3].click()
        # Real Qt events inside a palette remain part of the edit transaction.
        QTest.mouseClick(editor.edit_fill_color.recommended_buttons[6], Qt.LeftButton)
        self.assertIn('#fce7f3', panel.widget_controls['a'].styleSheet())
        QTest.mouseClick(panel.system_log.text.viewport(), Qt.LeftButton)
        self.assertEqual(panel.widget_controls['a'].styleSheet(), original)
        self.assertFalse(editor.chk_fill.isChecked())
        self.assertEqual(editor.edit_title.text(), 'Pending title')
        self.assertEqual(panel._cfg_by_id('a')['title'], 'Original')

    def test_group_and_tab_surfaces_revert_including_outer_frame(self):
        for kind in ('group_box', 'tab_container', 'shape_line'):
            with self.subTest(kind=kind):
                panel = self.panel(kind)
                editor = panel.properties.editor
                original = panel.widget_controls['a'].styleSheet()
                frame_style = panel.widget_frames['a'].styleSheet()
                if kind == 'shape_line':
                    editor.edit_stroke_color.recommended_buttons[3].click()
                else:
                    editor.edit_fill_color.recommended_buttons[3].click()
                    self.assertIn('#dcfce7', panel._container_pages[('a', 0)].canvas.styleSheet())
                self.assertIn('#dcfce7', panel.widget_controls['a'].styleSheet())
                panel.properties.cancel_color_preview()
                self.assertEqual(panel.widget_controls['a'].styleSheet(), original)
                self.assertEqual(panel.widget_frames['a'].styleSheet(), frame_style)
                if kind != 'shape_line':
                    self.assertNotIn('#dcfce7', panel._container_pages[('a', 0)].canvas.styleSheet())

    def test_mode_change_and_grid_operation_discard_preview(self):
        for action in ('mode', 'rebuild', 'close'):
            with self.subTest(action=action):
                panel = self.panel()
                original = panel.widget_controls['a'].styleSheet()
                before = copy.deepcopy(panel.widgets_config)
                panel.properties.editor.edit_fill_color.recommended_buttons[3].click()
                if action == 'mode':
                    panel.set_mode('standby')
                elif action == 'rebuild':
                    panel.rebuild_grid()
                else:
                    panel.close()
                self.assertEqual(panel.widget_controls['a'].styleSheet(), original)
                self.assertEqual(panel.widgets_config, before)
                self.assertIsNone(panel.properties._preview_id)

    def test_detached_window_previews_and_cancel_or_apply(self):
        panel = self.panel()
        original = panel.widget_controls['a'].styleSheet()
        def cancel(dialog):
            dialog.edit_fill_color.recommended_buttons[6].click()
            self.assertIn('#fce7f3', panel.widget_controls['a'].styleSheet())
            self.assertFalse(panel._cfg_by_id('a')['binding']['fill'])
            return QDialog.Rejected
        with patch.object(WidgetConfigDialog, 'exec_', cancel):
            panel.properties.btn_open_window.click()
        self.assertEqual(panel.widget_controls['a'].styleSheet(), original)
        def accept(dialog):
            dialog.edit_fill_color.recommended_buttons[3].click()
            self.assertIn('#dcfce7', panel.widget_controls['a'].styleSheet())
            return QDialog.Accepted
        with patch.object(WidgetConfigDialog, 'exec_', accept):
            panel.properties.btn_open_window.click()
        self.assertEqual(panel._cfg_by_id('a')['binding']['fill_color'], '#DCFCE7')
        self.assertIn('#dcfce7', panel.widget_controls['a'].styleSheet())

    def test_rx_rule_picker_previews_then_restores_on_cancel(self):
        rule = dict(enabled=True, source='value', op='ge', a=10, b=100,
                    target='background', color='#E8F1FF')
        panel = self.panel('label', dict(label_color_rules=[rule]))
        panel._update_widget_value(panel._cfg_by_id('a'), 20, force=True)
        editor = panel.properties.editor
        def cancel(dialog):
            dialog.editor._rows[0]['color'].recommended_buttons[3].click()
            self.assertIn('#dcfce7', panel.widget_controls['a'].styleSheet())
            self.assertEqual(panel._cfg_by_id('a')['binding']['label_color_rules'], [rule])
            panel._update_widget_value(panel._cfg_by_id('a'), 25, force=True)
            self.assertIn('#dcfce7', panel.widget_controls['a'].styleSheet())
            return QDialog.Rejected
        with patch.object(LabelColorRulesDialog, 'exec_', cancel):
            editor.btn_label_colors.click()
        self.assertIn('#e8f1ff', panel.widget_controls['a'].styleSheet())
        self.assertEqual(editor.label_color_rules, [rule])
        def accept(dialog):
            dialog.editor._rows[0]['color'].recommended_buttons[3].click()
            return QDialog.Accepted
        with patch.object(LabelColorRulesDialog, 'exec_', accept):
            editor.btn_label_colors.click()
        self.assertIn('#dcfce7', panel.widget_controls['a'].styleSheet())
        self.assertEqual(panel._cfg_by_id('a')['binding']['label_color_rules'], [rule])
        editor.accept()
        self.assertEqual(panel._cfg_by_id('a')['binding']['label_color_rules'][0]['color'], '#DCFCE7')


if __name__ == '__main__':
    unittest.main()
