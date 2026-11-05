"""User Panel INIT / sequence / periodic TX against the real main window (not a stub).

The other User Panel tests replace the main window with a SimpleNamespace; these run the
real UniversalCANMonitor receive/record paths so changes there (extended IDs, record
keyword arguments, device modules) are exercised end to end.
"""
import copy
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PyQt5.QtCore import QEvent
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication
from cantools.database.can import Message, Signal

from src.main_window import UniversalCANMonitor
from src.session_manager import SessionManager
from src.user_panel_v2.sequence import SequenceControl
from src.user_panel_v2.window import UserPanelWindow


def packet(ident, can_id, extended=False, cycle=0, data=None):
    return dict(packet_id=ident, bus=1, id=can_id, data=list(data or [1] * 8), length=8, cycle=cycle,
                is_fd=False, is_brs=False, is_extended_id=extended, note='', symbol=ident,
                count=0, crc_type='N/A')


def action(kind, target):
    return dict(kind=kind, target_packet_id=target)


class PanelOnRealMainTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

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
        self.main.ui_update_timer.stop()
        self.sent = []
        self.main.buses[1] = SimpleNamespace(send=self.sent.append, shutdown=lambda: None)
        # 29-bit message whose ID is below 0x7FF, plus an 11-bit response.
        self.main.db_messages[1] = {
            0x123: Message(0x123, 'ExtCmd', 8, [Signal('Level', 0, 8)], is_extended_frame=True),
            0x456: Message(0x456, 'Resp', 8, [Signal('State', 0, 8)]),
        }
        self.recorder = Mock()
        self.recorder.isVisible.return_value = True
        self.main.record_window = self.recorder
        self.panel = UserPanelWindow(self.main, self.main.db_messages)
        self.main.user_panel_window = self.panel
        self.addCleanup(self.panel.close)

    def load(self, packets, init=None, widgets=None):
        self.panel._load_panel_data(dict(tx_packets=packets, init_steps=init or [], widgets=widgets or []))
        self.panel.show()
        self.app.processEvents()

    def wait(self, predicate, timeout=1.5):
        deadline = time.monotonic() + timeout
        while not predicate() and time.monotonic() < deadline:
            QTest.qWait(10)
        return predicate()

    def recorded(self):
        """(can_id, is_ext, is_rx) rows written to the Record window."""
        return [(c.args[1], c.args[3], c.args[6]) for c in self.recorder.add_log_entry.call_args_list]

    def control(self, steps, failures=None):
        cfg = dict(id='seq', title='Seq', widget_type='sequence', behavior='tx',
                   binding=dict(sequence_steps=steps, sequence_failure_steps=failures or []))
        ctrl = SequenceControl(self.panel, cfg)
        self.panel.widget_controls['seq'] = ctrl
        self.addCleanup(ctrl.deleteLater)
        return ctrl

    def test_init_sends_cmd_then_starts_periodic_and_records_extended_flag(self):
        cmd = packet('cmd', 0x123, extended=True)
        cyc = packet('cyc', 0x200, cycle=10)
        init = [dict(kind='CMD', name='Init CMD', packet=copy.deepcopy(cmd)), dict(kind='DEL', delay_ms=20)]
        self.load([cmd, cyc], init)
        self.panel.set_mode('run')
        self.assertTrue(self.wait(lambda: not self.panel._init_running))
        self.assertTrue(self.wait(lambda: any(m.arbitration_id == 0x200 for m in self.sent)))
        first = self.sent[0]
        self.assertEqual((first.arbitration_id, first.is_extended_id), (0x123, True))
        self.assertTrue(all(not m.is_extended_id for m in self.sent if m.arbitration_id == 0x200))
        self.assertTrue(self.wait(lambda: (0x200, False, False) in self.recorded()))
        self.assertIn((0x123, True, False), self.recorded())
        self.assertIn('Init', self.panel.init_control.log.toPlainText())

    def test_sequence_cmd_rcv_through_real_receive_path(self):
        cmd = packet('cmd', 0x123, extended=True)
        resp = packet('resp', 0x456, data=[7] + [0] * 7)
        self.load([cmd, resp])
        self.panel.set_mode('run')
        self.wait(lambda: not self.panel._init_running)
        ctrl = self.control([dict(kind='CMD', name='명령', packet=copy.deepcopy(cmd)),
                             dict(kind='RCV', name='응답', packet=copy.deepcopy(resp),
                                  mask=[255] + [0] * 7, timeout_ms=500)])
        ctrl.toggle()
        self.assertTrue(self.wait(lambda: any(m.arbitration_id == 0x123 for m in self.sent)))
        # Response arrives through the main window RX routing, as from CANReceiverThread.
        self.main.route_raw_msg_to_record(time.time(), 0x456, bytes([7] + [0] * 7),
                                          False, False, False, True, 1)
        self.assertTrue(self.wait(lambda: not ctrl.running))
        self.assertIn('전체 과정 완료', ctrl.log.toPlainText())
        self.assertIn((0x123, True, False), self.recorded())
        self.assertIn((0x456, False, True), self.recorded())

    def test_sequence_times_out_without_response_and_force_run_skips_rcv(self):
        cmd = packet('cmd', 0x123, extended=True)
        resp = packet('resp', 0x456)
        self.load([cmd, resp])
        self.panel.set_mode('run')
        self.wait(lambda: not self.panel._init_running)
        steps = [dict(kind='CMD', name='명령', packet=copy.deepcopy(cmd)),
                 dict(kind='RCV', name='응답', packet=copy.deepcopy(resp), mask=[255] * 8, timeout_ms=40)]
        ctrl = self.control(steps)
        ctrl.toggle()
        self.assertTrue(self.wait(lambda: not ctrl.running))
        self.assertNotIn('전체 과정 완료', ctrl.log.toPlainText())
        sent_before = len(self.sent)
        ctrl.force_toggle()  # 강제 실행: RCV 생략, 비밀번호 없음
        self.assertTrue(self.wait(lambda: not ctrl.running))
        self.assertIn('전체 과정 완료', ctrl.log.toPlainText())
        self.assertGreater(len(self.sent), sent_before)

    def test_packet_control_runs_start_stop_without_cmd(self):
        cyc = packet('cyc', 0x200, cycle=10)
        self.load([cyc], [action('STOP', 'cyc')])
        self.panel.set_mode('run')
        self.wait(lambda: not self.panel._init_running)
        self.assertIn('cyc', self.panel._paused_packets)
        ctrl = self.control([action('START', 'cyc'), dict(kind='DEL', delay_ms=40), action('STOP', 'cyc')])
        ctrl.toggle(packet_only=True)
        self.assertTrue(self.wait(lambda: not ctrl.running))
        self.assertTrue(any(m.arbitration_id == 0x200 for m in self.sent))
        self.assertIn('cyc', self.panel._paused_packets)

    def test_slider_value_frame_uses_dbc_extended_definition(self):
        binding = dict(bus=1, can_id=0x123, dlc=8, start_bit=0, bit_length=8, byte_order='little_endian',
                       is_signed=False, scale=1, offset=0, signal_name='Level')
        self.main.send_user_panel_value(binding, 5)
        self.assertEqual((self.sent[-1].arbitration_id, self.sent[-1].is_extended_id), (0x123, True))
        self.assertEqual(self.sent[-1].data[0], 5)
        self.assertIn((0x123, True, False), self.recorded())


if __name__ == '__main__':
    unittest.main()
