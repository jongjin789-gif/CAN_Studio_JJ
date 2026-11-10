import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import QApplication, QTableWidgetItem, QMessageBox
from PyQt5.QtTest import QTest
from src.log_viewer import LogViewerWindow
from src.can_threads import LogParserThread


class LogFilterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def view(self, count=50000):
        with patch.object(LogViewerWindow, 'start_parsing'):
            view = LogViewerWindow('unused.trc', {})
        self.addCleanup(view.close)
        view.raw_log_data = [dict(can_id=i % 512, offset_ms=i, bus=1, type='DT',
                                  dlc=1, data=bytes([i % 256])) for i in range(count)]
        view.log_table.setRowCount(count)
        view.show()
        self.app.processEvents()
        return view

    def finish_filter(self, view):
        deadline = time.monotonic() + 10
        while view._filtering and time.monotonic() < deadline:
            QTest.qWait(1)
        self.assertFalse(view._filtering, 'Filtering did not finish')

    def test_large_filter_yields_to_ui_and_uses_numeric_log_ids(self):
        view = self.view()
        sent = []
        view.main_window = SimpleNamespace(buses={1: SimpleNamespace(send=sent.append)})
        view.edit_filter_low.setText('0x100')
        view.edit_filter_high.setText('0x101')
        heartbeat = []
        QTimer.singleShot(0, lambda: heartbeat.append(view._filtering))
        # Filtering must work without reading or reparsing table cell text.
        with patch.object(view.log_table, 'item', side_effect=AssertionError('cell text read')):
            view.btn_filter.setChecked(True)
            self.assertTrue(view._filtering)
            self.assertFalse(view.btn_save.isEnabled())
            self.assertFalse(view.btn_send.isEnabled())
            self.assertEqual(view.get_visible_log_data(), [])
            view.start_sending()
            self.assertFalse(view.is_sending)
            self.assertEqual(sent, [])
            self.finish_filter(view)
        self.assertEqual(heartbeat, [True])
        self.assertTrue(view.btn_save.isEnabled())
        self.assertTrue(view.btn_send.isEnabled())
        expected = [entry for entry in view.raw_log_data if 0x100 <= entry['can_id'] <= 0x101]
        self.assertEqual(view.get_visible_log_data(), expected)
        self.assertTrue(view.log_table.isRowHidden(0))
        self.assertFalse(view.log_table.isRowHidden(0x100))
        self.assertTrue(view.log_table.updatesEnabled())

    def test_new_range_and_clear_replace_incomplete_filter(self):
        view = self.view()
        view.edit_filter_high.setText('0x0')
        view.btn_filter.setChecked(True)
        self.assertTrue(view._filtering)
        view.edit_filter_low.setText('0x1FF')
        view.edit_filter_high.setText('0x1FF')
        view.apply_filter(True)
        self.finish_filter(view)
        self.assertTrue(all(entry['can_id'] == 0x1FF for entry in view.get_visible_log_data()))
        self.assertTrue(view.log_table.isRowHidden(0))
        self.assertFalse(view.log_table.isRowHidden(0x1FF))

        view.edit_filter_low.setText('0x100')
        view.apply_filter(True)
        self.assertTrue(view._filtering)
        view.btn_filter.setChecked(False)
        self.finish_filter(view)
        self.assertEqual(view.get_visible_log_data(), view.raw_log_data)
        self.assertTrue(all(not view.log_table.isRowHidden(row) for row in range(view.log_table.rowCount())))

    def test_invalid_range_restores_all_rows_and_close_cancels_work(self):
        view = self.view()
        view.edit_filter_high.setText('0x100')
        view.btn_filter.setChecked(True)
        view.edit_filter_low.setText('0x20000000')
        with patch('src.log_viewer.QMessageBox.warning', return_value=QMessageBox.Ok) as warning:
            view.apply_filter(True)
        warning.assert_called_once()
        self.assertFalse(view.btn_filter.isChecked())
        self.finish_filter(view)
        self.assertEqual(view.get_visible_log_data(), view.raw_log_data)
        view.edit_filter_low.setText('0x0')
        view.btn_filter.setChecked(True)
        self.assertTrue(view._filter_timer.isActive())
        view.close()
        self.app.processEvents()
        self.assertFalse(view._filtering)
        self.assertFalse(view._filter_timer.isActive())

    def test_population_reapplies_filter_and_refresh_cancels_filter(self):
        view = self.view(4)
        view.edit_filter_low.setText('0x1')
        view.edit_filter_high.setText('0x2')
        view.btn_filter.setChecked(True)
        view.populate_log_table()
        self.assertTrue(view._populating)
        self.assertFalse(view.btn_filter.isEnabled())
        QTest.qWait(10)
        self.assertFalse(view._populating)
        self.assertEqual([entry['can_id'] for entry in view.get_visible_log_data()], [1, 2])

        view._filter_chunk_size = 1
        view.edit_filter_low.setText('0x0')
        view.apply_filter(True)
        self.assertTrue(view._filtering)
        with patch.object(view, 'start_parsing'):
            view.refresh_parsing()
        self.app.processEvents()
        self.assertFalse(view._filtering)
        self.assertFalse(view._filter_timer.isActive())
        self.assertEqual(view.log_table.rowCount(), 0)

    def test_bus_and_id_filters_combine_and_clear_for_all_four_buses(self):
        view = self.view(12)
        for row, entry in enumerate(view.raw_log_data):
            entry.update(bus=row % 4 + 1, can_id=0x100 if row < 8 else 0x101)
        view.edit_filter_low.setText('0x100')
        view.edit_filter_high.setText('0x100')
        view.btn_filter.setChecked(True)
        self.assertEqual(view.get_visible_log_data(), view.raw_log_data[:8])
        for bus in (1, 2, 3, 4):
            with self.subTest(bus=bus):
                view.combo_filter_bus.setCurrentIndex(view.combo_filter_bus.findData(bus))
                expected = [entry for entry in view.raw_log_data if entry['bus'] == bus and entry['can_id'] == 0x100]
                self.assertEqual(view.get_visible_log_data(), expected)
                for row, entry in enumerate(view.raw_log_data):
                    self.assertEqual(not view.log_table.isRowHidden(row), entry in expected)

        view.edit_filter_high.setText('0x101')
        view.edit_filter_high.editingFinished.emit()
        self.assertEqual([entry['bus'] for entry in view.get_visible_log_data()], [4, 4, 4])
        view.combo_filter_bus.setCurrentIndex(0)
        self.assertEqual(view.get_visible_log_data(), view.raw_log_data)
        view.combo_filter_bus.setCurrentIndex(view.combo_filter_bus.findData(2))
        view.btn_filter.setChecked(False)
        self.assertEqual(view.get_visible_log_data(), view.raw_log_data)

    def test_bus_change_replaces_pending_large_filter(self):
        view = self.view()
        for row, entry in enumerate(view.raw_log_data):
            entry['bus'] = row % 4 + 1
        view.edit_filter_high.setText('0x1FF')
        view.btn_filter.setChecked(True)
        self.assertTrue(view._filtering)
        view.combo_filter_bus.setCurrentIndex(view.combo_filter_bus.findData(2))
        self.finish_filter(view)
        self.assertEqual(view.get_visible_log_data(), view.raw_log_data[1::4])

    def test_cancel_pending_filter_only_restores_rows_already_hidden(self):
        view = self.view()
        self.assertEqual((view.edit_filter_low.text(), view.edit_filter_high.text()), ('0x000', '0x7FF'))
        self.assertFalse(view.btn_cancel_filter.isEnabled())
        view._filter_chunk_size = 32
        view.edit_filter_high.setText('0x0')
        view.btn_filter.setChecked(True)
        self.assertTrue(view._filtering)
        hidden = {row for row in range(view.log_table.rowCount()) if view.log_table.isRowHidden(row)}
        self.assertEqual(len(hidden), 31)

        class NoIdReads(list):
            def __getitem__(self, key):
                if isinstance(key, int):
                    raise AssertionError('Cancel must not recheck log IDs')
                return super().__getitem__(key)

        view.raw_log_data = NoIdReads(view.raw_log_data)
        with patch.object(view.log_table, 'setRowHidden', wraps=view.log_table.setRowHidden) as show:
            view.btn_cancel_filter.click()
            self.finish_filter(view)
        self.assertEqual({call.args for call in show.call_args_list}, {(row, False) for row in hidden})
        self.assertEqual(show.call_count, len(hidden))
        self.assertFalse(view.btn_filter.isChecked())
        self.assertFalse(view._filter_timer.isActive())
        self.assertFalse(view._hidden_log_rows)
        self.assertEqual(view.get_visible_log_data(), view.raw_log_data)
        self.assertEqual(view.edit_filter_high.text(), '0x0')

    def test_cancel_complete_filter_yields_and_repeated_cancel_does_not_restart(self):
        view = self.view()
        view.edit_filter_high.setText('0x0')
        view.btn_filter.setChecked(True)
        self.finish_filter(view)
        self.assertTrue(view.btn_cancel_filter.isEnabled())
        hidden_count = len(view._hidden_log_rows)
        heartbeat = []
        QTimer.singleShot(0, lambda: heartbeat.append(view._filtering))
        with patch.object(view.log_table, 'setRowHidden', wraps=view.log_table.setRowHidden) as show:
            view.btn_cancel_filter.click()
            self.assertTrue(view._filtering)
            self.assertFalse(view.btn_cancel_filter.isEnabled())
            progress = view._filter_index
            view.cancel_filter()
            self.assertEqual(view._filter_index, progress)
            self.finish_filter(view)
        self.assertEqual(heartbeat, [True])
        self.assertEqual(show.call_count, hidden_count)
        self.assertFalse(view._hidden_log_rows)
        self.assertEqual(view.get_visible_log_data(), view.raw_log_data)

    def test_unchanged_filter_request_does_not_repeat_work(self):
        view = self.view()
        view.btn_filter.setChecked(True)
        self.assertTrue(view._filtering)
        progress = view._filter_index
        view.edit_filter_high.editingFinished.emit()
        self.assertEqual(view._filter_index, progress)
        self.finish_filter(view)
        with patch.object(view, '_apply_filter_chunk', wraps=view._apply_filter_chunk) as chunk:
            view.apply_filter(True)
            chunk.assert_not_called()
        with patch.object(view.log_table, 'setRowHidden') as show:
            view.btn_cancel_filter.click()
            show.assert_not_called()
        self.assertFalse(view._filtering)

    def test_extended_filter_send_and_original_export(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'source.trc'
            header = b';$FILEVERSION=2.1\r\n; original header\r\n'
            rows = [b' 10 0.000 DT 1 0001 Rx - 1 AB\r\n',
                    b' 20 1.000 DT 1 001ABCDE Rx - 1 CD\r\n',
                    b' 30 2.000 DT 1 0002 Rx - 1 EF\r\n',
                    b' 40 3.000 DT 2 001ABCDE Rx - 1 12\r\n']
            source.write_bytes(header + b''.join(rows))
            results = []
            parser = LogParserThread(str(source), {})
            parser.finished_signal.connect(lambda *args: results.append(args[2]))
            parser.run()
            self.assertEqual(len(results[0]), 4)
            sent = []
            main = SimpleNamespace(buses={1: SimpleNamespace(send=sent.append),
                                          2: SimpleNamespace(send=sent.append)},
                                   bus_capabilities={1: {'is_fd': True}, 2: {'is_fd': True}},
                                   record_tx_activity=lambda *a, **kwargs: None,
                                   statusBar=lambda: SimpleNamespace(showMessage=lambda *a: None))
            with patch.object(LogViewerWindow, 'start_parsing'):
                view = LogViewerWindow(str(source), {}, main)
            view.raw_log_data = results[0]
            view.log_table.setRowCount(4)
            for row, entry in enumerate(view.raw_log_data):
                for col in range(7):
                    view.log_table.setItem(row, col, QTableWidgetItem(str(row)))
                view.log_table.item(row, 4).setText(f"{entry['can_id']:X}")
            view.edit_filter_low.setText('0x001ABCDE')
            view.edit_filter_high.setText('0x001ABCDE')
            view.btn_filter.setChecked(True)
            self.assertEqual(len(view.get_visible_log_data()), 2)
            view.combo_filter_bus.setCurrentIndex(view.combo_filter_bus.findData(2))
            self.assertEqual(len(view.get_visible_log_data()), 1)
            target = Path(folder) / 'filtered.trc'
            self.assertEqual(view.export_filtered_log(str(target)), 1)
            self.assertEqual(target.read_bytes(), header + rows[3].replace(b' 40 ', b' 1 ', 1))
            self.assertEqual(source.read_bytes(), header + b''.join(rows))
            view.start_sending()
            QTest.qWait(30)
            self.assertEqual([m.arbitration_id for m in sent], [0x1ABCDE])
            self.assertEqual([bytes(m.data) for m in sent], [b'\x12'])
            self.assertTrue(all(m.is_extended_id for m in sent))
            view.stop_sending()
            view.deleteLater()
