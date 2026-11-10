import copy
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt5.QtWidgets import QApplication

from src.user_panel_v2.binding import pack_value
from src.user_panel_v2.config_dialog import WidgetConfigDialog
from src.user_panel_v2.storage import load_bundle, save_bundle
from src.user_panel_v2.window import UserPanelWindow


class ProgressTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def config(self, **binding):
        values = dict(bus=1, can_id=0x130, dlc=2, start_bit=0, bit_length=16,
                      signed=True, scale=0.5, offset=-10, min=-50, max=150, unit='V')
        values.update(binding)
        return dict(id='progress', title='Voltage', widget_type='progress', behavior='rx',
                    row=0, col=0, row_span=3, col_span=6, binding=values)

    def panel(self, config=None):
        panel = UserPanelWindow(SimpleNamespace(), {})
        self.addCleanup(panel.deleteLater)
        self.addCleanup(panel.close)
        panel._load_panel_data(dict(grid=dict(rows=24, cols=36),
                                   widgets=[copy.deepcopy(config or self.config())]))
        return panel

    def receive(self, panel, value):
        binding = panel.widgets_config[0]['binding']
        panel.on_message_update(1, 0x130, pack_value(bytes(binding['dlc']), binding, value), 0)

    def test_display_modes_receive_physical_values_and_preserve_bar_fill(self):
        for mode, expected in ((None, '50%'), ('percent', '50%'),
                               ('value', '50 V'), ('both', '50% | 50 V')):
            with self.subTest(mode=mode):
                config = self.config()
                if mode is not None:
                    config['binding']['progress_text_mode'] = mode
                panel = self.panel(config)
                panel.set_mode('standby')
                self.receive(panel, 50)
                control = panel.widget_controls['progress']
                self.assertEqual(control.text(), expected)
                self.assertEqual(control.value(), 500)

    def test_outside_range_clamps_percent_but_keeps_received_value(self):
        panel = self.panel(self.config(progress_text_mode='both', min=0, max=100))
        panel.set_mode('standby')
        for value, expected, fill in ((150, '100% | 150 V', 1000), (-25.5, '0% | -25.5 V', 0)):
            self.receive(panel, value)
            control = panel.widget_controls['progress']
            self.assertEqual(control.text(), expected)
            self.assertEqual(control.value(), fill)

    def test_precision_and_placeholder_before_first_reception(self):
        panel = self.panel(self.config(progress_text_mode='value', value_precision_decimals=6,
                                       scale=0.000001, offset=0, bit_length=24, dlc=3,
                                       min=0, max=1))
        control = panel.widget_controls['progress']
        self.assertEqual(control.text(), '-')
        panel.set_mode('standby')
        self.receive(panel, 0.123456)
        self.assertEqual(control.text(), '0.123456 V')
        self.assertEqual(control.toolTip(), '0.123456 V')

    def test_properties_and_package_round_trip_keep_display_choice(self):
        panel = self.panel()
        restored = self.panel()
        with tempfile.TemporaryDirectory() as directory:
            for mode, expected in (('value', '50 V'), ('both', '50% | 50 V'), ('percent', '50%')):
                with self.subTest(mode=mode):
                    editor = panel.properties.editor
                    editor.combo_progress_display.setCurrentIndex(editor.combo_progress_display.findData(mode))
                    editor.accept()
                    self.assertEqual(panel.widgets_config[0]['binding']['progress_text_mode'], mode)
                    filename = save_bundle(str(Path(directory) / mode), panel._panel_data(), {})
                    with patch('src.user_panel_v2.storage.tempfile.mkdtemp', return_value=directory):
                        data, _ = load_bundle(filename)
                    restored._load_panel_data(data)
                    self.assertEqual(restored.properties.editor.combo_progress_display.currentData(), mode)
                    restored.set_mode('standby')
                    self.receive(restored, 50)
                    self.assertEqual(restored.widget_controls['progress'].text(), expected)

    def test_small_tools_get_new_default_and_loaded_minimum(self):
        for behavior, types in (('rx', ('label', 'progress', 'status_lamp')),
                                ('tx', ('button', 'toggle', 'spinbox'))):
            for widget_type in types:
                with self.subTest(widget_type=widget_type):
                    dialog = WidgetConfigDialog({}, fixed_behavior=behavior)
                    self.addCleanup(dialog.deleteLater)
                    dialog.combo_widget_type.setCurrentText(widget_type)
                    config = dialog.get_config()
                    self.assertEqual((config['row_span'], config['col_span']), (3, 6))
                    config.update(row_span=1, col_span=2)
                    panel = self.panel(config)
                    loaded = panel.widgets_config[0]
                    self.assertEqual((loaded['row_span'], loaded['col_span']), (3, 6))
                    panel.resize_selected_span(-1, -1)
                    self.assertEqual((loaded['row_span'], loaded['col_span']), (3, 6))
        panel = self.panel(dict(self.config(), row_span=5, col_span=9))
        self.assertEqual((panel.widgets_config[0]['row_span'], panel.widgets_config[0]['col_span']), (5, 9))


if __name__ == '__main__':
    unittest.main()
