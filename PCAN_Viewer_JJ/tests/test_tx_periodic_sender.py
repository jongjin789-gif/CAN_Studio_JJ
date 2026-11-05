import binascii
import os
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PyQt5.QtWidgets import QApplication
from cantools.database.can import Message, Signal

from src.periodic_sender import PeriodicSender, timer_resolution
from src.tx_panel import TxPanel
from src.tx_schedule import bus_schedule
from src.user_panel_v2.window import UserPanelWindow


def packet(key='p', bus=1):
    return dict(packet_id=key, bus=bus, id=0x163, length=32, data=[0]*32,
                is_fd=True, is_brs=True, cycle=10, crc_type='N/A', symbol='Test', note='',
                signal_counters={
                    'Alive': dict(mode='up', min=0, max=255, step=1),
                    'CRC': dict(mode='crc16', start=2, size=30, extra=[], poly=0x1021,
                                init=0xffff, xorout=0, refin=False, refout=False,
                                result_byte_order='big_endian')})


class MainPeriodicSenderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make_panel(self, slow_bus=False):
        self.sent, self.records = {1: [], 2: []}, []

        def send(bus, frame):
            if slow_bus and bus == 2:
                time.sleep(.08)
            self.sent[bus].append((time.perf_counter(), bytes(frame.data), threading.get_ident()))

        message = Message(0x163, 'Test', 32, [
            Signal('CRC', 7, 16, byte_order='big_endian'),
            Signal('Alive', 23, 8, byte_order='big_endian')], is_fd=True)
        self.main = SimpleNamespace(
            buses={b: SimpleNamespace(send=lambda frame, b=b: send(b, frame)) for b in (1, 2)},
            bus_capabilities={b: {'is_fd': True} for b in (1, 2)},
            record_tx_activity=lambda *args, **kwargs: self.records.append((threading.get_ident(), args)),
            log_bus_event=Mock())
        self.db = {b: {message.frame_id: message} for b in (1, 2)}
        panel = TxPanel(self.main.buses, self.db)
        panel.main_window = self.main
        self.addCleanup(panel.close)
        for bus in (1, 2):
            panel.add_packet_to_tree(dict(packet(bus=bus), count=0))
        self.items = [panel.tree.topLevelItem(i) for i in range(2)]
        # Tree sorting can change the order.
        self.items.sort(key=lambda item: item.packet_data['bus'])
        return panel

    def test_main_and_user_panel_send_while_gui_is_blocked(self):
        panel = self.make_panel()
        user = UserPanelWindow(self.main, self.db)
        self.addCleanup(user.close)
        user._load_panel_data(dict(tx_packets=[packet('user')], widgets=[]))
        user.show()
        self.app.processEvents()
        user.set_mode('run')
        for item in self.items:
            item.start_timer()
        tasks = [item._periodic_task for item in self.items]
        senders = list(panel._periodic_senders.values())
        time.sleep(.22)  # No Qt events: emulate a busy log view or modal operation.
        self.assertFalse(self.records)
        self.assertGreaterEqual(len(self.sent[1]), 20)  # Main + user panel.
        self.assertGreaterEqual(len(self.sent[2]), 10)
        user.set_mode('standby')
        panel.stop_all_timers()
        for item in self.items:
            bus = item.packet_data['bus']
            expected = tasks[bus - 1].sent
            self.assertEqual(item.packet_data['count'], expected)
            self.assertFalse(item.timer.isActive())
            self.assertTrue(all(tid != threading.get_ident() for _, _, tid in self.sent[bus]))
            for _, data, _ in self.sent[bus]:
                self.assertEqual(data[:2], binascii.crc_hqx(data[2:], 0xffff).to_bytes(2, 'big'))
        self.assertTrue(all(tid == threading.get_ident() for tid, _ in self.records))
        self.assertTrue(all(not sender.thread.is_alive() for sender in senders))
        count = len(self.sent[1])
        time.sleep(.03)
        self.assertEqual(len(self.sent[1]), count)

    def test_slow_bus_does_not_delay_other_bus_and_stop_waits_for_send(self):
        panel = self.make_panel(slow_bus=True)
        for item in self.items:
            item.start_timer()
        time.sleep(.22)
        self.assertGreaterEqual(len(self.sent[1]), 10)
        self.assertLessEqual(len(self.sent[2]), 3)
        panel.stop_all_timers()
        self.assertTrue(self.main.log_bus_event.called)
        for bus in (1, 2):
            frames = self.sent[bus]
            self.assertEqual([data[2] for _, data, _ in frames], list(range(len(frames))))
        counts = [len(self.sent[b]) for b in (1, 2)]
        time.sleep(.03)
        self.assertEqual(counts, [len(self.sent[b]) for b in (1, 2)])

    def test_slow_gui_item_updates_do_not_hold_the_send_lock(self):
        panel = self.make_panel()
        item = self.items[0]
        item.start_timer()
        time.sleep(.035)
        task = item._periodic_task
        before = task.sent
        original = item.setText

        def slow_update(column, text):
            if column == 7:
                time.sleep(.08)
            original(column, text)

        with patch.object(item, 'setText', side_effect=slow_update):
            item._sync_runtime(task.runtime)
        self.assertGreaterEqual(task.sent - before, 3)
        panel.stop_all_timers()

    def test_edit_running_packet_stops_old_bus_before_restart(self):
        panel = self.make_panel()
        item = self.items[0]
        item.start_timer()
        time.sleep(.035)
        old_task = item._periodic_task
        changed = dict(packet(bus=2), count=0, cycle=20)
        dialog = Mock()
        dialog.exec_.return_value = 1
        dialog.get_packet_data.return_value = changed
        with patch('src.tx_panel.TxPacketDialog', return_value=dialog), patch.object(panel, 'auto_save_packets'):
            panel.on_item_double_clicked(item, 0)
        self.assertFalse(old_task.active)
        self.assertEqual(item._periodic_task.period, .02)
        count = len(self.sent[1])
        time.sleep(.06)
        panel.stop_all_timers()
        self.assertEqual(len(self.sent[1]), count)
        self.assertGreaterEqual(len(self.sent[2]), 1)

    def test_failed_send_stops_worker_and_keeps_hyundai_alive(self):
        panel = self.make_panel()
        item = self.items[0]
        item.packet_data.update(crc_type='Hyundai_CRC', signal_counters={})
        self.main.buses[1].send = Mock(side_effect=RuntimeError('bus lost'))
        item.start_timer()
        time.sleep(.04)
        panel._drain_periodic_events()
        self.assertFalse(item.is_running)
        self.assertEqual(item.packet_data['count'], 0)
        self.assertEqual(item.packet_data.get('alive_counter', 0), 0)
        self.assertEqual(self.main.buses[1].send.call_count, 1)

    def test_log_yields_near_main_and_user_deadlines(self):
        panel = self.make_panel()
        item = self.items[0]
        item.start_timer(100000)
        task = item._periodic_task
        schedule = bus_schedule(self.main, 1)
        device = Mock()
        with task.lock:
            task.deadline = time.perf_counter() - .001
            self.assertFalse(schedule.send_log(device, object()))
            device.send.assert_not_called()
            task.deadline = time.perf_counter() + 10
            self.assertTrue(schedule.send_log(device, object()))
        item.stop_timer()
        self.assertTrue(schedule.send_log(device, object()))


