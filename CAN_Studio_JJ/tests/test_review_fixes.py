"""Regression tests for the 2026-11 review fixes (extended IDs, TRC 1.x, record temp file, packages)."""
import datetime
import json
import os
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PyQt5.QtWidgets import QApplication
from cantools.database.can import Message, Signal

from src.can_threads import LogParserThread
from src.db_frame_format import frame_is_extended
from src.record_window import RecordWindow
from src.tx_runtime import TxRuntime
from src.user_panel_v2.storage import load_bundle, save_bundle


def parse_trc(text, db=None):
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / 'sample.trc'
        path.write_text(text)
        results, errors = [], []
        parser = LogParserThread(str(path), db or {})
        parser.finished_signal.connect(lambda *args: results.append(args))
        parser.error_signal.connect(errors.append)
        parser.run()
    assert not errors, errors
    return results[0][2]


class ExtendedIdTest(unittest.TestCase):
    def test_resolution_order(self):
        ext_msg = Message(0x123, 'Ext', 8, [], is_extended_frame=True)
        std_msg = Message(0x123, 'Std', 8, [])
        self.assertTrue(frame_is_extended(0x123, None, ext_msg))
        self.assertFalse(frame_is_extended(0x123, None, std_msg))
        self.assertFalse(frame_is_extended(0x123, False, ext_msg))
        # Legacy fallback is unchanged when nothing else is known.
        self.assertFalse(frame_is_extended(0x7FF))
        self.assertTrue(frame_is_extended(0x800))

    def test_main_tx_uses_dbc_extended_flag_for_low_ids(self):
        ext_msg = Message(0x123, 'Ext', 8, [], is_extended_frame=True)
        sent = []
        bus = SimpleNamespace(send=sent.append)
        panel = SimpleNamespace(buses={1: bus}, db_messages={1: {0x123: ext_msg}},
                                main_window=SimpleNamespace())
        packet = dict(bus=1, id=0x123, data=[0] * 8, is_fd=False, length=8)
        TxRuntime(packet, panel, {}).send()
        self.assertTrue(sent[0].is_extended_id)

    def test_main_tx_without_db_keeps_legacy_guess(self):
        sent = []
        panel = SimpleNamespace(buses={1: SimpleNamespace(send=sent.append)}, db_messages={},
                                main_window=SimpleNamespace())
        for can_id, expected in ((0x123, False), (0x18FF0000, True)):
            TxRuntime(dict(bus=1, id=can_id, data=[0], is_fd=False, length=1), panel, {}).send()
            self.assertEqual(sent[-1].is_extended_id, expected)

    def test_record_tx_activity_marks_low_extended_id(self):
        from src.main_window import UniversalCANMonitor
        recorder = Mock()
        recorder.isVisible.return_value = True
        ext_msg = Message(0x123, 'Ext', 1, [], is_extended_frame=True)
        main = SimpleNamespace(rx_threads={}, record_window=recorder,
                               db_messages={1: {0x123: ext_msg}},
                               route_raw_msg_to_record=Mock())
        UniversalCANMonitor.record_tx_activity(main, 1, 0x123, b'\0', False, 1.0)
        self.assertTrue(main.route_raw_msg_to_record.call_args.args[3])
        UniversalCANMonitor.record_tx_activity(main, 1, 0x123, b'\0', False, 1.0, is_ext=False)
        self.assertFalse(main.route_raw_msg_to_record.call_args.args[3])


class TrcParserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_trc_13_rows(self):
        rows = parse_trc(
            ';$FILEVERSION=1.3\n'
            ';$STARTTIME=43000.5\n'
            '     1)      1059.900 1  Rx        0300 -  8    01 02 03 04 05 06 07 08\n'
            '     2)      1060.100 2  Tx    18FF0000 -  2    AA BB\n'
            '     3)      1061.000 1  Rx        0123 -  0    RTR\n')
        self.assertEqual(len(rows), 2)
        self.assertEqual((rows[0]['bus'], rows[0]['can_id'], rows[0]['data']),
                         (1, 0x300, bytes(range(1, 9))))
        self.assertFalse(rows[0]['is_extended_id'])
        self.assertEqual((rows[1]['bus'], rows[1]['can_id'], rows[1]['data']), (2, 0x18FF0000, b'\xaa\xbb'))
        self.assertTrue(rows[1]['is_extended_id'])

    def test_trc_10_rows(self):
        rows = parse_trc('     1)      1841  0001  8  00 11 22 33 44 55 66 77\n'
                         '     2)      1842  00000123  1  FF\n')
        self.assertEqual([(r['can_id'], r['dlc']) for r in rows], [(1, 8), (0x123, 1)])
        self.assertEqual(rows[0]['offset_ms'], 1841.0)
        self.assertFalse(rows[0]['is_extended_id'])
        self.assertTrue(rows[1]['is_extended_id'])

    def test_21_low_extended_id_detected_by_width(self):
        rows = parse_trc(';$FILEVERSION=2.1\n'
                         '       1         0.000 DT 1  00000123 Rx -  1    7B\n'
                         '       2         1.000 DT 1      0123 Rx -  1    7B\n')
        self.assertEqual([r['is_extended_id'] for r in rows], [True, False])


class RecordTempFileTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_lines_stream_to_temp_and_save_uses_first_frame_start_time(self):
        recorder = RecordWindow()
        self.addCleanup(recorder.deleteLater)
        self.addCleanup(recorder._discard_temp)
        first = datetime.datetime(2026, 11, 5, 9, 30, 0).timestamp()
        recorder.add_log_entry(first, 0x123, b'\x01', True, False, False, True)
        recorder.add_log_entry(first + 0.5, 0x10, b'\x02', False, False, False, False)
        temp = recorder.temp_path
        self.assertTrue(os.path.exists(temp))
        with tempfile.TemporaryDirectory() as folder:
            out = os.path.join(folder, 'out.trc')
            recorder._write_trc(out)
            text = Path(out).read_text(encoding='utf-8')
        self.assertIn('Start time: 2026-11-05 09:30:00.000', text)
        rows = [line for line in text.splitlines() if line and not line.startswith(';')]
        self.assertEqual(len(rows), 2)
        self.assertIn('00000123 Rx', rows[0])
        self.assertIn('500.000', rows[1])
        # Reloading gives back the original absolute time.
        reparsed = parse_trc(text)
        self.assertAlmostEqual(reparsed[0]['timestamp'], first, places=2)

    def test_close_button_discards_without_saving(self):
        recorder = RecordWindow()
        self.addCleanup(recorder.deleteLater)
        recorder.add_log_entry(1.0, 1, b'\0', False, False, False, True)
        temp = recorder.temp_path
        with patch.object(recorder, 'auto_save_log') as auto_save:
            recorder.on_btn_close_clicked()
        auto_save.assert_not_called()
        self.assertFalse(os.path.exists(temp))

    def test_window_close_auto_saves_then_removes_temp(self):
        recorder = RecordWindow()
        self.addCleanup(recorder.deleteLater)
        recorder.add_log_entry(1.0, 1, b'\0', False, False, False, True)
        temp = recorder.temp_path
        with patch.object(recorder, 'auto_save_log') as auto_save:
            recorder.close()
        auto_save.assert_called_once()
        self.assertFalse(os.path.exists(temp))

    def test_failed_auto_save_keeps_recording_and_can_retry(self):
        recorder = RecordWindow()
        self.addCleanup(recorder.deleteLater)
        self.addCleanup(recorder._discard_temp)
        recorder.show()
        recorder.add_log_entry(1.0, 1, b'\x01', False, False, False, True)
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(recorder, '_log_dir', return_value=folder), \
                patch('src.record_window.show_error') as error:
            with patch.object(recorder, '_write_trc', side_effect=PermissionError('denied')):
                self.assertFalse(recorder.close())
            error.assert_called_once()
            self.assertFalse(recorder.is_saved)
            self.assertTrue(recorder.isVisible())
            self.assertTrue(recorder.timer.isActive())
            self.assertTrue(Path(recorder.temp_path).exists())
            recorder.add_log_entry(2.0, 1, b'\x02', False, False, False, True)
            self.assertTrue(recorder.close())
            saved = next(Path(folder).glob('*.trc')).read_text(encoding='utf-8')
            self.assertEqual(len(parse_trc(saved)), 2)
            self.assertFalse(Path(recorder.temp_path).exists())

    def test_failed_write_preserves_destination_and_temp_recording(self):
        recorder = RecordWindow()
        self.addCleanup(recorder.deleteLater)
        self.addCleanup(recorder._discard_temp)
        recorder.add_log_entry(1.0, 1, b'\x01', False, False, False, True)
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'out.trc'
            target.write_text('original', encoding='utf-8')
            with patch('src.record_window.shutil.copyfileobj', side_effect=OSError('disk full')):
                with self.assertRaises(OSError):
                    recorder._write_trc(target)
            self.assertEqual(target.read_text(encoding='utf-8'), 'original')
            self.assertEqual(list(Path(folder).iterdir()), [target])
            self.assertTrue(Path(recorder.temp_path).exists())
            recorder._write_trc(target)
            self.assertEqual(len(parse_trc(target.read_text(encoding='utf-8'))), 1)


