import copy
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PyQt5.QtWidgets import QApplication
from src.user_panel_v2.window import UserPanelWindow
from src.user_panel_v2.config_dialog import WidgetConfigDialog


class PanelTabsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def panel(self, widgets):
        panel = UserPanelWindow(SimpleNamespace(), {})
        self.addCleanup(panel.close)
        panel._load_panel_data(dict(version=5, widgets=copy.deepcopy(widgets)))
        return panel

    def test_named_tabs_child_routing_and_roundtrip(self):
        widgets = [dict(id='tabs', widget_type='tab_container', tabs=['Status', 'Motor', 'IMU']),
                   dict(id='imu', widget_type='label', parent_id='tabs', tab_index=2),
                   dict(id='default', widget_type='label', parent_id='tabs'),
                   dict(id='clamped', widget_type='label', parent_id='tabs', tab_index=99)]
        panel = self.panel(widgets)
        restored = self.panel(panel._panel_data()['widgets'])
        for instance in (panel, restored):
            tabs = instance.widget_controls['tabs']
            self.assertEqual([tabs.tabText(i) for i in range(tabs.count())], ['Status', 'Motor', 'IMU'])
            self.assertIs(instance.widget_frames['imu'].parentWidget(), tabs.widget(2).canvas)
            self.assertIs(instance.widget_frames['default'].parentWidget(), tabs.widget(0).canvas)
            self.assertIs(instance.widget_frames['clamped'].parentWidget(), tabs.widget(2).canvas)

    def test_legacy_tabs_keep_default_names_and_first_tab(self):
        panel = self.panel([dict(id='tabs', widget_type='tab_container'),
                            dict(id='child', widget_type='label', parent_id='tabs')])
        tabs = panel.widget_controls['tabs']
        self.assertEqual([tabs.tabText(i) for i in range(tabs.count())], ['Tab 1', 'Tab 2'])
        self.assertIs(panel.widget_frames['child'].parentWidget(), tabs.widget(0).canvas)

    def test_create_tab_tool_and_edit_names_in_properties(self):
        editor = WidgetConfigDialog({}, live_preview_default=False)
        self.addCleanup(editor.close)
        editor.combo_widget_type.setCurrentText('tab_container')
        with patch('src.user_panel_v2.config_dialog.QInputDialog.getText', return_value=('진단', True)):
            editor.btn_add_tab.click()
        with patch('src.user_panel_v2.config_dialog.QInputDialog.getText', return_value=('CAN 상태', True)):
            editor.btn_rename_tab.click()
        panel = self.panel([editor.get_config()])
        cfg = panel.widgets_config[0]
        self.assertEqual(cfg['tabs'], ['Tab 1', 'Tab 2', 'CAN 상태'])
        panel.selected_widget_id = cfg['id']
        panel.selected_widget_ids = {cfg['id']}
        panel.properties.refresh(force=True)
        editor = panel.properties.editor
        self.assertFalse(editor.grp_tabs.isHidden())
        editor.list_tabs.setCurrentRow(0)
        with patch('src.user_panel_v2.config_dialog.QInputDialog.getText', return_value=('概要', True)):
            editor.btn_rename_tab.click()
        editor.accept()
        restored = self.panel(panel._panel_data()['widgets'])
        self.assertEqual(restored.widget_controls[cfg['id']].tabText(0), '概要')

    def test_delete_tabs_preserves_children_routing_and_undo(self):
        panel = self.panel([dict(id='tabs', widget_type='tab_container', tabs=['A', 'B', 'C', 'D']),
                            dict(id='a', widget_type='label', parent_id='tabs', tab_index=0),
                            dict(id='b', widget_type='group_box', parent_id='tabs', tab_index=1),
                            dict(id='nested', widget_type='label', parent_id='b'),
                            dict(id='c', widget_type='label', parent_id='tabs', tab_index=2),
                            dict(id='d', widget_type='label', parent_id='tabs', tab_index=3)])
        panel.selected_widget_id = 'tabs'
        panel.selected_widget_ids = {'tabs'}
        panel.properties.refresh(force=True)
        panel.widget_controls['tabs'].setCurrentIndex(3)
        editor = panel.properties.editor
        editor.list_tabs.setCurrentRow(1)
        editor.btn_delete_tab.click()
        editor.list_tabs.setCurrentRow(2)
        editor.btn_delete_tab.click()
        self.assertEqual(panel._cfg_by_id('tabs')['tabs'], ['A', 'B', 'C', 'D'])
        editor.accept()
        for instance in (panel, self.panel(panel._panel_data()['widgets'])):
            tabs = instance.widget_controls['tabs']
            self.assertEqual([tabs.tabText(i) for i in range(tabs.count())], ['A', 'C'])
            for ident, index in (('a', 0), ('b', 1), ('c', 1), ('d', 1)):
                self.assertEqual(instance._cfg_by_id(ident)['tab_index'], index)
                self.assertIs(instance.widget_frames[ident].parentWidget(), tabs.widget(index).canvas)
            self.assertIsNotNone(instance.widget_frames['nested'])
        self.assertEqual(panel.widget_controls['tabs'].currentIndex(), 1)
        panel.undo_edit()
        self.assertEqual(panel._cfg_by_id('tabs')['tabs'], ['A', 'B', 'C', 'D'])
        self.assertEqual(panel._cfg_by_id('d')['tab_index'], 3)
        panel.redo_edit()
        self.assertEqual(panel._cfg_by_id('tabs')['tabs'], ['A', 'C'])

    def test_tab_actions_cancel_blank_and_keep_one_tab(self):
        editor = WidgetConfigDialog({}, preset=dict(widget_type='tab_container', tabs=['Only']),
                                    live_preview_default=False)
        self.addCleanup(editor.close)
        self.assertFalse(editor.btn_delete_tab.isEnabled())
        editor._delete_tab()
        for result in (('', True), ('   ', True), ('Cancelled', False)):
            with patch('src.user_panel_v2.config_dialog.QInputDialog.getText', return_value=result):
                editor.btn_add_tab.click()
                editor.btn_rename_tab.click()
        self.assertEqual(editor.get_config()['tabs'], ['Only'])

    def test_choose_child_tab_and_preserve_selection_after_rebuild(self):
        panel = self.panel([dict(id='tabs', title='Tabs', widget_type='tab_container', tabs=['A', 'B']),
                            dict(id='group', widget_type='group_box'),
                            dict(id='child', widget_type='label', parent_id='tabs', tab_index=1)])
        panel.selected_widget_id = 'child'
        panel.selected_widget_ids = {'child'}
        panel.properties.refresh(force=True)
        editor = panel.properties.editor
        self.assertEqual(editor.combo_parent_tab.currentText(), 'B')
        editor.combo_parent_tab.setCurrentIndex(0)
        editor.accept()
        self.assertIs(panel.widget_frames['child'].parentWidget(), panel.widget_controls['tabs'].widget(0).canvas)
        panel.widget_controls['tabs'].setCurrentIndex(1)
        panel.rebuild_grid()
        self.assertEqual(panel.widget_controls['tabs'].currentIndex(), 1)
        editor = panel.properties.editor
        editor.combo_parent_tool.setCurrentIndex(editor.combo_parent_tool.findData('group'))
        self.assertTrue(editor.combo_parent_tab.isHidden())
