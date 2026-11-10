import copy
import os
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PyQt5.QtWidgets import QApplication
from PyQt5.QtTest import QTest
from src.user_panel_v2.window import UserPanelWindow
from src.user_panel_v2.sequence import SequenceControl, validate_steps
from src.user_panel_v2.sequence_dialog import SequenceDialog, PacketActionDialog
from src.user_panel_v2.config_dialog import WidgetConfigDialog


def packet(ident='p', cycle=10):
    return dict(packet_id=ident, bus=1, id=0x123 if ident == 'p' else 0x456,
                data=[1] * 8, length=8, cycle=cycle, is_fd=False, is_brs=False,
                note='', symbol=ident, count=0, crc_type='N/A')


def action(kind, target='*'):
    return dict(kind=kind, target_packet_id=target)


def receive(p, timeout=15):
    return dict(kind='RCV', packet=copy.deepcopy(p), mask=[255]*8, timeout_ms=timeout)


class InitSequenceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make_panel(self, packets, init=None):
        self.sent = []
        main = SimpleNamespace(buses={1: SimpleNamespace(send=self.sent.append)},
                               bus_capabilities={1: {'is_fd': False}}, record_tx_activity=lambda *args, **kwargs: None)
        panel = UserPanelWindow(main, {})
        panel._load_panel_data(dict(tx_packets=packets, init_steps=init or [], widgets=[]))
        panel.show()
        self.app.processEvents()
        self.addCleanup(panel.close)
        return panel

    def control(self, panel, steps, failures=None):
        cfg = dict(id='seq', title='Test', widget_type='sequence', behavior='tx',
                   binding=dict(sequence_steps=steps, sequence_failure_steps=failures or []))
        ctrl = SequenceControl(panel, cfg)
        panel.widget_controls['seq'] = ctrl
        self.addCleanup(ctrl.deleteLater)
        return ctrl

    def test_init_before_periodic_and_persistent_stop(self):
        p, q = packet(), packet('q')
        panel = self.make_panel([p, q], [dict(kind='DEL', delay_ms=60), action('STOP', 'p')])
        panel.set_mode('run')
        self.assertTrue(panel._init_running)
        self.assertFalse(panel._frame_timers)
        QTest.qWait(25)
        self.assertFalse(self.sent)
        QTest.qWait(80)
        self.assertFalse(panel._init_running)
        self.assertTrue(self.sent)
        self.assertTrue(all(m.arbitration_id == q['id'] for m in self.sent))
        self.assertIn('p', panel._paused_packets)
        panel.set_packet_transmission('p', True)
        QTest.qWait(30)
        self.assertIn(p['id'], [m.arbitration_id for m in self.sent])

    def test_init_sequence_panel_is_in_resizable_vertical_splitter(self):
        panel = self.make_panel([], [dict(kind='DEL', delay_ms=1000)])
        panel.resize(1200, 900)
        panel.set_mode('run')
        self.app.processEvents()

        splitter = panel.workspace_splitter
        self.assertIs(splitter.widget(0), panel.init_control)
        self.assertTrue(panel.init_control.isVisible())
        self.assertGreater(panel.init_control.maximumHeight(), 150)
        splitter.setSizes([210, 500, 140])
        self.app.processEvents()
        self.assertGreaterEqual(splitter.sizes()[0], 190)
        self.assertTrue(splitter.isCollapsible(0))

    def test_init_sequence_panel_hides_when_leaving_run(self):
        panel = self.make_panel([], [dict(kind='DEL', delay_ms=1000)])
        panel.set_mode('run')
        self.assertTrue(panel.init_control.isVisible())

        panel.set_mode('edit')

        self.assertTrue(panel.init_control.isHidden())
        self.assertFalse(panel._init_running)

    def test_init_cmd_start_stop_order_and_run_reentry(self):
        p = packet()
        panel = self.make_panel([p], [action('STOP'), dict(kind='CMD', packet=p),
                                     action('START'), dict(kind='DEL', delay_ms=60)])
        panel.set_mode('run')
        QTest.qWait(20)
        self.assertEqual(len(self.sent), 1)  # CMD works while periodic TX is stopped.
        self.assertFalse(panel._frame_timers)
        QTest.qWait(80)
        deadline = time.monotonic() + 0.3
        while len(self.sent) <= 1 and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.005)
        self.assertGreater(len(self.sent), 1)
        panel.set_mode('standard')
        self.sent.clear()
        panel.set_mode('run')
        QTest.qWait(20)
        self.assertEqual(len(self.sent), 1)
        self.assertTrue(panel._init_running)

    def test_standard_cancels_init_and_no_delayed_restart(self):
        panel = self.make_panel([packet()], [dict(kind='DEL', delay_ms=50), action('START')])
        panel.set_mode('run')
        panel.set_mode('standard')
        QTest.qWait(80)
        self.assertFalse(self.sent)
        self.assertFalse(panel._frame_timers)
        self.assertFalse(panel.init_control.running)

    def test_init_failure_returns_to_standard(self):
        p = packet()
        panel = self.make_panel([p], [dict(kind='CMD', packet=p)])
        with patch.object(panel.main_window.buses[1], 'send', side_effect=RuntimeError('TX error')):
            panel.set_mode('run')
        self.assertEqual(panel.mode, 'standby')
        self.assertFalse(panel._frame_timers)
        self.assertIn('INIT 실패', panel.system_log.text.toPlainText())

    def test_timeout_runs_failure_commands_once_and_reports_ng(self):
        p = packet()
        panel = self.make_panel([p])
        panel.set_mode('run')
        ctrl = self.control(panel, [action('STOP'), dict(kind='CMD', packet=p), receive(p)],
                            [dict(kind='CMD', packet=p), action('START', 'p')])
        results = []
        ctrl.finished.connect(results.append)
        ctrl.toggle()
        QTest.qWait(70)
        self.assertEqual(results, [False])
        self.assertIn('실패 처리 완료', ctrl.log.toPlainText())
        self.assertIn('NG', ctrl.button.text())
        self.assertNotIn('p', panel._paused_packets)
        self.assertIn('p', panel._frame_timers)
        self.assertGreaterEqual(len(self.sent), 2)

    def test_matching_receive_ok_does_not_run_failure_list(self):
        p = packet(cycle=0)
        panel = self.make_panel([p])
        panel.set_mode('run')
        ctrl = self.control(panel, [receive(p, 100)], [dict(kind='CMD', packet=p)])
        results = []
        ctrl.finished.connect(results.append)
        ctrl.toggle()
        ctrl.receive(time.time(), 1, p['id'], p['data'], False, False)
        QTest.qWait(20)
        self.assertEqual(results, [True])
        self.assertFalse(self.sent)
        self.assertIn('OK', ctrl.button.text())

    def test_cancel_and_failure_in_failure_do_not_recurse(self):
        p = packet(cycle=0)
        panel = self.make_panel([p])
        panel.set_mode('run')
        ctrl = self.control(panel, [receive(p, 20)], [dict(kind='CMD', packet=p)])
        ctrl.toggle()
        ctrl.stop()
        QTest.qWait(30)
        self.assertFalse(self.sent)
        results = []
        ctrl.finished.connect(results.append)
        with patch.object(panel.main_window.buses[1], 'send', side_effect=RuntimeError('failure action error')) as send:
            ctrl.toggle()
            QTest.qWait(40)
            self.assertEqual(send.call_count, 1)
        self.assertEqual(results, [False])
        self.assertFalse(ctrl.running)

    def test_init_ui_disallows_receive_and_settings_roundtrip(self):
        p = packet()
        init = [action('STOP', 'p')]
        failures = [action('START', 'p')]
        editor = SequenceDialog({}, init, tx_packets=[p], actions_only=True, allow_empty=True, allow_failure=False)
        combo = editor.table.cellWidget(0, 0)
        self.assertEqual(combo.findText('RCV'), -1)
        self.assertTrue(editor.btn_failure.isHidden())
        with self.assertRaises(ValueError):
            validate_steps([receive(p)], actions_only=True)
        action_editor = PacketActionDialog([p], action('STOP', 'p'))
        action_editor.accept()
        self.assertEqual(action_editor.result_step['target_packet_id'], 'p')
        cfg = dict(id='s', title='s', widget_type='sequence', behavior='tx',
                   binding=dict(sequence_steps=init, sequence_failure_steps=failures))
        dialog = WidgetConfigDialog({}, preset=cfg, tx_packets=[p])
        saved_cfg = dialog.get_config()
        self.assertEqual(saved_cfg['binding']['sequence_failure_steps'], failures)
        panel = self.make_panel([p], init)
        panel.widgets_config = [saved_cfg]
        saved = copy.deepcopy(panel._panel_data())
        panel._load_panel_data(saved)
        self.assertEqual(panel.init_steps, init)
        self.assertEqual(panel.widgets_config[0]['binding']['sequence_failure_steps'], failures)
        for d in (editor, action_editor, dialog):
            d.close()

    def test_general_sequence_blocked_during_init(self):
        p = packet(cycle=0)
        panel = self.make_panel([p], [dict(kind='DEL', delay_ms=50)])
        panel.set_mode('run')
        ctrl = self.control(panel, [dict(kind='CMD', packet=p)])
        ctrl.toggle()
        self.assertFalse(ctrl.running)
        self.assertFalse(self.sent)
