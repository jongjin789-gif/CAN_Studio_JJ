"""R013 tools: bus load, ASC/BLF logs, trigger capture, gateway, timeouts, CSV export."""
import csv
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import can
import cantools

from PyQt5.QtCore import Qt, QPoint
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QHeaderView

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.bus_monitor import BusLoadMeter, frame_seconds
from src.can_log_formats import (read_vector_entries, write_vector_log, trc_line, parse_app_trc_line,
                                 convert_app_trc, write_trc_frames)
from src.csv_export import export_signals_csv
from src.gateway import Gateway, parse_id_list, parse_remap, compile_rules, normalized_rules
from src.signal_monitor import TimeoutMonitor, normalized_timeout_settings
from src.trigger_capture import TriggerCapture, TriggerConfig


SPEED_DBC = '''VERSION ""
BU_: ECU
BO_ 256 Speed: 8 ECU
 SG_ Value : 0|16@1+ (0.1,0) [0|6553.5] "km/h" Vector__XXX
'''


def speed_message():
    return cantools.database.load_string(SPEED_DBC, 'dbc').get_message_by_frame_id(0x100)


def speed_payload(raw):
    return int(raw).to_bytes(2, 'little') + bytes(6)


class BusLoadTest(unittest.TestCase):
    def test_classic_frame_time_matches_bit_count(self):
        # 11-bit, 8 bytes: 47 + 64 = 111 bits, ~10 % stuffing, at 500 kbit/s.
        self.assertAlmostEqual(frame_seconds(8, False, False, False, 500_000, None), 111 * 1.1 / 500_000)
        self.assertGreater(frame_seconds(8, True, False, False, 500_000, None),
                           frame_seconds(8, False, False, False, 500_000, None))

    def test_fd_brs_is_faster_than_without_brs(self):
        slow = frame_seconds(64, False, True, False, 500_000, 2_000_000)
        fast = frame_seconds(64, False, True, True, 500_000, 2_000_000)
        self.assertLess(fast, slow)

    def test_meter_reports_load_fps_and_errors(self):
        now = [0.0]
        meter = BusLoadMeter(500_000, clock=lambda: now[0])
        for _ in range(1000):
            meter.feed(8, False, False, False, False)
        meter.feed(0, False, False, False, True)
        now[0] = 1.0
        meter.sample()
        self.assertAlmostEqual(meter.load, 1000 * 111 * 1.1 / 500_000 * 100, places=3)
        self.assertEqual(meter.fps, 1000)
        self.assertEqual(meter.error_frames, 1)
        self.assertIn('TEC 3 / REC 4', meter.text((3, 4)))
        now[0] = 2.0
        meter.sample()
        self.assertEqual(meter.load, 0.0)


class VectorLogTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        start = 1_700_000_000.0
        self.entries = [
            dict(timestamp=start, offset_ms=0.0, type='DT', bus=1, can_id=0x123, dlc=3,
                 data=b'\x01\x02\x03', is_extended_id=False, direction='Rx'),
            dict(timestamp=start + 0.25, offset_ms=250.0, type='FB', bus=3, can_id=0x18DAF110, dlc=12,
                 data=bytes(range(12)), is_extended_id=True, direction='Tx'),
        ]

    def test_asc_and_blf_round_trip_keep_bus_direction_and_frame_type(self):
        for ext in ('asc', 'blf'):
            path = os.path.join(self.temp.name, 'log.' + ext)
            self.assertEqual(write_vector_log(path, self.entries), 2)
            read = read_vector_entries(path)
            self.assertEqual([(e['bus'], e['can_id'], e['data'], e['type'], e['direction'], e['is_extended_id'])
                              for e in read],
                             [(1, 0x123, b'\x01\x02\x03', 'DT', 'Rx', False),
                              (3, 0x18DAF110, bytes(range(12)), 'FB', 'Tx', True)], ext)
            self.assertAlmostEqual(read[1]['offset_ms'], 250.0, delta=1.5)
            self.assertAlmostEqual(read[0]['timestamp'], self.entries[0]['timestamp'], delta=0.01)
            self.assertEqual(read[0]['format'], ext)

    def test_record_trc_body_converts_to_vector(self):
        source = os.path.join(self.temp.name, 'body.tmp')
        with open(source, 'w', encoding='utf-8') as stream:
            stream.write(trc_line(1, 0.0, 0x321, b'\xAA', False, False, True, 2) + '\n')
            stream.write(trc_line(2, 10.5, 0x1ABCDE, bytes(16), True, True, False, 4) + '\n')
        entry = parse_app_trc_line(trc_line(2, 10.5, 0x1ABCDE, bytes(16), True, True, False, 4), 100.0)
        self.assertEqual((entry['bus'], entry['can_id'], entry['type'], entry['direction']), (4, 0x1ABCDE, 'FD', 'Tx'))
        self.assertAlmostEqual(entry['timestamp'], 100.0105)
        dest = os.path.join(self.temp.name, 'out.blf')
        self.assertEqual(convert_app_trc(source, 100.0, dest), 2)
        self.assertEqual([e['can_id'] for e in read_vector_entries(dest)], [0x321, 0x1ABCDE])

    def test_log_parser_decodes_asc_signals(self):
        from src.can_threads import LogParserThread
        path = os.path.join(self.temp.name, 'speed.asc')
        write_vector_log(path, [dict(timestamp=1_700_000_000.0 + i, type='DT', bus=2, can_id=0x100, data=speed_payload(i * 10),
                                     is_extended_id=False) for i in range(3)])
        parser = LogParserThread(path, {1: {}, 2: {0x100: speed_message()}, 3: {}, 4: {}})
        results = []
        parser.finished_signal.connect(lambda *args: results.append(args))
        parser.run()
        signal_data, found, raw = results[0]
        self.assertEqual(found, {(2, 0x100)})
        self.assertEqual(signal_data[(2, 0x100)]['Value'][1], [0.0, 1.0, 2.0])
        self.assertEqual(len(raw), 3)

    def test_asc_with_unconvertible_start_date_falls_back_to_relative_times(self):
        path = os.path.join(self.temp.name, 'old.asc')
        write_vector_log(path, [dict(timestamp=10.0 + i, type='DT', bus=1, can_id=0x100, data=b'',
                                     is_extended_id=False) for i in range(2)])
        entries = read_vector_entries(path)
        self.assertEqual([round(e['offset_ms']) for e in entries], [0, 1000])


class TriggerCaptureTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.message = speed_message()
        self.capture = TriggerCapture(directory=self.temp.name,
                                      resolve_message=lambda bus, can_id: self.message if (bus, can_id) == (1, 0x100) else None)
        self.saved = []
        self.capture.saved.connect(lambda path, count: self.saved.append((path, count)))
        self.capture.set_config(TriggerConfig(1, 0x100, 'Speed', 'Value', '>', 50.0, pre_s=1.0, post_s=1.0))

    def feed(self, ts, raw=0, can_id=0x100, bus=1):
        self.capture.feed(ts, bus, can_id, speed_payload(raw), False, False, True)

    def saved_times(self):
        from src.can_threads import LogParserThread
        parser = LogParserThread(self.saved[-1][0], {b: {} for b in (1, 2, 3, 4)})
        results = []
        parser.finished_signal.connect(lambda *args: results.append(args))
        parser.run()
        return [round(entry['offset_ms']) for entry in results[0][2]]

    def test_saves_pre_and_post_window_on_rising_edge(self):
        for i in range(50):                      # 0.0 .. 4.9 s, every 100 ms
            ts = 100.0 + i * 0.1
            raw = 600 if 20 <= i < 25 else 0    # Value > 50 at t = 102.0 .. 102.4
            self.feed(ts, raw)
            self.feed(ts + 0.01, can_id=0x200, bus=2)  # other BUS traffic is saved too
        self.assertEqual(self.capture.fired, 1)  # level stays true: no re-trigger
        self.assertEqual(len(self.saved), 1)
        offsets = self.saved_times()
        # 1 s before the trigger (101.0) through 1 s after it (103.0).
        self.assertEqual(offsets[0], 0)
        self.assertAlmostEqual(offsets[-1], 2010, delta=15)
        self.assertTrue(os.path.basename(self.saved[0][0]).startswith('Trigger_'))

    def test_retrigger_while_saving_restarts_post_time(self):
        for i in range(60):
            ts = 100.0 + i * 0.1
            raw = 600 if i in (20, 28) else 0   # second edge 0.8 s after the first
            self.feed(ts, raw)
        self.assertEqual(self.capture.fired, 2)
        self.assertEqual(len(self.saved), 1)
        offsets = self.saved_times()
        self.assertAlmostEqual(offsets[-1], 2800, delta=15)  # 101.0 .. 103.8

    def test_poll_saves_when_bus_goes_quiet_and_single_config(self):
        self.feed(100.0, 0)
        self.feed(100.1, 600)
        self.assertTrue(self.capture.capturing)
        self.capture.poll(100.1 + 0.5)
        self.assertEqual(self.saved, [])
        self.capture.poll(100.1 + 1.3)
        self.assertEqual(len(self.saved), 1)
        self.capture.set_config(TriggerConfig(1, 0x100, 'Speed', 'Value', '<', 1.0))
        self.assertEqual(self.capture.config.op, '<')
        self.assertIsNone(TriggerConfig.from_dict({'bus': 1}))
        restored = TriggerConfig.from_dict(dict(self.capture.config.to_dict(), pre_s=9))
        self.assertEqual(restored.pre_s, 2.0)


class GatewayTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_parsing_and_validation(self):
        self.assertEqual(parse_id_list('100-1FF, 0x321'), [(0x100, 0x1FF), (0x321, 0x321)])
        self.assertEqual(parse_remap('100=200 18DAF110=18DA10F1'), {0x100: 0x200, 0x18DAF110: 0x18DA10F1})
        for bad in ('xyz', '200-100', '1FFFFFFFF'):
            with self.assertRaises(ValueError):
                parse_id_list(bad)
        with self.assertRaisesRegex(ValueError, '1행'):
            compile_rules([dict(enabled=True, src=1, dst=1, mode='all', ids='', remap='')])
        with self.assertRaisesRegex(ValueError, 'ID 목록'):
            compile_rules([dict(enabled=True, src=1, dst=2, mode='pass', ids='', remap='')])
        self.assertEqual(normalized_rules([{'src': 9, 'dst': 1}, 'x', dict(src=1, dst=2)])[0]['mode'], 'all')

    def make(self, rules, dest_fails=False):
        sent = []

        def send(msg):
            if dest_fails:
                raise can.CanError('no FD')
            sent.append(msg)
        main = SimpleNamespace(buses={1: object(), 2: SimpleNamespace(send=send), 3: None, 4: None})
        gateway = Gateway(None)
        gateway.main = main
        gateway.configure(rules, True)
        return gateway, sent

    def test_forward_filter_block_and_remap(self):
        rules = [dict(enabled=True, src=1, dst=2, mode='pass', ids='100-10F', remap='101=7FF, 102=1ABCDE'),
                 dict(enabled=True, src=1, dst=2, mode='block', ids='0-7FF', remap=''),
                 dict(enabled=False, src=1, dst=2, mode='all', ids='', remap='')]
        gateway, sent = self.make(rules)
        for can_id in (0x101, 0x102, 0x200, 0x18000000):
            gateway.forward(1, can.Message(arbitration_id=can_id, is_extended_id=can_id > 0x7FF, data=b'\x01'))
        gateway.forward(2, can.Message(arbitration_id=0x101, is_extended_id=False))  # no rule for BUS 2
        self.assertEqual([(m.arbitration_id, m.is_extended_id) for m in sent],
                         [(0x7FF, False), (0x1ABCDE, True), (0x18000000, True)])
        self.assertEqual(gateway.statistics()[0][0], 2)
        self.assertIsNone(gateway.statistics()[2])

    def test_send_failure_is_counted_and_disabled_gateway_is_idle(self):
        gateway, sent = self.make([dict(enabled=True, src=1, dst=2, mode='all', ids='', remap='')], dest_fails=True)
        failures = []
        gateway.failed.connect(lambda *args: failures.append(args))
        for _ in range(3):
            gateway.forward(1, can.Message(arbitration_id=1, is_extended_id=False))
        self.assertEqual(gateway.statistics()[0][1], 3)
        self.assertEqual(len(failures), 1)  # reported at most every 5 s
        gateway.configure(gateway.rules, False)
        self.assertEqual(gateway.statistics(), [])


