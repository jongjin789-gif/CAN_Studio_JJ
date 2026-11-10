import os
import unittest
from types import SimpleNamespace

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PyQt5.QtWidgets import QApplication, QWidget, QTreeWidget
from cantools.database.can import Message, Signal
from cantools.database.conversion import BaseConversion
from src.tx_counter import counter_payload
from src.tx_panel import TxPacketDialog, TxPacketItem


class CounterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def message(self, length=8, endian='little_endian', signed=False):
        return Message(0x123, 'Test', length, [Signal(
            'Value', 7 if endian == 'big_endian' else 0, 8,
            byte_order=endian, is_signed=signed,
            conversion=BaseConversion.factory(scale=2, offset=10))])

    def test_sequences_preserve_padding_and_use_raw(self):
        for endian in ('little_endian', 'big_endian'):
            msg = self.message(endian=endian, signed=True)
            for mode, expected in [('up', [-2, 0, 2, -2, 0]),
                                   ('down', [-2, 2, 0, -2, 2]),
                                   ('alternate', [-2, 0, 2, 0, -2])]:
                states = {}
                cfg = {'Value': dict(mode=mode, min=-2, max=2, step=2)}
                values = []
                for _ in expected:
                    payload, states = counter_payload(msg, bytes([254] + [0xAA] * 7), cfg, states)
                    values.append(msg.decode(payload, scaling=False)['Value'])
                    self.assertEqual(payload[1:], bytes([0xAA] * 7))
                self.assertEqual(values, expected)

    def test_rx_synchronized_counter_copies_changes_then_counts_up(self):
        message = Message(0x123, 'Test', 1, [Signal('Alive', 0, 8)])
        config = {'Alive': dict(mode='rx_up', min=0, max=255, step=1)}
        states = {}
        values = []
        for received in (7, 7, 12, 12):
            payload, states = counter_payload(
                message, bytes([0]), config, states, received_payload=bytes([received]))
            values.append(message.decode(payload, scaling=False)['Alive'])
        self.assertEqual(values, [7, 8, 12, 13])

    def test_rx_synchronized_counter_supports_down_and_alternate_fallback(self):
        message = Message(0x123, 'Test', 1, [Signal('Alive', 0, 8)])
        for mode, expected in (('rx_down', [7, 6, 5, 9]),
                               ('rx_alternate', [7, 8, 9, 8])):
            states = {}
            values = []
            config = {'Alive': dict(mode=mode, min=5, max=9, step=1)}
            for received in (7, 7, 7, 7):
                payload, states = counter_payload(
                    message, bytes([0]), config, states, received_payload=bytes([received]))
                values.append(message.decode(payload, scaling=False)['Alive'])
            self.assertEqual(values, expected)

    def test_fd_initialization_length_and_edit_roundtrip(self):
        parent = QWidget()
        parent.main_window = SimpleNamespace(bus_capabilities={1: {'is_fd': True}})
        for length, wire_length in [(8, 8), (9, 12), (32, 32), (64, 64)]:
            msg = self.message(length)
            dialog = TxPacketDialog({1: {msg.frame_id: msg}}, parent)
            self.assertEqual(dialog.combo_type.currentText(), 'FD')
            self.assertTrue(dialog.check_brs.isEnabled())
            dialog.combo_symbol.setCurrentIndex(1)
            self.assertEqual(dialog.combo_length.currentText(), str(wire_length))
            self.assertEqual(len(dialog.get_packet_data()['data']), wire_length)
            counter_mode = dialog.table_signals.cellWidget(0, 4)
            counter_mode.setCurrentIndex(counter_mode.findData('rx_up'))
            self.assertEqual(counter_mode.currentData(), 'rx_up')
            self.assertFalse(dialog.table_signals.isColumnHidden(5))
            data = dialog.get_packet_data()
            restored = TxPacketDialog(dialog.db_messages, parent)
            restored.set_packet_data(data)
            self.assertEqual(restored.get_packet_data(), data)
            dialog.close()
            restored.close()
        parent.close()

    def test_failed_send_does_not_advance(self):
        msg = self.message()
        dialog = TxPacketDialog({1: {msg.frame_id: msg}})
        dialog.combo_symbol.setCurrentIndex(1)
        dialog.table_signals.cellWidget(0, 4).setCurrentIndex(1)
        data = dialog.get_packet_data()
        sent = []
        bus = SimpleNamespace(send=sent.append)
        panel = SimpleNamespace(buses={1: bus}, db_messages=dialog.db_messages,
                                main_window=SimpleNamespace(bus_capabilities={1: {'is_fd': True}}))
        tree = QTreeWidget()
        tree.setColumnCount(12)
        item = TxPacketItem(tree, data, panel)
        item.send_packet()
        self.assertEqual(sent[-1].data[0], 0)
        def fail(_):
            raise RuntimeError('test failure')
        bus.send = fail
        item.send_packet()
        bus.send = sent.append
        item.send_packet()
        self.assertEqual(sent[-1].data[0], 1)
        self.assertEqual(item.text(9), '2')
        dialog.close()


if __name__ == '__main__':
    unittest.main()
