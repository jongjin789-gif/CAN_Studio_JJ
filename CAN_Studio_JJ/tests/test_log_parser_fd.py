import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PyQt5.QtWidgets import QApplication
from cantools.database.can import Message, Signal
from src.can_threads import LogParserThread
from src.log_viewer import LogViewerWindow


class LogParserFDTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_fd_lengths_decode_and_populate_tree(self):
        for code, length in [(9, 12), (10, 16), (11, 20), (12, 24),
                             (13, 32), (14, 48), (15, 64), (8, 8)]:
            for field in {code, length}:
                for with_bus in (True, False):
                    with self.subTest(code=code, field=field, with_bus=with_bus):
                        message = Message(0x2A, 'Command', length,
                                          [Signal('Tail', (length - 1) * 8, 8)], is_fd=True)
                        db = {1: {0x2A: message}}
                        payload = bytes(length - 1) + b'\x7b'
                        row = (f'1 0.000 FB 1 002A Rx - {field} ' if with_bus else
                               f'1 0.000 FB 002A Rx {field} ')
                        with tempfile.TemporaryDirectory() as folder:
                            path = Path(folder) / 'sample.trc'
                            path.write_text(';$FILEVERSION=2.1\n' + row + payload.hex(' ') + '\n')
                            results, errors = [], []
                            parser = LogParserThread(str(path), db)
                            parser.finished_signal.connect(lambda *args: results.append(args))
                            parser.error_signal.connect(errors.append)
                            parser.run()
                            self.assertEqual(errors, [])
                            signals, found, raw = results[0]
                            self.assertEqual(raw[0]['dlc'], length)
                            self.assertEqual(raw[0]['data'], payload)
                            self.assertEqual(signals[(1, 0x2A)]['Tail'][1], [123.0])
                            with patch.object(LogViewerWindow, 'start_parsing'):
                                view = LogViewerWindow(str(path), db)
                            view.signal_data = signals
                            view.populate_tree(found)
                            self.assertEqual(view.tree.topLevelItem(0).child(0).text(0), 'Tail')
                            view.close()

    def test_byte_count_is_not_unconditionally_expanded(self):
        length, data = LogParserThread._parse_payload(12, ['AB'] * 12, 'FB')
        self.assertEqual((length, data), (12, b'\xab' * 12))
