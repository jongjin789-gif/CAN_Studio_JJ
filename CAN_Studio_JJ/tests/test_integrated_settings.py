import copy
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PyQt5.QtCore import QRect, Qt
from PyQt5.QtWidgets import QApplication
from cantools.database.can import Database, Message, Signal
from src.main_window import UniversalCANMonitor
from src.session_manager import SessionManager
from src.session_storage import read_session, write_session, compile_formula
from src.graph_realtime import SignalGraphWindow
from src.combined_graph_view import CombinedGraphView
from src.user_panel import UserPanelWindow


def packet():
    return dict(bus=1, id=0x123, is_fd=False, is_brs=False, length=1, data=[7],
                cycle=100, note='saved packet', symbol='M', crc_type='N/A', count=42)


class IntegratedSettingsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        with patch.object(UniversalCANMonitor, 'search_can_channels'), patch.object(SessionManager, 'start'):
            self.main = UniversalCANMonitor()
        self.session = self.main.session
        self.main.search_can_channels = Mock()
        self.session.directory = Path(self.temp.name)
        self.session.autosave_path = Path(self.temp.name) / 'auto.pjjsettings'
        self.session.report = Mock()
        self.addCleanup(self.cleanup_window)
        self.file = Path(self.temp.name) / 'test.pjjsettings'

    def cleanup_window(self):
        self.session.ready = False
        self.main.close()
        self.main.deleteLater()

    def db(self, bus=1, sym=False):
        if sym:
            raw = b'FormatVersion=6.0\n{SEND}\n[Test]\nID=123h\nType=Standard\nLen=1\nVar=Value unsigned 0,8\n'
            name = 'same.sym'
        else:
            db = Database(messages=[Message(frame_id=i, name=f'M{i}', length=1,
                       signals=[Signal('Value', 0, 8)]) for i in (0x123, 0x124)])
            raw = db.as_dbc_string().encode('cp1252')
            name = 'same.dbc'
        path = Path(self.temp.name) / name
        path.write_bytes(raw)
        self.main.load_db_from_path(str(path), bus)
        self.assertEqual(self.main.list_db_files[bus].count(), 1)
        path.unlink()
        return raw

    def graph(self):
        keys, bindings = [], {}
        for can_id in (0x123, 0x124):
            key, binding = self.session.bind_signal(1, can_id, 'Value')
            keys.append(key)
            bindings[key] = binding
        graph = SignalGraphWindow(keys, self.main)
        graph.signal_bindings = bindings
        graph._bound_signals = self.main.resolve_graph_bindings(bindings)
        graph._current_title = 'Two messages'
        graph.display_names[keys[0]] = '첫 신호'
        formulas = {f'Y{i}': dict(enabled=i == 1, name='Sum', unit='V', expr='X1 + X2' if i == 1 else '',
                                 compiled=compile_formula('X1 + X2', 2) if i == 1 else None)
                    for i in (1, 2, 3)}
        graph.apply_formulas(formulas)
        graph.curves[keys[1]].setVisible(False)
        self.main.active_graphs.append(graph)
        graph.show()
        return graph

    def test_unregistered_panel_tools_restore_without_enabling_transmission(self):
        self.main.open_user_panel()
        panel = self.main.user_panel_window
        panel._load_panel_data(dict(widgets=[dict(id='legacy', title='Unfinished TX',
            behavior='tx', widget_type='toggle', binding=dict(bus=2, can_id=0x2b))]))
        saved = self.session.capture()
        write_session(self.file, saved)
        self.assertTrue(self.session.load(self.file))
        self.session.report.assert_not_called()
        restored = self.main.user_panel_window
        self.assertEqual(restored.mode, 'standby')
        self.assertEqual(restored.widgets_config[0]['id'], 'legacy')
        self.assertEqual(restored.tx_packets, [])
        self.assertIn('송신 준비 미완료', restored.system_log.text.toPlainText())
        restored.set_mode('run')
        self.assertEqual(restored.mode, 'standby')
        self.assertFalse(restored._frame_timers)

    def test_invalid_registered_packet_still_rejects_unfinished_panel(self):
        self.main.open_user_panel()
        panel = self.main.user_panel_window
        panel._load_panel_data(dict(widgets=[dict(id='legacy', behavior='tx', widget_type='toggle',
                                                binding=dict(bus=2, can_id=0x2b))]))
        saved = self.session.capture()
        bad = dict(packet(), packet_id='broken', length=12, is_fd=False)
        saved['panel']['data']['tx_packets'] = [bad]
        with self.assertRaises(ValueError):
            self.session.prepare(saved)
        self.assertIs(self.main.user_panel_window, panel)

    def test_oversized_panel_and_graph_restore_with_title_bars_on_screen(self):
        self.db()
        self.graph()
        self.main.open_user_panel()
        saved = self.session.capture()
        area = QRect(0, 0, 1920, 1032)
        screen = Mock()
        screen.availableGeometry.return_value = area
        oversized = [area.left(), area.top(), area.width() + 1000, area.height() + 1000]
        saved['panel']['geometry'] = oversized
        saved['graphs'][0]['geometry'] = oversized
        write_session(self.file, saved)
        with patch('src.session_graph.QApplication.screens', return_value=[screen]), \
             patch('src.session_graph.QApplication.primaryScreen', return_value=screen):
            self.assertTrue(self.session.load(self.file))
        self.app.processEvents()
        self.session.report.assert_not_called()
        for window in [self.main.user_panel_window, *self.main.active_graphs]:
            self.assertTrue(window.isVisible())
            self.assertTrue(area.contains(window.frameGeometry()), window.frameGeometry().getRect())

    def test_rx_label_color_rules_survive_integrated_restore(self):
        self.db()
        self.main.open_user_panel()
        rule = dict(enabled=True, source='value', op='between', a=5, b=10,
                    target='background', color='#DCFCE7')
        self.main.user_panel_window._load_panel_data(dict(widgets=[dict(
            id='rx', widget_type='label', behavior='rx', binding=dict(bus=1, can_id=0x123,
            signal_name='Value', start_bit=0, bit_length=8, label_color_rules=[rule]))]))
        write_session(self.file, self.session.capture())
        self.assertTrue(self.session.load(self.file))
        self.session.report.assert_not_called()
        panel = self.main.user_panel_window
        self.assertEqual(panel._cfg_by_id('rx')['binding']['label_color_rules'], [rule])
        panel.on_message_update(1, 0x123, b'\x07', 0)
        self.assertIn('#dcfce7', panel.widget_controls['rx'].styleSheet())
        panel.on_message_update(1, 0x123, b'\x04', 0)
        self.assertEqual(panel.widget_controls['rx'].styleSheet(), '')

    def test_roundtrip_embedded_db_graph_panel_and_stopped_tx(self):
        raw = self.db()
        sym = self.db(2, sym=True)
        graph = self.graph()
        graph.chk_sync.setChecked(True)
        combined = CombinedGraphView([graph], self.main)
        self.main.combined_view_window = combined
        combined.show()
        self.main.tx_panel.add_packet_to_tree(packet())
        self.main.open_user_panel()
        panel = self.main.user_panel_window
        panel._load_panel_data(dict(pages=[dict(id='one', name='A'), dict(id='two', name='B')],
                                   active_page_id='two', widgets=[dict(id='shape', title='Box',
                                   widget_type='shape_rect', behavior='none', page_id='two', binding={})],
                                   tx_packets=[dict(packet(), packet_id='p')],
                                   init_steps=[dict(kind='START', target_packet_id='p')]))
        state = self.session.capture()
        write_session(self.file, state)
        self.assertTrue(graph.chk_sync.isChecked())
        self.assertTrue(graph.is_in_combined_view)
        self.assertTrue(self.session.load(self.file))
        self.session.report.assert_not_called()
        self.assertEqual(bytes(self.main.list_db_files[1].item(0).data(Qt.UserRole + 2)), raw)
        self.assertEqual(bytes(self.main.list_db_files[2].item(0).data(Qt.UserRole + 2)), sym)
        restored = self.main.active_graphs[0]
        self.assertTrue(restored.isVisible())
        self.assertFalse(restored.chk_sync.isChecked())
        self.assertIsNone(self.main.combined_view_window)
        self.assertEqual(restored._current_title, 'Two messages')
        self.assertEqual(restored.formulas['Y1']['expr'], 'X1 + X2')
        self.assertFalse(restored.curves[restored.signal_names[1]].isVisible())
        self.assertTrue(all(not values for values in restored.values.values()))
        self.assertEqual(self.main.tree.topLevelItemCount(), 0)
        tx = self.main.tx_panel.tree.topLevelItem(0)
        self.assertFalse(tx.is_running)
        self.assertEqual(tx.packet_data['count'], 0)
        self.assertEqual(tx.packet_data['note'], 'saved packet')
        panel = self.main.user_panel_window
        self.assertEqual(panel.active_page_id, 'two')
        self.assertEqual(len(panel.pages), 2)
        self.assertEqual(panel.init_steps[0]['target_packet_id'], 'p')
        self.assertEqual(panel.mode, 'standby')
        self.assertFalse(panel._frame_timers)
        self.assertEqual(read_session(self.session.autosave_path)['panel']['data']['pages'], state['panel']['data']['pages'])

    def test_bad_db_and_formula_preserve_live_windows_one_report(self):
        self.db()
        graph = self.graph()
        state = self.session.capture()
        state['graphs'][0]['formulas']['Y1']['expr'] = '__import__("os")'
        state['databases']['1'][0]['content'] = 'YnJva2Vu'
        write_session(self.file, state)
        self.assertFalse(self.session.load(self.file))
        self.assertIs(self.main.active_graphs[0], graph)
        self.assertTrue(graph.isVisible())
        self.assertEqual(self.main.list_db_files[1].count(), 1)
        self.session.report.assert_called_once()

    def test_frame_identity_keeps_same_signal_names_separate(self):
        self.db()
        graph = self.graph()
        self.main.route_raw_msg_to_record(1, 0x123, b'\x03', False, False, False, True, 1)
        self.main.route_raw_msg_to_record(1, 0x124, b'\x07', False, False, False, True, 1)
        self.main.update_bound_graphs()
        self.assertEqual(list(graph.values[graph.signal_names[0]]), [3])
        self.assertEqual(list(graph.values[graph.signal_names[1]]), [7])
        # A frame with the same numeric ID but the other frame format is ignored.
        self.main.route_raw_msg_to_record(2, 0x123, b'\x63', True, False, False, True, 1)
        self.main.update_bound_graphs()
        self.assertEqual(list(graph.values[graph.signal_names[0]]), [3])

    def test_failed_channels_aggregate_and_keep_graph(self):
        self.db()
        self.graph()
        m = self.main
        for b in (1, 2):
            m.combo_channels[b].addItem(f'device{b}', dict(bustype='virtual', handle=f'd{b}', is_fd=False))
        state = self.session.capture()
        state['can']['1'].update(is_open=True, data_bitrate='2 MBit/s')
        state['can']['2'].update(is_open=True)
        state['can']['3'].update(is_open=True, device=dict(bustype='pcan', handle=999))
        write_session(self.file, state)
        def fake_open(bus):
            self.assertEqual(bus, 2)
            m.buses[bus] = Mock()
        with patch.object(m, 'open_can', side_effect=fake_open) as opened:
            self.assertTrue(self.session.load(self.file))
        opened.assert_called_once_with(2)
        self.session.report.assert_called_once()
        self.assertIsNone(m.buses[1])
        self.assertIsNotNone(m.buses[2])
        self.assertIsNone(m.buses[3])
        self.assertTrue(m.active_graphs[0].isVisible())
        saved = read_session(self.session.autosave_path)
        self.assertEqual(saved['can']['1']['data_bitrate'], '2 MBit/s')
        self.assertFalse(saved['can']['1']['is_open'])

    def test_linux_session_restore_keeps_saved_socketcan_channel_closed(self):
        channel = dict(bustype='socketcan', handle='vcan0', is_fd=True)
        combo = self.main.combo_channels[1]
        combo.addItem('vcan0', channel)
        saved_cfg = dict(device=dict(bustype='socketcan', handle='vcan0'), channel='vcan0',
                         bitrate=self.main.combo_bitrate[1].currentText(),
                         fd_iso=self.main.combo_fd_iso[1].currentText(), data_bitrate='Off',
                         is_open=True)

        platform_patch = patch('src.session_manager.platform.system', return_value='Linux')
        open_can_patch = patch.object(self.main, 'open_can')
        platform_patch.start()
        open_can = open_can_patch.start()
        self.addCleanup(platform_patch.stop)
        self.addCleanup(open_can_patch.stop)
        try:
            self.session.errors = []
            self.session.apply_can(1, saved_cfg)
        finally:
            open_can_patch.stop()
            platform_patch.stop()

        open_can.assert_not_called()
        self.assertIsNone(self.main.buses[1])
        self.assertFalse(self.session.errors)

    def test_empty_replaces_and_closes_log_and_stops_everything(self):
        empty = self.session.capture()
        self.db()
        self.graph()
        self.main.tx_panel.add_packet_to_tree(packet())
        item = self.main.tx_panel.tree.topLevelItem(0)
        item.is_running = True
        item.timer.start(100000)
        viewer = Mock()
        viewer.close.side_effect = lambda: self.main.log_viewers.remove(viewer)
        self.main.log_viewers.append(viewer)
        self.main.open_user_panel()
        old_panel = self.main.user_panel_window
        old_panel.mode = 'run'
        write_session(self.file, empty)
        self.assertTrue(self.session.load(self.file))
        viewer.stop_sending.assert_called()
        viewer.close.assert_called_once()
        self.assertFalse(item.timer.isActive())
        self.assertEqual(old_panel.mode, 'standby')
        self.assertEqual(self.main.active_graphs, [])
        self.assertIsNone(self.main.user_panel_window)
        self.assertEqual(self.main.tx_panel.tree.topLevelItemCount(), 0)
        self.assertFalse(any(self.main.db_messages.values()))

    def test_export_preserves_original_and_does_not_overwrite(self):
        raw = self.db()
        folder = Path(self.temp.name) / 'export'
        folder.mkdir()
        with patch('src.session_manager.QFileDialog.getExistingDirectory', return_value=str(folder)):
            self.session.export_databases()
            self.session.export_databases()
        self.assertEqual((folder / 'BUS1/same.dbc').read_bytes(), raw)
        self.assertEqual((folder / 'BUS1/same_1.dbc').read_bytes(), raw)

    def test_atomic_save_failure_preserves_file(self):
        state = self.session.capture()
        write_session(self.file, state)
        before = self.file.read_bytes()
        with patch('src.session_storage.os.replace', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                write_session(self.file, state)
        self.assertEqual(self.file.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
