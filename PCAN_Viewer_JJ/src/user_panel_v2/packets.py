from src.bus_config import BUS_IDS, PACKET_BUS_IDS
"""Registered TX packets, bit overlays, and panel-owned transmission state."""
import copy
import uuid
import threading
import can
from PyQt5.QtCore import Qt, QAbstractTableModel, QModelIndex, QItemSelectionModel
from PyQt5.QtGui import QKeySequence
from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QPushButton,
                             QListWidget, QDialogButtonBox, QMessageBox, QComboBox, QSpinBox, QLabel, QCheckBox, QShortcut,
                             QAbstractItemView, QTableView, QHeaderView)
from src.tx_panel import TxPacketDialog
from src.tx_counter import counter_payload, latest_rx_payload
from src.tx_schedule import bus_schedule
from src.db_frame_format import compatible_signal_mapping
from src.crc_utils import calculate_crc16_ccitt_false
from .binding import pack_value, bit_positions
from .commands import command_bindings
from .masked_data import MaskedDataInput


def validate_packet(packet, db_messages):
    if type(packet.get('bus')) is not int or packet['bus'] not in PACKET_BUS_IDS:
        raise ValueError('BUS는 미선택 또는 1~4이어야 합니다.')
    n = packet['length']
    if n not in (list(range(9)) + ([12, 16, 20, 24, 32, 48, 64] if packet['is_fd'] else [])) or len(packet['data']) != n:
        raise ValueError('등록 패킷의 데이터 길이를 확인하세요.')
    if type(packet.get('cycle')) is not int or not 0 <= packet['cycle'] <= 600000:
        raise ValueError('패킷 딜레이는 0~600000 ms로 입력하세요.')
    can.Message(arbitration_id=packet['id'], data=packet['data'], is_fd=packet['is_fd'],
                is_extended_id=packet.get('is_extended_id', packet['id'] > 0x7FF),
                bitrate_switch=packet.get('is_brs', False), check=True)
    if packet.get('crc_type') == 'Hyundai_CRC' and (n < 3 or packet['id'] + 0xF800 > 65535):
        raise ValueError('Hyundai CRC는 3바이트 이상 및 호환 가능한 CAN ID가 필요합니다.')
    if packet.get('signal_counters') and packet['bus']:
        msg = db_messages.get(packet['bus'], {}).get(packet['id'])
        if msg is None:
            raise ValueError('카운트/CRC 신호가 포함된 DBC/SYM을 불러오세요.')
        try:
            counter_payload(msg, bytes(packet['data']), packet['signal_counters'], {})
        except Exception as exc:
            raise ValueError(f'등록 패킷의 카운트/CRC 설정을 확인하세요: {exc}') from exc


def find_packet(packets, binding):
    packet_id = binding.get('packet_id')
    if packet_id:
        return next((p for p in packets if p['packet_id'] == packet_id), None)
    return next((p for p in packets if (p['bus'], p['id']) ==
                 (int(binding.get('bus', 1)), int(binding.get('can_id', 0)))), None)


def bind_packet(binding, packet):
    binding.update(packet_id=packet['packet_id'], bus=packet['bus'], can_id=packet['id'],
                   dlc=packet['length'], is_fd=packet['is_fd'], brs=packet.get('is_brs', False))
    binding.pop('tx_cycle_mode', None)
    binding.pop('tx_cycle_ms', None)


def references_packet(steps, packet):
    for step in steps:
        if step.get('kind') in ('START', 'STOP') and step.get('target_packet_id') == packet['packet_id']:
            return True
        if step.get('kind') == 'CMD':
            p = step.get('packet', {})
            if find_packet([packet], dict(packet_id=p.get('packet_id'), bus=p.get('bus', 1), can_id=p.get('id', 0))):
                return True
    return False


class UnregisteredPacketError(ValueError):
    """An editable tool has no registered transmission target yet."""


