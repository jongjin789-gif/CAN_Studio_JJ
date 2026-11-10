import copy
import json
from PyQt5.QtCore import Qt, QEvent, QItemSelectionModel
from PyQt5.QtGui import QKeySequence
from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QTableWidget,
    QTableWidgetItem, QComboBox, QLineEdit, QSpinBox, QLabel, QMessageBox, QCheckBox, QHeaderView, QApplication)
from src.tx_panel import TxPacketDialog
from .sequence import validate_steps, format_can_id
from .inline_editor import show_inline_editor
from .packets import RegisteredCommandDialog
from .masked_data import MaskedDataInput, format_masked
from src.spin_input import spin_with_hint


class PacketActionDialog(QDialog):
    def __init__(self, packets, step, parent=None):
        super().__init__(parent)
        self.kind = step['kind']
        self.setWindowTitle('주기 패킷 시작 / 정지')
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel('등록 패킷의 주기 전송만 제어합니다. CMD 송신은 유지됩니다.'))
        self.target = QComboBox()
        self.target.addItem('전체 등록 패킷', '*')
        for p in packets or []:
            self.target.addItem(f"BUS {p['bus']} · 0x{p['id']:X} · {p.get('symbol', 'N/A')}", p['packet_id'])
        self.target.setCurrentIndex(max(0, self.target.findData(step.get('target_packet_id', '*'))))
        layout.addWidget(self.target)
        button = QPushButton('OK')
        button.clicked.connect(self.accept)
        layout.addWidget(button)

    def accept(self):
        self.result_step = dict(kind=self.kind, target_packet_id=self.target.currentData(), summary=self.target.currentText())
        super().accept()


def bit_text(data, mask):
    lines = []
    for i, (value, selected) in enumerate(zip(data, mask)):
        bits = ''.join(str((value >> b) & 1) if selected & (1 << b) else 'X' for b in range(7, -1, -1))
        lines.append(f"[{i}]: {bits[:4]} {bits[4:]}")
    return '\n'.join(lines)


