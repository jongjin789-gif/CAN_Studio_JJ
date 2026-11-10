import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import can
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication
from cantools.database.can import Message, Signal
from cantools.database.conversion import BaseConversion
from src.can_threads import CANReceiverThread
from src.main_window import UniversalCANMonitor
from src.session_manager import SessionManager
from src.user_panel_v2.window import UserPanelWindow


class RxSignalIdentityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        with patch.object(UniversalCANMonitor, 'search_can_channels'), patch.object(SessionManager, 'start'):
            self.main = UniversalCANMonitor()
        self.main.session.ready = False
        self.addCleanup(self.dispose)
        path = Path(__file__).with_name('duplicate_alive_counter.sym')
        database = self.main._load_sym_v6(str(path))
        self.main.install_database(database, 2, path.name, path.read_bytes(), '')

    def dispose(self):
        self.main.close()
        self.main.deleteLater()

    def receive(self, frames, bus=2):
        messages = iter(can.Message(arbitration_id=can_id, data=payload, timestamp=index + 1,
                                    is_extended_id=False, is_rx=True)
                        for index, (can_id, payload) in enumerate(frames))
        device = Mock()
        worker = CANReceiverThread(device, self.main.db_messages[bus])

        def recv(timeout):
            message = next(messages, None)
            if message is None:
                worker.running = False
            return message

        device.recv.side_effect = recv
        self.main.rx_threads[bus] = worker
        worker.run()
        self.main.update_ui_data()
        return worker

    def signal_item(self, can_id, name='B00_Alive_Counter', bus=2):
        message = self.main.msg_tree_items[(bus, can_id)]
        return next(message.child(row) for row in range(message.childCount())
                    if message.child(row).text(3).strip() == name)

    def test_duplicate_b00_counters_update_in_both_receive_orders(self):
        # The screenshot's 0x510 payload has B00=0xB (11), and both angles=-0.62.
        frames = [(0x510, bytes.fromhex('5B 52 35 C2 FF 00 C2 FF')),
                  (0x500, bytes.fromhex('07 00 00 00 00 00 00 00'))]
        for ordered in (frames, list(reversed(frames))):
            with self.subTest(order=[can_id for can_id, _ in ordered]):
                self.main.reset_tree_values(2)
                worker = self.receive(ordered)
                self.assertEqual(worker.latest_data[(0x510, 'B00_Alive_Counter')][0], 11)
                self.assertEqual(worker.latest_data[(0x500, 'B00_Alive_Counter')][0], 7)
                self.assertEqual(self.signal_item(0x510).text(5), '11')
                self.assertEqual(self.signal_item(0x500).text(5), '7')
                self.assertEqual(self.signal_item(0x510, 'B24_ECM_L_Angle').text(5), '-0.620')

    def test_uncheck_all_clears_both_duplicate_signals_and_stays_clear_on_rx(self):
        frames = [(0x510, bytes([11]) + bytes(7)), (0x500, bytes([7]) + bytes(7))]
        self.receive(frames)
        for can_id, _ in frames:
            self.signal_item(can_id).setCheckState(3, Qt.Checked)
        self.main.btn_uncheck_all.click()
        self.main.update_ui_data()
        for can_id, _ in frames:
            message = self.main.msg_tree_items[(2, can_id)]
            self.assertEqual(message.checkState(3), Qt.Unchecked)
            self.assertTrue(all(message.child(row).checkState(3) == Qt.Unchecked
                                for row in range(message.childCount())))

    def test_local_tx_keeps_the_other_can_id_value_and_reset_clears_exact_keys(self):
        self.receive([(0x510, bytes([11]) + bytes(7)), (0x500, bytes([7]) + bytes(7))])
        self.main.record_tx_activity(2, 0x500, bytes([3]) + bytes(7), False, timestamp=10**12)
        self.main.update_ui_data()
        self.assertEqual(self.signal_item(0x500).text(5), '3')
        self.assertEqual(self.signal_item(0x510).text(5), '11')
        self.main.add_message_to_tree(1, 0x510)
        self.main.reset_tree_values(2)
        self.assertEqual(self.main.rx_threads[2].latest_data, {})
        self.assertFalse(any(key[0] == 2 for key in self.main.signal_tree_items))
        self.assertFalse(any(key[0] == 2 for key in self.main.signal_display_decimals))
        self.assertIn((1, 0x510), self.main.msg_tree_items)

    def test_duplicate_names_use_each_messages_enum_precision_and_unit(self):
        for can_id, label, scale, unit in ((0x600, 'COMMAND', 0.1, 'V'), (0x601, 'STATUS', 0.0625, 'A')):
            self.main.db_messages[2][can_id] = Message(can_id, f'M{can_id:X}', 2, [
                Signal('State', 0, 8, conversion=BaseConversion.factory(choices={1: label})),
                Signal('Reading', 8, 8, conversion=BaseConversion.factory(scale=scale), unit=unit),
            ])
        self.receive([(0x600, bytes([1, 1])), (0x601, bytes([1, 1]))])
        self.assertEqual(self.signal_item(0x600, 'State').text(5), 'COMMAND (1)')
        self.assertEqual(self.signal_item(0x601, 'State').text(5), 'STATUS (1)')
        self.assertEqual(self.signal_item(0x600, 'Reading').text(5), '0.100')
        self.assertEqual(self.signal_item(0x601, 'Reading').text(5), '0.0625')
        self.assertEqual(self.signal_item(0x600, 'Reading').text(6), 'V')
        self.assertEqual(self.signal_item(0x601, 'Reading').text(6), 'A')

    def test_panel_signal_updates_include_the_source_can_id(self):
        panel = Mock()
        panel.isVisible.return_value = True
        self.main.user_panel_window = panel
        self.receive([(0x510, bytes([11]) + bytes(7)), (0x500, bytes([7]) + bytes(7))])
        b00_calls = [call for call in panel.on_signal_update.call_args_list if call.args[1] == 'B00_Alive_Counter']
        self.assertEqual([(call.args[2], call.kwargs['can_id']) for call in b00_calls], [(11, 0x510), (7, 0x500)])
        self.main.user_panel_window = None

    def test_panel_controls_keep_values_from_their_own_can_ids(self):
        panel = UserPanelWindow(SimpleNamespace(), self.main.db_messages)
        self.addCleanup(panel.deleteLater)
        self.addCleanup(panel.close)
        panel._load_panel_data(dict(widgets=[dict(
            id=str(can_id), widget_type='progress', behavior='rx', row=index * 3,
            binding=dict(bus=2, can_id=can_id, signal_name='B00_Alive_Counter',
                         dlc=8, start_bit=0, bit_length=4, scale=1, offset=0,
                         signed=False, min=0, max=15, progress_text_mode='value'),
        ) for index, can_id in enumerate((0x500, 0x510))]))
        panel.set_mode('standby')
        panel.on_signal_update(2, 'B00_Alive_Counter', 7, '', 1, can_id=0x500)
        panel.on_signal_update(2, 'B00_Alive_Counter', 11, '', 2, can_id=0x510)
        self.assertEqual(panel.widget_controls[str(0x500)].text(), '7')
        self.assertEqual(panel.widget_controls[str(0x510)].text(), '11')


if __name__ == '__main__':
    unittest.main()
