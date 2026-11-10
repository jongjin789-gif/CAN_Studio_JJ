import copy
import os
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import can
from PyQt5.QtCore import QEvent
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QTableWidgetItem
from cantools.database.can import Message, Signal
from cantools.database.conversion import BaseConversion

from src.can_threads import LogParserThread
from src.log_viewer import LogViewerWindow
from src.main_window import UniversalCANMonitor
from src.session_manager import SessionManager
from src.tx_schedule import bus_schedule
from src.user_panel_v2.window import UserPanelWindow


class LogPanelDisplayTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        # Earlier modules in the same process can leave deferred deletes and dead Python
        # wrappers; flush them before this class pumps the event loop with QTest.qWait.
        import gc
        gc.collect()
        cls.app.sendPostedEvents(None, QEvent.DeferredDelete)
        cls.app.processEvents()

    def setUp(self):
        self.addCleanup(lambda: self.app.sendPostedEvents(None, QEvent.DeferredDelete))
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        with patch.object(UniversalCANMonitor, 'search_can_channels'), patch.object(SessionManager, 'start'):
            self.main = UniversalCANMonitor()
        self.main.session.ready = False
        self.main.session.directory = Path(self.temp.name)
        self.main.session.autosave_path = Path(self.temp.name) / 'auto.pjjsettings'
        self.addCleanup(self.main.deleteLater)
        self.addCleanup(self.main.close)
        self.sent = []
        self.main.buses[1] = SimpleNamespace(send=self.sent.append, shutdown=lambda: None)
        self.main.db_messages[1] = {
            0x130: Message(0x130, 'State', 1, [Signal('State', 0, 8,
                conversion=BaseConversion.factory(choices={0: 'OFF', 1: 'ON', 2: 'Error_Func'}))]),
            0x131: Message(0x131, 'Reading', 1, [Signal('Value', 0, 8)]),
        }
        rules = [dict(enabled=True, source='enum', raw=raw, target='background', color=color)
                 for raw, color in ((1, '#DCFCE7'), (2, '#FEE2E2'))]
        configs = [dict(id='state', widget_type='label', behavior='rx', binding=dict(
                        bus=1, can_id=0x130, signal_name='State', start_bit=0, bit_length=8, label_color_rules=rules)),
                   dict(id='value', widget_type='label', behavior='rx', row=4, binding=dict(
                        bus=1, can_id=0x131, signal_name='Value', start_bit=0, bit_length=8))]
        self.panel = UserPanelWindow(self.main, self.main.db_messages)
        self.main.user_panel_window = self.panel
        self.panel._load_panel_data(dict(widgets=configs, grid=dict(rows=24, cols=36)))
        self.panel.set_mode('standby')
        self.panel.show()
        self.main.ui_update_timer.stop()
        self.app.processEvents()

    def viewer(self, entries):
        with patch.object(LogViewerWindow, 'start_parsing'):
            view = LogViewerWindow('unused.trc', self.main.db_messages, self.main)
        self.addCleanup(view.deleteLater)
        self.addCleanup(view.close)
        view.raw_log_data = entries
        view.log_table.setRowCount(len(entries))
        for row in range(len(entries)):
            for column in range(7):
                view.log_table.setItem(row, column, QTableWidgetItem(''))
        return view

    def wait_for(self, predicate):
        deadline = time.monotonic() + 2
        while not predicate() and time.monotonic() < deadline:
            QTest.qWait(10)
        self.assertTrue(predicate())

    def log_entries(self):
        source = Path(self.temp.name) / 'replay.txt'
        source.write_text('Timestamp;Type;ID;Data\n'
                          '2026_10_23T12_00_00_000;0;130;01\n'
                          '2026_10_23T12_00_00_050;8;131;2A\n', encoding='utf-8')
        results = []
        parser = LogParserThread(str(source), self.main.db_messages)
        parser.finished_signal.connect(lambda *args: results.append(args[2]))
        parser.run()
        return results[0]

    def test_selected_rx_and_tx_log_rows_update_enum_colors_without_echo(self):
        entries = self.log_entries()
        self.assertEqual([entry['direction'] for entry in entries], ['Rx', 'Tx'])
        tx = copy.deepcopy(entries[0])
        tx.update(direction='Tx', data=b'\x02')
        view = self.viewer([entries[0], tx])
        control = self.panel.widget_controls['state']
        for row, text, color in ((0, 'ON', '#dcfce7'), (1, 'Error_Func', '#fee2e2')):
            view.log_table.selectRow(row)
            view.on_send_single_clicked()
            self.wait_for(lambda: control.text() == text)
            self.assertIn(color, control.styleSheet())
        self.assertEqual([bytes(msg.data) for msg in self.sent], [b'\x01', b'\x02'])
        self.assertIsNone(self.main.rx_threads[1])
        self.assertFalse(self.main.ui_update_timer.isActive())

    def test_continuous_log_replay_updates_each_matching_control(self):
        view = self.viewer(self.log_entries())
        view.start_sending()
        self.wait_for(lambda: len(self.sent) == 2 and not view.is_sending
                      and self.panel.widget_controls['value'].text() == '42')
        self.assertEqual(self.panel.widget_controls['state'].text(), 'ON')

    def test_log_replay_keeps_main_and_user_periodic_tx_running(self):
        packet = dict(bus=1, id=0x140, length=1, data=[0], is_fd=False,
                      cycle=10, count=0, crc_type='N/A', symbol='', note='')
        self.main.tx_panel.add_packet_to_tree(packet)
        item = self.main.tx_panel.tree.topLevelItem(0)
        self.panel._load_panel_data(dict(widgets=[], tx_packets=[dict(packet, packet_id='periodic', id=0x141)]))
        self.panel.set_mode('run')
        item.start_timer()
        view = self.viewer(self.log_entries())
        view.start_sending()
        self.assertTrue(item.timer.isActive())
        self.assertTrue(self.panel._frame_timers['periodic'].isActive())
        time.sleep(.15)  # Replay GUI is blocked; both periodic senders keep running.
        ids = [frame.arbitration_id for frame in self.sent]
        self.assertGreaterEqual(ids.count(0x140), 5)
        self.assertGreaterEqual(ids.count(0x141), 5)
        self.wait_for(lambda: not view.is_sending)
        self.assertTrue(item.timer.isActive())
        self.assertTrue(self.panel._frame_timers['periodic'].isActive())

    def test_continuous_replay_retries_busy_bus_without_advancing_or_dropping(self):
        view = self.viewer(self.log_entries())
        with patch.object(bus_schedule(self.main, 1), 'send_log', return_value=False):
            view.start_sending()
            self.assertEqual(view.current_send_index, 0)
            self.assertFalse(self.sent)
        self.wait_for(lambda: not view.is_sending)
        self.assertEqual([bytes(frame.data) for frame in self.sent], [b'\x01', b'\x2a'])

    def test_selected_retry_keeps_original_packet_and_stop_cancels_pending_send(self):
        view = self.viewer(self.log_entries())
        view.log_table.selectRow(0)
        with patch.object(bus_schedule(self.main, 1), 'send_log', return_value=False):
            view.on_send_single_clicked()
            self.assertIsNotNone(view._pending_single)
            self.assertFalse(self.sent)
            view.log_table.selectRow(1)
        self.wait_for(lambda: len(self.sent) == 1)
        self.assertEqual(self.sent[0].arbitration_id, 0x130)
        with patch.object(bus_schedule(self.main, 1), 'send_log', return_value=False):
            view.on_send_single_clicked()
        view.stop_sending()
        view._retry_single_send()
        self.assertIsNone(view._pending_single)
        self.assertEqual(len(self.sent), 1)

    def test_successful_fd_send_updates_manual_binding_without_dbc(self):
        cfg = copy.deepcopy(self.panel._cfg_by_id('value'))
        cfg['binding'].update(can_id=0x230, signal_name=None, start_bit=120, bit_length=8, dlc=16, is_fd=True)
        self.panel._load_panel_data(dict(widgets=[cfg]))
        self.panel.set_mode('standby')
        self.main.bus_capabilities[1]['is_fd'] = True
        view = self.viewer([dict(bus=1, can_id=0x230, offset_ms=0, type='FB', dlc=16,
                                 data=bytes(15) + b'\x2A')])
        view.log_table.selectRow(0)
        view.on_send_single_clicked()
        self.wait_for(lambda: self.panel.widget_controls['value'].text() == '42')
        self.assertTrue(self.sent[0].is_fd)
        self.assertTrue(self.sent[0].bitrate_switch)

    def test_log_of_closed_bus_is_blocked_not_sent_to_another_bus(self):
        entry = self.log_entries()[0]
        entry['bus'] = 2
        view = self.viewer([entry])
        view.log_table.selectRow(0)
        with patch('src.log_viewer.QMessageBox.warning') as warning:
            view.on_send_single_clicked()
            view.start_sending()
        self.assertEqual(warning.call_count, 2)
        self.assertIn('BUS 2', warning.call_args[0][2])
        self.assertFalse(view.is_sending)
        self.assertFalse(view.btn_send.isChecked())
        self.assertEqual(self.sent, [])

    def test_fd_log_on_classic_bus_is_blocked(self):
        entries = self.log_entries()
        fd = dict(bus=1, can_id=0x230, offset_ms=100, type='FD', dlc=12, data=bytes(12))
        view = self.viewer(entries + [fd])
        with patch('src.log_viewer.QMessageBox.warning') as warning:
            view.start_sending()
        warning.assert_called_once()
        self.assertIn('Classic', warning.call_args[0][2])
        self.assertEqual(self.sent, [])     # whole replay blocked, not partially sent

    def test_send_buttons_follow_bus_open_and_close(self):
        bus = self.main.buses[1]
        self.main.buses[1] = None
        view = self.viewer(self.log_entries())
        view.log_table.selectRow(0)
        self.assertFalse(view.btn_send.isEnabled())
        self.assertFalse(view.btn_send_single.isEnabled())
        self.main.buses[1] = bus
        self.main.bus_state_changed.emit(1)
        self.assertTrue(view.btn_send.isEnabled())
        self.assertTrue(view.btn_send_single.isEnabled())
        self.main.buses[1] = None
        self.main.bus_state_changed.emit(1)
        self.assertFalse(view.btn_send.isEnabled())

    def test_send_failure_does_not_update_display(self):
        self.main.buses[1].send = Mock(side_effect=can.CanError('send failed'))
        view = self.viewer(self.log_entries())
        view.log_table.selectRow(0)
        with patch('src.log_viewer.show_error') as error:
            view.on_send_single_clicked()
        error.assert_called_once()
        self.main.flush_panel_messages()
        self.assertEqual(self.panel.widget_controls['state'].text(), '-')
        self.assertEqual(self.panel.latest_raw_by_msg, {})

    def test_tx_rx_display_is_batched_but_sequence_receives_only_real_rx(self):
        with patch.object(self.panel, 'on_message_update', wraps=self.panel.on_message_update) as update, \
             patch.object(self.panel, 'on_sequence_receive') as receive:
            for raw in range(100):
                self.main.route_raw_msg_to_record(raw, 0x131, bytes([raw]), False, False, False, False, 1)
            self.main.route_raw_msg_to_record(100, 0x130, b'\x01', False, False, False, True, 1)
            self.main.route_raw_msg_to_record(101, 0x130, b'\x02', False, True, False, True, 1)
            self.main.flush_panel_messages()
            self.assertEqual(update.call_count, 2)
            receive.assert_called_once()
            self.assertEqual(self.panel.widget_controls['value'].text(), '99')
            self.assertEqual(self.panel.widget_controls['state'].text(), 'ON')
        with patch.object(self.panel, 'on_sequence_receive') as receive:
            self.main.record_tx_activity(1, 0x130, b'\x02', False)
            self.main.flush_panel_messages()
            receive.assert_not_called()

    def test_reset_or_disconnect_drops_pending_display_frames(self):
        self.main.record_tx_activity(1, 0x130, b'\x01', False)
        self.main.reset_tree_values(1)
        self.main.flush_panel_messages()
        self.assertEqual(self.panel.widget_controls['state'].text(), '-')
        self.main.record_tx_activity(1, 0x130, b'\x01', False)
        self.main.close_can(1)
        self.main.flush_panel_messages()
        self.assertEqual(self.panel.widget_controls['state'].text(), '-')

    def test_log_replay_also_updates_rx_grid_cells(self):
        self.panel._load_panel_data(dict(widgets=[dict(
            id='grid', widget_type='grid_view', behavior='rx', row_span=10, col_span=28,
            binding=dict(grid_signals=[dict(bus=1, can_id=0x131, signal_name='Value')]))]))
        self.panel.set_mode('standby')
        control = self.panel.widget_controls['grid']
        view = self.viewer(self.log_entries())
        view.log_table.selectRow(1)
        view.on_send_single_clicked()
        self.wait_for(lambda: control.table.item(0, len(control.columns) - 1).text() == '42')


if __name__ == '__main__':
    unittest.main()