def validate_tool(packets, cfg):
    if cfg.get('behavior') != 'tx':
        return
    if cfg.get('widget_type') == 'sequence':
        binding = cfg.get('binding', {})
        for step in binding.get('sequence_steps', []) + binding.get('sequence_failure_steps', []):
            if step.get('kind') in ('START', 'STOP'):
                target = step.get('target_packet_id')
                if target != '*' and not any(p['packet_id'] == target for p in packets):
                    raise ValueError('시퀀스 시작/정지 대상 패킷이 등록되어 있지 않습니다.')
            if step.get('kind') == 'CMD':
                p = step.get('packet', {})
                packet = find_packet(packets, dict(packet_id=p.get('packet_id'), bus=p.get('bus', 1), can_id=p.get('id', 0)))
                if packet is None:
                    raise ValueError('시퀀스 CMD는 등록한 TX 패킷을 선택하세요.')
                step['packet'] = copy.deepcopy(packet)
                if 'data_override' in step:
                    validate_command_data(step['data_override'], packet['length'])
                    if 'data_mask' in step:
                        validate_command_data(step['data_mask'], packet['length'])
                    step['packet']['data'] = list(step['data_override'])
        return
    for binding in command_bindings(cfg):
        packet = find_packet(packets, binding)
        if packet is None:
            raise UnregisteredPacketError(f"{cfg.get('title', 'TX')}: 먼저 TX 패킷을 등록하고 도구에 연결하세요.")
        bind_packet(binding, packet)
        bit_positions(binding, packet['length'])


def validate_command_data(data, length):
    if not isinstance(data, (list, bytes, bytearray)) or len(data) != length or any(type(b) is not int or not 0 <= b <= 255 for b in data):
        raise ValueError('CMD 전용 데이터 길이/HEX 값을 확인하세요. 등록 패킷 길이와 일치해야 합니다.')


def connected_packet_bus(main, packet):
    if packet.get('bus') not in BUS_IDS:
        raise ValueError('CAN BUS 미선택 패킷은 송신하지 않습니다.')
    bus = getattr(main, 'buses', {}).get(packet['bus'])
    if bus is None:
        raise ValueError(f"CAN BUS {packet['bus']}가 연결되지 않았습니다.")
    capabilities = getattr(main, 'bus_capabilities', {}).get(packet['bus'], {})
    if 'is_fd' not in capabilities:
        raise ValueError(f"CAN BUS {packet['bus']}의 연결 타입을 확인할 수 없습니다.")
    if bool(packet.get('is_fd', False)) != bool(capabilities['is_fd']):
        packet_type = 'FD' if packet.get('is_fd') else 'Classic'
        bus_type = 'FD' if capabilities['is_fd'] else 'Classic'
        raise ValueError(f"CAN BUS {packet['bus']} 타입 불일치: 패킷 {packet_type} / 연결 {bus_type}")
    return bus


