import os
import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PyQt5.QtWidgets import QApplication, QDialog
from cantools.database.can import Message, Signal
from cantools.database.conversion import BaseConversion
from src.tx_crc import calculate_crc, validate_crc, parse_extra, crc_order
from src.tx_crc_dialog import SignalCRCDialog
from src.tx_counter import counter_payload
from src.tx_panel import TxPacketDialog, TxPanel, TxPacketItem
from src.crc_utils import calculate_crc16_ccitt_false


def config(mode='crc16', **kwargs):
    return dict(dict(mode=mode, start=2, size=30, extra=[0, 0],
                     poly=0x1021 if mode == 'crc16' else 7,
                     init=0xFFFF if mode == 'crc16' else 0,
                     xorout=0, refin=False, refout=False), **kwargs)


class SignalCRCTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_known_crc_vectors_and_reflection(self):
        data = b'123456789'
        for width, poly, init, xorout, refin, refout, expected in [
            (8, 7, 0, 0, False, False, 0xF4),
            (8, 0x31, 0, 0, True, True, 0xA1),
            (16, 0x1021, 0xFFFF, 0, False, False, 0x29B1),
            (16, 0x8005, 0, 0, True, True, 0xBB3D),
            (16, 0x1021, 0xFFFF, 0xFFFF, True, True, 0x906E),
        ]:
            self.assertEqual(calculate_crc(data, width, poly, init, xorout, refin, refout), expected)

    def test_counter_then_crc_matches_hyundai_calculation(self):
        for endian in ('little_endian', 'big_endian'):
            sig = Signal('CRC', 0 if endian == 'little_endian' else 7, 16,
                         byte_order=endian, is_signed=True,
                         conversion=BaseConversion.factory(scale=2, offset=10))
            msg = Message(0x123, 'Test', 32, [sig, Signal('Count', 16, 8)])
            cfgs = {'CRC': config(), 'Count': dict(mode='up', min=0, max=255, step=1)}
            payload = bytes([0, 0, 5] + list(range(3, 32)))
            states = {}
            for expected_count in (5, 6, 7):
                result, states = counter_payload(msg, payload, cfgs, states)
                self.assertEqual(result[2], expected_count)
                expected = calculate_crc16_ccitt_false(result[2:32] + b'\x00\x00')
                self.assertEqual(result[:2], expected.to_bytes(2, 'little' if endian == 'little_endian' else 'big'))
                self.assertEqual(result[3:], payload[3:])

    def test_crc8_and_non_byte_aligned_self_overlap(self):
        sig = Signal('CRC', 0, 8)
        msg = Message(1, 'Test', 8, [sig])
        cfg = config('crc8', start=1, size=7)
        result, _ = counter_payload(msg, bytes(range(8)), {'CRC': cfg}, {})
        self.assertEqual(result[0], calculate_crc(bytes(range(1, 8)) + b'\0\0', 8, 7, 0, 0))
        for endian, start in [('little_endian', 4), ('big_endian', 3)]:
            sig = Signal('CRC', start, 16, byte_order=endian)
            for byte in (0, 1, 2):
                with self.assertRaisesRegex(ValueError, '자신'):
                    validate_crc(sig, config(start=byte, size=1), 32)
            validate_crc(sig, config(start=3, size=29), 32)

    def test_validation(self):
        sig = Signal('CRC', 0, 16)
        for overrides in [dict(size=31), dict(start=-1), dict(size=0), dict(poly=65536),
                          dict(poly=0), dict(init=-1), dict(xorout=65536), dict(extra=[256])]:
            with self.assertRaises(ValueError):
                validate_crc(sig, config(**overrides), 32)
        with self.assertRaises(ValueError):
            validate_crc(Signal('Small', 0, 8), config(), 32)
        self.assertEqual(parse_extra('0x0000'), [0, 0])
        self.assertEqual(parse_extra('0x12, 0x34'), [0x12, 0x34])
        self.assertEqual(parse_extra(''), [])
        for text in ('0x0', 'GG', '123'):
            with self.assertRaises(ValueError):
                parse_extra(text)

    def test_crc_dependency_order_and_cycle(self):
        msg = Message(1, 'Test', 8, [Signal('A', 0, 8), Signal('B', 8, 8)])
        cfgs = {'A': config('crc8', start=1, size=7), 'B': config('crc8', start=2, size=6)}
        self.assertEqual(crc_order(msg, cfgs, 8), ['B', 'A'])
        cfgs['B'] = config('crc8', start=0, size=1)
        with self.assertRaisesRegex(ValueError, '순환'):
            crc_order(msg, cfgs, 8)

    def test_settings_dialog_blocks_self_range_and_roundtrip(self):
        sig = Signal('CRC', 0, 16)
        settings = SignalCRCDialog(sig, 'crc16', 32)
        with patch('src.tx_crc_dialog.QMessageBox.warning') as warning:
            settings.accept()
            warning.assert_called_once()
            self.assertNotEqual(settings.result(), QDialog.Accepted)
        settings.start.setValue(2)
        settings.size.setValue(30)
        settings.extra.setText('0x0000')
        settings.accept()
        expected_config = config(result_byte_order='little_endian')
        self.assertEqual(settings.config, expected_config)
        msg = Message(1, 'Test', 32, [sig])
        dialog = TxPacketDialog({1: {1: msg}})
        dialog.combo_symbol.setCurrentIndex(1)
        combo = dialog.table_signals.cellWidget(0, 4)
        combo.setCurrentIndex(combo.findData('crc16'))
        self.assertIsNotNone(dialog.table_signals.cellWidget(0, 5))
        with self.assertRaises(ValueError):
            dialog.get_packet_data()
        with patch('src.tx_panel.SignalCRCDialog', return_value=settings), patch.object(settings, 'exec_', return_value=QDialog.Accepted):
            dialog.edit_signal_crc(0)
        data = dialog.get_packet_data()
        self.assertEqual(data['signal_counters']['CRC'], expected_config)
        restored = TxPacketDialog(dialog.db_messages)
        restored.set_packet_data(data)
        self.assertEqual(restored.get_packet_data(), data)
        combo.setCurrentIndex(combo.findData('up'))
        self.assertIsNone(dialog.table_signals.cellWidget(0, 5))
        self.assertEqual(dialog.get_packet_data()['signal_counters']['CRC']['mode'], 'up')
        combo.setCurrentIndex(combo.findData('crc16'))
        self.assertEqual(dialog.get_packet_data()['signal_counters']['CRC'], expected_config)
        for widget in (settings, dialog, restored):
            widget.close()

    def test_explicit_crc16_order_overrides_dbc_order(self):
        for dbc_order in ('little_endian', 'big_endian'):
            sig = Signal('CRC', 0 if dbc_order == 'little_endian' else 7, 16, byte_order=dbc_order)
            msg = Message(1, 'Test', 32, [sig])
            payload = bytes(range(32))
            expected = calculate_crc16_ccitt_false(payload[2:] + b'\0\0')
            for order, byteorder in [('little_endian', 'little'), ('big_endian', 'big')]:
                cfg = config(result_byte_order=order)
                result, _ = counter_payload(msg, payload, {'CRC': cfg}, {})
                self.assertEqual(result[:2], expected.to_bytes(2, byteorder))
                self.assertEqual(result[2:], payload[2:])

    def test_order_selection_and_legacy_restore(self):
        sig = Signal('CRC', 7, 16, byte_order='big_endian')
        legacy = SignalCRCDialog(sig, 'crc16', 32, config())
        self.assertEqual(legacy.result_byte_order.currentData(), 'big_endian')
        legacy.result_byte_order.setCurrentIndex(0)
        cfg = legacy.get_config()
        self.assertEqual(cfg['result_byte_order'], 'little_endian')
        restored = SignalCRCDialog(sig, 'crc16', 32, cfg)
        self.assertEqual(restored.get_config(), cfg)
        with self.assertRaises(ValueError):
            validate_crc(sig, config(result_byte_order='auto'), 32)
        fresh = SignalCRCDialog(sig, 'crc16', 32)
        self.assertEqual(fresh.result_byte_order.currentData(), 'little_endian')
        for d in (legacy, restored, fresh):
            d.close()

    def test_xmt_save_load_and_send(self):
        msg = Message(1, 'Test', 32, [Signal('CRC', 0, 16)])
        db = {1: {1: msg}}
        dialog = TxPacketDialog(db)
        dialog.combo_symbol.setCurrentIndex(1)
        data = dialog.get_packet_data()
        data['signal_counters'] = {'CRC': config()}
        data['data'] = list(range(32))
        panel = TxPanel({}, db)
        panel.auto_save_packets = lambda: None
        item = TxPacketItem(panel.tree, data, panel)
        loaded = []
        panel.add_packet_to_tree = loaded.append
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / 'crc.xmt')
            with patch('src.tx_panel.QFileDialog.getSaveFileName', return_value=(path, '')), patch('src.tx_panel.QMessageBox.information'):
                panel.on_save_packets()
            with patch('src.tx_panel.QFileDialog.getOpenFileName', return_value=(path, '')), patch('src.tx_panel.QMessageBox.critical') as error:
                panel.on_load_packets()
                error.assert_not_called()
        self.assertEqual(loaded[0]['signal_counters'], data['signal_counters'])
        sent = []
        panel.buses = {1: SimpleNamespace(send=sent.append)}
        panel.main_window = SimpleNamespace(bus_capabilities={1: {'is_fd': True}})
        item.packet_data = loaded[0]
        item.send_packet()
        self.assertEqual(len(sent), 1)
        expected = calculate_crc16_ccitt_false(bytes(range(2, 32)) + b'\0\0')
        self.assertEqual(sent[0].data[:2], expected.to_bytes(2, 'little'))
        panel.close()
        dialog.close()


if __name__ == '__main__':
    unittest.main()
