import copy
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PyQt5.QtWidgets import QApplication
from PyQt5.QtTest import QTest
from cantools.database.can import Message, Signal
from cantools.database.conversion import BaseConversion
from src.user_panel_v2.packets import RegisteredCommandDialog, PacketRuntime, validate_tool
from src.user_panel_v2.sequence import SequenceControl
from src.user_panel_v2.window import UserPanelWindow
from src.crc_utils import calculate_crc16_ccitt_false


def packet():
    return dict(packet_id='p', bus=1, id=0x123, length=8, is_fd=False, is_brs=False,
                data=[0]*8, cycle=0, note='', count=0, crc_type='N/A', symbol='Test')


class CommandValuesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_signal_and_hex_edit_roundtrip(self):
        p = packet()
        msg = Message(p['id'], 'Test', 8, [Signal('Value', 0, 8, conversion=BaseConversion.factory(scale=2, offset=10))])
        db = {1: {p['id']: msg}}
        editor = RegisteredCommandDialog([p], dict(kind='CMD'), db_messages=db)
        editor.editor.table_signals.item(0, 2).setText('30')
        self.assertEqual(bytes.fromhex(editor.editor.edit_data.text())[0], 10)
        editor.accept()
        self.assertEqual(editor.result_step['data_override'][0], 10)
        self.assertEqual(p['data'], [0]*8)
        saved = copy.deepcopy(editor.result_step)
        validate_tool([p], dict(behavior='tx', widget_type='sequence', binding=dict(sequence_steps=[saved])))
        self.assertEqual(saved['packet']['data'][0], 10)
        restored = RegisteredCommandDialog([p], saved, db_messages=db)
        self.assertEqual(float(restored.editor.table_signals.item(0, 2).text()), 30)
        restored.editor.edit_data.setText('01 02 03 04 05 06 07 08')
        restored.accept()
        self.assertEqual(restored.result_step['data_override'], list(range(1, 9)))
        restored.use_values.setChecked(False)
        restored.accept()
        self.assertNotIn('data_override', restored.result_step)
        for d in (editor, restored):
            d.close()

    def test_override_is_retained_and_counter_crc_run_after_values(self):
        p = packet()
        p['signal_counters'] = {'Count': dict(mode='up', min=0, max=255, step=1),
            'CRC': dict(mode='crc16', start=2, size=6, extra=[], poly=0x1021, init=65535,
                        xorout=0, refin=False, refout=False, result_byte_order='little_endian')}
        msg = Message(p['id'], 'Test', 8, [Signal('CRC', 0, 16), Signal('Count', 16, 8)])
        sent = []
        main = SimpleNamespace(buses={1: SimpleNamespace(send=sent.append)}, bus_capabilities={1: {'is_fd': False}})
        runtime = PacketRuntime(p, {1: {p['id']: msg}}, main)
        runtime.overlay[3] = 99
        override = [0, 0, 4, 17, 18, 19, 20, 21]
        runtime.send(override)
        runtime.send(override)
        self.assertEqual([m.data[2] for m in sent], [4, 5])
        self.assertEqual(sent[0].data[3:], bytes(override[3:]))
        self.assertEqual(int.from_bytes(sent[0].data[:2], 'little'), calculate_crc16_ccitt_false(sent[0].data[2:]))
        runtime.send()
        self.assertEqual(sent[-1].data[3], 17)
        self.assertEqual(runtime.overlay, sent[-1].data)
        self.assertEqual(p['data'], [0]*8)

    def test_init_normal_and_failure_cmd_send_saved_values(self):
        p = packet()
        sent = []
        main = SimpleNamespace(buses={1: SimpleNamespace(send=sent.append)},
                               bus_capabilities={1: {'is_fd': False}}, record_tx_activity=lambda *args, **kwargs: None)
        cmd = dict(kind='CMD', packet=p, data_override=[11]*8)
        panel = UserPanelWindow(main, {})
        panel._load_panel_data(dict(tx_packets=[p], widgets=[], init_steps=[cmd]))
        panel.show()
        panel.set_mode('run')
        QTest.qWait(20)
        self.assertEqual(sent[-1].data, bytes([11]*8))
        normal = dict(kind='CMD', packet=p, data_override=[22]*8)
        failure = dict(kind='CMD', packet=p, data_override=[33]*8)
        rcv = dict(kind='RCV', packet=p, mask=[255]*8, timeout_ms=10)
        ctrl = SequenceControl(panel, dict(title='Test', binding=dict(sequence_steps=[normal, rcv], sequence_failure_steps=[failure])))
        ctrl.toggle()
        QTest.qWait(40)
        self.assertEqual([m.data[0] for m in sent], [11, 22, 33])
        self.assertEqual(panel._packet_runtimes['p'].overlay, bytes([33]*8))
        saved = copy.deepcopy(panel._panel_data())
        panel._load_panel_data(saved)
        self.assertEqual(panel.init_steps[0]['data_override'], [11]*8)
        ctrl.close()
        panel.close()

    def test_wrong_length_is_rejected_and_legacy_uses_current_values(self):
        p = packet()
        editor = RegisteredCommandDialog([p], dict(kind='CMD', packet=p))
        self.assertFalse(editor.use_values.isChecked())
        editor.use_values.setChecked(True)
        editor.editor.edit_data.setText('FF')
        with patch('src.user_panel_v2.packets.QMessageBox.warning') as warning:
            editor.accept()
            warning.assert_called_once()
        step = dict(kind='CMD', packet=p, data_override=[0])
        with self.assertRaises(ValueError):
            validate_tool([p], dict(behavior='tx', widget_type='sequence', binding=dict(sequence_steps=[step])))
        editor.close()

    def test_failed_cmd_preserves_last_successful_data(self):
        p = packet()
        sent = []
        main = SimpleNamespace(buses={1: SimpleNamespace(send=sent.append)}, bus_capabilities={1: {'is_fd': False}})
        runtime = PacketRuntime(p, {}, main)
        runtime.send([17]*8)
        with patch.object(main.buses[1], 'send', side_effect=RuntimeError('failed')):
            with self.assertRaises(RuntimeError):
                runtime.send([99]*8)
        runtime.send()
        self.assertEqual(sent[-1].data, bytes([17]*8))

    def test_retained_cmd_survives_run_reentry_and_explicit_reset(self):
        p = packet()
        p['cycle'] = 10
        sent = []
        main = SimpleNamespace(buses={1: SimpleNamespace(send=sent.append)},
                               bus_capabilities={1: {'is_fd': False}}, record_tx_activity=lambda *args, **kwargs: None)
        panel = UserPanelWindow(main, {})
        panel._load_panel_data(dict(tx_packets=[p], widgets=[]))
        panel.show()
        panel.set_mode('run')
        runtime = panel._packet_runtimes['p']
        runtime.send([42]*8)
        panel.set_mode('standard')
        panel.set_mode('run')
        self.assertIs(panel._packet_runtimes['p'], runtime)
        panel._flush_frame('p')
        self.assertEqual(sent[-1].data, bytes([42]*8))
        runtime.stage(dict(start_bit=0, bit_length=8), 9)
        runtime.send()
        self.assertEqual(sent[-1].data, bytes([9]+[42]*7))
        runtime.send([0]*8)
        panel._flush_frame('p')
        self.assertEqual(sent[-1].data, bytes(8))
        panel.close()