class PackageLoadTest(unittest.TestCase):
    def test_same_db_name_on_two_buses_does_not_overwrite(self):
        with tempfile.TemporaryDirectory() as folder:
            a = Path(folder, 'a'); a.mkdir()
            b = Path(folder, 'b'); b.mkdir()
            (a / 'net.dbc').write_text('BUS1')
            (b / 'net.dbc').write_text('BUS2')
            pkg = save_bundle(str(Path(folder, 'panel')), {'title': 'x'},
                              {1: [str(a / 'net.dbc')], 2: [str(b / 'net.dbc')]})
            _panel, paths = load_bundle(pkg)
            self.assertNotEqual(paths[1][0], paths[2][0])
            self.assertEqual(Path(paths[1][0]).read_text(), 'BUS1')
            self.assertEqual(Path(paths[2][0]).read_text(), 'BUS2')
            self.assertEqual(Path(paths[1][0]).name, Path(paths[2][0]).name)

    def test_foreign_zip_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            pkg = Path(folder, 'x.pjjupkg')
            with zipfile.ZipFile(pkg, 'w') as zf:
                zf.writestr('manifest.json', json.dumps({'format': 'OTHER'}))
                zf.writestr('panel/panel.json', '{}')
            with self.assertRaises(ValueError):
                load_bundle(str(pkg))

    def test_invalid_panel_is_rejected_before_any_change(self):
        from src.user_panel_v2.window import UserPanelWindow
        for bad in ([], {'pages': [{'id': 'p', 'name': 'a'}, {'id': 'p', 'name': 'b'}]},
                    {'widgets': 'nope'}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                UserPanelWindow._validate_panel_data(bad)
        UserPanelWindow._validate_panel_data({'title': 'ok'})


class LinuxCommandTest(unittest.TestCase):
    """SocketCAN ip/sudo handling now lives in src/can_devices/device_socketcan.py."""
    PROMPT = 'src.can_devices.device_socketcan.QInputDialog.getText'

    def backend(self, responses=None, run=None):
        from src.can_devices.device_socketcan import SocketCanBackend
        backend = SocketCanBackend(None)
        calls = []

        def fake(command, **kwargs):
            calls.append(command)
            return run(command) if run else responses.pop(0)

        backend.run_subprocess = fake
        backend.ip_binary = lambda: '/usr/sbin/ip'
        return backend, calls

    def test_timeout_returns_failed_result_instead_of_hanging(self):
        from src.can_devices.device_socketcan import SocketCanBackend
        backend = SocketCanBackend(None)
        backend.CMD_TIMEOUT_S = 1
        with patch('subprocess.run', side_effect=subprocess.TimeoutExpired('ip', 1)):
            result = backend.run_subprocess(['ip', 'link'])
        self.assertEqual(result.returncode, 1)

    def test_sudo_ticket_is_tried_before_prompting(self):
        backend, calls = self.backend(run=lambda command: subprocess.CompletedProcess(
            command, 1 if command[0] != 'sudo' else 0, '', 'Operation not permitted'))
        with patch('os.geteuid', create=True, return_value=1000), patch(self.PROMPT) as prompt:
            result = backend.run_ip_command(['ip', 'link', 'set', 'can0', 'down'])
        prompt.assert_not_called()
        self.assertEqual(result.returncode, 0)
        self.assertEqual(calls[0][0], '/usr/sbin/ip')
        self.assertEqual(calls[1][:3], ['sudo', '-n', '--'])

    def test_command_error_after_sudo_ticket_does_not_prompt_again(self):
        denied = subprocess.CompletedProcess([], 2, '', 'RTNETLINK answers: Operation not permitted')
        bad_arg = subprocess.CompletedProcess([], 2, '', 'RTNETLINK answers: Invalid argument')
        backend, calls = self.backend([denied, bad_arg])
        with patch('os.geteuid', create=True, return_value=1000), patch(self.PROMPT) as prompt:
            result = backend.run_ip_command(['ip', 'link', 'set', 'can0', 'up'])
        prompt.assert_not_called()
        self.assertIn('Invalid argument', result.stderr)
        self.assertEqual(len(calls), 2)

    def test_password_prompt_when_sudo_needs_it_and_cache_expires(self):
        denied = subprocess.CompletedProcess([], 2, '', 'Operation not permitted')
        need_pw = subprocess.CompletedProcess([], 1, '', 'sudo: a password is required')
        ok = subprocess.CompletedProcess([], 0, '', '')
        backend, calls = self.backend([denied, need_pw, ok])
        with patch('os.geteuid', create=True, return_value=1000), \
                patch(self.PROMPT, return_value=('pw', True)) as prompt:
            result = backend.run_ip_command(['ip', 'link', 'set', 'can0', 'up'])
        prompt.assert_called_once()
        self.assertEqual(result.returncode, 0)
        self.assertEqual(calls[2][:4], ['sudo', '-S', '-p', ''])
        self.assertEqual(backend._sudo_password[0], 'pw')
        # After the TTL the cached password is not reused.
        backend._sudo_password = ('pw', backend._sudo_password[1] - 61)
        backend2, _ = self.backend([denied, need_pw, ok])
        backend.run_subprocess = backend2.run_subprocess
        with patch('os.geteuid', create=True, return_value=1000), \
                patch(self.PROMPT, return_value=('', False)) as prompt:
            self.assertIsNone(backend.run_ip_command(['ip', 'link']))
        prompt.assert_called_once()


if __name__ == '__main__':
    unittest.main()
