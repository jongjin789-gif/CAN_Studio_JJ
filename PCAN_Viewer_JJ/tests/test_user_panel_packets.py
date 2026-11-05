import copy
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PyQt5.QtWidgets import QApplication, QDialog
from PyQt5.QtTest import QTest
from PyQt5.QtCore import QModelIndex, Qt, QPoint, QPointF, QEvent
from PyQt5.QtGui import QMouseEvent
from cantools.database.can import Message, Signal
from src.user_panel_v2.packets import PacketRuntime, RegisteredPacketDialog, PacketRegistryDialog, bind_packet
from src.user_panel_v2.window import UserPanelWindow
from src.user_panel_v2.config_dialog import WidgetConfigDialog
from src.crc_utils import calculate_crc16_ccitt_false


def packet(ident='p', can_id=0x123, cycle=20):
    return dict(packet_id=ident, bus=1, id=can_id, length=8, is_fd=False, is_brs=False,
                data=[0xAA] * 8, cycle=cycle, symbol='Test', note='', count=0, crc_type='N/A')


def tool(p, ident='tool', start=24, kind='slider'):
    binding = dict(start_bit=start, bit_length=8, byte_order='little_endian', scale=1,
                   offset=0, min=0, max=255, tx_initial_value=7, tx_release_value=0,
                   tx_off_value=0, tx_on_value=1, tx_press_value=1)
    bind_packet(binding, p)
    return dict(id=ident, widget_type=kind, behavior='tx', binding=binding, title=ident,
                row=0, col=0, row_span=3, col_span=4)


