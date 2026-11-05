import json
import os
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PyQt5.QtWidgets import QApplication
from PyQt5.QtTest import QTest
from src.user_panel_v2.masked_data import parse_masked, masked_match, MaskedDataInput
from src.user_panel_v2.packets import RegisteredCommandDialog, PacketRuntime
from src.user_panel_v2.sequence import SequenceControl, validate_steps
from src.user_panel_v2.sequence_dialog import SequencePacketDialog
from src.user_panel_v2.window import UserPanelWindow


def packet():
    return dict(packet_id='p', bus=1, id=0x123, length=8, is_fd=False, is_brs=False,
                data=list(range(8)), cycle=0, note='', count=0, crc_type='N/A', symbol='N/A')


class MaskedSequenceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_parse_hex_bin_partial_and_invalid(self):
        self.assertEqual(parse_masked('01 02 03 04 05 06 XX 08', 8),
                         ([1, 2, 3, 4, 5, 6, 0, 8], [255]*6+[0, 255]))
        self.assertEqual(parse_masked('0010 0001 1100 XXXX', 2, 'BIN'), ([0x21, 0xC0], [255, 240]))
        self.assertEqual(parse_masked('CX x1', 2), ([0xC0, 1], [240, 15]))
        self.assertEqual(parse_masked('11 22', 8), ([17, 34]+[0]*6, [255]*2+[0]*6))
        for text in ('', '1', 'GG', '00'*9):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_masked(text, 8)
        with self.assertRaises(ValueError):
            parse_masked('001X', 2, 'BIN')

    def test_format_preserves_masks(self):
        widget = MaskedDataInput()
        widget.set_data([0x21, 0xC0], [255, 240])
        widget.format.setCurrentText('BIN')
        self.assertEqual(widget.text.text(), '0010 0001 1100 XXXX')
        widget.format.setCurrentText('HEX')
        self.assertEqual(widget.text.text(), '21 CX')
        widget.set_data([128], [128], 'HEX')
        self.assertEqual(widget.mode, 'BIN')
        with patch('src.user_panel_v2.masked_data.QMessageBox.warning') as warning:
            widget.format.setCurrentText('HEX')
            warning.assert_called_once()
        self.assertEqual(widget.values(1), ([128], [128]))
        widget.close()

    def test_send_preserves_current_bits_and_retains_values(self):
        p = packet()
        sent = []
        main = SimpleNamespace(buses={1: SimpleNamespace(send=sent.append)}, bus_capabilities={1: {'is_fd': False}})
        runtime = PacketRuntime(p, {}, main)
        runtime.overlay[1] = 0xAB
        data, mask = parse_masked('21 CX', 8)
        runtime.send(data, mask)
        self.assertEqual(sent[-1].data, bytes([0x21, 0xCB]+list(range(2, 8))))
        runtime.stage(dict(start_bit=8, bit_length=4), 5)
        runtime.send(data, mask)
        runtime.send()
        self.assertEqual(sent[-1].data[1], 0xC5)
        self.assertEqual(p['data'], list(range(8)))

    def test_cmd_roundtrip_without_dbc(self):
        p = packet()
        dlg = RegisteredCommandDialog([p], dict(kind='CMD'))
        dlg.direct_input.setChecked(True)
        dlg.masked_input.text.setText('01 02 03 04 05 06 XX 08')
        dlg.accept()
        step = json.loads(json.dumps(dlg.result_step))
        validate_steps([step], actions_only=True)
        restored = RegisteredCommandDialog([p], step, db_messages={})
        self.assertTrue(restored.direct_input.isChecked())
        self.assertEqual(restored.masked_input.text.text(), '01 02 03 04 05 06 XX 08')
        restored.accept()
        self.assertEqual(restored.result_step['data_mask'], [255]*6+[0, 255])
        dlg.close()
        restored.close()

    def test_receive_editor_roundtrip_and_all_x_rejected(self):
        dlg = SequencePacketDialog({}, dict(kind='RCV', packet=packet()))
        self.assertFalse(dlg.allow_length_mismatch.isChecked())
        dlg.direct_input.setChecked(True)
        dlg.allow_length_mismatch.setChecked(True)
        dlg.masked_input.text.setText('11 22')
        dlg.accept()
        self.assertEqual(dlg.result(), dlg.Accepted)
        step = json.loads(json.dumps(dlg.result_step))
        self.assertEqual(step['mask'], [255, 255]+[0]*6)
        restored = SequencePacketDialog({}, step)
        restored.accept()
        self.assertEqual(restored.result_step['mask'], step['mask'])
        self.assertTrue(restored.result_step['allow_length_mismatch'])
        restored.masked_input.text.setText('XX')
        with patch('src.user_panel_v2.sequence_dialog.QMessageBox.warning') as warning:
            restored.accept()
            warning.assert_called_once()
        dlg.close()
        restored.close()

    def test_short_receive_safety(self):
        for text in ('11 22', '11 22 XX XX'):
            data, mask = parse_masked(text, 8)
            self.assertTrue(masked_match([17, 34, 204, 255], data, mask, True))
            self.assertFalse(masked_match([17, 34, 204, 255], data, mask))
            self.assertFalse(masked_match([17], data, mask, True))
            self.assertFalse(masked_match([17, 35, 204, 255], data, mask, True))
        data, mask = parse_masked('11 22 XX XX 55', 8)
        self.assertFalse(masked_match([17, 34, 204, 255], data, mask, True))
        self.assertFalse(masked_match([], [0]*8, [0]*8, True))

    def test_init_and_receive_execute_without_dbc(self):
        p = packet()
        data, mask = parse_masked('11 22', 8)
        cmd = dict(kind='CMD', packet=p, data_override=data, data_mask=mask)
        sent = []
        main = SimpleNamespace(buses={1: SimpleNamespace(send=sent.append)},
                               bus_capabilities={1: {'is_fd': False}}, record_tx_activity=lambda *a, **kwargs: None)
        panel = UserPanelWindow(main, {})
        self.addCleanup(panel.close)
        panel._load_panel_data(json.loads(json.dumps(dict(tx_packets=[p], widgets=[], init_steps=[cmd]))))
        panel.show()
        panel.set_mode('run')
        QTest.qWait(20)
        self.assertEqual(sent[-1].data, bytes([17, 34]+list(range(2, 8))))
        self.assertEqual(panel._panel_data()['init_steps'][0]['data_mask'], mask)
        expected = dict(p, data=data)
        rcv = dict(kind='RCV', packet=expected, mask=mask, timeout_ms=1000, allow_length_mismatch=True)
        ctrl = SequenceControl(panel, dict(title='Test', binding=dict(sequence_steps=[rcv])))
        self.addCleanup(ctrl.close)
        ctrl.toggle()
        for bus, can_id, extended, fd, brs, payload in (
                (2, p['id'], False, False, False, [17, 34]),
                (1, p['id']+1, False, False, False, [17, 34]),
                (1, p['id'], True, False, False, [17, 34]),
                (1, p['id'], False, True, False, [17, 34]),
                (1, p['id'], False, False, True, [17, 34]),
                (1, p['id'], False, False, False, [17])):
            ctrl.receive(time.time(), bus, can_id, payload, extended, fd, brs)
            self.assertFalse(ctrl.matched)
        ctrl.receive(time.time(), 1, p['id'], [17, 34, 204, 255], False, False)
        self.assertTrue(ctrl.matched)
        QTest.qWait(10)
        self.assertFalse(ctrl.running)