class PacketRuntime:
    def __init__(self, packet, db_messages, main):
        self.packet = packet
        self.db_messages = db_messages
        self.main = main
        self.overlay = bytearray(packet['data'])
        self.states = {}
        self.alive = 0
        self.lock = threading.RLock()
        self.schedule = bus_schedule(main, packet['bus'])
        self.bus_lock = self.schedule.lock

    def stage(self, binding, value):
        with self.lock:
            updated = pack_value(self.overlay, binding, value)
            changed = updated != self.overlay
            self.overlay = updated
            return changed

    def send(self, data_override=None, data_mask=None, record=True):
        with self.lock, self.bus_lock:
            payload = self._send_locked(data_override, data_mask)
        if record and hasattr(self.main, 'record_tx_activity'):
            p = self.packet
            self.main.record_tx_activity(p['bus'], p['id'], payload, p['is_fd'],
                                         is_ext=p.get('is_extended_id', p['id'] > 0x7FF))
        return payload

    def _send_locked(self, data_override=None, data_mask=None):
        p = self.packet
        bus = connected_packet_bus(self.main, p)
        if data_override is not None:
            validate_command_data(data_override, p['length'])
        payload = bytes(self.overlay if data_override is None else data_override)
        if data_mask is not None:
            validate_command_data(data_mask, p['length'])
            if data_override is None:
                raise ValueError('CMD 마스크에는 송신값이 필요합니다.')
            payload = bytes((old & (~mask & 255)) | (value & mask)
                            for old, value, mask in zip(self.overlay, data_override, data_mask))
        states = self.states
        if p.get('signal_counters'):
            message = self.db_messages.get(p['bus'], {}).get(p['id'])
            if message is None:
                raise ValueError('카운트/CRC 신호가 포함된 DBC/SYM을 불러오세요.')
            received_payload = latest_rx_payload(self.main, p['bus'], p['id'])
            payload, states = counter_payload(message, payload, p['signal_counters'], self.states,
                                              received_payload=received_payload)
        if p.get('crc_type') == 'Hyundai_CRC':
            body = bytes([self.alive]) + payload[3:]
            crc = calculate_crc16_ccitt_false(body + (0xF800 + p['id']).to_bytes(2, 'little'))
            payload = crc.to_bytes(2, 'little') + body
        message = can.Message(arbitration_id=p['id'], data=payload, is_fd=p['is_fd'],
                              bitrate_switch=p.get('is_brs', False),
                              is_extended_id=p.get('is_extended_id', p['id'] > 0x7FF), check=True)
        bus.send(message)
        # The last successful transmission becomes the current packet value.
        # A failed CMD must not replace the value used by periodic transmission.
        self.overlay = bytearray(payload)
        self.states = states
        self.alive = (self.alive + 1) % 256
        return payload


class RegisteredPacketDialog(TxPacketDialog):
    def __init__(self, db_messages, parent=None, packet=None):
        super().__init__(db_messages, parent)
        self.setWindowTitle('유저 패널 TX 패킷 등록')
        self.edit_cycle.setRange(-1, 600000)
        self.edit_cycle.setSpecialValueText('딜레이 입력 필요')
        self.cycle_field.hint.setText('ms   (0 = 도구 값 변경 시 전송)')
        tip = '주기 전송 간격(ms). 0이면 도구 값을 바꿀 때만 전송합니다. 입력 전에는 「딜레이 입력 필요」로 표시됩니다.'
        self.edit_cycle.setToolTip(tip)
        self.cycle_field.hint.setToolTip(tip)
        self.edit_cycle.setValue(-1)
        if packet:
            self.set_packet_data(packet)
        self._preserve_packet_format = bool(packet)

    def get_packet_data(self):
        if self.edit_cycle.value() < 0:
            raise ValueError('패킷 딜레이를 입력하세요. 0은 도구 값이 변경될 때만 전송합니다.')
        packet = super().get_packet_data()
        validate_packet(packet, self.db_messages)
        return packet