class TimeoutMonitorTest(unittest.TestCase):
    def stats(self, count, last):
        return dict(count=count, last_time=last, direction='Rx')

    def test_dbc_cycle_timeout_and_recovery(self):
        monitor = TimeoutMonitor()
        new, recovered = monitor.check(10.0, [(1, 0x100, self.stats(1, 9.99), 100)])
        self.assertEqual((new, recovered), ([], []))
        new, _ = monitor.check(10.4, [(1, 0x100, self.stats(1, 9.99), 100)])
        self.assertEqual([(b, c) for b, c, *_ in new], [(1, 0x100)])
        new, _ = monitor.check(10.5, [(1, 0x100, self.stats(1, 9.99), 100)])
        self.assertEqual(new, [])  # notified once
        _, recovered = monitor.check(10.6, [(1, 0x100, self.stats(2, 10.55), 100)])
        self.assertEqual(recovered, [(1, 0x100)])

    def test_measured_cycle_needs_frames_and_tx_or_disabled_is_ignored(self):
        monitor = TimeoutMonitor()
        monitor.check(1.0, [(1, 0x200, self.stats(1, 1.0), None)])
        self.assertEqual(monitor.check(5.0, [(1, 0x200, self.stats(3, 1.2), None)]), ([], []))
        monitor.check(1.6, [(1, 0x200, self.stats(7, 1.6), None)])   # 6 frames in 0.6 s -> 100 ms
        new, _ = monitor.check(2.0, [(1, 0x200, self.stats(7, 1.6), None)])
        self.assertEqual(len(new), 1)
        self.assertEqual(monitor.check(9.0, [(1, 0x300, dict(count=1, last_time=1.0, direction='Tx'), 10)]),
                         ([], []))
        late = TimeoutMonitor()  # first seen after many frames: use the last interval
        self.assertEqual(len(late.check(3.0, [(1, 0x400, dict(count=30, last_time=1.0, cycle=10.0,
                                                               direction='Rx'), None)])[0]), 1)
        monitor.configure(dict(enabled=False))
        self.assertEqual(monitor.timed_out, {})
        self.assertEqual(normalized_timeout_settings({'factor': 100})['factor'], 3.0)


class CsvExportTest(unittest.TestCase):
    def test_sample_only_and_fill_forward(self):
        with tempfile.TemporaryDirectory() as temp:
            path = os.path.join(temp, 'out.csv')
            series = [('A', [1.0, 2.0], [10.0, 20.0]), ('B', [1.5], [5])]
            self.assertEqual(export_signals_csv(path, series), 3)
            with open(path, encoding='utf-8-sig') as stream:
                rows = list(csv.reader(stream))
            self.assertEqual(rows[0], ['Time', 'Offset (s)', 'A', 'B'])
            self.assertEqual([r[1:] for r in rows[1:]],
                             [['0.000000', '10', ''], ['0.500000', '', '5'], ['1.000000', '20', '']])
            export_signals_csv(path, series, fill_forward=True)
            with open(path, encoding='utf-8-sig') as stream:
                rows = list(csv.reader(stream))
            self.assertEqual([r[2:] for r in rows[1:]], [['10', ''], ['10', '5'], ['20', '5']])


class RecordFilterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make(self):
        from src.record_window import RecordWindow
        window = RecordWindow(None, bitrates={1: '500 kBit/s'})
        window.is_closed_by_btn = True
        self.addCleanup(window.close)
        return window

    def record(self, window, ids):
        for i, can_id in enumerate(ids):
            window.add_log_entry(100.0 + i * 0.01, can_id, b'', can_id > 0x7FF, False, False, True, 1)

    def saved_ids(self, window, ext='trc'):
        from src.can_threads import LogParserThread
        path = os.path.join(self.temp, 'rec.' + ext)
        window._write_vector(path) if ext != 'trc' else window._write_trc(path)
        parser = LogParserThread(path, {b: {} for b in (1, 2, 3, 4)})
        results = []
        parser.finished_signal.connect(lambda *args: results.append(args))
        parser.run()
        return [entry['can_id'] for entry in results[0][2]], path

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.temp = temp.name

    def test_exclude_and_only_ranges(self):
        window = self.make()
        self.record(window, [0x100, 0x200])                       # before the filter: all kept
        window.combo_filter.setCurrentIndex(1)                     # 범위 제외
        window.edit_filter.setText('100-1FF, 18DAF110')
        self.assertTrue(window.apply_filter())
        self.record(window, [0x100, 0x150, 0x200, 0x18DAF110, 0x18DAF111])
        window.combo_filter.setCurrentIndex(2)                     # 범위만 기록
        window.edit_filter.setText('0x300')
        self.assertTrue(window.apply_filter())
        self.record(window, [0x100, 0x300])
        self.assertEqual(window.filtered_count, 4)
        ids, path = self.saved_ids(window)
        self.assertEqual(ids, [0x100, 0x200, 0x200, 0x18DAF111, 0x300])
        with open(path, encoding='utf-8') as stream:
            header = stream.read()
        self.assertIn('ID filter from', header)
        self.assertIn('exclude 100-1FF, 18DAF110', header)
        self.assertIn('only 0x300', header)
        self.assertEqual(self.saved_ids(window, 'blf')[0], ids)

    def test_invalid_range_keeps_previous_filter_and_all_resets(self):
        window = self.make()
        window.combo_filter.setCurrentIndex(1)
        window.edit_filter.setText('zz')
        self.assertFalse(window.apply_filter())
        self.assertEqual(window.filter_mode, 'all')
        window.edit_filter.setText('')
        self.assertFalse(window.apply_filter())                    # a range is required
        window.edit_filter.setText('100')
        self.assertTrue(window.apply_filter())
        window.combo_filter.setCurrentIndex(0)                     # 전체 기록 applies at once
        self.assertEqual(window.filter_mode, 'all')
        self.record(window, [0x100])
        self.assertEqual(window.msg_count, 1)


