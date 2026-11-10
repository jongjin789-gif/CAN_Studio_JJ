import copy
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication, QDialog, QSlider
from src.user_panel_v2.config_dialog import WidgetConfigDialog
from src.user_panel_v2.packets import bind_packet, validate_tool
from src.user_panel_v2.binding import validate_config
from src.user_panel_v2.window import UserPanelWindow


class MultiCommandTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setup_panel(self, kind='slider', same_packet=False, primary=True, extra=True):
        packets = [dict(packet_id=str(i), bus=i + 1, id=0x100 + i, length=8,
                        data=[0] * 8, is_fd=False, is_brs=False, cycle=0) for i in range(2)]
        bindings = []
        for i in range(2):
            b = dict(start_bit=i * 8 if same_packet else 0, bit_length=8,
                     min=0, max=100, scale=1, offset=0, tx_initial_value=3,
                     tx_press_value=10 + i, tx_release_value=0,
                     tx_on_value=20 + i, tx_off_value=0)
            bind_packet(b, packets[0 if same_packet else i])
            bindings.append(b)
        cfg = dict(id='tool', title='Multi', widget_type=kind, behavior='tx',
                   binding=bindings[0], primary_enabled=primary,
                   tx_commands=[dict(title='Extra', enabled=extra, binding=bindings[1])])
        self.sent = []
        main = SimpleNamespace(buses={i: SimpleNamespace(send=self.sent.append) for i in (1, 2)},
                               bus_capabilities={i: {'is_fd': False} for i in (1, 2)},
                               record_tx_activity=lambda *args, **kwargs: None)
        panel = UserPanelWindow(main, {})
        self.addCleanup(panel.close)
        panel._load_panel_data(dict(tx_packets=packets, widgets=[cfg]))
        panel.show()
        self.app.processEvents()
        panel.set_mode('run')
        self.assertEqual(panel.mode, 'run')
        return panel, panel.widgets_config[0]

    def test_slider_multiple_buses_and_same_packet_combined(self):
        for same_packet in (False, True):
            with self.subTest(same_packet=same_packet):
                panel, cfg = self.setup_panel(same_packet=same_packet)
                panel.widget_controls['tool'].findChild(QSlider).setValue(9)
                self.assertEqual(len(self.sent), 1 if same_packet else 2)
                if same_packet:
                    self.assertEqual(list(self.sent[0].data[:2]), [9, 9])
                else:
                    self.assertEqual([m.data[0] for m in self.sent], [9, 9])
                    self.assertEqual([m.arbitration_id for m in self.sent], [0x100, 0x101])
                panel.close()

    def test_button_and_toggle_use_each_commands_action_values(self):
        for kind, expected in [('button', [10, 11, 0, 0]), ('toggle', [20, 21, 0, 0])]:
            with self.subTest(kind=kind):
                panel, cfg = self.setup_panel(kind)
                ctrl = panel.widget_controls['tool']
                ctrl.click()
                if kind == 'toggle':
                    ctrl.click()
                self.assertEqual([m.data[0] for m in self.sent], expected)
                panel.close()

    def test_disabled_primary_extra_and_all_disabled(self):
        for primary, extra in [(True, False), (False, True), (False, False)]:
            with self.subTest(primary=primary, extra=extra):
                panel, cfg = self.setup_panel(primary=primary, extra=extra)
                self.assertEqual([panel._packet_runtimes[str(i)].overlay[0] for i in range(2)],
                                 [3 if primary else 0, 3 if extra else 0])
                panel._emit_tx(cfg, 8)
                self.assertEqual([m.arbitration_id for m in self.sent],
                                 [ident for ident, enabled in [(0x100, primary), (0x101, extra)] if enabled])
                panel.close()

    def test_editor_checkbox_save_reload_copy_and_undo(self):
        panel, cfg = self.setup_panel()
        panel.set_mode('edit')
        panel.selected_widget_id = 'tool'
        panel.selected_widget_ids = {'tool'}
        panel.properties.refresh(force=True)
        editor = panel.properties.editor
        editor.chk_primary_enabled.setChecked(False)
        editor.command_list.item(0).setCheckState(Qt.Unchecked)
        editor.accept()
        saved = copy.deepcopy(panel._panel_data())
        self.assertFalse(saved['widgets'][0]['primary_enabled'])
        self.assertFalse(saved['widgets'][0]['tx_commands'][0]['enabled'])
        panel.undo_edit()
        self.assertTrue(panel.widgets_config[0]['tx_commands'][0]['enabled'])
        panel.redo_edit()
        self.assertFalse(panel.widgets_config[0]['tx_commands'][0]['enabled'])
        panel._load_panel_data(saved)
        panel.copy_selected_tools()
        panel.paste_tools()
        panel.widgets_config[1]['tx_commands'][0]['binding']['tx_on_value'] = 99
        self.assertEqual(panel.widgets_config[0]['tx_commands'][0]['binding']['tx_on_value'], 21)

    def test_command_add_edit_remove(self):
        panel, cfg = self.setup_panel('button')
        editor = WidgetConfigDialog({}, preset=cfg, tx_packets=panel.tx_packets,
                                    live_preview_default=False)
        self.addCleanup(editor.close)
        def accept_child(dialog):
            dialog.edit_title.setText('New command')
            dialog.spin_press_value.setValue(42)
            return QDialog.Accepted
        with patch.object(WidgetConfigDialog, 'exec_', accept_child):
            editor._edit_command(False)
            self.assertEqual(len(editor.tx_commands), 2)
            editor.command_list.setCurrentRow(0)
            editor._edit_command(True)
        self.assertEqual(editor.tx_commands[0]['binding']['tx_press_value'], 42)
        editor.command_list.setCurrentRow(1)
        editor._remove_command()
        self.assertEqual(len(editor.tx_commands), 1)

    def test_invalid_extra_validated_before_any_runtime_changes(self):
        panel, cfg = self.setup_panel()
        bad = copy.deepcopy(cfg)
        bad['tx_commands'][0]['binding']['scale'] = 0
        with self.assertRaises(ValueError):
            validate_config(bad)
        before = bytes(panel._packet_runtimes['0'].overlay)
        panel._emit_tx(bad, 8)
        self.assertEqual(bytes(panel._packet_runtimes['0'].overlay), before)
        self.assertEqual(self.sent, [])
        bad['tx_commands'][0]['binding']['packet_id'] = 'missing'
        with self.assertRaises(ValueError):
            validate_tool(panel.tx_packets, bad)

    def test_failed_channel_does_not_block_other_channels_and_can_retry(self):
        for failure in ('disconnected', 'type_mismatch', 'driver_error'):
            for failed_bus in (1, 2):
                with self.subTest(failure=failure, bus=failed_bus):
                    panel, cfg = self.setup_panel()
                    main = panel.main_window
                    bus = main.buses[failed_bus]
                    if failure == 'disconnected':
                        main.buses[failed_bus] = None
                    elif failure == 'type_mismatch':
                        main.bus_capabilities[failed_bus]['is_fd'] = True
                    else:
                        bus.send = lambda message: (_ for _ in ()).throw(RuntimeError('device lost'))
                    panel._emit_tx(cfg, 8)
                    self.assertEqual([m.arbitration_id for m in self.sent],
                                     [0x101 if failed_bus == 1 else 0x100])
                    failed = panel._packet_runtimes[str(failed_bus - 1)]
                    self.assertEqual(failed.overlay[0], 3)
                    self.assertEqual(failed.alive, 0)
                    self.assertIn(f'BUS {failed_bus}', panel.system_log.text.toPlainText())
                    main.buses[failed_bus] = bus
                    bus.send = self.sent.append
                    main.bus_capabilities[failed_bus]['is_fd'] = False
                    self.sent.clear()
                    panel._emit_tx(cfg, 8)
                    self.assertEqual([m.arbitration_id for m in self.sent], [0x100 + failed_bus - 1])
                    panel.close()

    def test_three_bus_button_continues_after_middle_channel_failure(self):
        panel, cfg = self.setup_panel('button')
        panel.set_mode('edit')
        data = copy.deepcopy(panel._panel_data())
        third = dict(data['tx_packets'][1], packet_id='2', bus=3, id=0x102)
        data['tx_packets'].append(third)
        extra = copy.deepcopy(data['widgets'][0]['tx_commands'][0])
        bind_packet(extra['binding'], third)
        data['widgets'][0]['tx_commands'].append(extra)
        panel.main_window.buses[3] = SimpleNamespace(send=self.sent.append)
        panel.main_window.bus_capabilities[3] = {'is_fd': False}
        panel.main_window.buses[2] = None
        panel._load_panel_data(data)
        panel.set_mode('run')
        panel.widget_controls['tool'].click()
        self.assertEqual([(m.arbitration_id, m.data[0]) for m in self.sent],
                         [(0x100, 10), (0x102, 11), (0x100, 0), (0x102, 0)])
