import datetime
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


class CL2000LogTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_decode_timing_tree_and_filtered_export(self):
        header = b'# Logger type: CL2000\r\n# Time format: 6\r\nTimestamp;Type;ID;Data\r\n'
        rows = [b'2026_10_06T23_59_59_939;0;108;0100000000000000\r\n',
                b'2026_10_07T00_00_00_940;9;108;0200000000000000\r\n']
        db = {1: {0x108: Message(0x108, 'Test', 8, [Signal('Value', 0, 8)])}}
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'sample.TXT'
            source.write_bytes(header + b''.join(rows))
            results, errors = [], []
            parser = LogParserThread(str(source), db)
            parser.finished_signal.connect(lambda *args: results.append(args))
            parser.error_signal.connect(errors.append)
            parser.run()
            self.assertEqual(errors, [])
            signals, found, logs = results[0]
            self.assertEqual(len(logs), 2)
            self.assertEqual([r['offset_ms'] for r in logs], [0, 1001])
            self.assertEqual(logs[0]['timestamp'], datetime.datetime(2026, 10, 6, 23, 59, 59, 939000).timestamp())
            self.assertEqual(signals[(1, 0x108)]['Value'][1], [1.0, 2.0])
            self.assertTrue(logs[1]['is_extended_id'])
            self.assertEqual(logs[1]['direction'], 'Tx')
            with patch.object(LogViewerWindow, 'start_parsing'):
                view = LogViewerWindow(str(source), db)
            view.signal_data = signals
            view.populate_tree(found)
            self.assertEqual(view.tree.topLevelItem(0).child(0).text(0), 'Value')
            target = Path(folder) / 'filtered.txt'
            self.assertEqual(view.export_filtered_log(str(target), logs[1:]), 1)
            self.assertEqual(target.read_bytes(), header + rows[1])
            self.assertEqual(source.read_bytes(), header + b''.join(rows))
            view.close()