def signal_mask(sig, length):
    result = [0] * length
    bit = int(sig.start)
    for _ in range(sig.length):
        if bit < 0 or bit >= length * 8:
            raise ValueError("Signal exceeds packet length.")
        result[bit // 8] |= 1 << (bit % 8)
        bit = bit + 1 if sig.byte_order == "little_endian" else (bit + 15 if bit % 8 == 0 else bit - 1)
    return result


class BitsDialog(QDialog):
    def __init__(self, data, mask, receive, parent=None):
        super().__init__(parent)
        self.setWindowTitle("RCV bits: X / 0 / 1" if receive else "CMD bits: 0 / 1")
        layout = QVBoxLayout(self)
        self.table = QTableWidget(len(data), 2)
        self.table.setHorizontalHeaderLabels(["Byte", "bit 7 → bit 0"])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        for i, line in enumerate(bit_text(data, mask).splitlines()):
            item = QTableWidgetItem(f"[{i}]")
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(i, 0, item)
            self.table.setItem(i, 1, QTableWidgetItem(line.split(': ')[1]))
        layout.addWidget(self.table)
        self.receive = receive
        button = QPushButton("OK")
        button.clicked.connect(self.accept)
        layout.addWidget(button)
        self.resize(360, 420)

    def accept(self):
        try:
            self.data, self.mask = [], []
            for row in range(self.table.rowCount()):
                bits = self.table.item(row, 1).text().replace(' ', '').upper()
                if len(bits) != 8 or any(c not in ('01X' if self.receive else '01') for c in bits):
                    raise ValueError(f"Byte {row}: enter exactly 8 bits.")
                self.data.append(int(bits.replace('X', '0'), 2))
                self.mask.append(int(''.join('0' if c == 'X' else '1' for c in bits), 2))
            super().accept()
        except ValueError as exc:
            QMessageBox.warning(self, "Bits", str(exc))


class SequencePacketDialog(TxPacketDialog):
    def __init__(self, db_messages, step, parent=None):
        self.receive_mode = step['kind'] == 'RCV'
        self.saved_mask = list(step.get('mask', []))
        super().__init__(db_messages, parent)
        self.setWindowTitle("RCV 조건" if self.receive_mode else "CMD 패킷")
        self.extended = QCheckBox("Extended ID")
        self.timeout = QSpinBox()
        self.timeout.setRange(1, 600000)
        self.timeout.setValue(int(step.get('timeout_ms', 1000)))
        self.repeat = QSpinBox()
        self.repeat.setRange(1, 10000)
        self.repeat.setValue(int(step.get('repeat', 1)))
        self.bits = QPushButton("비트 값 편집")
        self.bits.clicked.connect(self.edit_bits)
        extras = QHBoxLayout()
        extras.addWidget(self.extended)
        if self.receive_mode:
            extras.addWidget(spin_with_hint(self.timeout, "ms timeout", "응답을 기다릴 최대 시간(ms)"))
        else:
            extras.addWidget(QLabel("Repeat:"))
            extras.addWidget(spin_with_hint(self.repeat, "회", "이 CMD를 반복 송신할 횟수"))
        extras.addWidget(self.bits)
        self.layout().insertLayout(1, extras)
        if step.get('packet'):
            self.set_packet_data(step['packet'])
            self.edit_id.setText(f"{step['packet']['id']:X}")
            self.extended.setChecked(step['packet'].get('is_extended_id', step['packet']['id'] > 0x7FF))
        else:
            self.combo_length.setCurrentText("8")
            self.on_length_changed()
        self.edit_id.textChanged.connect(self._suggest_extended)
        self._mark_signals()
        self.direct_input = QCheckBox('직접 HEX / BIN / X 입력 (신호 체크 없이 비교)')
        self.direct_input.setChecked(step.get('direct_input', False))
        self.masked_input = MaskedDataInput(self)
        n = int(self.combo_length.currentText() or 0)
        current = self.get_packet_data()['data']
        self.masked_input.set_data(current, (self.saved_mask + [0] * n)[:n], step.get('input_format', 'HEX'))
        self.allow_length_mismatch = QCheckBox('수신 DLC 차이 허용: 지정한 비교 비트가 모두 수신되면 비교')
        self.allow_length_mismatch.setChecked(step.get('allow_length_mismatch', False))
        self.allow_length_mismatch.setToolTip('기본은 DLC 일치 필수입니다. 켜면 비교하지 않는 뒤쪽 바이트는 없어도 되지만, 지정한 비트가 누락되면 실패합니다.')
        self.layout().insertWidget(2, self.direct_input)
        self.layout().insertWidget(3, self.masked_input)
        self.layout().insertWidget(4, self.allow_length_mismatch)
        self.direct_input.toggled.connect(self.update_direct_mode)
        self.update_direct_mode()
        if self.receive_mode:
            self.edit_cycle.setEnabled(False)
            self.edit_cycle.setToolTip("RCV uses timeout; cycle is not executed.")
            self.crc_combo.setToolTip("RCV compares saved bits; CRC is not recalculated.")

    def update_direct_mode(self):
        self.direct_input.setVisible(self.receive_mode)
        self.masked_input.setVisible(self.receive_mode and self.direct_input.isChecked())
        self.allow_length_mismatch.setVisible(self.receive_mode)
        if self.receive_mode:
            self.table_signals.setEnabled(not self.direct_input.isChecked())
            self.edit_data.setEnabled(not self.direct_input.isChecked())

    def bus_signal_names(self):
        if not self.receive_mode or not self.current_db_msg:
            return None
        return [sig.name for sig in self.current_db_msg.signals
                if any(a & b for a, b in zip(signal_mask(sig, self.current_db_msg.length), self.saved_mask))]

    def on_bus_changed(self):
        if not self._updating and self.receive_mode and hasattr(self, 'direct_input'):
            packet = TxPacketDialog.get_packet_data(self)
            self.saved_mask = self.comparison(packet)[0]
            if self.direct_input.isChecked():
                data, _ = self.masked_input.values(packet['length'])
                self._updating = True
                self.edit_data.setText(' '.join(f'{b:02X}' for b in data))
                self._updating = False
        super().on_bus_changed()
        self._mark_signals()
        if self.receive_mode and hasattr(self, 'direct_input') and self.current_db_msg is None:
            packet = TxPacketDialog.get_packet_data(self)
            self.masked_input.set_data(packet['data'], self.saved_mask, self.masked_input.mode)
            self.direct_input.setChecked(True)

    def _suggest_extended(self):
        try:
            if int(self.edit_id.text(), 16) > 0x7FF:
                self.extended.setChecked(True)
        except ValueError:
            pass

    def on_symbol_changed(self):
        super().on_symbol_changed()
        self._mark_signals()
        if hasattr(self, 'extended') and self.current_db_msg:
            self.extended.setChecked(bool(getattr(self.current_db_msg, 'is_extended_frame', False)))

    def _mark_signals(self):
        if not self.receive_mode or not self.current_db_msg:
            return
        self.table_signals.blockSignals(True)
        try:
            for row, sig in enumerate(self.current_db_msg.signals):
                item = self.table_signals.item(row, 0)
                if item:
                    mask = signal_mask(sig, self.current_db_msg.length)
                    selected = len(mask) == len(self.saved_mask) and all((a & b) == a for a, b in zip(mask, self.saved_mask))
                    item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                    item.setCheckState(Qt.Checked if selected else Qt.Unchecked)
        finally:
            self.table_signals.blockSignals(False)

    def comparison(self, packet):
        if hasattr(self, 'direct_input') and self.direct_input.isChecked():
            data, mask = self.masked_input.values(packet['length'])
            return mask, bit_text(data, mask)
        mask = (self.saved_mask + [0] * packet['length'])[:packet['length']]
        labels = []
        if self.current_db_msg:
            # Preserve partial-bit selections that cannot be represented by a whole-signal checkbox.
            for row, sig in enumerate(self.current_db_msg.signals):
                item = self.table_signals.item(row, 0)
                sm = signal_mask(sig, packet['length'])
                whole = all((a & b) == a for a, b in zip(sm, mask))
                if item.checkState() == Qt.Checked:
                    mask = [a | b for a, b in zip(mask, sm)]
                    labels.append(f"{sig.name}={self.table_signals.item(row, 2).text()}")
                elif whole:
                    mask = [a & ~b for a, b in zip(mask, sm)]
        return mask, ', '.join(labels)

    def edit_bits(self):
        try:
            packet = self.get_packet_data()
            if self.receive_mode and self.direct_input.isChecked():
                packet['data'], _ = self.masked_input.values(packet['length'])
            mask = self.comparison(packet)[0] if self.receive_mode else [255] * packet['length']
            dlg = BitsDialog(packet['data'], mask, self.receive_mode, self)
            if getattr(self, '_inline_editing', False):
                def apply_bits():
                    self.saved_mask = dlg.mask
                    if self.receive_mode and self.direct_input.isChecked():
                        self.masked_input.set_data(dlg.data, dlg.mask, self.masked_input.mode)
                    self.edit_data.setText(' '.join(f'{b:02X}' for b in dlg.data))
                    self.on_data_edited()
                    self._mark_signals()
                show_inline_editor(self, dlg, apply_bits)
                return
            if dlg.exec_() == dlg.Accepted:
                self.saved_mask = dlg.mask
                if self.receive_mode and self.direct_input.isChecked():
                    self.masked_input.set_data(dlg.data, dlg.mask, self.masked_input.mode)
                self.edit_data.setText(' '.join(f'{b:02X}' for b in dlg.data))
                self.on_data_edited()
                self._mark_signals()
        except Exception as exc:
            QMessageBox.warning(self, "Packet", str(exc))

    def accept(self):
        try:
            raw_hex = self.edit_data.text().replace(' ', '')
            if len(raw_hex) % 2:
                raise ValueError("HEX data must contain complete bytes.")
            if not self.edit_id.text().strip():
                raise ValueError("Enter a CAN ID (0 is allowed).")
            packet = self.get_packet_data()
            if self.receive_mode and self.direct_input.isChecked():
                packet['data'], _ = self.masked_input.values(packet['length'])
            packet['is_extended_id'] = self.extended.isChecked()
            self.result_step = dict(kind='RCV' if self.receive_mode else 'CMD', packet=packet,
                                    timeout_ms=self.timeout.value(), repeat=self.repeat.value())
            if self.receive_mode:
                mask, labels = self.comparison(packet)
                self.result_step['allow_length_mismatch'] = self.allow_length_mismatch.isChecked()
                self.result_step['direct_input'] = self.direct_input.isChecked()
                self.result_step['input_format'] = self.masked_input.mode
                self.result_step['mask'] = mask
                self.result_step['summary'] = labels or bit_text(packet['data'], mask)
            else:
                self.result_step['summary'] = f"Bus {packet['bus']} ID {format_can_id(packet)}: " + bytes(packet['data']).hex(' ').upper()
            validate_steps([self.result_step])
            QDialog.accept(self)
        except Exception as exc:
            QMessageBox.warning(self, "Packet", str(exc))


class SequenceDialog(QDialog):
    def __init__(self, db_messages, steps, parent=None, tx_packets=None,
                 actions_only=False, allow_empty=False, failure_steps=None, allow_failure=True):
        super().__init__(parent)
        self.setWindowTitle("명령 시퀀스 설정")
        self.resize(850, 480)
        self.db_messages = db_messages
        self.steps = copy.deepcopy(steps) if steps or allow_empty else [dict(kind='CMD')]
        self.tx_packets = tx_packets
        self.actions_only = actions_only
        self.allow_empty = allow_empty
        self.failure_steps = copy.deepcopy(failure_steps or [])
        layout = QVBoxLayout(self)
        toolbar = QHBoxLayout()
        for title, action in (("생성", self.add), ("삭제", self.delete),
                              ("복사", self.copy_row), ("붙여넣기", self.paste_row),
                              ("위로", lambda: self.move(-1)), ("아래로", lambda: self.move(1))):
            button = QPushButton(title)
            button.clicked.connect(action)
            toolbar.addWidget(button)
        layout.addLayout(toolbar)
        hint = QLabel('CMD: 송신 · DEL: 대기 · START/STOP: 주기 시작/정지' + ('' if actions_only else ' · RCV: 수신 비교'))
        layout.addWidget(hint)
        if actions_only and any(step.get('kind') == 'RCV' for step in self.steps):
            notice = QLabel('기존 RCV 단계가 있습니다. 이 설정에서는 RCV를 사용할 수 없으므로 삭제하거나 종류를 변경하세요.')
            notice.setWordWrap(True)
            layout.addWidget(notice)
        self.btn_failure = QPushButton(f'실패 시 실행할 명령 설정 ({len(self.failure_steps)}단계)')
        self.btn_failure.clicked.connect(self.edit_failure_steps)
        self.btn_failure.setVisible(allow_failure and not actions_only)
        layout.addWidget(self.btn_failure)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["종류", "생성 / 편집", "명령어 / 응답 이름", "명령어 뷰어"])
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.installEventFilter(self)
        self.table.setToolTip('Ctrl+C: 선택 단계 복사 / Ctrl+V: 선택 단계 아래에 붙여넣기')
        self.table.setStyleSheet(
            'QTableWidget::item:selected { background: palette(base); color: palette(text); }')
        self.table.verticalHeader().setMinimumWidth(48)
        self.table.verticalHeader().setHighlightSections(False)
        self.table.horizontalHeader().setHighlightSections(False)
        self.table.itemSelectionChanged.connect(self.update_selection)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        layout.addWidget(self.table)
        buttons = QHBoxLayout()
        for title, action in (("OK", self.accept), ("Cancel", self.reject)):
            button = QPushButton(title)
            button.clicked.connect(action)
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.refresh()

    def refresh(self, selected=0):
        self.table.setRowCount(0)
        for row, step in enumerate(self.steps):
            self.table.insertRow(row)
            self.table.setVerticalHeaderItem(row, QTableWidgetItem(str(row + 1)))
            for col in range(4):
                item = QTableWidgetItem()
                item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                self.table.setItem(row, col, item)
            combo = QComboBox()
            combo.addItems(['CMD', 'DEL', 'START', 'STOP'] if self.actions_only else ['CMD', 'RCV', 'DEL', 'START', 'STOP'])
            if self.actions_only and step['kind'] == 'RCV':
                combo.addItem('RCV')
                combo.model().item(combo.count() - 1).setEnabled(False)
            combo.setCurrentText(step['kind'])
            combo.currentTextChanged.connect(lambda kind, r=row: self.change(r, kind))
            self.table.setCellWidget(row, 0, combo)
            button = QPushButton("시간 설정" if step['kind'] == 'DEL' else ('대상 패킷 선택' if step['kind'] in ('START', 'STOP') else "패킷 생성 / 편집"))
            button.clicked.connect(lambda _, r=row: self.edit(r))
            self.table.setCellWidget(row, 1, button)
            name = QLineEdit(step.get('name', ''))
            name.setPlaceholderText("명령어 / 응답 이름")
            name.textChanged.connect(lambda text, s=step: s.update(name=text))
            self.table.setCellWidget(row, 2, name)
            p = step.get('packet', {})
            if step['kind'] == 'RCV' and p and p['id'] not in self.db_messages.get(p['bus'], {}):
                viewer = QPushButton(f"{format_can_id(p)} · 비트 조건 보기 / 편집")
                viewer.clicked.connect(lambda _, r=row: self.edit(r, True))
            else:
                summary = step.get('summary', '미설정')
                if p:
                    prefix = f"Bus {p['bus']} ID {format_can_id(p)}: "
                    summary = prefix + (bytes(p['data']).hex(' ').upper() if step['kind'] == 'CMD' else summary)
                    if step['kind'] == 'CMD' and 'data_mask' in step:
                        summary = prefix + format_masked(step['data_override'], step['data_mask'], step.get('input_format', 'HEX'))
                viewer = QLineEdit(summary)
                viewer.setReadOnly(True)
            self.table.setCellWidget(row, 3, viewer)
            for widget in (combo, button, name, viewer):
                widget.setProperty('sequenceRow', row)
                widget.installEventFilter(self)
            if self.actions_only and step['kind'] == 'RCV':
                button.setEnabled(False)
                viewer.setEnabled(False)
        if self.steps:
            self.table.selectRow(max(0, min(selected, len(self.steps)-1)))
        self.update_selection()

    def eventFilter(self, obj, event):
        if event.type() in (QEvent.ShortcutOverride, QEvent.KeyPress):
            is_copy = event.matches(QKeySequence.Copy)
            is_paste = event.matches(QKeySequence.Paste)
            text_editing = isinstance(obj, QLineEdit) and (
                not obj.isReadOnly() or (is_copy and obj.hasSelectedText()))
            if (is_copy or is_paste) and not text_editing:
                if event.type() == QEvent.KeyPress:
                    (self.copy_row if is_copy else self.paste_row)()
                event.accept()
                return True
        if event.type() in (QEvent.MouseButtonPress, QEvent.FocusIn) and not getattr(self, '_selecting_row', False):
            row = obj.property('sequenceRow')
            if row is not None and 0 <= row < self.table.rowCount() and row != self.table.currentRow():
                self._selecting_row = True
                try:
                    col = next((c for c in range(4) if self.table.cellWidget(row, c) is obj), 0)
                    index = self.table.model().index(row, col)
                    selection = self.table.selectionModel()
                    selection.setCurrentIndex(index, QItemSelectionModel.NoUpdate)
                    selection.select(index, QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows)
                finally:
                    self._selecting_row = False
        return super().eventFilter(obj, event)

    def update_selection(self):
        rows = {index.row() for index in self.table.selectionModel().selectedRows()}
        for row in range(self.table.rowCount()):
            header = self.table.verticalHeaderItem(row)
            if header is not None:
                header.setText(f'▶ {row + 1}' if row in rows else str(row + 1))
                header.setToolTip('선택된 단계' if row in rows else '')

    def add(self):
        row = self.table.currentRow()
        row = row + 1 if row >= 0 else len(self.steps)
        self.steps.insert(row, dict(kind='CMD'))
        self.refresh(row)

    def copy_row(self):
        row = self.table.currentRow()
        if row >= 0:
            QApplication.clipboard().setText(json.dumps(
                dict(format='pcan-sequence-row', step=self.steps[row]), ensure_ascii=False))

    def paste_row(self):
        try:
            data = json.loads(QApplication.clipboard().text())
            if not isinstance(data, dict) or data.get('format') != 'pcan-sequence-row':
                return
            step = data.get('step')
            if not isinstance(step, dict) or step.get('kind') not in ('CMD', 'RCV', 'DEL', 'START', 'STOP'):
                return
        except (ValueError, TypeError):
            return
        if self.actions_only and step['kind'] == 'RCV':
            QMessageBox.warning(self, 'Sequence', '이 설정에서는 RCV 단계를 사용할 수 없습니다.')
            return
        row = self.table.currentRow()
        row = row + 1 if row >= 0 else len(self.steps)
        self.steps.insert(row, copy.deepcopy(step))
        self.refresh(row)

    def delete(self):
        row = self.table.currentRow()
        if row >= 0:
            self.steps.pop(row)
            self.refresh(row)

    def move(self, direction):
        row = self.table.currentRow()
        target = row + direction
        if row >= 0 and 0 <= target < len(self.steps):
            self.steps[row], self.steps[target] = self.steps[target], self.steps[row]
            self.refresh(target)

    def change(self, row, kind):
        if self.steps[row].get('packet') or self.steps[row].get('summary'):
            if QMessageBox.question(self, "종류 변경", "기존 단계 설정을 지우고 변경할까요?") != QMessageBox.Yes:
                self.refresh(row)
                return
        self.steps[row] = dict(kind=kind, name=self.steps[row].get('name', ''))
        self.refresh(row)

    def edit_failure_steps(self):
        dialog = SequenceDialog(self.db_messages, self.failure_steps, self, self.tx_packets,
                                actions_only=True, allow_empty=True, allow_failure=False)
        dialog.setWindowTitle('실패 시 실행할 명령 (수신 비교 없음)')
        def apply():
            self.failure_steps = copy.deepcopy(dialog.steps)
            self.btn_failure.setText(f'실패 시 실행할 명령 설정 ({len(self.failure_steps)}단계)')
        if getattr(self, '_inline_editing', False):
            show_inline_editor(self, dialog, apply)
        elif dialog.exec_() == dialog.Accepted:
            apply()

    def detail_dialog(self, step):
        if step['kind'] in ('START', 'STOP'):
            return PacketActionDialog(self.tx_packets, step, self)
        if step['kind'] == 'CMD' and self.tx_packets is not None:
            return RegisteredCommandDialog(self.tx_packets, step, self)
        return SequencePacketDialog(self.db_messages, step, self)

    def edit(self, row, bits=False):
        step = self.steps[row]
        if self.actions_only and step['kind'] == 'RCV':
            return
        if getattr(self, '_inline_editing', False):
            if step['kind'] == 'DEL':
                from PyQt5.QtWidgets import QInputDialog
                dlg = QInputDialog(self)
                dlg.setInputMode(QInputDialog.IntInput)
                dlg.setLabelText("대기시간 (ms)")
                dlg.setIntRange(0, 600000)
                dlg.setIntValue(step.get('delay_ms', 0))
                def apply_detail():
                    value = dlg.intValue()
                    self.steps[row] = dict(kind='DEL', delay_ms=value, summary=f'{value} ms', name=step.get('name', ''))
                    self.refresh(row)
            elif bits:
                dlg = BitsDialog(step['packet']['data'], step['mask'], True, self)
                def apply_detail():
                    step['packet']['data'], step['mask'] = dlg.data, dlg.mask
                    step['summary'] = bit_text(dlg.data, dlg.mask)
                    self.refresh(row)
            else:
                dlg = self.detail_dialog(step)
                def apply_detail():
                    dlg.result_step['name'] = step.get('name', '')
                    self.steps[row] = dlg.result_step
                    self.refresh(row)
            show_inline_editor(self, dlg, apply_detail)
            return
        if step['kind'] == 'DEL':
            from PyQt5.QtWidgets import QInputDialog
            value, ok = QInputDialog.getInt(self, "DEL", "대기시간 (ms)", step.get('delay_ms', 0), 0, 600000)
            if ok:
                self.steps[row] = dict(kind='DEL', delay_ms=value, summary=f'{value} ms', name=step.get('name', ''))
        elif bits:
            dlg = BitsDialog(step['packet']['data'], step['mask'], True, self)
            if dlg.exec_() == dlg.Accepted:
                if not any(dlg.mask):
                    QMessageBox.warning(self, "RCV", "Select at least one bit.")
                    return
                step['packet']['data'], step['mask'] = dlg.data, dlg.mask
                step['summary'] = bit_text(dlg.data, dlg.mask)
        else:
            dlg = self.detail_dialog(step)
            if dlg.exec_() == dlg.Accepted:
                dlg.result_step['name'] = step.get('name', '')
                self.steps[row] = dlg.result_step
        self.refresh(row)

    def accept(self):
        try:
            validate_steps(self.steps, actions_only=self.actions_only, allow_empty=self.allow_empty)
            validate_steps(self.failure_steps, actions_only=True, allow_empty=True)
            if self.tx_packets is not None:
                from .packets import validate_tool
                validate_tool(self.tx_packets, dict(behavior='tx', widget_type='sequence',
                              binding=dict(sequence_steps=self.steps, sequence_failure_steps=self.failure_steps)))
            super().accept()
        except Exception as exc:
            QMessageBox.warning(self, "Sequence", str(exc))
