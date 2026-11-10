import copy
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication, QListWidgetItem, QMessageBox
from src.main_window import UniversalCANMonitor
from src.session_manager import SessionManager
from src.user_panel_v2.dbc_identity import database_signature


class PanelClearHistoryTest(unittest.TestCase):
    def test_main_paste_unassigned_and_session_roundtrip(self):
        import json
        from src.tx_panel import TxPacketDialog
        p = dict(bus=1, id=0x123, data=list(range(12)), length=12, is_fd=True,
                 is_brs=True, cycle=20, symbol='Copied', note='memo', count=4, crc_type='N/A')
        previous_clipboard = QApplication.clipboard().text()
        self.addCleanup(lambda: QApplication.clipboard().setText(previous_clipboard))
        QApplication.clipboard().setText(json.dumps([p]))
        self.main.tx_panel.paste_packets()
        item = self.main.tx_panel.tree.topLevelItem(0)
        self.assertEqual(item.packet_data['bus'], 0)
        self.assertIn('미선택', item.text(0))
        item.start_timer()
        item.send_packet()
        self.assertFalse(item.timer.isActive())
        self.assertEqual(item.packet_data['count'], 0)
        editor = TxPacketDialog({}, self.main.tx_panel)
        self.addCleanup(editor.close)
        editor.set_packet_data(item.packet_data)
        self.assertEqual(editor.selected_bus(), 0)
        editor.combo_bus.setCurrentText('2')
        restored = editor.get_packet_data()
        for key in ('is_fd', 'is_brs', 'length', 'data'):
            self.assertEqual(restored[key], p[key])
        self.panel.tx_packets = [dict(item.packet_data, packet_id='unassigned')]
        state = self.main.session.capture()
        prepared = self.main.session.prepare(state)
        try:
            self.assertEqual(state['tx_packets'][0]['bus'], 0)
            self.assertEqual(prepared['panel'].tx_packets[0]['bus'], 0)
        finally:
            self.main.session.dispose(prepared)

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        with patch.object(SessionManager, 'start'), patch.object(UniversalCANMonitor, 'search_can_channels'):
            self.main = UniversalCANMonitor()
        self.temp = tempfile.TemporaryDirectory()
        self.main.session.directory = Path(self.temp.name)
        self.main.session.ready = False
        self.main.open_user_panel()
        self.panel = self.main.user_panel_window
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.main.close)

    def add_db(self, raw, bus=1, name='test.dbc'):
        item = QListWidgetItem(name)
        item.setData(Qt.UserRole + 2, raw)
        self.main.list_db_files[bus].addItem(item)
        return item

    def test_display_test_selection_and_mode_guard(self):
        self.panel._load_panel_data(dict(widgets=[
            dict(id='rx', behavior='rx', widget_type='label', binding={})]))
        self.panel.selected_widget_id = None
        self.panel.selected_widget_ids.clear()
        self.panel._refresh_selection_ui()
        self.assertEqual(self.panel.inspector_tabs.count(), 1)
        self.panel.selected_widget_id = 'rx'
        self.panel.selected_widget_ids = {'rx'}
        self.panel._refresh_selection_ui()
        self.assertEqual(self.panel.inspector_tabs.tabText(1), '표시 테스트')
        self.panel.spin_sim_value.setValue(42)
        with patch.object(self.panel, '_update_widget_value') as update:
            self.panel.simulate_selected_rx()
            update.assert_called_once_with(self.panel.widgets_config[0], 42.0, force=True)
        self.panel.btn_sim_auto.setChecked(True)
        self.assertTrue(self.panel._sim_timer.isActive())
        self.panel.selected_widget_id = None
        self.panel.selected_widget_ids.clear()
        self.panel._refresh_selection_ui()
        self.assertEqual(self.panel.inspector_tabs.count(), 1)
        self.assertFalse(self.panel._sim_timer.isActive())
        self.assertFalse(self.panel.btn_sim_auto.isChecked())
        self.panel.selected_widget_id = 'rx'
        self.panel.selected_widget_ids = {'rx'}
        self.panel._refresh_selection_ui()
        self.panel.btn_sim_auto.setChecked(True)
        self.panel.set_mode('standby')
        self.assertFalse(self.panel._sim_timer.isActive())
        self.assertEqual(self.panel.inspector_tabs.count(), 1)
        with patch.object(self.panel, '_update_widget_value') as update:
            self.panel.simulate_selected_rx()
            self.panel._toggle_auto_sim(True)
            self.panel._on_sim_timer()
            update.assert_not_called()
        self.assertFalse(self.panel.btn_sim_auto.isChecked())

    def configure(self):
        p = dict(packet_id='p', bus=1, id=0x123, data=[0]*8, length=8, cycle=10, is_fd=False, is_brs=False)
        steps = [dict(kind='CMD', packet=p)]
        self.panel._load_panel_data(dict(tx_packets=[p], init_steps=steps, widgets=[
            dict(id='button', behavior='tx', widget_type='button', binding=dict(packet_id='p', bus=1, can_id=0x123)),
            dict(id='sequence', behavior='tx', widget_type='sequence', binding=dict(sequence_steps=steps, sequence_failure_steps=steps))]))
        return copy.deepcopy(self.panel._panel_data())

    def clear(self, scope):
        with patch('src.user_panel_v2.window.QMessageBox.question', return_value=QMessageBox.Yes):
            self.panel.clear_panel(scope)

    def test_packet_clear_detaches_references_and_undo_restores_everything(self):
        before = self.configure()
        self.clear('packets')
        self.assertEqual(len(self.panel.widgets_config), 2)
        self.assertEqual(self.panel.tx_packets, [])
        self.assertEqual(self.panel.init_steps, [])
        self.assertNotIn('packet_id', self.panel.widgets_config[0]['binding'])
        self.assertEqual(self.panel.widgets_config[1]['binding']['sequence_steps'], [])
        self.assertEqual(self.panel.widgets_config[1]['binding']['sequence_failure_steps'], [])
        self.panel.undo_edit()
        for key in ('widgets', 'tx_packets', 'init_steps'):
            self.assertEqual(self.panel._panel_data()[key], before[key])
        self.panel.redo_edit()
        self.assertEqual(self.panel.tx_packets, [])

    def test_tools_clear_preserves_packets_init_and_all_clear_preserves_dbc(self):
        self.add_db(b'database')
        before = self.configure()
        self.clear('tools')
        self.assertEqual(self.panel.widgets_config, [])
        self.assertEqual(self.panel.tx_packets, before['tx_packets'])
        self.assertEqual(self.panel.init_steps, before['init_steps'])
        self.clear('all')
        self.assertEqual(self.panel.tx_packets, [])
        self.assertEqual(self.panel.init_steps, [])
        self.assertEqual(self.main.list_db_files[1].count(), 1)
        self.panel.undo_edit()
        self.assertEqual(self.panel.tx_packets, before['tx_packets'])

    def test_clear_denied_outside_edit_and_cancel_keeps_data(self):
        before = self.configure()
        with patch('src.user_panel_v2.window.QMessageBox.question', return_value=QMessageBox.No):
            self.panel.clear_panel('all')
        self.assertEqual(self.panel._panel_data(), before)
        self.panel.set_mode('standby')
        with patch('src.user_panel_v2.window.QMessageBox.question') as ask:
            self.panel.clear_panel('all')
            ask.assert_not_called()

    def test_same_contents_different_filename_reuses_without_prompt(self):
        item = self.add_db(b'same')
        self.panel.dbc_signature = database_signature(self.main)
        self.panel.close()
        item.setText('renamed.dbc')
        with patch.object(self.main, 'choose_panel_history') as choose:
            self.main.open_user_panel()
            choose.assert_not_called()
        self.assertIs(self.main.user_panel_window, self.panel)
        self.assertEqual(self.panel.mode, 'standby')

    def test_changed_database_cancel_then_reuse_preserves_tools(self):
        self.configure()
        old = copy.deepcopy(self.panel.dbc_signature)
        self.add_db(b'different')
        self.panel.refresh_dbc_bindings()
        self.assertEqual(self.panel.dbc_signature, old)
        self.panel.close()
        with patch.object(self.main, 'choose_panel_history', return_value='cancel'):
            self.main.open_user_panel()
        self.assertFalse(self.panel.isVisible())
        self.assertEqual(self.panel.dbc_signature, old)
        with patch.object(self.main, 'choose_panel_history', return_value='reuse') as choose:
            self.main.open_user_panel()
            choose.assert_called_once()
        self.assertTrue(self.panel.isVisible())
        self.assertEqual(len(self.panel.widgets_config), 2)
        self.assertEqual(self.panel.dbc_signature, database_signature(self.main))

    def test_new_panel_archives_previous_data_and_keeps_main_databases(self):
        self.configure()
        self.add_db(b'new')
        with patch.object(self.main, 'choose_panel_history', return_value='new'):
            self.main.open_user_panel()
        self.assertEqual(self.panel.widgets_config, [])
        self.assertEqual(self.panel.tx_packets, [])
        self.assertEqual(self.main.list_db_files[1].count(), 1)
        backups = list((Path(self.temp.name) / 'panel_history').glob('*.upp.json'))
        self.assertEqual(len(backups), 1)
        import json
        self.assertEqual(len(json.loads(backups[0].read_text(encoding='utf-8'))['widgets']), 2)

    def test_signature_tracks_bus_mapping_and_persists_in_panel_data(self):
        item = self.add_db(b'content')
        self.panel.dbc_signature = database_signature(self.main)
        original = self.panel._panel_data()['dbc_signature']
        self.main.list_db_files[1].takeItem(0)
        self.main.list_db_files[2].addItem(item)
        self.assertNotEqual(original, database_signature(self.main))
        self.panel._load_panel_data(dict(widgets=[], dbc_signature=original))
        self.assertEqual(self.panel.dbc_signature, original)

    def test_absent_device_reports_root_cause_without_inventing_fd_capability(self):
        cfg = dict(device=dict(bustype='pcan', handle=999), channel='Missing', bitrate='500 kBit/s',
                   fd_iso='ISO', data_bitrate='2 MBit/s', is_open=True)
        self.main.session.errors = []
        self.main.session.apply_can(1, cfg)
        self.assertEqual(len(self.main.session.errors), 1)
        self.assertIn('저장된 장치를 찾을 수 없습니다', self.main.session.errors[0])
        self.assertNotIn('FD를 지원하지', self.main.session.errors[0])
        self.assertNotIn('지원하지 않는 설정', self.main.session.errors[0])
        self.assertIn(1, self.main.session.pending_can)

    def test_startup_missing_saved_device_stays_closed_without_alert(self):
        from src.session_storage import write_session

        state = self.main.session.capture()
        state['can']['1'].update(device=dict(bustype='pcan', handle=999),
                                 channel='Missing', is_open=True)
        path = Path(self.temp.name) / 'missing-device.pjjsettings'
        write_session(path, state)
        self.main.session.autosave_path = Path(self.temp.name) / 'auto.pjjsettings'
        self.main.search_can_channels = Mock()
        self.main.combo_channels[1].clear()
        self.main.combo_channels[1].addItem('No device', None)
        self.main.session.report = Mock()

        with patch.object(self.main, 'on_channel_changed'):
            self.assertTrue(self.main.session.load(path, startup=True))

        self.assertIsNone(self.main.buses[1])
        self.assertIn(1, self.main.session.pending_can)
        self.main.session.report.assert_not_called()

        with patch.object(self.main, 'on_channel_changed'):
            self.assertTrue(self.main.session.load(path))
        self.main.session.report.assert_called_once()
        self.assertIn('저장된 장치를 찾을 수 없습니다', self.main.session.report.call_args.args[2][0])