class DeadlineTest(unittest.TestCase):
    def test_late_send_preserves_phase_without_catchup_burst(self):
        clock = SimpleNamespace(now=100.)
        sender = PeriodicSender.__new__(PeriodicSender)
        sender.condition = threading.Condition()
        sender.tasks = set()
        sender.closed = False
        sender.failed = Mock()

        def send(record=False):
            clock.now = 100.036  # A 26ms driver stall on a 10ms schedule.
            return b'\0'

        sender.completed = lambda *_: setattr(sender, 'closed', True)
        with patch('src.periodic_sender.time.perf_counter', side_effect=lambda: clock.now):
            task = sender.add(SimpleNamespace(packet={'cycle': 10}, send=send))
            clock.now = task.deadline
            sender._run_active()
        self.assertAlmostEqual(task.deadline, 100.04)
        self.assertEqual(task.missed, 2)
        self.assertEqual(task.sent, 1)
        sender.failed.assert_not_called()

    def test_windows_resolution_request_is_balanced_on_exception(self):
        winmm = Mock()
        winmm.timeBeginPeriod.return_value = 0
        with patch('src.periodic_sender.sys.platform', 'win32'), \
                patch('src.periodic_sender.ctypes.WinDLL', return_value=winmm, create=True):
            with self.assertRaises(RuntimeError):
                with timer_resolution():
                    raise RuntimeError('stop')
        winmm.timeBeginPeriod.assert_called_once_with(1)
        winmm.timeEndPeriod.assert_called_once_with(1)


if __name__ == '__main__':
    unittest.main()