class RegisteredCommandDialog(QDialog):
    def __init__(self, packets, step, parent=None, db_messages=None):
        super().__init__(parent)
        self.setWindowTitle('CMD 패킷 / 값 편집')
        self.resize(1060, 720)
        self.packets = packets
        layout = QVBoxLayout(self)
        self.combo = QComboBox()
        for packet in packets:
            self.combo.addItem(f"BUS {packet['bus']} · 0x{packet['id']:X} · {packet.get('symbol', 'N/A')}", packet['packet_id'])
        self.combo.setCurrentIndex(max(0, self.combo.findData(step.get('packet', {}).get('packet_id'))))
        layout.addWidget(self.combo)
        self.use_values = QCheckBox('CMD 전용 값 사용 (HEX 또는 DBC Signals의 Value 편집)')
        self.use_values.setChecked('data_override' in step or not step.get('packet'))
        layout.addWidget(self.use_values)
        self.direct_input = QCheckBox('직접 HEX / BIN / X 입력 (신호 선택 없이)')
        self.direct_input.setChecked('data_mask' in step)
        layout.addWidget(self.direct_input)
        self.masked_input = MaskedDataInput(self)
        layout.addWidget(self.masked_input)
        layout.addWidget(QLabel('송신 성공한 CMD 값은 이후 주기 전송에도 유지됩니다. 체크 해제: 현재 패킷 값 사용. 카운트 → CRC 적용.'))
        self.editor = TxPacketDialog(db_messages if db_messages is not None else getattr(parent, 'db_messages', {}), self)
        self.editor.setWindowFlags(Qt.Widget)
        self.editor.btn_ok.hide()
        self.editor.btn_cancel.hide()
        layout.addWidget(self.editor)
        self.use_values.toggled.connect(self.update_value_mode)
        self.direct_input.toggled.connect(self.update_value_mode)
        self.update_value_mode()
        self.combo.currentIndexChanged.connect(self.load_packet)
        self.load_packet()
        if 'data_override' in step:
            self.editor.edit_data.setText(' '.join(f'{b:02X}' for b in step['data_override']))
            self.editor.on_data_edited()
            self.masked_input.set_data(step['data_override'], step.get('data_mask', [255] * len(step['data_override'])), step.get('input_format', 'HEX'))
        layout.addWidget(QLabel('반복 횟수 (간격은 등록 패킷의 딜레이 사용)'))
        self.repeat = QSpinBox()
        self.repeat.setRange(1, 10000)
        self.repeat.setValue(step.get('repeat', 1))
        layout.addWidget(self.repeat)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def update_value_mode(self):
        active = self.use_values.isChecked()
        self.direct_input.setEnabled(active)
        self.masked_input.setVisible(self.direct_input.isChecked())
        self.masked_input.setEnabled(active)
        self.editor.setEnabled(active and not self.direct_input.isChecked())

    def load_packet(self):
        if self.combo.currentIndex() < 0:
            return
        packet = copy.deepcopy(self.packets[self.combo.currentIndex()])
        previous = getattr(self, '_loaded_packet', None)
        values = mask = None
        manual = False
        if previous and previous['id'] == packet['id'] and previous['bus'] != packet['bus'] and self.use_values.isChecked():
            if previous['length'] != packet['length']:
                self.combo.blockSignals(True)
                self.combo.setCurrentIndex(self.combo.findData(previous['packet_id']))
                self.combo.blockSignals(False)
                QMessageBox.warning(self, 'CMD', '대상 패킷의 길이가 다릅니다. 기존 값 유지를 위해 같은 길이의 패킷을 선택하세요.')
                return
            try:
                if self.direct_input.isChecked():
                    values, mask = self.masked_input.values(previous['length'])
                else:
                    values = self.editor.get_packet_data()['data']
                    mask = [255] * previous['length']
            except ValueError as exc:
                self.combo.blockSignals(True)
                self.combo.setCurrentIndex(self.combo.findData(previous['packet_id']))
                self.combo.blockSignals(False)
                QMessageBox.warning(self, 'CMD', str(exc))
                return
            source = self.editor.current_db_msg
            target = self.editor.db_messages.get(packet['bus'], {}).get(packet['id'])
            manual = not compatible_signal_mapping(source, target)
            packet['data'] = list(values)
            if manual:
                packet['dbc_manual'] = True
        packet.setdefault('count', 0)
        self.editor.set_packet_data(packet)
        self._loaded_packet = copy.deepcopy(packet)
        self.masked_input.set_data(packet['data'], mask if mask is not None else [255] * packet['length'])
        if manual:
            self.direct_input.setChecked(True)
        for widget in (self.editor.combo_bus, self.editor.edit_id, self.editor.combo_symbol,
                       self.editor.combo_type, self.editor.combo_length, self.editor.check_brs,
                       self.editor.crc_combo, self.editor.edit_cycle, self.editor.edit_note):
            widget.setEnabled(False)
        for col in (4, 5, 6):
            self.editor.table_signals.setColumnHidden(col, True)

    def accept(self):
        if self.combo.currentIndex() < 0:
            QMessageBox.warning(self, 'TX 패킷', '먼저 TX 패킷을 등록하세요.')
            return
        packet = copy.deepcopy(self.packets[self.combo.currentIndex()])
        override = None
        mask = None
        if self.use_values.isChecked():
            try:
                if self.direct_input.isChecked():
                    override, mask = self.masked_input.values(packet['length'])
                else:
                    override = list(bytes.fromhex(self.editor.edit_data.text()))
                validate_command_data(override, packet['length'])
            except ValueError as exc:
                QMessageBox.warning(self, 'CMD 값', str(exc))
                return
            packet['data'] = override
        self.result_step = dict(kind='CMD', packet=packet, repeat=self.repeat.value(),
                                summary=f"BUS {packet['bus']} · 0x{packet['id']:X} · {packet['cycle']} ms")
        if override is not None:
            self.result_step['data_override'] = override
        if mask is not None:
            self.result_step['data_mask'] = mask
            self.result_step['input_format'] = self.masked_input.mode
        super().accept()


