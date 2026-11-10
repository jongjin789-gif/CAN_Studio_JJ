import copy
import json
import os
import time
import unittest
from unittest.mock import patch
from types import SimpleNamespace

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QPushButton
from cantools.database.can import Message, Signal
from src.user_panel_v2.window import UserPanelWindow
from src.user_panel_v2.sequence import SequenceControl, validate_steps
from src.user_panel_v2.sequence_dialog import SequencePacketDialog, SequenceDialog, BitsDialog, signal_mask
from src.user_panel_v2.config_dialog import WidgetConfigDialog


def packet():
    return dict(bus=1, id=0x123, length=8, data=[1]+[0]*7, is_fd=True, is_brs=False,
                cycle=0, note='', crc_type='N/A', symbol='N/A')


class SequenceTest(unittest.TestCase):
    def test_copied_receive_bus_change_keeps_mask_and_falls_back_to_direct_input(self):
        source = Message(0x123, 'Example', 8, [Signal('A', 0, 8), Signal('B', 8, 8)])
        match = Message(0x123, 'Example', 8, [Signal('A', 0, 8), Signal('B', 8, 8)])
        mismatch = Message(0x123, 'Example', 8, [Signal('Different', 0, 8)])
        step = dict(kind='RCV', packet=packet(), mask=[255] + [0] * 7, timeout_ms=300)
        for target, direct in (({0x123: match}, False), ({}, True), ({0x123: mismatch}, True)):
            with self.subTest(direct=direct, target=target):
                editor = SequencePacketDialog({1: {0x123: source}, 2: target}, copy.deepcopy(step))
                self.addCleanup(editor.close)
                editor.combo_bus.setCurrentText('2')
                editor.accept()
                result = editor.result_step
                self.assertEqual(result['packet']['bus'], 2)
                self.assertEqual(result['packet']['data'], step['packet']['data'])
                self.assertEqual(result['mask'], step['mask'])
                self.assertEqual(result['timeout_ms'], 300)
                self.assertEqual(result['direct_input'], direct)
                if direct:
                    self.assertEqual(result['packet']['symbol'], 'N/A')
                    restored = SequencePacketDialog({2: target}, result)
                    self.addCleanup(restored.close)
                    self.assertIsNone(restored.current_db_msg)
                    self.assertTrue(restored.direct_input.isChecked())

    def test_row_shortcuts_copy_full_step_between_tool_and_init_editors(self):
        step = dict(kind='CMD', packet=packet(), name='Command', repeat=3,
                    data_override=[2] * 8, data_mask=[255] * 8)
        editor = SequenceDialog({}, [step])
        init = SequenceDialog({}, [], actions_only=True, allow_empty=True, allow_failure=False)
        self.addCleanup(editor.close)
        self.addCleanup(init.close)
        old_clipboard = self.app.clipboard().text()
        self.addCleanup(lambda: self.app.clipboard().setText(old_clipboard))
        editor.show()
        editor.activateWindow()
        editor.table.setFocus()
        self.app.processEvents()
        QTest.keyClick(editor.table, Qt.Key_C, Qt.ControlModifier)
        init.show()
        init.activateWindow()
        init.table.setFocus()
        self.app.processEvents()
        QTest.keyClick(init.table, Qt.Key_V, Qt.ControlModifier)
        self.assertEqual(init.steps, [step])
        button = init.table.cellWidget(0, 1)
        button.setFocus()
        QTest.keyClick(button, Qt.Key_V, Qt.ControlModifier)
        self.assertEqual(init.steps, [step, step])
        init.steps[1]['packet']['data'][0] = 99
        self.assertEqual(init.steps[0]['packet']['data'][0], 1)
        self.assertEqual(editor.steps[0]['packet']['data'][0], 1)

    def test_sequence_text_editing_keeps_native_copy_and_paste(self):
        editor = SequenceDialog({}, [dict(kind='DEL', delay_ms=10, name='First')])
        self.addCleanup(editor.close)
        old_clipboard = self.app.clipboard().text()
        self.addCleanup(lambda: self.app.clipboard().setText(old_clipboard))
        editor.show()
        editor.activateWindow()
        name = editor.table.cellWidget(0, 2)
        name.setFocus()
        name.selectAll()
        self.app.processEvents()
        QTest.keyClick(name, Qt.Key_C, Qt.ControlModifier)
        self.assertEqual(self.app.clipboard().text(), 'First')
        self.app.clipboard().setText('Renamed')
        QTest.keyClick(name, Qt.Key_V, Qt.ControlModifier)
        self.assertEqual(editor.steps[0]['name'], 'Renamed')
        self.assertEqual(len(editor.steps), 1)

    def test_init_paste_rejects_receive_steps(self):
        old_clipboard = self.app.clipboard().text()
        self.addCleanup(lambda: self.app.clipboard().setText(old_clipboard))
        self.app.clipboard().setText(json.dumps(dict(format='pcan-sequence-row', step=dict(kind='RCV'))))
        init = SequenceDialog({}, [], actions_only=True, allow_empty=True)
        self.addCleanup(init.close)
        with patch('src.user_panel_v2.sequence_dialog.QMessageBox.warning') as warning:
            init.paste_row()
        warning.assert_called_once()
        self.assertEqual(init.steps, [])

    def test_sequence_cell_selection_remains_visible_when_deleting(self):
        steps = [dict(kind='DEL', delay_ms=10, name='First'),
                 dict(kind='DEL', delay_ms=20, name='Second')]
        editor = SequenceDialog({}, steps)
        self.addCleanup(editor.close)
        editor.show()
        self.app.processEvents()
        QTest.mouseClick(editor.table.cellWidget(1, 2), Qt.LeftButton)
        self.assertEqual(editor.table.currentRow(), 1)
        self.assertEqual(editor.table.verticalHeaderItem(1).text(), '▶ 2')
        self.assertEqual(editor.table.verticalHeaderItem(0).text(), '1')
        delete = next(b for b in editor.findChildren(QPushButton) if b.text() == '삭제')
        delete.setFocus()
        self.app.processEvents()
        self.assertEqual(editor.table.verticalHeaderItem(1).text(), '▶ 2')
        QTest.mouseClick(delete, Qt.LeftButton)
        self.assertEqual([s['name'] for s in editor.steps], ['First'])
        self.assertEqual(editor.table.verticalHeaderItem(0).text(), '▶ 1')

    def test_sequence_log_uses_compact_font(self):
        control = SequenceControl(SimpleNamespace(), dict(title='Test', binding={}))
        self.addCleanup(control.deleteLater)
        self.assertEqual(control.log.font().pointSize(), max(7, self.app.font().pointSize() - 2))

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.sent = []
        self.main = SimpleNamespace(buses={1: SimpleNamespace(send=self.sent.append)},
                                    bus_capabilities={1: {'is_fd': True}}, record_tx_activity=lambda *a, **kwargs: None)
        self.panel = UserPanelWindow(self.main, {})
        self.panel.show()
        self.panel.set_mode('run')

    def tearDown(self):
        self.panel.close()
        self.panel.deleteLater()

    def control(self, steps):
        from src.user_panel_v2.packets import PacketRuntime
        for step in steps:
            if step['kind'] == 'CMD':
                registered = copy.deepcopy(step['packet'])
                registered['packet_id'] = f"{registered['bus']}:{registered['id']}"
                self.panel.tx_packets = [p for p in self.panel.tx_packets if p['packet_id'] != registered['packet_id']] + [registered]
                self.panel._packet_runtimes[registered['packet_id']] = PacketRuntime(registered, {}, self.main)
        cfg = dict(id='seq', title='Init', widget_type='sequence', behavior='tx', binding=dict(sequence_steps=steps))
        ctrl = SequenceControl(self.panel, cfg)
        self.panel.widget_controls['seq'] = ctrl
        self.addCleanup(ctrl.deleteLater)
        return ctrl

    def test_cmd_fast_response_delay_complete(self):
        p = packet()
        c = self.control([dict(kind='CMD', name='Init 명령', packet=p), dict(kind='RCV', name='Init 응답', packet=p, mask=[3]+[0]*7, timeout_ms=100), dict(kind='DEL', delay_ms=5)])
        c.toggle()
        c.receive(time.time(), 1, p['id'], p['data'], False, True)
        # Other GUI tests can leave deferred widget cleanup in the event queue.
        # Wait for completion with a bound instead of assuming a 50 ms dispatch.
        deadline = time.monotonic() + 1.0
        while c.running and time.monotonic() < deadline:
            QTest.qWait(10)
        self.assertFalse(c.running)
        self.assertIn('전체 과정 완료', c.log.toPlainText())
        self.assertEqual(len(self.sent), 1)
        self.assertTrue(self.sent[0].is_fd)
        self.assertIn('1/3 CMD BUS_1 0x123 Init 명령 · 송신 완료', c.log.toPlainText())
        self.assertIn('2/3 RCV BUS_1 0x123 Init 응답 · 응답 조건 일치', c.log.toPlainText())
        self.assertNotIn('01 00 00 00', c.log.toPlainText())

    def test_timeout_stop_restart_close_and_mode(self):
        p = packet()
        c = self.control([dict(kind='RCV', packet=p, mask=[255]+[0]*7, timeout_ms=20), dict(kind='CMD', packet=p)])
        c.toggle()
        c.receive(time.time()-10, 1, p['id'], p['data'], False, True)
        QTest.qWait(40)
        self.assertIn('타임아웃', c.log.toPlainText())
        self.assertEqual(self.sent, [])
        c.toggle()
        c.toggle()
        c.receive(time.time(), 1, p['id'], p['data'], False, True)
        QTest.qWait(30)
        self.assertEqual(self.sent, [])
        c.toggle()
        self.assertEqual(c.index, 0)
        self.panel.set_mode('standby')
        self.assertFalse(c.running)
        self.panel.set_mode('run')
        self.assertFalse(c.running)
        c.toggle()
        self.panel.close()
        QTest.qWait(30)
        self.assertFalse(c.running)
        self.assertEqual(self.sent, [])

    def test_no_frame_reuse_and_wrong_bus(self):
        p = packet()
        s = dict(kind='RCV', packet=p, mask=[255]+[0]*7, timeout_ms=100)
        c = self.control([s, s])
        c.toggle()
        c.receive(time.time(), 2, p['id'], p['data'], False, True)
        self.assertFalse(c.matched)
        c.receive(time.time(), 1, p['id'], p['data'], False, True)
        QTest.qWait(10)
        self.assertEqual(c.index, 1)
        self.assertFalse(c.matched)

    def test_dbc_checkbox_mask_and_offline_edit_round_trip(self):
        signals = [Signal('First', 0, 2), Signal('Second', 8, 4)]
        msg = Message(0x123, 'Response', 8, signals, is_fd=True)
        dlg = SequencePacketDialog({1: {0x123: msg}}, dict(kind='RCV', packet=packet()))
        for row in range(2):
            dlg.table_signals.item(row, 0).setCheckState(Qt.Checked)
        dlg.accept()
        self.assertEqual(dlg.result(), dlg.Accepted)
        step = json.loads(json.dumps(dlg.result_step))
        self.assertEqual(step['mask'], [3, 15]+[0]*6)
        offline = SequencePacketDialog({}, step)
        offline.edit_data.setText('02 03 00 00 00 00 00 00')
        offline.accept()
        self.assertEqual(offline.result_step['packet']['data'][:2], [2, 3])
        self.assertEqual(offline.result_step['mask'], step['mask'])
        cfg = dict(widget_type='sequence', behavior='tx', binding=dict(sequence_steps=[offline.result_step]))
        config = WidgetConfigDialog({}, preset=cfg)
        self.assertEqual(config.get_config()['binding']['sequence_steps'], [offline.result_step])
        for obj in (dlg, offline, config):
            obj.deleteLater()

    def test_cmd_offline_bits_and_delay_editing(self):
        dlg = SequencePacketDialog({}, dict(kind='CMD', packet=packet()))
        dlg.edit_data.setText('AB 00 00 00 00 00 00 00')
        dlg.repeat.setValue(2)
        dlg.accept()
        self.assertEqual(dlg.result_step['packet']['data'][0], 0xAB)
        bits = BitsDialog([0], [0], True)
        bits.table.item(0, 1).setText('XXXX XX01')
        bits.accept()
        self.assertEqual((bits.data, bits.mask), ([1], [3]))
        editor = SequenceDialog({}, [])
        editor.table.cellWidget(0, 2).setText('Init 이름')
        editor.add()
        self.assertEqual(len(editor.steps), 2)
        editor.delete()
        self.assertEqual(len(editor.steps), 1)
        editor.refresh()
        self.assertEqual(editor.table.cellWidget(0, 2).text(), 'Init 이름')
        self.assertEqual(json.loads(json.dumps(editor.steps))[0]['name'], 'Init 이름')
        for obj in (dlg, bits, editor):
            obj.deleteLater()

    def test_crc_repeat_and_disconnect(self):
        p = packet()
        p['crc_type'] = 'Hyundai_CRC'
        p['cycle'] = 5
        c = self.control([dict(kind='CMD', packet=p, repeat=2)])
        c.toggle()
        QTest.qWait(40)
        self.assertEqual(len(self.sent), 2)
        self.assertEqual([m.data[2] for m in self.sent], [0, 1])
        self.assertNotEqual(self.sent[0].data[:2], self.sent[1].data[:2])
        c = self.control([dict(kind='DEL', delay_ms=500), dict(kind='CMD', packet=packet())])
        c.toggle()
        self.main.buses[1] = None
        QTest.qWait(150)
        self.assertFalse(c.running)
        self.assertIn('연결 해제', c.log.toPlainText())

    def test_mask_endianness_and_validation(self):
        self.assertEqual(signal_mask(Signal('BE', 7, 12, byte_order='big_endian'), 2), [255, 240])
        with self.assertRaises(ValueError):
            validate_steps([dict(kind='RCV', packet=packet(), mask=[0]*8, timeout_ms=10)])

    def test_hold_and_periodic_timers_stop(self):
        cfg = dict(id='button', widget_type='button', behavior='tx', binding={})
        self.panel._emit_tx = lambda *a: None
        button, _ = self.panel._create_runtime_widget(cfg)
        self.panel.widget_controls['button'] = button
        button.pressed.emit()
        timer = button.findChild(QTimer, 'panel_hold_timer')
        self.assertIsNone(timer)  # Repetition now belongs to the registered packet.
        frame_timer = QTimer(self.panel)
        frame_timer.start(100)
        self.panel._frame_timers[(1, 1, 8)] = frame_timer
        self.panel.set_mode('edit')
        self.assertFalse(frame_timer.isActive())
        button.deleteLater()