class DeviceRemovalTest(unittest.TestCase):
    """Unplugging a device is logged to the BUS log without dialogs."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_pcan_poll_reports_removed_device(self):
        from src.can_devices.device_pcan import PcanBackend
        backend = PcanBackend(None)

        class Api:
            condition = 2   # PCAN_CHANNEL_OCCUPIED while this program uses it

            def GetValue(self, handle, parameter):
                return 0, self.condition
        bus = SimpleNamespace(status=lambda: 0, m_objPCANBasic=Api(), m_PcanHandle=0x51)
        self.assertEqual(backend.poll_status(bus)[0], 'ok')
        bus.m_objPCANBasic.condition = 0   # PCAN_CHANNEL_UNAVAILABLE
        self.assertEqual(backend.poll_status(bus)[0], 'lost')
        bus = SimpleNamespace(status=lambda: 0x1400)   # PCAN_ERROR_ILLHW
        self.assertEqual(backend.poll_status(bus)[0], 'lost')
        bus = SimpleNamespace(status=lambda: 0x10)     # PCAN_ERROR_BUSOFF stays bus-off
        self.assertEqual(backend.poll_status(bus)[0], 'busoff')

    def setUp(self):
        from src.main_window import UniversalCANMonitor
        from src.session_manager import SessionManager
        with patch.object(UniversalCANMonitor, 'search_can_channels'), patch.object(SessionManager, 'start'):
            self.main = UniversalCANMonitor()
        self.main.session.ready = False
        self.addCleanup(self.main.deleteLater)
        self.addCleanup(self.main.close)
        combo = self.main.combo_channels[1]
        combo.clear()
        combo.addItem('p', dict(bustype='pcan', handle=0x51, is_fd=False))
        self.device = SimpleNamespace(shutdown=self.fail_shutdown)
        self.main.buses[1] = self.device

    @staticmethod
    def fail_shutdown():
        raise can.CanError('Function canBusOff failed - A hardware error was detected')

    def test_removed_device_closes_after_two_polls_without_dialog(self):
        from src.can_devices.device_pcan import PcanBackend
        with patch.object(PcanBackend, 'poll_status', return_value=('lost', 'PCAN status: 0x1400')),                 patch('src.main_window.show_error') as dialog, patch('src.error_dialog.show_error') as dialog2:
            self.main.poll_bus_status()
            self.assertIs(self.main.buses[1], self.device)   # one poll is not enough
            self.main.poll_bus_status()
        self.assertIsNone(self.main.buses[1])
        dialog.assert_not_called()
        dialog2.assert_not_called()
        self.assertEqual(self.main.bus_summary(1), ('장치 분리 감지 · 연결 해제', 'ERROR'))
        messages = [event['message'] for event in self.main.bus_events[1]]
        self.assertTrue(any(m.startswith('연결 해제 오류') for m in messages))

    def test_repeating_warnings_merge_and_keep_the_original_cause(self):
        main = self.main
        main.bus_events[1].clear()
        main.log_bus_event(1, '장치 분리 감지 · 연결 해제', 'ERROR')
        for i in range(800):
            main.log_bus_event(1, f'TX 주기 지연: 0x1A0, 누락 1회 (누적 {i}), 최대 지연 {i / 10:.1f} ms', 'WARN')
        main.log_bus_event(1, 'TX 주기 지연: 0x2B0, 누락 1회 (누적 1), 최대 지연 1.0 ms', 'WARN')
        events = list(main.bus_events[1])
        self.assertEqual([e['message'][:22] for e in events],
                         ['장치 분리 감지 · 연결 해제', 'TX 주기 지연: 0x1A0, 누락 1회', 'TX 주기 지연: 0x2B0, 누락 1회'])
        self.assertEqual(events[1]['count'], 800)
        self.assertIn('누적 799', events[1]['message'])   # newest numbers are shown

    def test_interleaved_repeats_stay_one_line_each_and_error_frame_cause(self):
        from src.main_window import describe_error_frame
        main = self.main
        main.bus_events[1].clear()
        for i in range(50):
            main.log_bus_event(1, f'오류 프레임 {5000 + i:,}개/초 · ACK 없음', 'WARN', issue=False)
            main.log_bus_event(1, f'TX 주기 지연: 0x0, 누락 1회 (누적 {i})', 'WARN', issue=False)
            main.log_bus_event(1, '연결됨 · 경고: CAN 버스 오류', 'WARN')
        self.assertEqual([e['count'] for e in main.bus_events[1]], [50, 50, 50])
        cause, details = describe_error_frame((8, bytes([0, 0x19, 0, 128])), 'pcan')
        self.assertIn('ACK 없음', cause)
        self.assertIn('TEC 128', details)
        self.assertEqual(describe_error_frame((4, bytes([1, 0x08, 9, 0])), 'pcan')[0], '스터프 오류 (수신 중)')
        self.assertEqual(describe_error_frame(None, 'kvaser')[0], '버스 오류')

    def test_receive_error_is_logged_without_dialog(self):
        with patch('src.main_window.show_error') as dialog, patch('src.error_dialog.show_error') as dialog2:
            self.main.handle_rx_error('Rx Error: device removed', 1)
        self.assertIsNone(self.main.buses[1])
        dialog.assert_not_called()
        dialog2.assert_not_called()
        self.assertEqual(self.main.bus_summary(1)[0], '오류로 연결 해제: Rx Error: device removed')


class SpinInputTest(unittest.TestCase):
    """Typing into the cycle/timeout boxes replaces the value without deleting hint text."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def type_into(self, dialog, spin, text):
        from PyQt5.QtTest import QTest
        dialog.show()
        self.addCleanup(dialog.close)
        dialog.activateWindow()
        spin.setFocus()
        QTest.qWait(20)                      # select-all runs after the focus event
        QTest.keyClicks(spin, text)
        spin.interpretText()
        return spin.value()

    def test_tx_cycle_has_only_the_number_and_typing_replaces_it(self):
        from src.tx_panel import TxPacketDialog
        dialog = TxPacketDialog({b: {} for b in (1, 2, 3, 4)})
        dialog.edit_cycle.setValue(10)
        self.assertEqual(dialog.edit_cycle.text(), '10')
        self.assertIn('0 = 1회 송신', dialog.cycle_field.hint.text())
        self.assertEqual(self.type_into(dialog, dialog.edit_cycle, '250'), 250)

    def test_user_panel_cycle_typing_over_placeholder(self):
        from src.user_panel_v2.packets import RegisteredPacketDialog
        dialog = RegisteredPacketDialog({b: {} for b in (1, 2, 3, 4)})
        self.assertEqual(dialog.edit_cycle.text(), '딜레이 입력 필요')
        self.assertIn('도구 값 변경 시 전송', dialog.cycle_field.hint.text())
        self.assertEqual(self.type_into(dialog, dialog.edit_cycle, '50'), 50)