class PacketTableModel(QAbstractTableModel):
    headers = ('BUS', '주소 (HEX)', '명칭', 'CAN 타입', 'BRS', 'DLC (bytes)', '주기', '노트')

    def __init__(self, registry):
        super().__init__(registry)
        self.registry = registry

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.registry.packets)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.headers)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole:
            return self.headers[section] if orientation == Qt.Horizontal else section + 1

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        p = self.registry.packets[index.row()]
        if role == Qt.UserRole:
            return p['packet_id']
        if role == Qt.TextAlignmentRole:
            return int(Qt.AlignVCenter | (Qt.AlignLeft if index.column() in (2, 7) else Qt.AlignHCenter))
        if role in (Qt.DisplayRole, Qt.ToolTipRole):
            return (str(p['bus']) if p['bus'] else '미선택', f"0x{p['id']:X}", p.get('symbol') or 'N/A',
                    'FD' if p['is_fd'] else 'Classic', 'ON' if p.get('is_brs') else 'OFF',
                    str(p['length']), f"{p['cycle']} ms" if p['cycle'] else '값 변경 시',
                    p.get('note', ''))[index.column()]

    def flags(self, index):
        return (Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsDragEnabled
                if index.isValid() else Qt.ItemIsDropEnabled)

    def supportedDropActions(self):
        return Qt.MoveAction

    def moveRows(self, sourceParent, sourceRow, count, destinationParent, destinationChild):
        packets = self.registry.packets
        if (sourceParent.isValid() or destinationParent.isValid() or count != 1
                or not 0 <= sourceRow < len(packets) or not 0 <= destinationChild <= len(packets)
                or destinationChild in (sourceRow, sourceRow + 1)):
            return False
        if not self.beginMoveRows(sourceParent, sourceRow, sourceRow, destinationParent, destinationChild):
            return False
        packet = packets.pop(sourceRow)
        packets.insert(destinationChild - (destinationChild > sourceRow), packet)
        self.endMoveRows()
        return True