class PanelPacketTest(unittest.TestCase):
    def test_sequence_command_bus_change_keeps_override_and_uses_manual_for_missing_signals(self):
        from src.user_panel_v2.packets import RegisteredCommandDialog
        source = Message(0x123, 'Example', 8, [Signal('A', 0, 8)])
        matching = Message(0x123, 'Example', 8, [Signal('A', 0, 8)])
        other = Message(0x123, 'Other', 8, [Signal('Different', 0, 8)])
        first, second = packet('first'), packet('second')
        second['bus'] = 2
        for target, manual in (({0x123: matching}, False), ({}, True), ({0x123: other}, True)):
            with self.subTest(manual=manual, target=target):
                step = dict(kind='CMD', packet=copy.deepcopy(first), data_override=[3] * 8, repeat=2)
                dialog = RegisteredCommandDialog([first, second], step, db_messages={1: {0x123: source}, 2: target})
                self.addCleanup(dialog.close)
                dialog.combo.setCurrentIndex(1)
                dialog.accept()
                self.assertEqual(dialog.result_step['packet']['bus'], 2)
                self.assertEqual(dialog.result_step['data_override'], [3] * 8)
                self.assertEqual(dialog.result_step['repeat'], 2)
                self.assertEqual(dialog.direct_input.isChecked(), manual)
                if manual:
                    self.assertIsNone(dialog.editor.current_db_msg)

    def test_registry_move_buttons_preserve_multi_selection_and_picker_order(self):
        from PyQt5.QtWidgets import QPushButton
        from src.user_panel_v2.sequence_dialog import PacketActionDialog
        panel = self.panel([packet(str(i), 0x120 + i) for i in range(5)])
        registry = PacketRegistryDialog(panel)
        self.addCleanup(registry.close)
        registry.list.selectRows([1, 2])
        up = next(b for b in registry.findChildren(QPushButton) if b.text() == '위로')
        down = next(b for b in registry.findChildren(QPushButton) if b.text() == '아래로')
        up.click()
        self.assertEqual([p['packet_id'] for p in registry.packets], ['1', '2', '0', '3', '4'])
        self.assertEqual(registry.list.selectedRows(), [0, 1])
        up.click()
        self.assertEqual([p['packet_id'] for p in registry.packets], ['1', '2', '0', '3', '4'])
        down.click()
        self.assertEqual([p['packet_id'] for p in registry.packets], ['0', '1', '2', '3', '4'])
        registry.list.selectRows([2, 4])
        up.click()
        self.assertEqual([p['packet_id'] for p in registry.packets], ['0', '2', '1', '4', '3'])
        self.assertEqual(registry.list.selectedRows(), [1, 3])
        self.assertEqual([p['packet_id'] for p in panel.tx_packets], ['0', '1', '2', '3', '4'])
        panel.tx_packets = copy.deepcopy(registry.packets)
        self.assertEqual([p['packet_id'] for p in panel._panel_data()['tx_packets']], ['0', '2', '1', '4', '3'])
        picker = PacketActionDialog(panel.tx_packets, dict(kind='START'))
        self.addCleanup(picker.close)
        self.assertEqual([picker.target.itemData(i) for i in range(1, picker.target.count())], ['0', '2', '1', '4', '3'])

    def test_registry_ctrl_shift_drag_selection_and_multi_paste(self):
        panel = self.panel([packet(str(i), 0x120 + i) for i in range(5)])
        registry = PacketRegistryDialog(panel)
        self.addCleanup(registry.close)
        registry.show()
        registry.activateWindow()
        registry.list.setFocus()
        self.app.processEvents()
        view = registry.list
        def point(row):
            return view.visualRect(view.model().index(row, 1)).center()
        QTest.mouseClick(view.viewport(), Qt.LeftButton, Qt.NoModifier, point(0))
        QTest.mouseClick(view.viewport(), Qt.LeftButton, Qt.ControlModifier, point(2))
        QTest.mouseClick(view.viewport(), Qt.LeftButton, Qt.ControlModifier, point(4))
        self.assertEqual(view.selectedRows(), [0, 2, 4])
        QTest.keyClick(view, Qt.Key_C, Qt.ControlModifier)
        QTest.keyClick(view, Qt.Key_V, Qt.ControlModifier)
        self.assertEqual([p['id'] for p in registry.packets[5:]], [0x120, 0x122, 0x124])
        self.assertEqual([p['bus'] for p in registry.packets[5:]], [0, 0, 0])
        self.assertEqual(len({p['packet_id'] for p in registry.packets}), 8)
        self.assertEqual(view.selectedRows(), [5, 6, 7])
        registry.packets[5]['data'][0] = 1
        self.assertEqual(panel._packet_clipboard[0]['data'][0], 0xAA)
        view.scrollToTop()
        QTest.mouseClick(view.viewport(), Qt.LeftButton, Qt.NoModifier, point(1))
        QTest.mouseClick(view.viewport(), Qt.LeftButton, Qt.ShiftModifier, point(3))
        self.assertEqual(view.selectedRows(), [1, 2, 3])
        view.clearSelection()
        QTest.mousePress(view.viewport(), Qt.LeftButton, Qt.NoModifier, point(0))
        move = QMouseEvent(QEvent.MouseMove, QPointF(point(3)), Qt.NoButton, Qt.LeftButton, Qt.NoModifier)
        QApplication.sendEvent(view.viewport(), move)
        QTest.mouseRelease(view.viewport(), Qt.LeftButton, Qt.NoModifier, point(3))
        self.assertEqual(view.selectedRows(), [0, 1, 2, 3])
        self.assertEqual(len(panel.tx_packets), 5)

    def test_unassigned_packets_do_not_block_run_or_start_timers(self):
        a, b = packet('a'), packet('b')
        a['bus'] = b['bus'] = 0
        panel = self.panel([a, b, packet('live', 0x456)])
        panel.set_mode('run')
        self.assertEqual(panel.mode, 'run')
        self.assertEqual(set(panel._frame_timers), {'live'})
        with self.assertRaisesRegex(ValueError, '미선택'):
            panel._packet_runtimes['a'].send()
        QTest.qWait(50)
        self.assertTrue(self.sent)
        self.assertTrue(all(msg.arbitration_id == 0x456 for msg in self.sent))
        dialog = RegisteredPacketDialog({}, packet=a)
        self.addCleanup(dialog.close)
        self.assertEqual(dialog.combo_bus.currentText(), '미선택')
        self.assertEqual(dialog.get_packet_data()['bus'], 0)

    def test_packet_grid_columns_and_drop_move_entire_packet(self):
        first = packet('a')
        first.update(is_fd=True, is_brs=True, length=12, data=[0] * 12, note='Test note')
        panel = self.panel([first, packet('b', 0x124)])
        registry = PacketRegistryDialog(panel)
        registry.show()
        self.app.processEvents()
        self.addCleanup(registry.close)
        model = registry.list.model()
        self.assertEqual([model.data(model.index(0, col)) for col in range(8)],
                         ['1', '0x123', 'Test', 'FD', 'ON', '12', '20 ms', 'Test note'])
        self.assertEqual(model.data(model.index(0, 7), Qt.ToolTipRole), 'Test note')
        registry.list.setCurrentRow(0)
        target = registry.list.visualRect(model.index(1, 0))
        accepted = []
        event = SimpleNamespace(source=lambda: registry.list,
                                pos=lambda: QPoint(target.center().x(), target.bottom() - 1),
                                setDropAction=lambda action: None, accept=lambda: accepted.append(True),
                                ignore=lambda: self.fail('Internal move was rejected'))
        registry.list.dropEvent(event)
        self.assertTrue(accepted)
        self.assertEqual([p['packet_id'] for p in registry.packets], ['b', 'a'])
        self.assertEqual(model.data(model.index(1, 7)), 'Test note')
        self.assertEqual(registry.list.currentRow(), 1)

    def test_copied_packet_preserves_format_and_payload_when_changing_bus(self):
        for fd, brs, length in [(True, True, 32), (True, False, 12), (False, False, 7)]:
            with self.subTest(fd=fd, brs=brs, length=length):
                original = packet()
                original.update(is_fd=fd, is_brs=brs, length=length, data=list(range(length)))
                dialog = RegisteredPacketDialog({}, packet=original)
                self.addCleanup(dialog.close)
                for bus in (1, 2, 3):
                    dialog.combo_bus.setCurrentText(str(bus))
                    result = dialog.get_packet_data()
                    self.assertEqual(result['bus'], bus)
                    for key in ('is_fd', 'is_brs', 'length', 'data', 'cycle'):
                        self.assertEqual(result[key], original[key])

    def test_registry_reorder_preserves_identity_and_copy_delete_target(self):
        original = [packet('a', 0x123), packet('b', 0x124), packet('c', 0x125)]
        panel = self.panel(original)
        registry = PacketRegistryDialog(panel)
        model = registry.list.model()
        self.assertTrue(model.moveRows(QModelIndex(), 0, 1, QModelIndex(), 3))
        self.assertEqual([p['packet_id'] for p in registry.packets], ['b', 'c', 'a'])
        self.assertTrue(model.moveRows(QModelIndex(), 2, 1, QModelIndex(), 0))
        self.assertEqual(registry.packets, original)
        self.assertTrue(model.moveRows(QModelIndex(), 0, 1, QModelIndex(), 3))
        registry.list.setCurrentRow(0)
        registry.copy_packet()
        self.assertEqual(panel._packet_clipboard[0]['packet_id'], 'b')
        registry.remove_packet()
        self.assertEqual([p['packet_id'] for p in registry.packets], ['c', 'a'])
        registry.reject()
        self.assertEqual(panel.tx_packets, original)

    def test_registry_copy_paste_creates_independent_packet(self):
        panel = self.panel([packet()])
        registry = PacketRegistryDialog(panel)
        registry.list.setCurrentRow(0)
        registry.copy_packet()
        copied = copy.deepcopy(panel._packet_clipboard)
        with patch('src.user_panel_v2.packets.RegisteredPacketDialog') as editor:
            registry.paste_packet()
            editor.assert_not_called()
        self.assertEqual(len(registry.packets), 2)
        self.assertEqual(registry.packets[1]['bus'], 0)
        self.assertNotEqual(registry.packets[0]['packet_id'], registry.packets[1]['packet_id'])
        self.assertEqual(registry.packets[1]['data'], copied[0]['data'])
        registry.packets[1]['data'][0] = 0
        self.assertEqual(panel._packet_clipboard, copied)
        self.assertEqual(len(panel.tx_packets), 1)
        self.assertEqual(registry.list.currentRow(), 1)
        registry.close()

    def test_registry_paste_cancel_and_duplicate_keep_packets(self):
        panel = self.panel([packet()])
        registry = PacketRegistryDialog(panel)
        registry.list.setCurrentRow(0)
        registry.copy_packet()
        registry.paste_packet()
        registry.paste_packet()
        self.assertEqual([p['bus'] for p in registry.packets], [1, 0, 0])
        with patch('src.user_panel_v2.packets.RegisteredPacketDialog') as editor:
            editor.return_value.exec_.return_value = QDialog.Rejected
            registry.edit_packet(True)
            self.assertEqual(len(registry.packets), 3)
            editor.return_value.exec_.return_value = QDialog.Accepted
            editor.return_value.get_packet_data.return_value = copy.deepcopy(panel._packet_clipboard[0])
            with patch('src.user_panel_v2.packets.QMessageBox.warning') as warning:
                registry.edit_packet(True)
                warning.assert_called_once()
            self.assertEqual(len(registry.packets), 3)
        registry.close()

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def panel(self, packets, configs=(), db=None):
        self.sent = []
        main = SimpleNamespace(buses={1: SimpleNamespace(send=self.sent.append)},
                               bus_capabilities={1: {'is_fd': False}}, record_tx_activity=lambda *args, **kwargs: None)
        panel = UserPanelWindow(main, db or {})
        panel._load_panel_data(dict(tx_packets=copy.deepcopy(packets), widgets=copy.deepcopy(list(configs))))
        panel.show()
        self.app.processEvents()
        self.addCleanup(panel.close)
        return panel

    def test_run_starts_packets_without_tools_and_standard_stops(self):
        p = packet()
        panel = self.panel([p])
        panel.set_mode('run')
        QTest.qWait(65)
        self.assertGreaterEqual(len(self.sent), 1)
        self.assertEqual(self.sent[0].data, bytes(p['data']))
        panel.set_mode('standard')
        count = len(self.sent)
        QTest.qWait(50)
        self.assertEqual(len(self.sent), count)
        self.assertFalse(panel._frame_timers)

    def test_zero_only_changed_bits_and_all_tool_overlays(self):
        p = packet(cycle=0)
        a, b = tool(p), tool(p, 'second', start=32, kind='toggle')
        panel = self.panel([p], [a, b])
        panel.set_mode('run')
        self.assertEqual(self.sent, [])
        panel._emit_tx(a, 7)
        self.assertEqual(self.sent, [])
        panel._emit_tx(a, 20)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sent[-1].data, bytes([0xAA] * 3 + [20, 0] + [0xAA] * 3))
        panel._emit_tx(a, 20)
        self.assertEqual(len(self.sent), 1)
        panel._emit_tx(b, 1)
        self.assertEqual(self.sent[-1].data[3:5], bytes([20, 1]))
        self.assertEqual(panel.tx_packets[0]['data'], p['data'])
        panel.set_mode('standby')
        panel._emit_tx(a, 21)
        self.assertEqual(len(self.sent), 2)

    def test_periodic_initial_tool_value_and_counter_then_crc(self):
        p = packet()
        p['data'] = [0, 0, 0, 0, 0xAA, 0xAA, 0xAA, 0xAA]
        p['signal_counters'] = {
            'Count': dict(mode='up', min=0, max=255, step=1),
            'CRC': dict(mode='crc16', start=2, size=6, extra=[0, 0], poly=0x1021,
                        init=0xFFFF, xorout=0, refin=False, refout=False)}
        msg = Message(p['id'], 'Test', 8, [Signal('CRC', 0, 16), Signal('Count', 16, 8), Signal('Value', 24, 8)])
        panel = self.panel([p], [tool(p)], {1: {p['id']: msg}})
        panel.set_mode('run')
        panel._flush_frame('p')
        panel._flush_frame('p')
        self.assertEqual([m.data[2] for m in self.sent], [0, 1])
        for m in self.sent:
            self.assertEqual(m.data[3], 7)
            self.assertEqual(m.data[4:], bytes([0xAA] * 4))
            self.assertEqual(int.from_bytes(m.data[:2], 'little'), calculate_crc16_ccitt_false(m.data[2:] + b'\0\0'))

    def test_failed_send_keeps_counter_state(self):
        p = packet()
        p['data'][0] = 3
        p['signal_counters'] = {'Value': dict(mode='up', min=0, max=10, step=1)}
        msg = Message(p['id'], 'Test', 8, [Signal('Value', 0, 8)])
        panel = self.panel([p], db={1: {p['id']: msg}})
        runtime = PacketRuntime(p, panel.db_messages, panel.main_window)
        with patch.object(panel.main_window.buses[1], 'send', side_effect=RuntimeError('failed')):
            with self.assertRaises(RuntimeError):
                runtime.send()
        self.assertEqual(runtime.states, {})
        runtime.send()
        self.assertEqual(self.sent[0].data[0], 3)

    def test_rx_counter_syncs_packet_runtime_then_counts_when_rx_is_unchanged(self):
        p = packet(cycle=0)
        p['signal_counters'] = {'Alive': dict(mode='rx_up', min=0, max=255, step=1)}
        msg = Message(p['id'], 'Test', 8, [Signal('Alive', 0, 8)])
        panel = self.panel([p], db={1: {p['id']: msg}})
        panel.main_window.rx_threads = {
            1: SimpleNamespace(latest_rx_data={p['id']: (bytes([7] + [0] * 7), 1.0)})}
        runtime = PacketRuntime(p, panel.db_messages, panel.main_window)

        runtime.send()
        runtime.send()
        panel.main_window.rx_threads[1].latest_rx_data[p['id']] = (bytes([12] + [0] * 7), 2.0)
        runtime.send()

        self.assertEqual([message.data[0] for message in self.sent], [7, 8, 12])

    def test_registered_packet_checks_connection_type_at_each_send(self):
        for packet_fd in (False, True):
            p = packet(cycle=0)
            p['is_fd'] = packet_fd
            panel = self.panel([p])
            runtime = PacketRuntime(p, {}, panel.main_window)
            panel.main_window.bus_capabilities[1]['is_fd'] = not packet_fd
            with self.assertRaisesRegex(ValueError, '타입 불일치'):
                runtime.send()
            self.assertEqual(self.sent, [])
            self.assertEqual(runtime.alive, 0)
            panel.main_window.bus_capabilities[1]['is_fd'] = packet_fd
            runtime.send()
            self.assertEqual(len(self.sent), 1)
            self.assertEqual(self.sent[0].is_fd, packet_fd)
            panel.main_window.buses[1] = None
            with self.assertRaisesRegex(ValueError, '연결되지'):
                runtime.send()
            self.assertEqual(runtime.alive, 1)
            panel.close()

    def test_periodic_failure_stops_only_affected_packet(self):
        p, q = packet(), packet('q', can_id=0x124)
        q.update(bus=2, is_fd=True)
        panel = self.panel([p, q])
        panel.main_window.buses[2] = SimpleNamespace(send=self.sent.append)
        panel.main_window.bus_capabilities[2] = {'is_fd': False}
        panel.set_mode('run')
        panel._flush_frame('q')
        self.assertNotIn('q', panel._frame_timers)
        self.assertIn('p', panel._frame_timers)
        panel._flush_frame('p')
        self.assertEqual([m.arbitration_id for m in self.sent], [p['id']])

    def test_registration_required_delay_and_tx_filter_rx_unchanged(self):
        dialog = RegisteredPacketDialog({})
        with self.assertRaises(ValueError):
            dialog.get_packet_data()
        dialog.edit_cycle.setValue(0)
        self.assertEqual(dialog.get_packet_data()['cycle'], 0)
        dialog.close()
        db = {1: {1: Message(1, 'Registered', 8, []), 2: Message(2, 'Other', 8, [])}}
        p = packet(can_id=1)
        tx = WidgetConfigDialog(db, fixed_behavior='tx', tx_packets=[p])
        self.assertEqual([tx.combo_message.itemData(i) for i in range(tx.combo_message.count())], [1])
        self.assertNotIn('tx_cycle_ms', tx.get_config()['binding'])
        self.assertNotIn('tx_cycle_mode', tx.get_config()['binding'])
        self.assertFalse(hasattr(tx, 'combo_tx_cycle_mode'))
        rx = WidgetConfigDialog(db, fixed_behavior='rx', tx_packets=[p])
        self.assertEqual([rx.combo_message.itemData(i) for i in range(rx.combo_message.count())], [None, 1, 2])
        tx.close()
        rx.close()

    def test_packet_save_restore_and_batch_rebind(self):
        p, q = packet(), packet('q', 0x456, 0)
        panel = self.panel([p, q], [tool(p, 'a'), tool(p, 'b', 32)])
        panel.selected_widget_ids = {'a', 'b'}
        panel.properties.refresh(force=True)
        props = panel.properties
        self.assertNotIn('tx_cycle_ms', props.fields)
        self.assertNotIn('is_fd', props.fields)
        props.checks['packet_id'].setChecked(True)
        props.fields['packet_id'].setCurrentIndex(props.fields['packet_id'].findData('q'))
        props.apply_checked()
        self.assertTrue(all(c['binding']['packet_id'] == 'q' for c in panel.widgets_config))
        saved = copy.deepcopy(panel._panel_data())
        panel._load_panel_data(saved)
        self.assertEqual(panel.tx_packets, [p, q])
        self.assertTrue(all(c['binding']['packet_id'] == 'q' for c in panel.widgets_config))

    def test_unregistered_legacy_tool_blocks_run(self):
        panel = self.panel([], [tool(packet())])
        with patch('src.user_panel_v2.window.QMessageBox.warning') as warning:
            panel.set_mode('run')
            warning.assert_not_called()
        self.assertIn('RUN 시작 불가', panel.system_log.text.toPlainText())
        self.assertNotEqual(panel.mode, 'run')
        self.assertFalse(self.sent)

    def test_run_reentry_preserves_selected_toggle_and_starts_periodic(self):
        p = packet(cycle=10)
        a, b = tool(p, 'a', kind='toggle'), tool(p, 'b', kind='toggle')
        a['binding']['tx_on_value'] = 15
        b['binding']['tx_on_value'] = 25
        panel = self.panel([p], [a, b])
        panel.set_mode('run')
        panel.widget_controls['a'].setChecked(True)
        panel.set_mode('standard')
        self.sent.clear()
        panel.set_mode('run')
        QTest.qWait(30)
        self.assertTrue(self.sent)
        self.assertTrue(all(m.data[3] == 15 for m in self.sent))

    def test_packet_edit_rebinds_tools_and_history_restores_registry(self):
        from src.user_panel_v2.packets import validate_tool
        p = packet()
        panel = self.panel([p], [tool(p)])
        panel.tx_packets[0].update(id=0x456, is_fd=True, is_brs=True, cycle=50)
        validate_tool(panel.tx_packets, panel.widgets_config[0])
        panel.rebuild_grid()
        b = panel.widgets_config[0]['binding']
        self.assertEqual((b['can_id'], b['is_fd'], b['brs']), (0x456, True, True))
        panel.undo_edit()
        self.assertEqual(panel.tx_packets[0]['id'], 0x123)
        self.assertEqual(panel.widgets_config[0]['binding']['can_id'], 0x123)
        panel.redo_edit()
        self.assertEqual(panel.tx_packets[0]['cycle'], 50)
