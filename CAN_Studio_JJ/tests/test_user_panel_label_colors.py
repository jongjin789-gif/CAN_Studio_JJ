import copy
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt5.QtCore import QEvent, QItemSelectionModel, Qt
from PyQt5.QtWidgets import QApplication, QDialog
from cantools.database.can import Message, Signal
from cantools.database.conversion import BaseConversion

from src.user_panel_v2.config_dialog import WidgetConfigDialog
from src.user_panel_v2.label_color_dialog import LabelColorRulesDialog, LabelColorRulesEditor
from src.user_panel_v2.label_colors import label_color_style, matches_value, validate_color_rules
from src.user_panel_v2.property_table import PropertyTable
from src.user_panel_v2.storage import load_panel_json, save_panel_json
from src.user_panel_v2.window import UserPanelWindow


def value_rule(op='ge', a=10, b=20, color='#DCFCE7', target='background', enabled=True):
    return dict(enabled=enabled, source='value', op=op, a=a, b=b, target=target, color=color)


def enum_rule(raw=1, color='#FEE2E2', target='background'):
    return dict(enabled=True, source='enum', raw=raw, target=target, color=color)


class LabelColorsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        # Drain deleted editor trees after cleanup, before later timer-based tests.
        self.addCleanup(lambda: self.app.sendPostedEvents(None, QEvent.DeferredDelete))

    def database(self, enum=True, scale=2, offset=10):
        signal = Signal('State', 0, 8, unit='V', conversion=BaseConversion.factory(
            scale=scale, offset=offset, choices={0: 'Idle', 1: 'Active', 2: 'Fault'} if enum else None))
        return {1: {0x123: Message(0x123, 'Status', 1, [signal])}}

    def config(self, rules=None, ident='label', can_id=0x123):
        return dict(id=ident, widget_type='label', behavior='rx', title='State',
                    row_span=3, col_span=8, binding=dict(bus=1, can_id=can_id, signal_name='State',
                    dlc=1, start_bit=0, bit_length=8, scale=2, offset=10,
                    label_color_rules=copy.deepcopy(rules or [])))

    def panel(self, configs, db=None):
        panel = UserPanelWindow(SimpleNamespace(), db or {})
        self.addCleanup(panel.deleteLater)
        self.addCleanup(panel.close)
        panel._load_panel_data(dict(widgets=copy.deepcopy(configs), grid=dict(rows=24, cols=36)))
        return panel

    def select(self, panel, ident='label'):
        panel.selected_widget_id = ident
        panel.selected_widget_ids = {ident}
        panel.properties.refresh(force=True)
        return panel.properties.editor

    def test_all_comparison_boundaries(self):
        for op, expected in (
            ('lt', [True, False, False, False, False]),
            ('le', [True, True, False, False, False]),
            ('eq', [False, True, False, False, False]),
            ('ne', [True, False, True, True, True]),
            ('gt', [False, False, True, True, True]),
            ('ge', [False, True, True, True, True]),
            ('between', [False, True, True, True, False]),
            ('outside', [True, False, False, False, True]),
        ):
            with self.subTest(op=op):
                self.assertEqual([matches_value(value, op, 10, 20) for value in (9, 10, 15, 20, 21)], expected)
        self.assertTrue(matches_value(3 * 0.1, 'eq', 0.3))
        self.assertFalse(matches_value(3 * 0.1, 'gt', 0.3))

    def test_raw_enum_and_physical_ranges_reset_on_no_match_and_keep_id_identity(self):
        db = self.database()
        db[1][0x124] = Message(0x124, 'Other', 1, [Signal('State', 0, 8)])
        panel = self.panel([self.config([enum_rule(), value_rule('between', 14, 16)]),
                            self.config([enum_rule(color='#E8F1FF')], 'other', 0x124)], db)
        panel.set_mode('standby')
        ctrl = panel.widget_controls['label']
        panel.on_message_update(1, 0x123, b'\x01', 0)
        self.assertEqual(ctrl.text(), 'Active V')
        self.assertIn('#fee2e2', ctrl.styleSheet())
        self.assertEqual(panel.widget_controls['other'].styleSheet(), '')
        panel.on_signal_update(1, 'State', 99, 'V', 0, can_id=0x124)
        self.assertIn('#fee2e2', ctrl.styleSheet())
        panel.on_message_update(1, 0x123, b'\x02', 0)
        self.assertIn('#dcfce7', ctrl.styleSheet())  # RAW 2 => physical 14.
        panel.on_message_update(1, 0x123, b'\x04', 0)
        self.assertEqual(ctrl.styleSheet(), '')
        self.assertEqual(ctrl.text(), '18 V')

    def test_simulator_enum_priority_independent_text_color_and_disabled_rows(self):
        panel = self.panel([self.config([value_rule(a=0, color='#000000', enabled=False),
                                       enum_rule(color='#1E3A8A'), value_rule(a=0, color='#FEF9C3'),
                                       value_rule(a=0, target='text', color='#FFEDD5')])], self.database())
        cfg = panel._cfg_by_id('label')
        panel._update_widget_value(cfg, 12, force=True)
        ctrl = panel.widget_controls['label']
        self.assertIn('#1e3a8a', ctrl.styleSheet())
        self.assertIn('#ffedd5', ctrl.styleSheet())
        panel._update_widget_value(cfg, 12.5, force=True)
        self.assertIn('#fef9c3', ctrl.styleSheet())  # Do not round a fractional RAW to Enum 1.
        self.assertIn('color: #ffffff', label_color_style(dict(scale=1, label_color_rules=[enum_rule(color='#1E3A8A')]), 1))

    def test_rule_table_enum_availability_order_delete_and_color_picker(self):
        table = LabelColorRulesEditor(enum_entries=[(0, 10, 'Idle'), (1, 12, 'Active')])
        self.addCleanup(table.deleteLater)
        self.addCleanup(table.close)
        table.btn_enum.click()
        table.btn_enum.click()
        self.assertEqual(len(table.rules()), 2)
        table._rows[1]['color'].recommended_buttons[6].click()
        table.table.selectionModel().select(table.table.model().index(1, 0),
                                            QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows)
        table.btn_up.click()
        self.assertEqual(table.rules()[0]['raw'], 1)
        self.assertEqual(table.rules()[0]['color'], '#FCE7F3')
        table.btn_delete.click()
        self.assertEqual([rule['raw'] for rule in table.rules()], [0])
        table.btn_add.click()
        row = table._rows[-1]
        row['condition'].setCurrentIndex(row['condition'].findData('outside'))
        self.assertTrue(row['b'].isEnabled())
        row['source'].setCurrentIndex(row['source'].findData('enum'))
        self.assertFalse(row['b'].isEnabled())
        self.assertFalse(row['condition'].isEnabled())
        plain = LabelColorRulesEditor()
        self.addCleanup(plain.deleteLater)
        self.addCleanup(plain.close)
        plain.btn_add.click()
        self.assertFalse(plain.btn_enum.isEnabled())
        self.assertEqual(plain._rows[0]['source'].count(), 1)

    def test_saved_enum_rules_survive_missing_dbc_and_json_roundtrip(self):
        cfg = self.config([enum_rule()])
        panel = self.panel([cfg])
        editor = self.select(panel)
        rules = LabelColorRulesEditor(editor.label_color_rules, editor._enum_entries)
        self.addCleanup(rules.deleteLater)
        self.addCleanup(rules.close)
        self.assertEqual(rules.rules(), [enum_rule()])
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'label.upp.json'
            save_panel_json(path, panel._panel_data())
            restored = self.panel(load_panel_json(path)['widgets'])
        restored.set_mode('standby')
        restored.on_message_update(1, 0x123, b'\x01', 0)
        self.assertIn('#fee2e2', restored.widget_controls['label'].styleSheet())

    def test_invalid_rules_are_rejected_before_applying(self):
        for rule in (value_rule('between', 20, 10), value_rule(a=float('nan')),
                     value_rule(color='invalid'), dict(enum_rule(), raw=1.5)):
            with self.subTest(rule=rule), self.assertRaises(ValueError):
                validate_color_rules([rule])
        dialog = LabelColorRulesDialog([value_rule('between', 20, 10)])
        self.addCleanup(dialog.deleteLater)
        self.addCleanup(dialog.close)
        with patch('src.user_panel_v2.label_color_dialog.QMessageBox.warning') as warning:
            dialog.accept()
        warning.assert_called_once()
        self.assertNotEqual(dialog.result(), QDialog.Accepted)

    def test_rule_edit_cancel_apply_clear_undo_and_table_properties(self):
        panel = self.panel([self.config([enum_rule()])], self.database())
        editor = self.select(panel)
        self.assertIsInstance(editor.grp_tool.findChild(PropertyTable), PropertyTable)
        self.assertEqual(editor.settings_tabs.tabText(0), '기본')
        self.assertFalse(editor.grp_label_colors.isHidden())
        def cancel(dialog):
            dialog.editor.set_rules([value_rule()])
            return QDialog.Rejected
        with patch.object(LabelColorRulesDialog, 'exec_', cancel):
            editor.btn_label_colors.click()
        self.assertEqual(editor.label_color_rules, [enum_rule()])
        def accept(dialog):
            dialog.editor.set_rules([value_rule()])
            return QDialog.Accepted
        with patch.object(LabelColorRulesDialog, 'exec_', accept):
            editor.btn_label_colors.click()
        self.assertEqual(panel._cfg_by_id('label')['binding']['label_color_rules'], [enum_rule()])
        editor.accept()
        self.assertEqual(panel._cfg_by_id('label')['binding']['label_color_rules'], [value_rule()])
        editor = panel.properties.editor
        editor.label_color_rules = []
        editor.accept()
        self.assertNotIn('label_color_rules', panel._cfg_by_id('label')['binding'])
        panel.undo_edit()
        self.assertEqual(panel._cfg_by_id('label')['binding']['label_color_rules'], [value_rule()])
        panel.redo_edit()
        self.assertNotIn('label_color_rules', panel._cfg_by_id('label')['binding'])

    def test_detached_editor_carries_pending_edits_and_cancel_is_atomic(self):
        panel = self.panel([self.config([enum_rule()])], self.database())
        editor = self.select(panel)
        editor.edit_title.setText('Pending')
        before = copy.deepcopy(panel.widgets_config)
        def cancel(dialog):
            self.assertEqual(dialog.edit_title.text(), 'Pending')
            dialog.edit_title.setText('Cancelled')
            return QDialog.Rejected
        with patch.object(WidgetConfigDialog, 'exec_', cancel):
            panel.properties.btn_open_window.click()
        self.assertEqual(panel.widgets_config, before)
        self.assertEqual(panel.properties.editor.edit_title.text(), 'Pending')
        def accept(dialog):
            self.assertEqual(dialog.label_color_rules, [enum_rule()])
            dialog.edit_title.setText('Applied')
            dialog.label_color_rules = [value_rule('lt', 20)]
            return QDialog.Accepted
        with patch.object(WidgetConfigDialog, 'exec_', accept):
            panel.properties.btn_open_window.click()
        self.assertEqual(panel._cfg_by_id('label')['title'], 'Applied')
        self.assertEqual(panel._cfg_by_id('label')['binding']['label_color_rules'], [value_rule('lt', 20)])
        panel.undo_edit()
        self.assertEqual(panel.widgets_config, before)
        panel.set_mode('standby')
        panel.properties.refresh(force=True)
        self.assertFalse(panel.properties.btn_open_window.isEnabled())

    def test_detached_tab_deletions_compose_with_pending_dock_changes(self):
        panel = self.panel([dict(id='tabs', widget_type='tab_container', tabs=['A', 'B', 'C', 'D']),
                            dict(id='child', widget_type='label', parent_id='tabs', tab_index=3)])
        editor = self.select(panel, 'tabs')
        editor.list_tabs.setCurrentRow(1)
        editor.btn_delete_tab.click()  # Pending A/C/D; original child D now maps to 2.
        def accept(dialog):
            dialog.list_tabs.setCurrentRow(1)
            dialog.btn_delete_tab.click()  # A/D; original child must now map to 1.
            return QDialog.Accepted
        with patch.object(WidgetConfigDialog, 'exec_', accept):
            panel.properties.btn_open_window.click()
        self.assertEqual(panel._cfg_by_id('tabs')['tabs'], ['A', 'D'])
        self.assertEqual(panel._cfg_by_id('child')['tab_index'], 1)


if __name__ == '__main__':
    unittest.main()