class PacketTableView(QTableView):
    def selectedRows(self):
        return sorted(index.row() for index in self.selectionModel().selectedRows())

    def selectRows(self, rows):
        self.clearSelection()
        for row in rows:
            self.selectionModel().select(self.model().index(row, 0),
                                         QItemSelectionModel.Select | QItemSelectionModel.Rows)
        if rows:
            self.selectionModel().setCurrentIndex(self.model().index(rows[-1], 0), QItemSelectionModel.NoUpdate)

    def mousePressEvent(self, event):
        # Dragging a new row extends selection; dragging an existing selection moves it.
        index = self.indexAt(event.pos())
        self.setDragEnabled(index.isValid() and index.row() in self.selectedRows()
                            and not event.modifiers())
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        self.setDragEnabled(True)

    def currentRow(self):
        return self.currentIndex().row()

    def setCurrentRow(self, row):
        self.selectRows([row] if 0 <= row < self.model().rowCount() else [])

    def dragMoveEvent(self, event):
        if event.source() is self:
            super().dragMoveEvent(event)
            event.setDropAction(Qt.MoveAction)
            event.accept()
        else:
            event.ignore()

    def dropEvent(self, event):
        if event.source() is not self:
            event.ignore()
            return
        rows = self.selectedRows()
        if not rows:
            event.ignore()
            return
        target = self.indexAt(event.pos())
        destination = (target.row() + (event.pos().y() > self.visualRect(target).center().y())
                       if target.isValid() else self.model().rowCount())
        model = self.model()
        packets = model.registry.packets
        selected = [packets[row] for row in rows]
        selected_rows = set(rows)
        remaining = [p for row, p in enumerate(packets) if row not in selected_rows]
        destination -= sum(row < destination for row in rows)
        model.beginResetModel()
        model.registry.packets = remaining[:destination] + selected + remaining[destination:]
        model.endResetModel()
        self.selectRows(list(range(destination, destination + len(selected))))
        event.setDropAction(Qt.MoveAction)
        event.accept()


