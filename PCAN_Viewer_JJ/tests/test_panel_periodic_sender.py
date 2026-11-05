import binascii
import os
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PyQt5.QtWidgets import QApplication
from cantools.database.can import Message, Signal
from src.user_panel_v2.window import UserPanelWindow
from src.record_window import RecordWindow


def packet(key='p', bus=1):
    return dict(packet_id=key, bus=bus, id=0x163, length=32, data=[0]*32,
                is_fd=True, is_brs=True, cycle=10, crc_type='N/A', symbol='Test', note='',
                signal_counters={
                    'Alive': dict(mode='up', min=0, max=255, step=1),
                    'CRC': dict(mode='crc16', start=2, size=30, extra=[], poly=0x1021,
                                init=0xffff, xorout=0, refin=False, refout=False,
                                result_byte_order='big_endian')})


class PeriodicSenderTest(unittest.TestCase):
    def test_delayed_tx_record_does_not_replace_newer_message_stats(self):
        from src.main_window import UniversalCANMonitor
        stats = dict(count=1, cycle=10, last_time=101.0, data=b'new', is_fd=True)
        recorder = Mock()
        recorder.isVisible.return_value = True
        main = SimpleNamespace(rx_threads={1: SimpleNamespace(latest_msg_stats={0x163: stats})},
                               record_window=recorder)
        UniversalCANMonitor.record_tx_activity(main, 1, 0x163, b'old', True, 100.5)
        self.assertEqual(stats['count'], 2)
        self.assertEqual(stats['last_time'], 101.0)
        self.assertEqual(stats['data'], b'new')
        self.assertEqual(stats['cycle'], 10)
        self.assertEqual(recorder.add_log_entry.call_args.args[0], 100.5)
        self.assertTrue(recorder.add_log_entry.call_args.kwargs['preserve_timestamp'])

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make_panel(self, slow_bus=False):
        self.sent, self.records = {1: [], 2: []}, []
        def send(bus, msg):
            if slow_bus and bus == 2:
                time.sleep(.08)
            self.sent[bus].append((time.perf_counter(), bytes(msg.data), threading.get_ident()))
        message = Message(0x163, 'Test', 32, [
            Signal('CRC', 7, 16, byte_order='big_endian'),
            Signal('Alive', 23, 8, byte_order='big_endian'),
            Signal('Angle', 71, 16, byte_order='big_endian')], is_fd=True)
        main = SimpleNamespace(
            buses={b: SimpleNamespace(send=lambda msg, b=b: send(b, msg)) for b in (1, 2)},
            bus_capabilities={b: {'is_fd': True} for b in (1, 2)},
            record_tx_activity=lambda *args, **kwargs: self.records.append((threading.get_ident(), args)))
        panel = UserPanelWindow(main, {b: {0x163: message} for b in (1, 2)})
        panel._load_panel_data(dict(tx_packets=[packet(), packet('q', 2)], widgets=[]))
        panel.show()
        self.app.processEvents()
        self.addCleanup(panel.close)
        panel.set_mode('run')
        return panel

    def test_gui_block_does_not_block_send_and_crc_uses_current_angle_and_alive(self):
        panel = self.make_panel()
        gui = threading.get_ident()
        time.sleep(.12)  # Deliberately do not process Qt events.
        self.assertGreaterEqual(len(self.sent[1]), 5)
        self.assertFalse(self.records)  # UI telemetry is deferred, CAN sending is not.
        cfg = dict(behavior='tx', binding=dict(packet_id='p', bus=1, can_id=0x163,
                   start_bit=71, bit_length=16, byte_order='big_endian', scale=1, offset=0,
                   signed=False))
        panel._emit_tx(cfg, 0x1234)
        time.sleep(.12)
        tasks = list(panel._frame_timers.values())
        senders = list(panel._periodic_senders.values())
        panel.set_mode('standby')
        for bus in (1, 2):
            frames = self.sent[bus]
            self.assertGreaterEqual(len(frames), 10)
            self.assertTrue(all(tid != gui for _, _, tid in frames))
            for _, data, _ in frames:
                self.assertEqual(data[:2], binascii.crc_hqx(data[2:], 0xffff).to_bytes(2, 'big'))
            for a, b in zip(frames, frames[1:]):
                self.assertEqual((b[1][2]-a[1][2]) % 256, 1)
        self.assertEqual(self.sent[1][-1][1][8:10], bytes.fromhex('12 34'))
        self.assertTrue(all(tid == gui for tid, _ in self.records))
        count = len(self.sent[1])
        time.sleep(.04)
        self.assertEqual(len(self.sent[1]), count)
        self.assertTrue(all(not task.isActive() for task in tasks))
        self.assertTrue(all(not sender.thread.is_alive() for sender in senders))

    def test_slow_bus_isolated_and_disconnect_stops_without_restart(self):
        panel = self.make_panel(slow_bus=True)
        time.sleep(.22)
        self.assertGreaterEqual(len(self.sent[1]), 10)
        self.assertLessEqual(len(self.sent[2]), 3)
        panel._drain_periodic_events()
        self.assertIn('TX 주기 지연: BUS 2 / 0x163', panel.system_log.text.toPlainText())
        panel.stop_periodic_bus(1)
        panel.main_window.buses[1] = None
        count = len(self.sent[1])
        time.sleep(.05)
        self.assertEqual(len(self.sent[1]), count)
        self.assertIn('p', panel._paused_packets)
        panel._sync_frame_timers_from_configs()
        self.assertNotIn('p', panel._frame_timers)

    def test_tx_record_keeps_send_timestamp_when_gui_delivery_is_late(self):
        recorder = RecordWindow()
        self.addCleanup(recorder.deleteLater)
        self.addCleanup(recorder._discard_temp)
        recorder.add_log_entry(100, 1, b'\0', False, False, False, True)
        recorder.add_log_entry(101, 1, b'\0', False, False, False, True)
        recorder.add_log_entry(100.25, 0x163, bytes(32), False, False, True, False,
                               preserve_timestamp=True)
        self.assertEqual(float(recorder.last_line.split()[1]), 250.0)
        recorder.timer.stop()
