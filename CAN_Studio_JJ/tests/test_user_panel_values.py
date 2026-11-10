import os
import unittest
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication
from cantools.database.can import Message, Signal
from cantools.database.conversion import BaseConversion

from src.user_panel_v2.config_dialog import WidgetConfigDialog
from src.user_panel_v2.window import UserPanelWindow


class UserPanelValuesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def rx_panel(self, binding, messages=None):
        panel = UserPanelWindow(SimpleNamespace(), messages or {})
        self.addCleanup(panel.deleteLater)
        self.addCleanup(panel.close)
        config = dict(id='label', title='State', widget_type='label', behavior='rx',
                      row=0, col=0, row_span=3, col_span=8, binding=binding)
        panel._load_panel_data(dict(widgets=[config]))
        panel.set_mode('standby')
        return panel

    def test_rx_label_matches_signed_raw_enum_and_appends_dbc_unit(self):
        for byte_order, start in (('little_endian', 0), ('big_endian', 7)):
            with self.subTest(byte_order=byte_order):
                signal = Signal('State', start, 16, byte_order=byte_order, is_signed=True, unit='V',
                                conversion=BaseConversion.factory(scale=0.5, offset=10,
                                    choices={2: 'Run', -2: 'Reverse', 11: 'WrongPhysicalEnum', 10: 'Other'}))
                message = Message(0x130, 'Status', 2, [signal])
                binding = dict(bus=1, can_id=0x130, signal_name='State', dlc=2, start_bit=start,
                               bit_length=16, byte_order=byte_order, signed=True, scale=0.5, offset=10)
                panel = self.rx_panel(binding, {1: {0x130: message}})
                for raw, expected in ((2, 'Run V'), (-2, 'Reverse V'), (0, '10 V'), (3, '11.5 V')):
                    panel.on_message_update(1, 0x130, message.encode({'State': raw}, scaling=False), 0)
                    self.assertEqual(panel.widget_controls['label']._full_text, expected)
                    panel.on_signal_update(1, 'State', raw * 0.5 + 10, 'V', 0)
                    self.assertEqual(panel.widget_controls['label']._full_text, expected)

    def test_rx_label_keeps_exact_large_raw_enum_and_omits_absent_unit(self):
        raw = (1 << 60) + 1
        signal = Signal('State', 0, 64, conversion=BaseConversion.factory(
            scale=0.5, offset=10, choices={raw: 'ExactRaw'}))
        message = Message(0x130, 'Status', 8, [signal])
        binding = dict(bus=1, can_id=0x130, signal_name='State', dlc=8, start_bit=0,
                       bit_length=64, scale=0.5, offset=10)
        panel = self.rx_panel(binding, {1: {0x130: message}})
        panel.on_message_update(1, 0x130, message.encode({'State': raw}, scaling=False), 0)
        panel.on_signal_update(1, 'State', raw * 0.5 + 10, '', 0)
        self.assertEqual(panel.widget_controls['label']._full_text, 'ExactRaw')

    def test_rx_label_manual_unit_and_signal_only_enum_updates(self):
        panel = self.rx_panel(dict(bus=1, can_id=0x130, dlc=1, start_bit=0,
                                   bit_length=8, scale=0.5, offset=-10, unit='A'))
        panel.on_message_update(1, 0x130, bytes([25]), 0)
        self.assertEqual(panel.widget_controls['label']._full_text, '2.5 A')
        signal = Signal('State', 0, 8, unit='V', conversion=BaseConversion.factory(
            scale=0.1, offset=0, choices={12: 'Ready'}))
        message = Message(0x131, 'Status', 1, [signal])
        other = self.rx_panel(dict(bus=1, can_id=0x131, signal_name='State', dlc=1, start_bit=0,
                                  bit_length=8, scale=0.1, offset=0), {1: {0x131: message}})
        other.on_signal_update(1, 'State', 1.2, 'V', 0)
        self.assertEqual(other.widget_controls['label']._full_text, 'Ready V')
        other._update_widget_value(other.widgets_config[0], 1.2, force=True)
        self.assertEqual(other.widget_controls['label']._full_text, 'Ready V')

    def test_configured_values_survive_accept_and_runtime_clicks(self):
        for widget_type in ("button", "toggle"):
            for use_enum in (False, True):
                with self.subTest(widget_type=widget_type, use_enum=use_enum):
                    dialog = WidgetConfigDialog({}, fixed_behavior="tx")
                    dialog.combo_widget_type.setCurrentText(widget_type)
                    if use_enum:
                        dialog._load_enum_entries(SimpleNamespace(
                            choices={0: "Idle", 1: "Default", 7: "Active", 12: "Released"},
                            scale=1.0, offset=0.0,
                        ))
                        dialog._update_visibility()
                    if widget_type == "button":
                        editors = (dialog.spin_press_value, dialog.spin_release_value)
                        combos = (dialog.combo_press_enum, dialog.combo_release_enum)
                        keys = ("tx_press_value", "tx_release_value")
                    else:
                        editors = (dialog.spin_toggle_on_value, dialog.spin_toggle_off_value)
                        combos = (dialog.combo_toggle_on_enum, dialog.combo_toggle_off_enum)
                        keys = ("tx_on_value", "tx_off_value")
                    for spin, combo, value in zip(editors, combos, (7, 12)):
                        if use_enum:
                            combo.setCurrentIndex(combo.findData(float(value)))
                        else:
                            spin.setValue(value)
                    dialog.show()
                    self.app.processEvents()
                    before = dialog.get_config()
                    dialog.accept()
                    self.assertFalse(dialog.isVisible())
                    config = dialog.get_config()
                    self.assertEqual(config, before)
                    self.assertEqual([config["binding"][key] for key in keys], [7, 12])

                    sent = []
                    owner = SimpleNamespace(
                        mode="run",
                        _emit_tx=lambda cfg, value: sent.append(value),
                        _uncheck_signal_toggles=lambda cfg: None,
                    )
                    owner._emit_tool_action = lambda cfg, value, action: UserPanelWindow._emit_tool_action(owner, cfg, value, action)
                    button, _ = UserPanelWindow._create_runtime_widget(owner, config)
                    button.click()
                    if widget_type == "toggle":
                        button.click()
                    self.assertEqual(sent, [7, 12])
                    button.deleteLater()
                    dialog.deleteLater()

    def test_same_signal_toggles_are_exclusive_without_extra_transmissions(self):
        sent = []
        owner = SimpleNamespace(widgets_config=[], widget_controls={})
        owner._emit_tx = lambda cfg, value: sent.append((cfg["id"], value))
        owner._emit_tool_action = lambda cfg, value, action: UserPanelWindow._emit_tool_action(owner, cfg, value, action)
        owner._uncheck_signal_toggles = lambda cfg: UserPanelWindow._uncheck_signal_toggles(owner, cfg)
        bindings = [
            {}, {}, {"bus": 2}, {"can_id": 0x200}, {"start_bit": 8},
        ]
        for index, overrides in enumerate(bindings):
            cfg = {
                "id": str(index), "widget_type": "toggle", "behavior": "tx",
                "binding": dict(bus=1, can_id=0x100, start_bit=0, bit_length=8,
                                tx_on_value=index + 7, tx_off_value=0),
            }
            cfg["binding"].update(overrides)
            owner.widgets_config.append(cfg)
            button, _ = UserPanelWindow._create_runtime_widget(owner, cfg)
            owner.widget_controls[cfg["id"]] = button
        buttons = list(owner.widget_controls.values())
        for button in buttons[2:]:
            button.click()
        sent.clear()
        buttons[0].click()
        buttons[1].click()
        self.assertFalse(buttons[0].isChecked())
        self.assertEqual(buttons[0].text(), "OFF")
        self.assertTrue(buttons[1].isChecked())
        self.assertTrue(all(button.isChecked() for button in buttons[2:]))
        self.assertEqual(sent, [("0", 7), ("1", 8)])
        buttons[0].click()
        self.assertFalse(buttons[1].isChecked())
        self.assertEqual(sent[-1], ("0", 7))
        buttons[0].click()
        self.assertFalse(buttons[0].isChecked())
        self.assertEqual(sent[-1], ("0", 0))
        for button in buttons:
            button.deleteLater()


if __name__ == "__main__":
    unittest.main()