class PacketRegistryDialog(QDialog):
    def __init__(self, panel):
        super().__init__(panel)
        self.main_window = panel.main_window
        self.panel = panel
        self.packets = copy.deepcopy(panel.tx_packets)
        self.setWindowTitle('유저 패널 TX 패킷')
        self.resize(1050, 480)
        layout = QVBoxLayout(self)
        self.list = PacketTableView()
        self.list.setModel(PacketTableModel(self))
        self.list.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.list.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.list.setAlternatingRowColors(True)
        # 버튼 클릭으로 포커스를 잃어도 선택 행이 뚜렷하게 보이도록 active/inactive 동일 색상 지정
        self.list.setStyleSheet(
            'QTableView::item:selected, QTableView::item:selected:!active '
            '{ background-color: #1f6feb; color: white; }')
        self.list.setWordWrap(False)
        self.list.verticalHeader().setDefaultSectionSize(28)
        self.list.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.list.horizontalHeader().setStretchLastSection(True)
        for column, width in enumerate((65, 110, 210, 90, 65, 100, 110, 180)):
            self.list.setColumnWidth(column, width)
        self.list.setDragDropMode(QAbstractItemView.InternalMove)
        self.list.setDefaultDropAction(Qt.MoveAction)
        self.list.setDragDropOverwriteMode(False)
        self.list.setToolTip('드래그·Shift: 범위 선택 / Ctrl: 개별 선택 / 선택된 행 드래그: 순서 변경\n'
                             'Ctrl+C: 선택 패킷 복사 / Ctrl+V: BUS 미선택으로 일괄 복제 (송신 안 함)')
        self.copy_shortcut = QShortcut(QKeySequence.Copy, self.list)
        self.paste_shortcut = QShortcut(QKeySequence.Paste, self.list)
        for shortcut in (self.copy_shortcut, self.paste_shortcut):
            shortcut.setContext(Qt.WidgetShortcut)
        self.copy_shortcut.activated.connect(self.copy_packet)
        self.paste_shortcut.activated.connect(self.paste_packet)
        layout.addWidget(self.list)
        actions = QHBoxLayout()
        for label, callback in [('패킷 등록', lambda: self.edit_packet(False)),
                                ('선택 패킷 수정', lambda: self.edit_packet(True)),
                                ('선택 패킷 삭제', self.remove_packet),
                                ('위로', lambda: self.move_packets(-1)),
                                ('아래로', lambda: self.move_packets(1))]:
            button = QPushButton(label)
            if label == '선택 패킷 수정':
                self.edit_button = button
            button.clicked.connect(callback)
            actions.addWidget(button)
        layout.addLayout(actions)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.list.doubleClicked.connect(lambda _: self.edit_packet(True))
        self.list.selectionModel().selectionChanged.connect(self._update_edit_button)
        self.list.model().modelReset.connect(self._update_edit_button)
        self.refresh()

    def _update_edit_button(self, *_args):
        self.edit_button.setEnabled(len(self.list.selectedRows()) == 1)

    def refresh(self):
        self.list.model().beginResetModel()
        self.list.model().endResetModel()

    def copy_packet(self):
        rows = self.list.selectedRows()
        if rows:
            self.panel._packet_clipboard = copy.deepcopy([self.packets[row] for row in rows])

    def move_packets(self, direction):
        selected = set(self.list.selectedRows())
        if not selected:
            return
        rows = sorted(selected, reverse=direction > 0)
        for row in rows:
            target = row + direction
            if 0 <= target < len(self.packets) and target not in selected:
                self.packets[row], self.packets[target] = self.packets[target], self.packets[row]
                selected.remove(row)
                selected.add(target)
        self.refresh()
        self.list.selectRows(sorted(selected))
        self.list.scrollTo(self.list.currentIndex())
        self.list.setFocus()

    def paste_packet(self):
        clipboard = getattr(self.panel, '_packet_clipboard', None)
        if not clipboard:
            return
        packets = copy.deepcopy([clipboard] if isinstance(clipboard, dict) else clipboard)
        start = len(self.packets)
        for packet in packets:
            packet.update(packet_id=str(uuid.uuid4()), bus=0, count=0)
            self.packets.append(packet)
        self.refresh()
        self.list.selectRows(list(range(start, len(self.packets))))
        self.list.scrollTo(self.list.currentIndex())

    def edit_packet(self, editing, preset=None):
        row = self.list.currentRow()
        if editing:
            rows = self.list.selectedRows()
            if len(rows) != 1:
                return
            row = rows[0]
        old = self.packets[row] if editing else None
        dlg = RegisteredPacketDialog(self.panel.db_messages, self, old if editing else preset)
        if dlg.exec_() != QDialog.Accepted:
            return
        packet = dlg.get_packet_data()
        packet['packet_id'] = old['packet_id'] if old else str(uuid.uuid4())
        if packet['bus'] and any(p['packet_id'] != packet['packet_id'] and (p['bus'], p['id']) == (packet['bus'], packet['id']) for p in self.packets):
            QMessageBox.warning(self, '패킷 등록', '같은 BUS와 CAN ID의 패킷이 이미 등록되어 있습니다.')
            return
        if editing:
            self.packets[row] = packet
        else:
            self.packets.append(packet)
        self.refresh()
        self.list.setCurrentRow(row if editing else len(self.packets) - 1)

    def remove_packet(self):
        row = self.list.currentRow()
        if row >= 0:
            packet = self.packets[row]
            steps = list(getattr(self.panel, 'init_steps', []))
            for cfg in self.panel.widgets_config:
                binding = cfg.get('binding', {})
                steps += binding.get('sequence_steps', []) + binding.get('sequence_failure_steps', [])
            if references_packet(steps, packet):
                QMessageBox.warning(self, '패킷 삭제', 'Init/시퀀스/실패 처리에서 참조하는 패킷입니다. 해당 명령을 먼저 변경하세요.')
                return
            if any(c.get('behavior') == 'tx' and any(find_packet([packet], b) for b in command_bindings(c)) for c in self.panel.widgets_config):
                QMessageBox.warning(self, '패킷 삭제', '연결된 TX 도구를 먼저 삭제하거나 다른 패킷에 연결하세요.')
                return
            del self.packets[row]
            self.refresh()
