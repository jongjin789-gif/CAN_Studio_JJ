import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PyQt5.QtCore import QPoint, Qt
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QDialog
from cantools.database.can import Database, Message, Signal
from src.main_window import UniversalCANMonitor
from src.session_manager import SessionManager
from src.record_window import RecordWindow
from src.session_storage import write_session


class NewScreenTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        with patch.object(SessionManager, 'start'), patch.object(UniversalCANMonitor, 'search_can_channels'):
            self.main = UniversalCANMonitor()
        self.main.session.directory = Path(self.temp.name)
        self.main.session.autosave_path = Path(self.temp.name) / 'auto.pjjsettings'
        self.main.session.report = Mock()
        self.addCleanup(self.close)

    def close(self):
        self.main.session.ready = False
        self.main.close()
        self.main.deleteLater()

    def packet(self):
        return dict(bus=4, id=0x123, is_fd=False, is_brs=False, length=1, data=[7],
                    cycle=100, note='kept', symbol='M', crc_type='N/A', count=0)

    def fill(self):
        db = Database(messages=[Message(frame_id=0x123, name='M', length=1, signals=[Signal('A', 0, 8)])])
        self.main.install_database(db, 4, 'test.dbc', db.as_dbc_string().encode('cp1252'), '')
        self.main.tx_panel.add_packet_to_tree(self.packet())
        self.main.open_user_panel()
        panel = self.main.user_panel_window
        panel._load_panel_data(dict(title='Saved title', widgets=[dict(id='tool', widget_type='shape_rect', behavior='none', binding={})],
                                   tx_packets=[dict(self.packet(), packet_id='p')]))
        return panel

    def test_main_new_screen_disconnects_and_persists_empty_workspace(self):
        self.fill()
        device = Mock()
        self.main.buses[4] = device
        self.main.combo_bitrate[4].setCurrentText('250 kBit/s')
        self.main.search_input.setText('old filter')
        self.main.db_group.set_collapsed(True)
        self.main.session.ready = True
        self.assertTrue(self.main.session.new_screen())
        device.shutdown.assert_called_once()
        self.assertTrue(all(bus is None for bus in self.main.buses.values()))
        self.assertTrue(all(not messages for messages in self.main.db_messages.values()))
        self.assertEqual(self.main.tx_panel.tree.topLevelItemCount(), 0)
        self.assertIsNone(self.main.user_panel_window)
        self.assertEqual(self.main.search_input.text(), '')
        self.assertFalse(self.main.db_group.is_collapsed())
        saved = json.loads(self.main.session.autosave_path.read_text(encoding='utf-8'))
        self.assertEqual(saved['tx_packets'], [])
        self.assertIsNone(saved['panel'])
        self.assertTrue(all(cfg['bitrate'] == '500 kBit/s' and cfg['device'] is None for cfg in saved['can'].values()))

    def test_panel_new_clears_tools_packets_clipboards_and_history_only(self):
        panel = self.fill()
        device = Mock()
        self.main.buses[4] = device
        panel._tool_clipboard = [dict(id='old')]
        panel._packet_clipboard = [self.packet()]
        panel.act_new_panel.trigger()
        self.assertEqual(panel.widgets_config, [])
        self.assertEqual(panel.tx_packets, [])
        self.assertEqual(panel.init_steps, [])
        self.assertEqual(panel._undo_stack, [])
        self.assertEqual(panel._tool_clipboard, [])
        self.assertEqual(panel._packet_clipboard, [])
        self.assertEqual(panel.windowTitle(), 'User Panel')
        self.assertEqual(len(panel.pages), 1)
        self.assertEqual(panel.grid_rows, 36)
        self.assertIs(self.main.buses[4], device)
        self.assertEqual(self.main.list_db_files[4].count(), 1)
        self.assertEqual(self.main.tx_panel.tree.topLevelItemCount(), 1)

    def test_panel_title_can_be_renamed_locally_and_restored(self):
        panel = self.fill()
        with patch('src.user_panel_v2.window.QInputDialog.getText', return_value=('EMB Panel', True)):
            panel.act_rename_panel.trigger()
        saved = json.loads(json.dumps(panel._panel_data()))
        self.assertEqual(saved['title'], 'EMB Panel')
        self.assertEqual(self.main.session.capture()['panel']['data']['title'], 'EMB Panel')
        panel.setWindowTitle('temporary')
        panel._load_panel_data(saved)
        self.assertEqual(panel.windowTitle(), 'EMB Panel')
        self.assertFalse(hasattr(self.main, 'rename_user_panel'))

    def test_double_click_edit_keeps_expansion_for_accept_and_cancel(self):
        self.fill()
        self.main.show()
        self.app.processEvents()
        tree = self.main.tx_panel.tree
        item = tree.topLevelItem(0)
        for expanded in (False, True):
            for result in (QDialog.Accepted, QDialog.Rejected):
                with self.subTest(expanded=expanded, result=result):
                    item.setExpanded(expanded)
                    rect = tree.visualItemRect(item)
                    pos = QPoint(tree.columnViewportPosition(2) + 30, rect.center().y())
                    with patch('src.tx_panel.TxPacketDialog') as dialog:
                        dialog.return_value.exec_.return_value = result
                        dialog.return_value.get_packet_data.return_value = self.packet()
                        QTest.mouseClick(tree.viewport(), Qt.LeftButton, Qt.NoModifier, pos)
                        QTest.mouseDClick(tree.viewport(), Qt.LeftButton, Qt.NoModifier, pos)
                        self.app.processEvents()
                        dialog.assert_called_once()
                    self.assertEqual(item.isExpanded(), expanded)

    def test_record_preservation_failure_keeps_current_workspace(self):
        panel = self.fill()
        with patch.object(self.main.session, 'preserve_record', side_effect=OSError('disk full')):
            self.assertFalse(self.main.session.new_screen())
        self.assertIs(self.main.user_panel_window, panel)
        self.assertEqual(self.main.list_db_files[4].count(), 1)
        self.assertEqual(self.main.tx_panel.tree.topLevelItemCount(), 1)

    def start_recording(self):
        recorder = RecordWindow()
        recorder.show()
        recorder.add_log_entry(100.0, 0x123, b'\x01', True, False, False, True)
        recorder.add_log_entry(100.5, 0x124, b'\x02', False, False, False, False)
        self.main.record_window = recorder
        self.addCleanup(recorder.deleteLater)
        return recorder

    def assert_record_preserved(self):
        logs = list((Path(self.temp.name) / 'Log').glob('*.trc'))
        self.assertEqual(len(logs), 1)
        text = logs[0].read_text(encoding='utf-8')
        self.assertIn(';$STARTTIME=', text)
        self.assertIn('00000123 Rx', text)
        self.assertIn('500.000', text)
        self.assertIn('0124 Tx', text)

    def test_new_screen_preserves_streamed_recording(self):
        recorder = self.start_recording()
        self.assertTrue(self.main.session.new_screen())
        self.assert_record_preserved()
        self.assertFalse(Path(recorder.temp_path).exists())

    def test_session_load_preserves_streamed_recording(self):
        path = Path(self.temp.name) / 'saved.pjjsettings'
        write_session(path, self.main.session.capture())
        recorder = self.start_recording()
        self.assertTrue(self.main.session.load(path))
        self.assert_record_preserved()
        self.assertFalse(Path(recorder.temp_path).exists())

    def test_main_close_cancels_when_record_save_fails(self):
        self.main.show()
        recorder = self.start_recording()
        with patch.object(recorder, '_write_trc', side_effect=PermissionError('denied')), \
                patch('src.record_window.show_error'), \
                patch.object(self.main.session, 'stop_all') as stop:
            self.assertFalse(self.main.close())
        stop.assert_not_called()
        self.assertTrue(self.main.isVisible())
        self.assertTrue(recorder.isVisible())
        self.assertTrue(Path(recorder.temp_path).exists())
        recorder.on_btn_close_clicked()

    def test_failed_preservation_keeps_recording_and_workspace(self):
        recorder = self.start_recording()
        with patch.object(recorder, '_write_trc', side_effect=OSError('disk full')):
            self.assertFalse(self.main.session.new_screen())
        self.assertIs(self.main.record_window, recorder)
        self.assertFalse(recorder.is_saved)
        self.assertTrue(recorder.isVisible())
        self.assertTrue(Path(recorder.temp_path).exists())
        recorder.on_btn_close_clicked()


if __name__ == '__main__':
    unittest.main()
