import copy
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication, QComboBox
from PyQt5.QtTest import QTest
from cantools.database.can import Message, Signal
from src.user_panel_v2.window import UserPanelWindow
from src.user_panel_v2.config_dialog import WidgetConfigDialog
from src.user_panel_v2.binding import pack_value, unpack_raw, validate_config


class PropertiesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def config(self, ident="a", **binding):
        return dict(id=ident, title=ident, widget_type="spinbox", behavior="tx",
                    row=0, col=0, row_span=3, col_span=5, z_index=7,
                    binding=dict(bus=1, can_id=0x123, dlc=8, start_bit=8, bit_length=12, **binding))

    def database(self, start=8):
        message = Message(0x123, "Example", 8, [Signal("Speed", start, 12), Signal("State", 32, 8)])
        return {1: {0x123: message}}

    def panel(self, configs, main=None, db=None):
        p = UserPanelWindow(main or SimpleNamespace(), db or {})
        from src.user_panel_v2.packets import bind_packet
        packets = {}
        for cfg in configs:
            if cfg.get('behavior') != 'tx':
                continue
            if cfg.get('widget_type') == 'sequence':
                for step in cfg['binding'].get('sequence_steps', []):
                    if step['kind'] == 'CMD':
                        raw = step['packet']
                        raw.setdefault('packet_id', f"{raw['bus']}:{raw['id']}")
                        raw.setdefault('cycle', 0)
                        raw.setdefault('is_fd', False)
                        packets[raw['packet_id']] = copy.deepcopy(raw)
                continue
            b = cfg['binding']
            ident = f"{b['bus']}:{b['can_id']}"
            packet = dict(packet_id=ident, bus=b['bus'], id=b['can_id'], length=b['dlc'],
                          data=[0] * b['dlc'], cycle=0, is_fd=False, is_brs=False, symbol='N/A', note='', count=0)
            packets[ident] = packet
            bind_packet(b, packet)
        p._load_panel_data(dict(widgets=configs, tx_packets=list(packets.values()), grid=dict(rows=12, cols=18)))
        self.addCleanup(p.deleteLater)
        return p

    def test_dbc_selection_and_manual_matching(self):
        d = WidgetConfigDialog(self.database(), preset=self.config())
        self.addCleanup(d.deleteLater)
        self.assertEqual(d.combo_signal.currentData(), "Speed")
        d.combo_signal.setCurrentIndex(d.combo_signal.findData("State"))
        self.assertEqual((d.spin_start_bit.value(), d.spin_bit_length.value()), (32, 8))
        d.spin_start_bit.setValue(33)
        self.assertIsNone(d.combo_signal.currentData())
        self.assertIn("Unknown", d.combo_signal.currentText())
        d.spin_start_bit.setValue(32)
        self.assertEqual(d.combo_signal.currentData(), "State")
        d.edit_can_id.setText("0x456")
        d.edit_can_id.editingFinished.emit()
        self.assertIsNone(d.combo_signal.currentData())
        self.assertEqual(d.spin_start_bit.value(), 32)

    def test_bus_change_preserves_tool_binding_and_uses_matching_signal_only(self):
        original = self.config(signal_name='Speed', scale=0.25, offset=-10)
        source = self.database()[1]
        changed = Message(0x123, 'Other', 8, [Signal('OtherSignal', 8, 12)])
        for target, expected_signal in ((self.database()[1], 'Speed'), ({}, None),
                                        ({0x123: changed}, None)):
            with self.subTest(expected=expected_signal, target=target):
                editor = WidgetConfigDialog({1: source, 2: target}, preset=copy.deepcopy(original))
                self.addCleanup(editor.deleteLater)
                before = editor.get_config()['binding']
                editor.combo_bus.setCurrentText('2')
                after = editor.get_config()['binding']
                self.assertEqual(after['bus'], 2)
                self.assertEqual(after['signal_name'], expected_signal)
                for key in ('can_id', 'dlc', 'is_fd', 'brs', 'start_bit', 'bit_length',
                            'byte_order', 'scale', 'offset', 'tx_press_value', 'tx_release_value'):
                    self.assertEqual(after[key], before[key], key)
                if expected_signal is None:
                    self.assertEqual(editor.combo_message.currentText(), 'Manual Input')
                    self.assertTrue(after['dbc_manual'])
                    restored = WidgetConfigDialog({1: source, 2: target}, preset=editor.get_config())
                    self.addCleanup(restored.deleteLater)
                    self.assertIsNone(restored.combo_signal.currentData())


    def test_missing_changed_dbc_preserves_fields_and_values(self):
        cfg = self.config(signal_name="Speed", scale=0.25, offset=-10, tx_hold_period_ms=140)
        for db in ({}, self.database(start=16)):
            p = self.panel([copy.deepcopy(cfg)], db=db)
            editor = p.properties.editor
            self.assertFalse(editor.isWindow())
            self.assertEqual(editor.spin_start_bit.value(), 8)
            self.assertIsNone(editor.combo_signal.currentData())
            editor.spin_start_bit.setValue(24)
            editor.edit_title.setText("Changed")
            editor.accept()
            saved = p._panel_data()["widgets"][0]
            self.assertEqual(saved["binding"]["start_bit"], 24)
            self.assertEqual(saved["binding"]["tx_hold_period_ms"], 140)
            self.assertEqual(saved["binding"]["scale"], 0.25)
            self.assertEqual(saved["z_index"], 7)
            self.assertEqual(saved["title"], "Changed")
            restored = self.panel(copy.deepcopy(p._panel_data()["widgets"]))
            self.assertEqual(restored.widgets_config[0]["binding"], saved["binding"])

    def test_multi_selection_changes_checked_fields_only(self):
        a, b = self.config("a"), self.config("b")
        b["col_span"], b["col"] = 7, 6
        b["binding"]["bus"] = 2
        p = self.panel([a, b])
        p.list_tools.item(1).setSelected(True)
        self.assertEqual(len(p.selected_configs()), 2)
        before = copy.deepcopy(p.widgets_config)
        prop = p.properties
        self.assertIn("Mixed", prop.checks["col_span"].text())
        prop.checks["col_span"].setChecked(True)
        prop.fields["col_span"].setValue(6)
        prop.apply_checked()
        self.assertEqual(p.selected_widget_ids, {"a", "b"})
        for old, new in zip(before, p.widgets_config):
            old["col_span"] = 6
            self.assertEqual(old, new)

    def test_speed_preflight_is_atomic_and_updates_shared_bus(self):
        nominal = {}
        for bus in (1, 2):
            combo = QComboBox()
            combo.addItem("500 kBit/s", {"bitrate": 500000})
            combo.addItem("250 kBit/s", {"bitrate": 250000})
            nominal[bus] = combo
            self.addCleanup(combo.deleteLater)
        main = SimpleNamespace(combo_bitrate=nominal, buses={1: None, 2: object()})
        a, b = self.config("a"), self.config("b")
        b["binding"]["bus"] = 2
        p = self.panel([a, b], main)
        p.list_tools.item(1).setSelected(True)
        prop = p.properties
        prop.checks["bitrate"].setChecked(True)
        prop.fields["bitrate"].setCurrentIndex(prop.fields["bitrate"].findData(250000))
        prop.checks["col_span"].setChecked(True)
        prop.fields["col_span"].setValue(8)
        before = copy.deepcopy(p.widgets_config)
        with patch("src.user_panel_v2.properties.QMessageBox.warning") as warning:
            prop.apply_checked()
            warning.assert_called_once()
        self.assertEqual(p.widgets_config, before)
        self.assertEqual(nominal[1].currentIndex(), 0)
        main.buses[2] = None
        with patch.object(p, '_authorize_communication', return_value=True):
            prop.apply_checked()
        self.assertTrue(all(c.currentIndex() == 1 for c in nominal.values()))
        self.assertEqual(p.channel_settings, {"1": {"bitrate": 250000}, "2": {"bitrate": 250000}})

    def test_properties_typing_does_not_delete_or_move_tool(self):
        p = self.panel([self.config()])
        p.show()
        self.app.processEvents()
        editor = p.properties.editor
        editor.edit_title.setFocus()
        editor.edit_title.selectAll()
        QTest.keyClick(editor.edit_title, Qt.Key_Delete)
        self.assertEqual(len(p.widgets_config), 1)
        editor.spin_row.setFocus()
        QTest.keyClick(editor.spin_row, Qt.Key_Up)
        self.assertEqual(p.widgets_config[0]["row"], 0)
        QTest.keyClick(editor.edit_title, Qt.Key_Escape)
        self.assertFalse(editor.isHidden())
        p.close()

    def test_manual_codec_matches_dbc_and_preserves_other_bits(self):
        for order, start in (("little_endian", 5), ("big_endian", 2)):
            for signed, value in ((False, 987), (True, -123)):
                sig = Signal("Value", start, 12, byte_order=order, is_signed=signed)
                msg = Message(0x123, "Data", 8, [sig])
                binding = dict(start_bit=start, bit_length=12, byte_order=order, signed=signed)
                expected = msg.encode({"Value": value})
                self.assertEqual(bytes(pack_value(bytes(8), binding, value)), expected)
                self.assertEqual(unpack_raw(expected, binding), value)
                filled = pack_value(bytes([255] * 8), binding, value)
                self.assertEqual(msg.decode(filled)["Value"], value)
                self.assertEqual(filled[-1], 255)
        cfg = self.config()
        cfg["binding"]["start_bit"] = 60
        with self.assertRaises(ValueError):
            validate_config(cfg)

    def test_dbc_refresh_and_manual_rx_value(self):
        cfg = self.config(signal_name="Speed", scale=2, offset=1)
        cfg.update(widget_type="label", behavior="rx")
        db = self.database()
        p = self.panel([cfg], db=db)
        p.mode = "standby"
        payload = pack_value(bytes(8), cfg["binding"], 21)
        p.on_message_update(1, 0x123, payload, 0)
        p.on_signal_update(1, "Speed", 10, "", 0)
        self.assertEqual(p.widget_controls["a"].text(), "21")
        db[1].clear()
        p.refresh_dbc_bindings()
        self.assertIsNone(p.widgets_config[0]["binding"]["signal_name"])
        self.assertEqual(p.widgets_config[0]["binding"]["start_bit"], 8)
        db[1].update(self.database()[1])
        p.refresh_dbc_bindings()
        self.assertEqual(p.widgets_config[0]["binding"]["signal_name"], "Speed")

    def test_sequence_settings_open_in_separate_dialog(self):
        cfg = self.config()
        cfg["widget_type"] = "sequence"
        cfg["binding"]["sequence_steps"] = [dict(kind="DEL", delay_ms=10)]
        p = self.panel([cfg])
        editor = p.properties.editor
        opened = []

        def accept_sequence(dialog):
            opened.append(dialog)
            dialog.steps[0]["delay_ms"] = 125
            return dialog.Accepted

        with patch("src.user_panel_v2.sequence_dialog.SequenceDialog.exec_", new=accept_sequence):
            editor._edit_sequence()
        self.assertEqual(len(opened), 1)
        self.assertTrue(opened[0].isWindow())
        self.assertEqual(opened[0].width(), 850)
        editor.accept()
        self.assertEqual(p.widgets_config[0]["binding"]["sequence_steps"][0]["delay_ms"], 125)

    def test_motorola_overlap_checks_actual_bits(self):
        a, b = self.config("a"), self.config("b")
        a["binding"].update(start_bit=7, bit_length=8, byte_order="big_endian")
        b["binding"].update(start_bit=0, bit_length=1)
        p = self.panel([a, b])
        self.assertEqual(p._collect_overlap_details()[0], {"a", "b"})
        b["binding"].update(start_bit=8)
        self.assertEqual(p._collect_overlap_details()[0], set())

    def test_bus_batch_cannot_override_registered_tx_packet_bus(self):
        a, b = self.config("a"), self.config("b")
        b["widget_type"] = "sequence"
        b["binding"]["sequence_steps"] = [dict(kind="CMD", packet=dict(bus=2, id=0x456, data=[42], length=1))]
        p = self.panel([a, b])
        p.list_tools.item(1).setSelected(True)
        prop = p.properties
        prop.checks["bus"].setChecked(True)
        prop.fields["bus"].setValue(3)
        prop.apply_checked()
        self.assertEqual(p.widgets_config[0]["binding"]["bus"], 1)
        packet = p.widgets_config[1]["binding"]["sequence_steps"][0]["packet"]
        self.assertEqual((packet['bus'], packet['id'], packet['data']), (2, 0x456, [42]))

    def test_matching_ambiguous_signal_keeps_explicit_saved_selection(self):
        sig_a = Signal("A", 8, 8)
        sig_b = Signal("B", 8, 8)
        msg = SimpleNamespace(signals=[sig_a, sig_b], name="Multiplexed", length=8,
                              get_signal_by_name=lambda name: sig_a if name == "A" else sig_b)
        cfg = self.config(signal_name="B")
        cfg["binding"]["bit_length"] = 8
        d = WidgetConfigDialog({1: {0x123: msg}}, preset=cfg)
        self.addCleanup(d.deleteLater)
        self.assertEqual(d.combo_signal.currentData(), "B")

    def test_canvas_control_click_toggles_multiple_selection(self):
        a, b = self.config("a"), self.config("b")
        b["col"] = 7
        p = self.panel([a, b])
        p.show()
        self.app.processEvents()
        QTest.mouseClick(p.widget_frames["b"], Qt.LeftButton, Qt.ControlModifier)
        self.assertEqual(p.selected_widget_ids, {"a", "b"})
        QTest.mouseClick(p.widget_frames["a"], Qt.LeftButton, Qt.ControlModifier)
        self.assertEqual(p.selected_widget_ids, {"b"})
        p.close()


if __name__ == "__main__":
    unittest.main()
