import copy
import os
import time
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PyQt5.QtWidgets import QApplication
from PyQt5.QtTest import QTest
from src.user_panel_v2.window import UserPanelWindow
from src.user_panel_v2.sequence import SequenceControl
from src.user_panel_v2.connection_dialog import ConnectionDialog
from src.main_window import UniversalCANMonitor
from src.session_manager import SessionManager
from src.user_panel_v2.system_log import SystemLog


def packet(key='p', bus=1):
    return dict(packet_id=key, bus=bus, id=0x123 if key == 'p' else 0x124,
                data=[1] * 8, length=8, cycle=20, is_fd=False, is_brs=False)


class PanelOperationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def panel(self, steps, packets=None):
        self.sent = []
        main = SimpleNamespace(buses={1: SimpleNamespace(send=self.sent.append), 2: None, 3: None},
                               bus_capabilities={1: {'is_fd': False}}, communication_password='communication-secret')
        panel = UserPanelWindow(main, {}, security_config={'enabled': True, 'password': 'edit-secret'})
        panel._load_panel_data(dict(widgets=[], tx_packets=packets or [packet()], init_steps=steps))
        panel.set_mode('standby')
        panel.show()
        self.addCleanup(panel.close)
        self.app.processEvents()
        return panel

    def test_init_missing_bus_blocks_before_first_command(self):
        p, q = packet(), packet('q', 2)
        panel = self.panel([dict(kind='CMD', packet=p), dict(kind='CMD', packet=q)], [p, q])
        panel.set_mode('run')
        self.assertEqual(panel.mode, 'standby')
        self.assertFalse(self.sent)
        self.assertFalse(panel._frame_timers)
        self.assertIn('INIT 2단계', panel.system_log.text.toPlainText())

    def test_legacy_init_receive_is_preserved_but_blocks_normal_run(self):
        p = packet()
        steps = [dict(kind='CMD', packet=p), dict(kind='RCV', packet=p, mask=[255] * 8, timeout_ms=80)]
        panel = self.panel(steps)
        panel.set_mode('run')
        self.assertEqual(panel.mode, 'standby')
        self.assertFalse(panel._init_running)
        self.assertFalse(panel._frame_timers)
        self.assertFalse(self.sent)
        self.assertEqual(panel.init_steps, steps)
        self.assertIn('RCV', panel.system_log.text.toPlainText())

    def test_sequence_force_skips_receive_and_runs_remaining_steps(self):
        p, q = packet(), packet('q')
        panel = self.panel([], [p, q])
        panel.set_mode('run')
        steps = [dict(kind='STOP', target_packet_id='*'), dict(kind='CMD', packet=q),
                 dict(kind='RCV', packet=q, mask=[255]*8, timeout_ms=5000),
                 dict(kind='DEL', delay_ms=5), dict(kind='START', target_packet_id='p'),
                 dict(kind='DEL', delay_ms=30), dict(kind='STOP', target_packet_id='p')]
        cfg = dict(id='seq', title='Test', widget_type='sequence', behavior='tx',
                   binding=dict(sequence_steps=steps))
        control = SequenceControl(panel, cfg)
        self.assertEqual(control.force_button.minimumWidth(), panel.grid_cell_size)
        self.assertEqual(control.force_button.maximumWidth(), panel.grid_cell_size)
        panel.widget_controls['seq'] = control
        self.addCleanup(control.deleteLater)

        with patch('src.user_panel_v2.window.verify_communication_password', return_value=True) as verify:
            control.force_toggle()
            verify.assert_not_called()
        QTest.qWait(100)

        self.assertFalse(control.running)
        self.assertEqual(panel.mode, 'run')
        self.assertEqual(panel._paused_packets, {'p', 'q'})
        self.assertEqual({message.arbitration_id for message in self.sent}, {p['id'], q['id']})
        log = control.log.toPlainText()
        self.assertIn('RCV 1단계 건너뜀', log)
        self.assertIn('CMD BUS_1 0x124', log)
        self.assertIn('DEL', log)
        self.assertIn('주기 전송 시작', log)
        self.assertIn('주기 전송 정지', log)

    def test_sequence_force_runs_without_password_and_has_execution_icon(self):
        p = packet()
        panel = self.panel([], [p])
        panel.set_mode('run')
        control = SequenceControl(panel, dict(id='seq', title='Test', widget_type='sequence',
                                               behavior='tx', binding=dict(sequence_steps=[dict(kind='DEL', delay_ms=1000), dict(kind='CMD', packet=p)])))
        self.addCleanup(control.deleteLater)
        self.assertFalse(control.button.icon().isNull())
        ready_icon = control.button.icon().cacheKey()
        with patch('src.user_panel_v2.window.verify_communication_password', return_value=False) as verify:
            control.force_toggle()
            verify.assert_not_called()
        self.assertTrue(control.running)
        self.assertNotEqual(control.button.icon().cacheKey(), ready_icon)
        control.button.click()
        self.assertFalse(control.running)
        self.assertEqual(panel.mode, 'run')
        panel.set_mode('standby')
        control.force_toggle()
        self.assertFalse(control.running)

    def test_packet_control_skips_commands_and_receives_including_failure_steps(self):
        def wait_for_completion(control):
            # The GUI event loop may be busy draining earlier tests' widgets.
            deadline = time.monotonic() + 2
            while control.running and time.monotonic() < deadline:
                QTest.qWait(10)

        p = packet()
        panel = self.panel([], [p])
        panel.set_mode('run')
        steps = [dict(kind='CMD', packet=p), dict(kind='RCV', packet=p),
                 dict(kind='START', target_packet_id='p'), dict(kind='DEL', delay_ms=5),
                 dict(kind='STOP', target_packet_id='p')]
        failures = [dict(kind='CMD', packet=p), dict(kind='RCV', packet=p),
                    dict(kind='STOP', target_packet_id='p')]
        control = SequenceControl(panel, dict(id='seq', title='Test', widget_type='sequence',
            behavior='tx', binding=dict(sequence_steps=steps, sequence_failure_steps=failures)))
        self.addCleanup(control.deleteLater)
        self.assertFalse(control.packet_button.icon().isNull())
        with patch.object(panel, 'set_packet_transmission') as transmission, patch.object(control, '_send') as send:
            control.packet_button.click()
            wait_for_completion(control)
            self.assertEqual([call.args for call in transmission.call_args_list], [('p', True), ('p', False)])
            send.assert_not_called()
            self.assertFalse(control.running)
        with patch.object(panel, 'set_packet_transmission', side_effect=[ValueError('test failure'), None]) as transmission, patch.object(control, '_send') as send:
            control.packet_button.click()
            wait_for_completion(control)
            self.assertEqual([call.args for call in transmission.call_args_list], [('p', True), ('p', False)])
            send.assert_not_called()
            self.assertTrue(control.in_failure)
            self.assertFalse(control.running)
        self.assertIn('CMD·RCV 제외', control.log.toPlainText())

    def test_packet_control_without_start_stop_does_not_execute(self):
        panel = self.panel([])
        panel.set_mode('run')
        control = SequenceControl(panel, dict(id='seq', binding=dict(sequence_steps=[dict(kind='DEL', delay_ms=100)])))
        self.addCleanup(control.deleteLater)
        control.packet_button.click()
        self.assertFalse(control.running)
        self.assertFalse(control.timer.isActive())
        self.assertIn('START·STOP 단계가 없어', control.log.toPlainText())

    def test_force_run_is_not_a_panel_mode(self):
        panel = self.panel([])
        self.assertFalse(hasattr(panel, 'btn_force_run'))
        self.assertFalse(hasattr(panel, 'force_run'))
        self.assertNotIn('Force RUN', panel.label_mode.text())

    def test_init_type_change_during_delay_stops_before_next_command(self):
        p = packet()
        panel = self.panel([dict(kind='DEL', delay_ms=300), dict(kind='CMD', packet=p)])
        panel.set_mode('run')
        panel.main_window.bus_capabilities[1]['is_fd'] = True
        panel.init_control._check_connection()
        self.assertEqual(panel.mode, 'standby')
        self.assertFalse(panel.init_control.running)
        self.assertFalse(self.sent)
        self.assertIn('타입 불일치', panel.system_log.text.toPlainText())

    def main(self, panel_only=False):
        with patch.object(SessionManager, 'start'), patch.object(UniversalCANMonitor, 'search_can_channels'):
            main = UniversalCANMonitor(user_panel_only=panel_only)
        main.session.ready = False
        self.addCleanup(main.close)
        return main

    def test_open_panel_preserves_bus_and_starts_standard(self):
        main = self.main()
        main.buses[1] = Mock()
        with patch.object(main, 'open_can') as connect, patch.object(main, 'close_can') as disconnect:
            main.open_user_panel()
            self.assertEqual(main.user_panel_window.mode, 'standby')
            connect.assert_not_called()
            disconnect.assert_not_called()
        main.buses[1] = None

    def test_communication_view_shares_settings_and_stops_before_changes(self):
        main = self.main()
        main.open_user_panel()
        panel = main.user_panel_window
        dialog = ConnectionDialog(panel)
        self.addCleanup(dialog.close)
        panel.set_mode('run')
        source = main.combo_bitrate[1]
        self.assertGreater(source.count(), 1)
        index = (source.currentIndex() + 1) % source.count()
        dialog.fields[1, 'bitrate'].setCurrentIndex(index)
        self.assertEqual(source.currentIndex(), index)
        self.assertEqual(panel.mode, 'standby')
        source.setCurrentIndex((index + 1) % source.count())
        dialog.refresh()
        self.assertEqual(dialog.fields[1, 'bitrate'].currentIndex(), source.currentIndex())
        main.buses[1] = Mock()
        dialog.refresh()
        self.assertFalse(dialog.fields[1, 'bitrate'].isEnabled())
        main.buses[1] = None

    def test_panel_only_close_shuts_down_main(self):
        main = self.main(panel_only=True)
        main.open_user_panel()
        self.assertFalse(main.isVisible())
        with patch.object(main, 'close_can') as disconnect:
            main.user_panel_window.close()
            self.assertTrue(main._is_closing)
            self.assertEqual(disconnect.call_count, 4)
        self.assertFalse(main.user_panel_window.isVisible())

    def test_panel_only_session_keeps_dbc_and_configuration(self):
        from cantools.database.can import Database, Message, Signal
        from src.session_storage import write_session
        main = self.main(panel_only=True)
        main.open_user_panel()
        db = Database(messages=[Message(0x123, 'Test', 8, [Signal('Value', 0, 8)])])
        raw = db.as_dbc_string().encode('cp1252')
        main.install_database(db, 1, 'test.dbc', raw)
        index = (main.combo_bitrate[1].currentIndex() + 1) % main.combo_bitrate[1].count()
        main.combo_bitrate[1].setCurrentIndex(index)
        main.user_panel_window._load_panel_data(dict(widgets=[], tx_packets=[packet()],
            init_steps=[dict(kind='RCV', packet=packet(), mask=[255]*8, timeout_ms=100)]))
        with tempfile.TemporaryDirectory() as directory:
            main.session.autosave_path = Path(directory) / 'auto.pjjsettings'
            path = Path(directory) / 'settings.pjjsettings'
            write_session(path, main.session.capture())
            with patch.object(main.session, 'report') as report:
                self.assertTrue(main.session.load(path))
                report.assert_not_called()
        self.assertTrue(main.user_panel_window.isVisible())
        self.assertFalse(main.isVisible())
        self.assertEqual(main.user_panel_window.mode, 'standby')
        self.assertEqual(main.combo_bitrate[1].currentIndex(), index)
        self.assertIn(0x123, main.db_messages[1])
        self.assertEqual(main.user_panel_window.init_steps[0]['kind'], 'RCV')

    def test_log_is_bounded_and_repeated_errors_are_coalesced(self):
        log = SystemLog()
        self.addCleanup(log.close)
        log.append('INIT: 전체 과정 완료 · OK')
        for _ in range(5):
            log.append('BUS 2 미연결', 'ERROR')
        lines = log.text.toPlainText().splitlines()
        self.assertEqual(log.text.blockCount(), 2)
        self.assertEqual(len(lines), 2)
        self.assertIn('전체 과정 완료 · OK', lines[0])
        self.assertIn('반복 5회', lines[1])
        for index in range(2100):
            log.append(str(index))
        self.assertEqual(log.text.blockCount(), 2000)

    def test_launch_flags_are_independent_of_session(self):
        import main as entry
        import json
        from unittest.mock import mock_open
        with patch('main.os.path.exists', return_value=True), patch('builtins.open', mock_open(read_data=json.dumps(
                {'viewer_mode_only': True, 'user_panel_only': True}))):
            self.assertEqual(entry.get_launch_options(), {'viewer_mode_only': False, 'user_panel_only': True})
