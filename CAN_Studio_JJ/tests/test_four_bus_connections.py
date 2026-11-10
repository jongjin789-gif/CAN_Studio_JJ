import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication, QAbstractItemView
from cantools.database.can import Database, Message, Signal
from src.main_window import UniversalCANMonitor
from src.session_manager import SessionManager
from src.session_storage import read_session, write_session, validate_structure
from src.tx_panel import TxPacketDialog
from src.user_panel_v2.connection_dialog import ConnectionDialog
from src.bus_config import normalized_signature
from src.connection_controls import ConnectionGrid, STATE_COLUMN
from src.user_panel_v2.packets import PacketRuntime, validate_packet
from src.user_panel_v2.storage import save_bundle, load_bundle
from src.PCANBasic import PCAN_ERROR_BUSHEAVY, PCAN_ERROR_BUSOFF


def packet(bus=4):
    return dict(packet_id='four', bus=bus, id=0x123, is_fd=False, is_brs=False,
                length=1, data=[7], cycle=100, note='kept', symbol='M',
                crc_type='N/A', count=0)


class FourBusConnectionsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        with patch.object(UniversalCANMonitor, 'search_can_channels'), patch.object(SessionManager, 'start'):
            self.main = UniversalCANMonitor()
        self.main.session.directory = Path(self.temp.name)
        self.main.session.autosave_path = Path(self.temp.name) / 'auto.pjjsettings'
        self.main.session.ready = False
        self.main.session.report = Mock()
        self.main.search_can_channels = Mock()
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.main.session.ready = False
        self.main.close()
        self.main.deleteLater()

    def connect(self, bus=4, handle='v4'):
        self.main.combo_channels[bus].addItem(handle, dict(bustype='virtual', handle=handle, is_fd=False))
        device, receiver = Mock(), Mock()
        receiver.latest_msg_stats = {}
        receiver.latest_data = {}
        receiver.latest_rx_data = {}
        receiver.last_error_details = ''
        with patch('src.main_window.can.interface.Bus', return_value=device), \
                patch('src.main_window.CANReceiverThread', return_value=receiver):
            self.main.toggle_can(bus)
        return device, receiver

    def install_db(self, bus, signals):
        db = Database(messages=[Message(frame_id=0x123, name=f'M{bus}', length=len(signals),
                                        signals=[Signal(name, i * 8, 8) for i, name in enumerate(signals)])])
        raw = db.as_dbc_string().encode('cp1252')
        self.main.install_database(db, bus, f'bus{bus}.dbc', raw, '')
        return raw

    def test_grid_has_fixed_bus_rows_full_labels_font_and_state_space(self):
        self.main.show()
        self.app.processEvents()
        grid = self.main.connection_grid
        self.assertEqual([grid.verticalHeaderItem(i).text() for i in range(4)], ['1', '2', '3', '4'])
        self.assertFalse(grid.isSortingEnabled())
        self.assertFalse(grid.verticalHeader().sectionsMovable())
        self.assertEqual(grid.dragDropMode(), QAbstractItemView.NoDragDrop)
        dialog = TxPacketDialog(self.main.db_messages, self.main.tx_panel)
        self.addCleanup(dialog.close)
        self.assertLess(grid.font().pointSizeF(), dialog.font().pointSizeF())
        self.assertEqual(self.main.combo_bitrate[4].currentText(), '500 kBit/s')
        self.assertGreaterEqual(grid.columnWidth(STATE_COLUMN), 200)
        self.assertEqual(set(self.main.list_db_files), {1, 2, 3, 4})

    def test_toggle_connection_and_panel_share_live_state(self):
        self.main.open_user_panel()
        panel = self.main.user_panel_window
        dialog = ConnectionDialog(panel)
        self.addCleanup(dialog.close)
        device, receiver = self.connect()
        dialog.refresh()
        self.assertEqual(self.main.btn_connect[4].text(), 'Close')
        self.assertEqual(dialog.buttons[4, 'connect'].text(), 'Close')
        self.assertFalse(dialog.fields[4, 'channels'].isEnabled())
        self.assertFalse(dialog.buttons[4, 'refresh'].isEnabled())
        dialog.buttons[4, 'connect'].click()
        self.assertIsNone(self.main.buses[4])
        receiver.stop.assert_called_once()
        device.shutdown.assert_called_once()
        self.assertEqual(self.main.btn_connect[4].text(), 'Open')
        self.assertEqual(panel.mode, 'standby')

    def test_failed_open_does_not_flip_button_and_details_keep_error(self):
        self.main.combo_channels[4].addItem('bad', dict(bustype='virtual', handle='bad', is_fd=False))
        with patch('src.main_window.can.interface.Bus', side_effect=RuntimeError('device lost')), \
                patch.object(self.main, 'notify_connection_error'):
            self.main.toggle_can(4)
        self.assertIsNone(self.main.buses[4])
        self.assertEqual(self.main.btn_connect[4].text(), 'Open')
        self.assertIn('device lost', self.main.bus_summary(4)[0])
        self.main.log_bus_event(4, 'DBC loaded')
        self.assertIn('device lost', self.main.bus_summary(4)[0])
        self.main.show_bus_log(4)
        view = self.main._bus_log_windows[4]
        self.addCleanup(view.close)
        self.assertIn('RuntimeError', view.text.toPlainText())
        self.assertIn('DBC loaded', view.text.toPlainText())

    def test_duplicate_device_is_rejected_without_affecting_connected_bus(self):
        device, _ = self.connect(1, 'same')
        self.main.combo_channels[4].addItem('same', dict(bustype='virtual', handle='same', is_fd=False))
        with patch('src.main_window.can.interface.Bus') as factory:
            self.main.toggle_can(4)
        factory.assert_not_called()
        self.assertIs(self.main.buses[1], device)
        self.assertIsNone(self.main.buses[4])
        self.assertIn('BUS 1', self.main.bus_summary(4)[0])

    def test_close_stops_tx_sequence_and_replay_before_device_shutdown(self):
        device, _ = self.connect()
        self.main.tx_panel.add_packet_to_tree(packet())
        tx = self.main.tx_panel.tree.topLevelItem(0)
        tx.start_timer()
        self.main.open_user_panel()
        control = self.main.user_panel_window.init_control
        control.required_buses = {4}
        replay = Mock()
        self.main.log_viewers = [replay]
        with patch.object(control, 'stop') as stopped:
            device.shutdown.side_effect = lambda: self.assertFalse(tx.is_running)
            self.main.toggle_can(4)
            stopped.assert_called_once()
        replay.stop_sending.assert_called()
        self.assertFalse(tx.timer.isActive())
        self.connect()
        self.assertFalse(tx.is_running)

    def test_refresh_skips_connected_bus_and_selects_first_for_empty_bus(self):
        device, _ = self.connect(1)
        source = self.main.combo_channels[1]
        original = [(source.itemText(i), source.itemData(i)) for i in range(source.count())]
        self.main.search_can_channels = UniversalCANMonitor.search_can_channels.__get__(self.main)
        def discover(bus=None):
            for b in (2, 3, 4):
                self.main.combo_channels[b].clear()
                self.main.combo_channels[b].addItem('new', dict(bustype='virtual', handle='new', is_fd=False))
        with patch('src.main_window.platform.system', return_value='Windows'), \
                patch.object(self.main, 'search_windows_can_channels', side_effect=discover):
            self.main.search_can_channels(1)
            self.assertEqual([(source.itemText(i), source.itemData(i)) for i in range(source.count())], original)
            self.main.search_can_channels()
        self.assertIs(self.main.buses[1], device)
        self.assertEqual(self.main.combo_channels[4].currentIndex(), 0)

    def test_refresh_preserves_selected_device_and_does_not_replace_missing_device(self):
        self.main.search_can_channels = UniversalCANMonitor.search_can_channels.__get__(self.main)
        combo = self.main.combo_channels[4]
        first = dict(bustype='virtual', handle='first', is_fd=False)
        selected = dict(bustype='virtual', handle='selected', is_fd=False)
        combo.addItem('selected', selected)
        combo.setCurrentIndex(combo.count() - 1)

        def discover(bus):
            combo.clear()
            combo.addItem('first', first)
            combo.addItem('selected', selected)

        with patch('src.main_window.platform.system', return_value='Windows'), \
                patch.object(self.main, 'search_windows_can_channels', side_effect=discover):
            self.main.search_can_channels(4)
        self.assertEqual(combo.currentData(), selected)

        def discover_missing(bus):
            combo.clear()
            combo.addItem('first', first)

        with patch('src.main_window.platform.system', return_value='Windows'), \
                patch.object(self.main, 'search_windows_can_channels', side_effect=discover_missing):
            self.main.search_can_channels(4)
        self.assertEqual(combo.currentIndex(), -1)

    def test_bus_four_dbc_signal_children_and_packet_roundtrip(self):
        self.install_db(1, ['One'])
        raw = self.install_db(4, ['A', 'B'])
        self.main.add_message_to_tree(1, 0x123)
        self.main.add_message_to_tree(4, 0x123)
        self.assertEqual(self.main.msg_tree_items[1, 0x123].childCount(), 1)
        self.assertEqual(self.main.msg_tree_items[4, 0x123].childCount(), 2)
        p = packet()
        p.update(length=2, data=[5, 9])
        validate_packet(p, self.main.db_messages)
        self.main.tx_panel.add_packet_to_tree(p)
        self.assertEqual(self.main.tx_panel.tree.topLevelItem(0).childCount(), 2)
        self.main.open_user_panel()
        panel = self.main.user_panel_window
        panel._load_panel_data(dict(tx_packets=[p], widgets=[]))
        path = Path(self.temp.name) / 'four.pjjsettings'
        write_session(path, self.main.session.capture())
        self.assertTrue(self.main.session.load(path))
        self.assertEqual(self.main.user_panel_window.tx_packets[0]['bus'], 4)
        self.assertEqual(bytes(self.main.list_db_files[4].item(0).data(Qt.UserRole + 2)), raw)
        self.assertEqual(self.main.tx_panel.tree.topLevelItem(0).packet_data['data'], [5, 9])

    def test_packet_runtime_can_send_on_fourth_bus(self):
        device, _ = self.connect()
        runtime = PacketRuntime(packet(), self.main.db_messages, self.main)
        runtime.send()
        device.send.assert_called_once()
        self.assertEqual(bytes(device.send.call_args.args[0].data), b'\x07')

    def test_legacy_session_keeps_panel_identity_and_backs_up_original(self):
        self.install_db(1, ['One'])
        self.main.open_user_panel()
        self.main.user_panel_window._load_panel_data(dict(widgets=[dict(id='old', widget_type='shape_rect', behavior='none', binding={})]))
        data = copy.deepcopy(self.main.session.capture())
        data['version'] = 1
        del data['can']['4'], data['databases']['4'], data['ui']
        del data['panel']['data']['dbc_signature']['4']
        raw = json.dumps(data).encode('utf-8')
        path = Path(self.temp.name) / 'legacy.pjjsettings'
        path.write_bytes(raw)
        with patch.object(self.main, 'choose_panel_history') as history:
            self.assertTrue(self.main.session.load(path))
            history.assert_not_called()
        self.assertEqual(Path(str(path) + '.v1.bak').read_bytes(), raw)
        self.assertEqual(self.main.user_panel_window.widgets_config[0]['id'], 'old')
        saved = read_session(self.main.session.autosave_path)
        self.assertEqual(saved['version'], 2)
        self.assertIsNone(saved['can']['4']['device'])
        self.assertFalse(saved['can']['4']['is_open'])
        self.assertEqual(saved['databases']['4'], [])

    def test_dbc_collapsed_state_roundtrip_preserves_hidden_databases(self):
        raw = self.install_db(4, ['A'])
        self.main.db_group.set_collapsed(True)
        self.main.conn_group.header.click()
        self.assertTrue(self.main.conn_group.is_collapsed())
        self.assertTrue(self.main.conn_group.content.isHidden())
        self.assertFalse(self.main.conn_group.header.icon().isNull())
        self.assertFalse(self.main.db_group.header.icon().isNull())
        path = Path(self.temp.name) / 'collapsed.pjjsettings'
        write_session(path, self.main.session.capture())
        self.main.db_group.set_collapsed(False)
        self.main.conn_group.set_collapsed(False)
        self.assertTrue(self.main.session.load(path))
        self.assertTrue(self.main.db_group.is_collapsed())
        self.assertTrue(self.main.conn_group.is_collapsed())
        self.assertEqual(bytes(self.main.list_db_files[4].item(0).data(Qt.UserRole + 2)), raw)
        self.main.open_user_panel()
        dialog = ConnectionDialog(self.main.user_panel_window)
        self.addCleanup(dialog.close)
        self.assertTrue(dialog.db_group.is_collapsed())
        dialog.db_group.set_collapsed(False)
        self.assertFalse(self.main.db_group.is_collapsed())

    def test_fourth_bus_package_and_legacy_empty_signature(self):
        raw = self.install_db(4, ['A'])
        db_path = Path(self.temp.name) / 'four.dbc'
        db_path.write_bytes(raw)
        bundle = save_bundle(str(Path(self.temp.name) / 'four.pjjupkg'), {'widgets': []}, {4: [str(db_path)]})
        _, paths = load_bundle(bundle)
        self.assertEqual(Path(paths[4][0]).read_bytes(), raw)
        self.assertEqual(normalized_signature({'1': ['token'], '2': [], '3': []}),
                         normalized_signature({'1': ['token'], '2': [], '3': [], '4': []}))

    def test_unknown_bus_and_invalid_fold_setting_are_rejected(self):
        data = self.main.session.capture()
        data['can']['5'] = data['can'].pop('4')
        with self.assertRaises(ValueError):
            validate_structure(data)

    def test_narrow_grid_compacts_only_display_without_changing_saved_speed(self):
        grid = ConnectionGrid(self.main, mirror=True)
        self.addCleanup(grid.close)
        grid.resize(940, grid.height())
        grid.show()
        self.app.processEvents()
        field = grid.fields[4, 'bitrate']
        self.assertTrue(field.compact)
        self.assertEqual(field.currentText(), '500 kBit/s')
        self.assertEqual(self.main.session.capture()['can']['4']['bitrate'], '500 kBit/s')
        grid.resize(1600, grid.height())
        self.app.processEvents()
        self.assertFalse(field.compact)
        self.assertGreaterEqual(grid.columnWidth(STATE_COLUMN), 200)

    def test_pcan_warning_recovery_and_bus_off_are_reported_per_bus(self):
        other, _ = self.connect(1)
        self.main.combo_channels[4].addItem('pcan', dict(bustype='pcan', handle=0x54, is_fd=True))
        device = Mock()
        self.main.buses[4] = device
        device.status.return_value = PCAN_ERROR_BUSHEAVY
        self.main.poll_bus_status()
        self.assertEqual(self.main.bus_summary(4)[1], 'WARN')
        self.assertIs(self.main.buses[4], device)
        device.status.return_value = 0
        self.main.poll_bus_status()
        self.assertEqual(self.main.bus_summary(4)[1], 'INFO')
        device.status.return_value = PCAN_ERROR_BUSOFF
        self.main.poll_bus_status()
        self.assertIsNone(self.main.buses[4])
        self.assertIn('Bus-Off', self.main.bus_summary(4)[0])
        self.assertIs(self.main.buses[1], other)

    def test_fatal_receive_error_keeps_diagnosis_after_close(self):
        _, receiver = self.connect()
        receiver.last_error_details = 'original worker traceback'
        with patch('src.main_window.show_error'):
            self.main.handle_rx_error('Rx Error: lost', 4)
        self.assertIsNone(self.main.buses[4])
        self.assertIn('lost', self.main.bus_summary(4)[0])
        self.assertTrue(any('original worker traceback' in entry['details'] for entry in self.main.bus_events[4]))

    def test_menu_open_and_close_call_explicit_operations(self):
        can_menu = next(item.menu() for item in self.main.menuBar().actions() if item.text() == 'CAN')
        bus_menu = next(item.menu() for item in can_menu.actions() if item.text() == 'BUS 4')
        opened = next(item for item in bus_menu.actions() if item.text() == '연결')
        closed = next(item for item in bus_menu.actions() if item.text() == '연결 해제')
        self.main.combo_channels[4].addItem('virtual', dict(bustype='virtual', handle='v4', is_fd=False))
        bus_menu.aboutToShow.emit()
        self.assertTrue(opened.isEnabled())
        self.assertFalse(closed.isEnabled())
        with patch.object(self.main, 'open_can') as open_call:
            opened.trigger()
            open_call.assert_called_once_with(4)
        self.connect()
        bus_menu.aboutToShow.emit()
        self.assertFalse(opened.isEnabled())
        self.assertTrue(closed.isEnabled())
        closed.trigger()
        self.assertIsNone(self.main.buses[4])
        data = self.main.session.capture()
        data['ui']['dbc_collapsed'] = 'false'
        with self.assertRaises(ValueError):
            validate_structure(data)


if __name__ == '__main__':
    unittest.main()