class MainWindowToolsTest(unittest.TestCase):
    """Real python-can virtual buses through the main window."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from src.main_window import UniversalCANMonitor
        from src.session_manager import SessionManager
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        with patch.object(UniversalCANMonitor, 'search_can_channels'), patch.object(SessionManager, 'start'):
            self.main = UniversalCANMonitor()
        self.main.session.ready = False
        self.main.session.directory = Path(self.temp.name)
        self.main.session.autosave_path = Path(self.temp.name) / 'auto.pjjsettings'
        self.addCleanup(self._dispose)
        tag = f'tools{id(self)}'
        for bus in (1, 2):
            combo = self.main.combo_channels[bus]
            combo.clear()
            combo.addItem(f'v{bus}', dict(bustype='virtual', handle=f'{tag}_{bus}', is_fd=False))
            self.main.open_can(bus)
        self.peer1 = can.Bus(interface='virtual', channel=f'{tag}_1')
        self.peer2 = can.Bus(interface='virtual', channel=f'{tag}_2')
        self.addCleanup(self.peer1.shutdown)
        self.addCleanup(self.peer2.shutdown)

    def _dispose(self):
        """Stop the receiver threads, then deliver their queued signals and delete the
        window now, so nothing from these real threads is left for later tests."""
        from PyQt5.QtCore import QCoreApplication, QEvent
        self.main.close()
        self.app.processEvents()
        self.main.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.app.processEvents()

    def wait(self, predicate, timeout=2.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            self.app.processEvents()
            if predicate():
                return True
            time.sleep(0.01)
        return predicate()

    def test_gateway_forwards_and_records_tx_on_destination(self):
        self.main.set_gateway([dict(enabled=True, src=1, dst=2, mode='all', ids='', remap='321=654')], True)
        self.peer1.send(can.Message(arbitration_id=0x321, is_extended_id=False, data=b'\x11\x22'))
        received = self.peer2.recv(2.0)
        self.assertIsNotNone(received)
        self.assertEqual((received.arbitration_id, bytes(received.data)), (0x654, b'\x11\x22'))
        self.assertTrue(self.wait(lambda: 0x654 in self.main.rx_threads[2].latest_msg_stats))
        self.assertEqual(self.main.rx_threads[2].latest_msg_stats[0x654]['direction'], 'Tx')
        self.assertIsNone(self.peer1.recv(0.2))  # no loop back to the source BUS
        self.assertEqual(self.main.btn_gateway.text(), 'GW ON')

    def test_load_text_trigger_column_stats_and_session_tools(self):
        db = cantools.database.load_string(SPEED_DBC, "dbc")
        self.main.install_database(db, 1, 'speed.dbc', b'', '', 'speed-id')
        for raw in (0, 10, 30):
            self.peer1.send(can.Message(arbitration_id=0x100, is_extended_id=False, data=speed_payload(raw)))
        self.assertTrue(self.wait(lambda: self.main.rx_threads[1].signal_stats.get((0x100, 'Value'), [0] * 4)[3] == 3))
        self.assertEqual(self.main.rx_threads[1].signal_stats[(0x100, 'Value')][:2], [0, 3.0])
        self.main.update_bus_load()
        self.assertIn('fps', self.main.connection_grid.states[1].toolTip())
        self.assertTrue(self.wait(lambda: (1, 0x100, 'Value') in self.main.signal_tree_items))
        item = self.main.signal_tree_items[(1, 0x100, 'Value')]
        self.main.trigger.directory = self.temp.name
        # Value stretches with the window; the trigger column keeps its width.
        header = self.main.tree.header()
        self.assertEqual(header.sectionResizeMode(5), QHeaderView.Stretch)
        self.assertEqual(header.sectionResizeMode(9), QHeaderView.Fixed)
        self.assertEqual(item.text(9), '＋ 설정')         # clickable hint before a trigger exists
        self.main.trigger.set_config(TriggerConfig(1, 0x100, 'Speed', 'Value', '>', 5.0))
        self.assertTrue(item.text(9).startswith('> 5'))
        self.assertFalse(item.icon(9).isNull())
        # Click cycle: active -> stop -> settings dialog (OK re-arms).
        title = self.main.tree.headerItem()
        self.assertEqual(title.text(9), 'Trigger · 감시')
        self.assertIn('Speed · Value', title.toolTip(9))
        self.main.on_tree_clicked(item, 9)
        self.assertFalse(self.main.trigger.active)
        self.assertIn('정지', item.text(9))
        self.assertEqual(title.text(9), 'Trigger · 정지')
        self.main.trigger.feed(1.0, 1, 0x100, speed_payload(30), False, False, True)
        self.assertEqual(self.main.trigger.fired, 0)       # stopped: no capture
        with patch.object(self.main, 'edit_trigger') as edit:
            self.main.on_tree_clicked(item, 9)
        edit.assert_called_once_with(item)
        self.assertFalse(self.main.session.capture()['tools']['trigger_active'])
        self.main.set_trigger_active(True)
        self.assertTrue(self.main.trigger.active)
        tools = self.main.session.capture()['tools']
        self.assertEqual(tools['trigger']['signal'], 'Value')
        self.main.session.apply_tools(dict(tools, trigger_active=False))
        self.assertFalse(self.main.trigger.active)
        self.main.session.apply_tools(dict(tools))
        self.main.set_gateway([dict(enabled=True, src=1, dst=2, mode='all', ids='', remap='')], True)
        self.main.session.apply_tools(dict(tools, gateway_rules=self.main.gateway.rules))
        self.assertFalse(self.main.gateway.enabled)        # never auto-starts
        self.assertEqual(len(self.main.gateway.rules), 1)
        self.assertEqual(self.main.trigger.config.op, '>')
        self.main.clear_trigger()
        self.assertEqual(item.text(9), '＋ 설정')
        self.assertEqual(title.text(9), 'Trigger')
        self.main.trigger.set_config(TriggerConfig(1, 0x100, 'Speed', 'Value', '>', 5.0), False)
        # Clearing the rows keeps the state visible on the title.
        self.main.reset_tree_values(1)
        self.assertEqual(title.text(9), 'Trigger · 정지')
        # Clicking the trigger title does not sort.
        self.main.show()
        QApplication.processEvents()
        for column in (2, 9):
            QTest.mouseClick(header.viewport(), Qt.LeftButton,
                             pos=QPoint(header.sectionViewportPosition(column) + 5, 5))
        self.assertEqual(header.sortIndicatorSection(), 2)   # CAN ID sorts, Trigger does not


if __name__ == '__main__':
    unittest.main()
