from src.button_icons import decorate_button
from src.bus_config import BUS_IDS
from src.error_dialog import show_error
import os
import re
import datetime
import can
import sys
import json
import math
import base64
import queue
from src.tx_runtime import TxRuntime
from src.periodic_sender import PeriodicSender
from src.db_frame_format import message_is_fd, compatible_signal_mapping
from src.tx_crc import validate_crc, crc_order
from src.tx_crc_dialog import SignalCRCDialog
from PyQt5.QtWidgets import *
from PyQt5.QtCore import Qt, QTimer, QEvent
from PyQt5.QtGui import QFont, QKeySequence
from src.utils import SortableTreeWidgetItem

class TxPacketDialog(QDialog):
    """송신 패킷을 생성하는 다이얼로그 창"""
    def __init__(self, db_messages, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Create Packet")
        self.resize(1000, 500)
        self.db_messages = db_messages # {bus_num: {can_id: msg}}
        self.current_db_msg = None
        self._updating = False
        
        self.init_ui()
        self.on_bus_changed()
        
    def bus_is_fd(self):
        panel = self.parent()
        owner = getattr(panel, "main_window", panel)
        return getattr(owner, "bus_capabilities", {}).get(self.selected_bus(), {}).get("is_fd", False)

    def selected_bus(self):
        return int(self.combo_bus.currentText()) if self.combo_bus.currentText().isdigit() else 0

    def fit_signal_name_column(self):
        if getattr(self, '_fitting_signal_column', False):
            return
        self._fitting_signal_column = True
        try:
            table = self.table_signals
            remaining = table.viewport().width() - sum(table.columnWidth(col) for col in range(1, table.columnCount()))
            table.setColumnWidth(0, max(205, remaining))
        finally:
            self._fitting_signal_column = False

    def eventFilter(self, watched, event):
        if watched is self.table_signals.viewport() and event.type() == QEvent.Resize:
            self.fit_signal_name_column()
        return super().eventFilter(watched, event)

    def add_counter_cells(self, row, sig):
        combo = QComboBox()
        for label, mode in [("None", "none"), ("카운트 업", "up"), ("카운트 다운", "down"),
                    ("순환(up,down,up...)", "alternate"),
                    ("수신값 동기화 + 업", "rx_up"), ("수신값 동기화 + 다운", "rx_down"),
                    ("수신값 동기화 + 왕복", "rx_alternate"),
                    ("CRC-8", "crc8"), ("CRC-16", "crc16")]:
            combo.addItem(label, mode)
        self.table_signals.setCellWidget(row, 4, combo)
        low = -(1 << (sig.length - 1)) if sig.is_signed else 0
        high = (1 << (sig.length - int(sig.is_signed))) - 1
        if getattr(sig, "is_float", False):
            low, high = 0, 100
        self.table_signals.setItem(row, 5, QTableWidgetItem(f"{low} ~ {high}"))
        self.table_signals.setItem(row, 6, QTableWidgetItem("1"))
        combo.currentIndexChanged.connect(self.update_counter_columns)
        self.update_counter_columns()

    def edit_signal_crc(self, row):
        combo = self.table_signals.cellWidget(row, 4)
        mode = combo.currentData()
        sig = self.current_db_msg.get_signal_by_name(self.table_signals.item(row, 0).text())
        configs = combo.property('crc_configs') or {}
        dialog = SignalCRCDialog(sig, mode, int(self.combo_length.currentText() or 0), configs.get(mode), self)
        if dialog.exec_() == QDialog.Accepted:
            configs[mode] = dialog.config
            combo.setProperty('crc_configs', configs)
            self.update_counter_columns()

    def update_counter_columns(self):
        for row in range(self.table_signals.rowCount()):
            combo = self.table_signals.cellWidget(row, 4)
            active = combo is not None and combo.currentData() != "none"
            is_crc = active and combo.currentData() in ('crc8', 'crc16')
            button = self.table_signals.cellWidget(row, 5)
            if is_crc:
                if button is None:
                    button = QPushButton('CRC 설정...')
                    button.clicked.connect(lambda checked=False, r=row: self.edit_signal_crc(r))
                    self.table_signals.setCellWidget(row, 5, button)
                cfg = (combo.property('crc_configs') or {}).get(combo.currentData())
                button.setText(f"설정: [{cfg['start']}:{cfg['start'] + cfg['size'] - 1}]..." if cfg else 'CRC 설정 (필수)...')
            elif button is not None:
                self.table_signals.removeCellWidget(row, 5)
            for col in (5, 6):
                item = self.table_signals.item(row, col)
                if item:
                    item.setFlags((Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable) if active and not is_crc else Qt.NoItemFlags)

    def restore_counters(self, counters):
        for row in range(self.table_signals.rowCount()):
            cfg = counters.get(self.table_signals.item(row, 0).text())
            if cfg:
                combo = self.table_signals.cellWidget(row, 4)
                if cfg['mode'] in ('crc8', 'crc16'):
                    if cfg['mode'] == 'crc16':
                        cfg = dict(cfg)
                        sig = self.current_db_msg.get_signal_by_name(self.table_signals.item(row, 0).text())
                        cfg.setdefault('result_byte_order', sig.byte_order)
                    combo.setProperty('crc_configs', {cfg['mode']: cfg})
                combo.setCurrentIndex(max(0, combo.findData(cfg["mode"])))
                if cfg['mode'] not in ('crc8', 'crc16'):
                    self.table_signals.item(row, 5).setText(f"{cfg['min']} ~ {cfg['max']}")
                    self.table_signals.item(row, 6).setText(str(cfg["step"]))
        self.update_counter_columns()

    def get_counters(self):
        counters = {}
        for row in range(self.table_signals.rowCount()):
            combo = self.table_signals.cellWidget(row, 4)
            if combo is None or combo.currentData() == "none":
                continue
            name = self.table_signals.item(row, 0).text()
            sig = self.current_db_msg.get_signal_by_name(name)
            if combo.currentData() in ('crc8', 'crc16'):
                cfg = (combo.property('crc_configs') or {}).get(combo.currentData())
                if cfg is None:
                    raise ValueError(f'{name}: 옆 셀에서 CRC 설정을 등록하세요.')
                validate_crc(sig, cfg, int(self.combo_length.currentText() or 0))
                counters[name] = cfg
                continue
            parse = float if getattr(sig, "is_float", False) else int
            try:
                low, high = [parse(v.strip()) for v in self.table_signals.item(row, 5).text().split("~")]
                step = parse(self.table_signals.item(row, 6).text().strip())
                if not all(math.isfinite(v) for v in (low, high, step)) or low > high or step <= 0:
                    raise ValueError()
                if not getattr(sig, "is_float", False):
                    raw_low = -(1 << (sig.length - 1)) if sig.is_signed else 0
                    raw_high = (1 << (sig.length - int(sig.is_signed))) - 1
                    if low < raw_low or high > raw_high:
                        raise ValueError()
            except (ValueError, OverflowError):
                raise ValueError(f"{name}: 범위는 원시값 '최소 ~ 최대', 분해능은 양수로 입력하세요. 신호 비트 범위를 초과할 수 없습니다.")
            counters[name] = dict(mode=combo.currentData(), min=low, max=high, step=step)
        if self.current_db_msg:
            crc_order(self.current_db_msg, counters, int(self.combo_length.currentText() or 0))
        return counters

    def accept(self):
        try:
            self.get_packet_data()
        except (ValueError, OverflowError) as exc:
            QMessageBox.warning(self, "입력 오류", str(exc))
            return
        super().accept()

    def _format_phys_val(self, val):
        if isinstance(val, float):
            val_str = f"{round(val, 10):.10f}".rstrip('0')
            if val_str.endswith('.'): val_str += '0'
            if val_str == "-0.0": val_str = "0.0"
            return val_str
        return str(val)
        
    def init_ui(self):
        layout = QVBoxLayout(self)
        
        form_layout = QFormLayout()
        
        # 1. CAN BUS
        self.combo_bus = QComboBox()
        self.combo_bus.addItems([str(b) for b in BUS_IDS])
        self.combo_bus.addItem('미선택', 0)
        self.combo_bus.currentIndexChanged.connect(self.on_bus_changed)
        form_layout.addRow("CAN BUS:", self.combo_bus)
        
        # 2. CAN ID / Symbol
        id_layout = QHBoxLayout()
        self.edit_id = QLineEdit()
        self.edit_id.setPlaceholderText("e.g. 1A2")
        self.edit_id.textEdited.connect(self.on_id_edited)
        
        self.combo_symbol = QComboBox()
        self.combo_symbol.setMinimumWidth(200)
        self.combo_symbol.currentIndexChanged.connect(self.on_symbol_changed)
        
        id_layout.addWidget(self.edit_id)
        id_layout.addWidget(self.combo_symbol)
        form_layout.addRow("CAN ID (HEX) / Symbol:", id_layout)
        
        # 3. Type & Length
        type_len_layout = QHBoxLayout()
        self.combo_type = QComboBox()
        self.combo_type.addItems(["Classic", "FD"])
        self.combo_type.currentIndexChanged.connect(self.on_type_changed)

        self.check_brs = QCheckBox("BRS")
        self.check_brs.setToolTip("Enable Bitrate Switch for CAN FD")
        
        self.combo_length = QComboBox()
        self.combo_length.currentIndexChanged.connect(self.on_length_changed)
        
        type_len_layout.addWidget(QLabel("Type:"))
        type_len_layout.addWidget(self.combo_type)
        type_len_layout.addWidget(self.check_brs)
        type_len_layout.addWidget(QLabel("Length:"))
        type_len_layout.addWidget(self.combo_length)
        type_len_layout.addStretch()
        form_layout.addRow("Format:", type_len_layout)
        
        # 4. Packet Type
        self.crc_combo = QComboBox()
        self.crc_combo.addItems(["적용 안함", "Hyundai_CRC"])
        self.crc_combo.setToolTip(
            "패킷의 유형을 선택합니다.\n\n"
            "- Hyundai_CRC: 전송 데이터의 [0],[1]은 CRC, [2]는 AliveCount로 자동 설정됩니다.\n"
            "  사용자 데이터는 3번 바이트부터 입력합니다.\n\n"
            "- 확장성 참고:\n"
            "  향후 추가될 패킷 타입은 비트 연산을 포함할 수 있습니다. 예를 들어,\n"
            "  Alive Count가 특정 바이트의 0~4비트만 사용하는 경우, 해당 바이트의\n"
            "  전체 데이터를 입력하면 프로그램이 비트마스크 연산으로 카운트를 적용합니다."
        )
        self.crc_combo.currentIndexChanged.connect(self.on_packet_type_changed)
        form_layout.addRow("패킷 타입:", self.crc_combo)
        
        # 5. Data
        self.data_label = QLabel("Data (HEX):")
        self.edit_data = QLineEdit()
        self.edit_data.setPlaceholderText("00 00 00 ...")
        self.edit_data.textEdited.connect(self.on_data_text_edited)
        self.edit_data.editingFinished.connect(self.on_data_edited)
        form_layout.addRow(self.data_label, self.edit_data)
        self.data_range_label = QLabel("")
        self.data_range_label.setStyleSheet("color: gray; font-style: italic;")
        form_layout.addRow("", self.data_range_label)
        
        # 6. Cycle Time
        self.edit_cycle = QSpinBox()
        self.edit_cycle.setRange(0, 600000)
        self.edit_cycle.setValue(0)
        self.edit_cycle.setSuffix(" ms (0=one-shot)")
        form_layout.addRow("Cycle Time:", self.edit_cycle)
        
        # 7. Note
        self.edit_note = QLineEdit()
        form_layout.addRow("Note:", self.edit_note)
        
        layout.addLayout(form_layout)
        
        # DBC Signal Grid
        layout.addWidget(QLabel("DBC Signals:"))
        self.table_signals = QTableWidget(0, 7)
        self.table_signals.setHorizontalHeaderLabels(["Signal Name", "Range", "Value", "Unit", "기능", "범위 (Raw) / CRC 설정", "분해능 (Raw)"])
        
        # Signal Name만 남은 공간에 맞추고, 최소 너비는 기존 293px의 약 70%.
        self.table_signals.horizontalHeader().setSectionResizeMode(0, QHeaderView.Fixed)
        self.table_signals.setColumnWidth(0, 205)
        self.table_signals.setColumnWidth(1, 140)
        self.table_signals.setColumnWidth(2, 60)
        self.table_signals.setColumnWidth(3, 60)
        self.table_signals.setColumnWidth(4, 150)
        self.table_signals.setColumnWidth(5, 180)
        self.table_signals.setColumnWidth(6, 110)
        self.table_signals.viewport().installEventFilter(self)
        self.table_signals.horizontalHeader().sectionResized.connect(self.fit_signal_name_column)
        self.table_signals.setToolTip("Value는 물리값입니다. 자동 증감 범위는 '최소 ~ 최대', 분해능은 Factor/Offset 적용 전 원시값입니다.\n첫 전송은 현재 값을 범위 안으로 제한하여 사용합니다. 이후 전송 성공마다 증감합니다.")
        self.table_signals.cellChanged.connect(self.on_signal_cell_changed)
        layout.addWidget(self.table_signals)
        
        # Buttons
        btn_layout = QHBoxLayout()
        self.btn_ok = QPushButton("OK")
        self.btn_cancel = QPushButton("Cancel")
        self.btn_ok.clicked.connect(self.accept)
        self.btn_cancel.clicked.connect(self.reject)
        btn_layout.addStretch()
        btn_layout.addWidget(self.btn_ok)
        btn_layout.addWidget(self.btn_cancel)
        layout.addLayout(btn_layout)
        
        self.on_type_changed() # Initialize length options
        
    def update_data_range_label(self):
        """사용자 데이터 입력 범위를 알려주는 라벨을 업데이트합니다."""
        packet_type = self.crc_combo.currentText()
        try:
            total_length = int(self.combo_length.currentText())
        except (ValueError, AttributeError):
            self.data_range_label.setText("")
            return

        if packet_type == "Hyundai_CRC":
            # 사용자에게 0,1,2 바이트가 자동 계산됨을 안내합니다.
            self.data_range_label.setText("패킷 타입 적용 시: 데이터 [0], [1]은 CRC, [2]는 AliveCount로 자동 계산되어 덮어쓰기됩니다.")
        else: # "적용 안함"
            self.data_range_label.setText("")

    def on_packet_type_changed(self, index):
        """패킷 타입 콤보박스 선택 변경 시 UI 갱신"""
        crc_type = self.crc_combo.currentText()
        is_crc_applied = (crc_type != "적용 안함")
        if is_crc_applied:
            self.data_label.setText("Data (HEX):")
            self.edit_data.setPlaceholderText("00 00 00 ... (0,1,2 바이트는 자동 계산)")
            # DBC 심볼을 사용하는 경우 CRC 기능과 충돌할 수 있으므로, CRC 사용 시 심볼 선택을 비활성화합니다.
            self.combo_symbol.setCurrentIndex(0)
            self.combo_symbol.setEnabled(False)
            self.table_signals.setRowCount(0)
            self.table_signals.setEnabled(False)
        else: # Unchecked
            self.data_label.setText("Data (HEX):")
            self.edit_data.setPlaceholderText("00 00 00 ...")
            self.combo_symbol.setEnabled(True)
            self.table_signals.setEnabled(True)
        self.on_type_changed()
        self.update_data_range_label()

    def on_bus_changed(self):
        if self._updating:
            return
        if not getattr(self, '_preserve_packet_format', False):
            return self._refresh_bus_fields()
        packet = TxPacketDialog.get_packet_data(self)
        source = self.current_db_msg
        target = self.db_messages.get(packet['bus'], {}).get(packet['id'])
        names = self.bus_signal_names() if hasattr(self, 'bus_signal_names') else None
        if source is not None and not compatible_signal_mapping(source, target, names):
            packet['dbc_manual'] = True
            packet['signal_counters'] = {}
        self.set_packet_data(packet)
        self.update_data_range_label()

    def _refresh_bus_fields(self):
        if self._updating: return
        self._updating = True
        bus_num = self.selected_bus()
        self.combo_symbol.clear()
        self.combo_symbol.addItem("Direct Input (N/A)", None)
        
        if bus_num in self.db_messages:
            for can_id, msg in sorted(self.db_messages[bus_num].items()):
                self.combo_symbol.addItem(f"{msg.name} (0x{can_id:X})", can_id)
                
        self.combo_symbol.setCurrentIndex(0)
        self.current_db_msg = None
        self.table_signals.setRowCount(0)
        self._updating = False
        self.combo_type.setCurrentText("FD" if self.bus_is_fd() else "Classic")
        self.on_type_changed()
        self.on_id_edited()
        
    def on_symbol_changed(self):
        if self._updating: return
        self._dbc_manual = False
        can_id = self.combo_symbol.currentData()
        if can_id is not None:
            self._updating = True
            self.edit_id.setText(f"{can_id:X}")
            bus_num = self.selected_bus()
            self.current_db_msg = self.db_messages[bus_num][can_id]
            
            # DBC 메시지에 맞게 Length와 Type 조정
            dlc = self.current_db_msg.length
            if message_is_fd(self.current_db_msg) or self.bus_is_fd():
                self.combo_type.setCurrentIndex(1) # FD
            else:
                self.combo_type.setCurrentIndex(0) # Classic
            self._updating = False
            self.on_type_changed()
            self._updating = True
            dlc = next((n for n in [*range(9), 12, 16, 20, 24, 32, 48, 64] if n >= dlc), dlc)
            idx = self.combo_length.findText(str(dlc))
            if idx >= 0:
                self.combo_length.setCurrentIndex(idx)
            
            # Signal Grid 셋업
            # 시그널 목록을 start_bit 기준으로 정렬하여 일관성 유지
            # .sym 파일 등 start_bit 속성이 없는 경우를 대비하여 에러 핸들링 추가
            try:
                sorted_signals = sorted(self.current_db_msg.signals, key=lambda s: s.start_bit)
            except AttributeError:
                sorted_signals = self.current_db_msg.signals # 정렬 불가 시 원본 순서 유지

            self.table_signals.setRowCount(0)
            for sig in sorted_signals:
                row = self.table_signals.rowCount()
                self.table_signals.insertRow(row)
                
                item_name = QTableWidgetItem(sig.name)
                item_name.setFlags(item_name.flags() & ~Qt.ItemIsEditable)
                self.table_signals.setItem(row, 0, item_name)
                
                range_str = "-"
                if not getattr(sig, 'is_float', False):
                    if sig.is_signed:
                        raw_min = -(2 ** (sig.length - 1))
                        raw_max = (2 ** (sig.length - 1)) - 1
                    else:
                        raw_min = 0
                        raw_max = (2 ** sig.length) - 1
                    scale = getattr(sig, 'scale', 1.0)
                    offset = getattr(sig, 'offset', 0.0)
                    
                    v1 = raw_min * scale + offset
                    v2 = raw_max * scale + offset
                    phys_min = min(v1, v2)
                    phys_max = max(v1, v2)
                    
                    range_str = f"{self._format_phys_val(phys_min)} ~ {self._format_phys_val(phys_max)}"
                    
                item_range = QTableWidgetItem(range_str)
                item_range.setFlags(item_range.flags() & ~Qt.ItemIsEditable)
                self.table_signals.setItem(row, 1, item_range)
                
                initial_val = sig.initial if sig.initial is not None else 0
                val_str = self._format_phys_val(initial_val)
                item_val = QTableWidgetItem(val_str)
                self.table_signals.setItem(row, 2, item_val)
                
                item_unit = QTableWidgetItem(sig.unit if sig.unit else "")
                item_unit.setFlags(item_unit.flags() & ~Qt.ItemIsEditable)
                self.table_signals.setItem(row, 3, item_unit)
                self.add_counter_cells(row, sig)
                
            self.update_data_from_signals()
            self._updating = False
        else:
            self.current_db_msg = None
            self.table_signals.setRowCount(0)
            
    def on_id_edited(self):
        if self._updating: return
        if getattr(self, '_dbc_manual', False):
            return
        id_text = self.edit_id.text().strip()
        try: can_id = int(id_text, 16)
        except ValueError:
            self.combo_symbol.setCurrentIndex(0)
            self.current_db_msg = None
            self.table_signals.setRowCount(0)
            return
            
        bus_num = self.selected_bus()
        if bus_num in self.db_messages and can_id in self.db_messages[bus_num]:
            idx = self.combo_symbol.findData(can_id)
            if idx >= 0:
                self._updating = True
                self.combo_symbol.setCurrentIndex(idx)
                self._updating = False
                self.on_symbol_changed()
        else:
            self._updating = True
            self.combo_symbol.setCurrentIndex(0)
            self._updating = False
            self.current_db_msg = None
            self.table_signals.setRowCount(0)
            
    def on_type_changed(self):
        if self._updating: return
        self._updating = True
        current_len = self.combo_length.currentText()
        self.combo_length.clear()
        
        is_crc_applied = (self.crc_combo.currentText() != "적용 안함")
        min_len = 3 if is_crc_applied else 0

        if self.combo_type.currentText() == "Classic":
            lengths = [str(i) for i in range(9) if i >= min_len]
            self.combo_length.addItems(lengths)
            self.check_brs.setEnabled(False)
            self.check_brs.setChecked(False)
        else:
            fd_lens = [i for i in range(9)] + [12, 16, 20, 24, 32, 48, 64]
            lengths = [str(l) for l in fd_lens if l >= min_len]
            self.combo_length.addItems(lengths)
            self.check_brs.setEnabled(True)
            
        idx = self.combo_length.findText(current_len)
        if idx >= 0: self.combo_length.setCurrentIndex(idx)
        elif self.combo_length.count() > 0: self.combo_length.setCurrentIndex(0)

        self._updating = False
        self.on_length_changed()
        
    def on_length_changed(self):
        if self._updating: return
        self._updating = True
        try:
            total_length = int(self.combo_length.currentText())
        except ValueError:
            total_length = 0
            
        data_text = self.edit_data.text().replace(" ", "")
        data_bytes = [data_text[i:i+2] for i in range(0, len(data_text), 2)]
        
        # CRC 적용 여부와 관계없이 항상 전체 길이를 기준으로 데이터 필드를 조정합니다.
        target_len = total_length
        
        if len(data_bytes) < target_len:
            data_bytes.extend(["00"] * (target_len - len(data_bytes)))
        elif len(data_bytes) > target_len:
            data_bytes = data_bytes[:target_len]
            
        new_text = " ".join(data_bytes)
        self.edit_data.setText(new_text)
        self._last_data_text_len = len(new_text)
        self._updating = False
        self.update_data_range_label()

    def on_data_text_edited(self, text):
        if self._updating: return
        self._updating = True
        
        is_addition = len(text) > getattr(self, '_last_data_text_len', 0)
        
        cursor_pos = self.edit_data.cursorPosition()
        text_before_cursor = text[:cursor_pos]
        chars_before_cursor = len(text_before_cursor.replace(" ", ""))
        
        raw_text = text.replace(" ", "")
        
        try: total_len = int(self.combo_length.currentText())
        except: total_len = 8
        max_bytes = total_len

        raw_text = raw_text[:max_bytes * 2]
        
        valid_hex = "0123456789abcdefABCDEF"
        formatted_chunks = []
        for i in range(0, len(raw_text), 2):
            chunk = raw_text[i:i+2]
            if not all(c in valid_hex for c in chunk):
                chunk = "00" if len(chunk) == 2 else "0"
            formatted_chunks.append(chunk)
            
        new_text = " ".join(formatted_chunks).upper()
        
        if is_addition and len(raw_text) % 2 == 0 and len(raw_text) > 0 and len(raw_text) < max_bytes * 2:
            if cursor_pos == len(text):
                new_text += " "
                
        self.edit_data.setText(new_text)
        self._last_data_text_len = len(new_text)
        
        if cursor_pos == len(text):
            self.edit_data.setCursorPosition(len(new_text))
        else:
            new_pos = next((i for i, c in enumerate(new_text) if sum(1 for x in new_text[:i+1] if x != ' ') > chars_before_cursor), len(new_text))
            self.edit_data.setCursorPosition(new_pos)
            
        self._updating = False

    def on_data_edited(self):
        if self._updating: return
        data_text = self.edit_data.text().replace(" ", "")
        data_bytes = [data_text[i:i+2] for i in range(0, len(data_text), 2)]
        
        try: total_length = int(self.combo_length.currentText())
        except: total_length = 8
        target_len = total_length
        
        if len(data_bytes) < target_len: data_bytes.extend(["00"] * (target_len - len(data_bytes)))
        elif len(data_bytes) > target_len: data_bytes = data_bytes[:target_len]

        formatted_data = " ".join(f"{int(b, 16):02X}" if b else "00" for b in data_bytes)
        self.edit_data.setText(formatted_data)
        self._last_data_text_len = len(formatted_data)
        
        if self.current_db_msg:
            try:
                # 사용자가 입력한 전체 데이터를 기준으로 DBC 디코딩을 수행합니다.
                # 전송 시에는 CRC 타입에 따라 앞 3바이트가 덮어쓰기됩니다.
                full_raw_bytes = bytes(int(b, 16) for b in data_bytes)

                decoded = self.current_db_msg.decode(full_raw_bytes, decode_choices=False)
                self._updating = True
                for row in range(self.table_signals.rowCount()):
                    sig_name = self.table_signals.item(row, 0).text()
                    if sig_name in decoded:
                        val = decoded[sig_name]
                        self.table_signals.item(row, 2).setText(self._format_phys_val(val))
                self._updating = False
            except Exception:
                pass
                
    def on_signal_cell_changed(self, row, col):
        if self._updating or col != 2 or not self.current_db_msg: return
        self.update_data_from_signals()
        
    def update_data_from_signals(self):
        if not self.current_db_msg: return
        self._updating = True
        
        # 1. 기존 Data(HEX)를 디코딩하여 수정하지 않은 시그널의 베이스라인(기존 값)을 유지합니다.
        data_text = self.edit_data.text().replace(" ", "")
        data_bytes = [int(data_text[i:i+2], 16) for i in range(0, len(data_text), 2)] if data_text else []
        try: max_bytes = int(self.combo_length.currentText())
        except: max_bytes = 8
        if len(data_bytes) < max_bytes: data_bytes.extend([0] * (max_bytes - len(data_bytes)))
        
        data_dict = {}
        try: data_dict = self.current_db_msg.decode(bytes(data_bytes[:max_bytes]), decode_choices=False)
        except Exception: pass
        
        for row in range(self.table_signals.rowCount()):
            sig_name = self.table_signals.item(row, 0).text()
            val_text = self.table_signals.item(row, 2).text()
            try:
                val_text_strip = val_text.strip()
                if not val_text_strip or val_text_strip == "-":
                    continue # 빈 값이나 '-' 표기는 무시하고 베이스라인 값 유지
                    
                if val_text_strip.lower().startswith('0x'):
                    val = int(val_text_strip, 16) # 16진수 정수 처리
                else:
                    try: val = int(val_text_strip) # 10진수 정수 처리
                    except ValueError:
                        try: val = float(val_text_strip) # 실수(float/double) 처리
                        except ValueError: val = val_text_strip # Enum 문자열 등 처리
                        
                try:
                    # 입력된 값이 시그널의 비트(Bit) 허용 크기를 초과할 경우 자동으로 최댓값/최솟값으로 보정(Clamping)
                    sig_def = self.current_db_msg.get_signal_by_name(sig_name)
                    if isinstance(val, (int, float)) and sig_def:
                        if not getattr(sig_def, 'is_float', False):
                            if sig_def.is_signed:
                                raw_min = -(2 ** (sig_def.length - 1))
                                raw_max = (2 ** (sig_def.length - 1)) - 1
                            else:
                                raw_min = 0
                                raw_max = (2 ** sig_def.length) - 1
                                
                            scale = getattr(sig_def, 'scale', 1.0)
                            offset = getattr(sig_def, 'offset', 0.0)
                            
                            v1 = raw_min * scale + offset
                            v2 = raw_max * scale + offset
                            phys_min = min(v1, v2)
                            phys_max = max(v1, v2)
                            
                            original_val = val
                            if val < phys_min: val = phys_min
                            elif val > phys_max: val = phys_max
                            
                            if val != original_val:
                                self.table_signals.item(row, 2).setText(self._format_phys_val(val))
                except Exception:
                    pass

                data_dict[sig_name] = val
            except: pass
                
        # 2. 딕셔너리에 여전히 누락된 시그널이 있다면 초기값/최솟값/0 으로 채워서 인코딩 에러 방지
        for sig in self.current_db_msg.signals:
            if sig.name not in data_dict:
                data_dict[sig.name] = sig.initial if sig.initial is not None else (sig.minimum if sig.minimum is not None else 0)
                
        try:
            # 3. strict=False 옵션을 주어 Min/Max 허용치 초과 시 에러 대신 허용치 내로 Clamping 자동 적용
            try: raw_bytes = self.current_db_msg.encode(data_dict, strict=False)
            except TypeError:
                raw_bytes = self.current_db_msg.encode(data_dict) # 구버전 cantools 호환
                
            length = next((n for n in [*range(9), 12, 16, 20, 24, 32, 48, 64] if n >= len(raw_bytes)), len(raw_bytes))
            raw_bytes = raw_bytes.ljust(length, b'\x00')
            idx = self.combo_length.findText(str(length))
            if idx >= 0:
                self.combo_length.setCurrentIndex(idx)
                
            formatted_data = " ".join(f"{b:02X}" for b in raw_bytes)
            self.edit_data.setText(formatted_data)
            self._last_data_text_len = len(formatted_data)
        except Exception: pass
        self._updating = False

    def get_packet_data(self):
        bus_num = self.selected_bus()
        can_id_text = self.edit_id.text().strip()
        can_id = int(can_id_text, 16) if can_id_text else 0
        is_fd = (self.combo_type.currentText() == "FD")
        is_brs = self.check_brs.isChecked() if is_fd else False
        total_length = int(self.combo_length.currentText()) if self.combo_length.currentText() else 0
        
        data_text = self.edit_data.text().replace(" ", "")
        # CRC 적용 여부와 관계없이 사용자가 입력한 전체 데이터를 저장합니다.
        # 전송 시점에 TxPacketItem에서 CRC 타입에 따라 데이터를 재구성합니다.
        full_data = [int(data_text[i:i+2], 16) for i in range(0, len(data_text), 2)] if data_text else []
        
        if len(full_data) < total_length:
            full_data.extend([0] * (total_length - len(full_data)))
        elif len(full_data) > total_length:
            full_data = full_data[:total_length]
            
        # 저장될 crc_type 값을 최종 결정합니다.
        crc_type_text = self.crc_combo.currentText()
        crc_type = "N/A" if crc_type_text == "적용 안함" else crc_type_text

        symbol = self.combo_symbol.currentText()
        symbol = "N/A" if "Direct Input" in symbol else symbol.split(" (")[0]
            
        return {
            "bus": bus_num, "id": can_id, "is_fd": is_fd, "is_brs": is_brs, "length": total_length,
            "data": full_data, "cycle": self.edit_cycle.value(),
            "signal_counters": self.get_counters(),
            **({'dbc_manual': True} if getattr(self, '_dbc_manual', False) else {}),
            "note": self.edit_note.text().strip(), "symbol": symbol, "count": 0, "crc_type": crc_type
        }

    def set_packet_data(self, data):
        self._preserve_packet_format = True
        self._dbc_manual = data.get('dbc_manual', False)
        self._updating = True
        
        # 1. CAN BUS 설정
        idx = self.combo_bus.findText(str(data["bus"]) if data['bus'] else '미선택')
        if idx >= 0: self.combo_bus.setCurrentIndex(idx)
        
        # 1.5 CRC 설정
        crc_type = data.get("crc_type", "N/A")
        if crc_type == "Hyundai_CRC":
            self.crc_combo.setCurrentText("Hyundai_CRC")
        else:
            self.crc_combo.setCurrentText("적용 안함")
        
        # 2. Symbol Combo 수동 갱신 (이벤트 억제 상태)
        self.combo_symbol.clear()
        self.combo_symbol.addItem("Direct Input (N/A)", None)
        bus_num = data["bus"]
        if bus_num in self.db_messages:
            for can_id, msg in sorted(self.db_messages[bus_num].items()):
                self.combo_symbol.addItem(f"{msg.name} (0x{can_id:X})", can_id)
                
        self.edit_id.setText(f"{data['id']:X}" if data["id"] > 0 else "")
        
        idx = self.combo_symbol.findData(data["id"])
        if idx >= 0 and not self._dbc_manual:
            self.combo_symbol.setCurrentIndex(idx)
            self.current_db_msg = self.db_messages[bus_num][data["id"]]
            
            # 하위 시그널 그리드 행 생성 (값은 이후 on_data_edited를 통해 원본 Hex값으로 매칭됨)
            self.table_signals.setRowCount(0)
            for sig in self.current_db_msg.signals:
                row = self.table_signals.rowCount()
                self.table_signals.insertRow(row)
                
                item_name = QTableWidgetItem(sig.name)
                item_name.setFlags(item_name.flags() & ~Qt.ItemIsEditable)
                self.table_signals.setItem(row, 0, item_name)
                
                range_str = "-"
                if not getattr(sig, 'is_float', False):
                    if sig.is_signed:
                        raw_min = -(2 ** (sig.length - 1))
                        raw_max = (2 ** (sig.length - 1)) - 1
                    else:
                        raw_min = 0
                        raw_max = (2 ** sig.length) - 1
                    scale = getattr(sig, 'scale', 1.0)
                    offset = getattr(sig, 'offset', 0.0)
                    
                    v1 = raw_min * scale + offset
                    v2 = raw_max * scale + offset
                    phys_min = min(v1, v2)
                    phys_max = max(v1, v2)
                    
                    range_str = f"{self._format_phys_val(phys_min)} ~ {self._format_phys_val(phys_max)}"
                    
                item_range = QTableWidgetItem(range_str)
                item_range.setFlags(item_range.flags() & ~Qt.ItemIsEditable)
                self.table_signals.setItem(row, 1, item_range)
                
                self.table_signals.setItem(row, 2, QTableWidgetItem("-"))
                
                item_unit = QTableWidgetItem(sig.unit if sig.unit else "")
                item_unit.setFlags(item_unit.flags() & ~Qt.ItemIsEditable)
                self.table_signals.setItem(row, 3, item_unit)
                self.add_counter_cells(row, sig)
        else:
            self.combo_symbol.setCurrentIndex(0)
            self.current_db_msg = None
            self.table_signals.setRowCount(0)
            
        # 3. Type 및 Length
        is_fd = data.get("is_fd", False)
        self.combo_type.setCurrentText("FD" if is_fd else "Classic")
        is_brs = data.get("is_brs", False)
        self.check_brs.setChecked(is_brs)
        self.check_brs.setEnabled(is_fd)

        self.combo_length.clear()
        if is_fd:
            self.combo_length.addItems([str(i) for i in range(9)] + ["12", "16", "20", "24", "32", "48", "64"])
        else:
            self.combo_length.addItems([str(i) for i in range(9)])
            
        idx = self.combo_length.findText(str(data["length"]))
        if idx >= 0: self.combo_length.setCurrentIndex(idx)
        
        # 4. Data (HEX)
        data_hex = " ".join(f"{b:02X}" for b in data["data"])
        self.edit_data.setText(data_hex)
        self._last_data_text_len = len(data_hex)
        
        # 5. Cycle Time & Note
        self.edit_cycle.setValue(data["cycle"])
        self.edit_note.setText(data["note"])
        self.on_packet_type_changed(self.crc_combo.currentIndex())
        
        self._updating = False
        self.restore_counters(data.get("signal_counters", {}))
        self.on_data_edited() # Data Hex를 바탕으로 Grid Value 자동 갱신 트리거
        self.update_data_range_label()

class _TxTimer:
    """Compatibility handle for the main TX start/stop controls."""
    def __init__(self, item):
        self.item = item

    def start(self, interval=None):
        self.item.start_timer(interval)

    def stop(self):
        self.item.stop_timer()

    def isActive(self):
        task = self.item._periodic_task
        return task is not None and task.isActive()


class TxPacketItem(SortableTreeWidgetItem):
    """GUI owner of a packet; periodic CAN work runs on a bus worker."""
    def __init__(self, parent, data, tx_panel):
        super().__init__(parent)
        self.tx_panel = tx_panel
        self.packet_data = data
        self._periodic_task = None
        self.timer = _TxTimer(self)
        self.is_running = False
        self.counter_states = {}
        if data.get('crc_type') == 'Hyundai_CRC':
            data['alive_counter'] = 0
        self.update_ui()

    def update_ui(self):
        d = self.packet_data
        self.setText(0, str(d["bus"]) if d['bus'] else '미선택 (송신 안 함)')
        can_id_str = f"{d['id']:03X}h" if d['id'] <= 0x7FF else f"{d['id']:X}h"
        self.setText(1, can_id_str)
        self.setText(2, "FD" if d["is_fd"] else "Classic")
        self.setText(3, "On" if d.get("is_brs", False) else "Off")
        self.setText(4, str(d["length"]))
        self.setText(5, d.get("crc_type", "N/A"))
        self.setText(6, d["symbol"])
        self.setText(7, " ".join(f"{b:02X}" for b in d["data"]))
        self.setText(8, str(d["cycle"]))
        self.setText(9, str(d["count"]))
        self.setText(10, d["note"])
        
    def _can_send(self):
        d = self.packet_data
        if d.get('bus') not in BUS_IDS or not self.tx_panel.buses.get(d['bus']):
            return False
        capabilities = self.tx_panel.main_window.bus_capabilities.get(d['bus'], {})
        return d.get('length', 0) <= 8 or capabilities.get('is_fd', False)

    def _sync_runtime(self, runtime):
        # Only called on the GUI thread, including after stop joins an in-flight send.
        with runtime.lock:
            states = dict(runtime.states)
            alive = runtime.packet.get('alive_counter')
            count = runtime.packet['count'] + runtime.sent
            payload = runtime.last_payload
        # Qt sorting/painting and item-change callbacks must not hold the send lock.
        self.counter_states = states
        if alive is not None:
            self.packet_data['alive_counter'] = alive
        self.packet_data['count'] = count
        self.setText(7, ' '.join(f'{b:02X}' for b in payload))
        self.setText(9, str(count))

    def send_packet(self):
        if not self._can_send():
            self.stop_timer()
            self.tx_panel._update_action_button(self)
            return
        try:
            task = self._periodic_task
            runtime = task.runtime if task is not None else TxRuntime(
                self.packet_data, self.tx_panel, self.counter_states)
            payload = runtime.send()
            self._sync_runtime(runtime)
            if hasattr(self.tx_panel.main_window, 'record_tx_activity'):
                p = runtime.packet
                self.tx_panel.main_window.record_tx_activity(p['bus'], p['id'], payload, p['is_fd'],
                                                             is_ext=runtime.is_extended)
        except Exception as exc:
            print(f'Tx Error: {exc}')
            if self.is_running:
                self.stop_timer()
                self.tx_panel._update_action_button(self)

    def start_timer(self, interval=None):
        if self._periodic_task is not None:
            self.stop_timer()
        if not self._can_send():
            self.is_running = False
            return
        cycle = self.packet_data['cycle'] if interval is None else interval
        if cycle <= 0:
            self.send_packet()
            return
        runtime = TxRuntime(self.packet_data, self.tx_panel, self.counter_states)
        runtime.packet['cycle'] = cycle
        runtime.item = self
        self._periodic_task = self.tx_panel.start_periodic(runtime)
        self.is_running = True

    def stop_timer(self):
        task = self._periodic_task
        if task is not None:
            task.stop()
            self._sync_runtime(task.runtime)
            self._periodic_task = None
        self.is_running = False
        if hasattr(self.tx_panel, '_drain_periodic_events'):
            self.tx_panel._drain_periodic_events()

class TxPanel(QWidget):
    """메인 창 하단에 들어갈 송신 제어 전체 패널"""
    def __init__(self, buses, db_messages, parent=None):
        super().__init__(parent)
        self.buses = buses
        self.db_messages = db_messages
        self.main_window = parent
        self._is_loading = False
        self._periodic_senders = {}
        self._periodic_records = queue.Queue(maxsize=20000)
        self._periodic_errors = queue.SimpleQueue()
        self._periodic_dropped_records = 0
        self._periodic_monitor = QTimer(self)
        self._periodic_monitor.setInterval(20)
        self._periodic_monitor.timeout.connect(self._drain_periodic_events)
        self.init_ui()
        
    def start_periodic(self, runtime):
        bus = runtime.packet['bus']
        if bus not in self._periodic_senders:
            self._periodic_senders[bus] = PeriodicSender(
                bus, self._periodic_completed,
                lambda task, error: self._periodic_errors.put((task, error)))
        task = self._periodic_senders[bus].add(runtime)
        task.reported_missed = 0
        self._periodic_monitor.start()
        return task

    def _periodic_completed(self, task, payload, timestamp):
        # Worker callback: never touch Qt, files or logging here.
        try:
            self._periodic_records.put_nowait((task, payload, timestamp, task.missed,
                                               task.max_lateness_ms))
        except queue.Full:
            self._periodic_dropped_records += 1

    def _tx_warning(self, bus, message):
        if bus is not None and hasattr(self.main_window, 'log_bus_event'):
            self.main_window.log_bus_event(bus, message, 'WARN')
        elif hasattr(self.main_window, 'panel_system_event'):
            self.main_window.panel_system_event(message, 'WARN')

    def _drain_periodic_events(self):
        while not self._periodic_errors.empty():
            task, error = self._periodic_errors.get()
            item = task.runtime.item
            if item._periodic_task is task:
                item.stop_timer()
                self._update_action_button(item)
            self._tx_warning(task.runtime.packet['bus'], f'TX 중지: {error}')
        changed = set()
        for _ in range(500):
            try:
                task, payload, timestamp, missed, lateness = self._periodic_records.get_nowait()
            except queue.Empty:
                break
            p = task.runtime.packet
            if missed > task.reported_missed:
                self._tx_warning(p['bus'], f"TX 주기 지연: 0x{p['id']:X}, "
                                 f'누락 {missed - task.reported_missed}회 (누적 {missed}), '
                                 f'최대 지연 {lateness:.1f} ms')
                task.reported_missed = missed
            if hasattr(self.main_window, 'record_tx_activity'):
                self.main_window.record_tx_activity(p['bus'], p['id'], payload, p['is_fd'], timestamp,
                                                    is_ext=task.runtime.is_extended)
            if task.runtime.item._periodic_task is task:
                changed.add(task)
        for task in changed:
            task.runtime.item._sync_runtime(task.runtime)
        if self._periodic_dropped_records:
            self._tx_warning(None, f'화면 처리 지연으로 TX 기록 {self._periodic_dropped_records}개 생략 (송신은 계속됨)')
            self._periodic_dropped_records = 0
        if not any(sender.tasks for sender in self._periodic_senders.values()) and self._periodic_records.empty():
            self._periodic_monitor.stop()

    def closeEvent(self, event):
        self.stop_all_timers()
        super().closeEvent(event)

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        
        self.toolbar = QHBoxLayout()
        self.btn_add = QPushButton("Create Packet")
        self.btn_load = QPushButton("Load Tx List")
        self.btn_save = QPushButton("Save Tx List")
        self.btn_clear_all = QPushButton("Clear All Packets")
        
        decorate_button(self.btn_add, 'packet_add', '송신 패킷을 만듭니다.', 'Add Packet')
        decorate_button(self.btn_load, 'document_open', '송신 패킷 목록을 불러옵니다.', 'Load')
        decorate_button(self.btn_save, 'save', '송신 패킷 목록을 저장합니다.', 'Save')
        decorate_button(self.btn_clear_all, 'trash', '송신 패킷을 모두 삭제합니다.', 'Clear')
        self.btn_add.clicked.connect(self.on_add_packet)
        self.btn_load.clicked.connect(self.on_load_packets)
        self.btn_save.clicked.connect(self.on_save_packets)
        self.btn_clear_all.clicked.connect(self.on_clear_all_packets)
        
        self.toolbar.addWidget(self.btn_add)
        self.toolbar.addWidget(self.btn_clear_all)
        self.toolbar.addSpacing(20)
        self.toolbar.addWidget(self.btn_load)
        self.toolbar.addWidget(self.btn_save)
        self.toolbar.addStretch()
        layout.addLayout(self.toolbar)
        
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["CAN BUS", "CAN ID", "CAN Type", "BRS", "Length", "Packet Type", "DBC Symbol", "Data", "Cycle (ms)", "Count", "Note", "Action"])
        self.tree.setColumnWidth(0, 70)
        self.tree.setColumnWidth(1, 80)
        self.tree.setColumnWidth(2, 70)
        self.tree.setColumnWidth(3, 40)
        self.tree.setColumnWidth(4, 80)
        self.tree.setColumnWidth(5, 90)
        self.tree.setColumnWidth(6, 110)
        self.tree.setColumnWidth(7, 250)
        self.tree.setColumnWidth(8, 80)
        self.tree.setColumnWidth(9, 60)
        self.tree.setColumnWidth(10, 110)
        self.tree.setColumnWidth(11, 80)
        self.tree.setSortingEnabled(True)
        self.tree.setAlternatingRowColors(True)
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection) # 다중 선택 활성화
        self.tree.setStyleSheet(
            "QTreeView::item { border-bottom: 1px solid #E0E0E0; }"
            "QTreeView::item:selected { background-color: #0078D7; color: white; }" # 선택 시 글씨 색상 설정
        )
        layout.addWidget(self.tree)
        self.tree.itemDoubleClicked.connect(self.on_item_double_clicked)
        self.tree.setExpandsOnDoubleClick(False)
        
        # Delete 키 입력 시 삭제 이벤트 연결
        self.shortcut_delete = QShortcut(QKeySequence(Qt.Key_Delete), self.tree)
        self.shortcut_delete.setContext(Qt.WidgetWithChildrenShortcut)
        self.shortcut_delete.activated.connect(self.delete_selected_packets)
        
        # Ctrl+C, Ctrl+V 복사 붙여넣기 이벤트 연결
        self.shortcut_copy = QShortcut(QKeySequence.Copy, self.tree)
        self.shortcut_copy.setContext(Qt.WidgetWithChildrenShortcut)
        self.shortcut_copy.activated.connect(self.copy_selected_packets)
        
        self.shortcut_paste = QShortcut(QKeySequence.Paste, self.tree)
        self.shortcut_paste.setContext(Qt.WidgetWithChildrenShortcut)
        self.shortcut_paste.activated.connect(self.paste_packets)
        
        # 스페이스바 입력 시 1회 전송 이벤트 연결
        self.shortcut_send = QShortcut(QKeySequence(Qt.Key_Space), self.tree)
        self.shortcut_send.setContext(Qt.WidgetWithChildrenShortcut)
        self.shortcut_send.activated.connect(self.send_selected_packets_once)

        # 엔터 키 입력 시 수정 창 열기 이벤트 연결
        self.shortcut_edit = QShortcut(QKeySequence(Qt.Key_Return), self.tree)
        self.shortcut_edit.setContext(Qt.WidgetWithChildrenShortcut)
        self.shortcut_edit.activated.connect(self.edit_selected_packet)

    def insert_toolbar_widget_before_clear(self, widget):
        """외부 기능 버튼을 Clear All Packets 왼쪽에 배치합니다."""
        if not hasattr(self, 'toolbar'):
            return
        index = self.toolbar.indexOf(self.btn_clear_all)
        if index < 0:
            self.toolbar.addWidget(widget)
        else:
            self.toolbar.insertWidget(index, widget)

    def update_all_action_buttons(self):
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            if isinstance(item, TxPacketItem):
                self._update_action_button(item)

    def stop_all_timers(self):
        """모든 주기성 송신 타이머를 중지시킵니다."""
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            if isinstance(item, TxPacketItem) and item.is_running:
                item.stop_timer()
                self._update_action_button(item)
        for sender in self._periodic_senders.values():
            sender.close()
        self._periodic_senders.clear()
        while not self._periodic_records.empty():
            self._drain_periodic_events()
        self._periodic_monitor.stop()

    def copy_selected_packets(self):
        selected_items = self.tree.selectedItems()
        if not selected_items: return
        
        copied_items = []
        packets_to_copy = []
        for item in selected_items:
            target = item if isinstance(item, TxPacketItem) else item.parent()
            if isinstance(target, TxPacketItem) and target not in copied_items:
                copied_items.append(target)
                packets_to_copy.append(target.packet_data)
                
        if packets_to_copy:
            try:
                text_data = json.dumps(packets_to_copy)
                QApplication.clipboard().setText(text_data)
            except Exception: pass

    def paste_packets(self):
        text_data = QApplication.clipboard().text()
        if not text_data: return
        
        try:
            packets = json.loads(text_data)
            if not isinstance(packets, list): return
            for data in packets:
                if "bus" in data and "id" in data and "data" in data:
                    data["count"] = 0 # 붙여넣기 시 전송 카운트 초기화
                    data['bus'] = 0
                    self.add_packet_to_tree(data)
            self.auto_save_packets()
        except Exception: pass

    def edit_selected_packet(self):
        """엔터 키를 눌렀을 때 선택된 패킷의 수정 창을 엽니다."""
        selected_items = self.tree.selectedItems()
        if not selected_items:
            return
        
        # 첫 번째 선택된 아이템을 기준으로 수정 창을 엽니다.
        item = selected_items[0]
        # on_item_double_clicked는 column 인자를 받지만, 내부 로직에서 사용하지 않으므로 0을 전달합니다.
        self.on_item_double_clicked(item, 0)

    def send_selected_packets_once(self):
        selected_items = self.tree.selectedItems()
        if not selected_items: return
        
        sent_items = []
        for item in selected_items:
            target = item if isinstance(item, TxPacketItem) else item.parent()
            if isinstance(target, TxPacketItem) and target not in sent_items:
                sent_items.append(target)
                target.send_packet()

    def delete_selected_packets(self):
        selected_items = self.tree.selectedItems()
        if not selected_items: return
        
        items_to_delete = []
        for item in selected_items:
            parent = item.parent()
            target = item if parent is None else parent
            if target not in items_to_delete:
                items_to_delete.append(target)
                
        for item in items_to_delete:
            if hasattr(item, 'stop_timer'):
                item.stop_timer()
            idx = self.tree.indexOfTopLevelItem(item)
            if idx >= 0:
                self.tree.takeTopLevelItem(idx)
                
        self.auto_save_packets()
        
    def on_clear_all_packets(self):
        reply = QMessageBox.question(self, '확인', '모든 패킷을 삭제하시겠습니까?', QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes:
            for i in range(self.tree.topLevelItemCount()):
                item = self.tree.topLevelItem(i)
                if hasattr(item, 'stop_timer'):
                    item.stop_timer()
            self.tree.clear()
            self.auto_save_packets()

    def on_add_packet(self):
        dlg = TxPacketDialog(self.db_messages, self)
        if dlg.exec_() == QDialog.Accepted:
            self.add_packet_to_tree(dlg.get_packet_data())
            self.auto_save_packets()
            
    def add_packet_to_tree(self, data):
        item = TxPacketItem(self.tree, data, self)
        
        bus_num = data["bus"]
        can_id = data["id"]
        if bus_num in self.db_messages and can_id in self.db_messages[bus_num]:
            db_msg = self.db_messages[bus_num][can_id]
            
            # Raw Data를 DBC 포맷에 맞게 디코딩 (에러 핸들링 및 정렬 강화)
            decoded_vals = {}
            try:
                # 데이터 길이가 DBC에 정의된 것보다 짧으면 디코딩 에러가 발생하므로, 0으로 패딩합니다.
                raw_data_list = data["data"][:data["length"]]
                if len(raw_data_list) < db_msg.length:
                    raw_data_list.extend([0] * (db_msg.length - len(raw_data_list)))
                raw_bytes = bytes(raw_data_list)
                decoded_vals = db_msg.decode(raw_bytes, decode_choices=False)
            except Exception: pass
                
            try:
                # cantools에서 반환하는 signals 속성이 항상 정렬을 보장하지 않으므로,
                # start_bit 기준으로 명시적으로 정렬하여 UI에 일관되게 표시합니다.
                sorted_signals = sorted(db_msg.signals, key=lambda s: s.start_bit)
            except AttributeError:
                sorted_signals = db_msg.signals # .sym 파일 등 start_bit가 없는 경우 원본 순서 유지

            for sig in sorted_signals:
                sig_item = SortableTreeWidgetItem(item)
                sig_item.setText(6, sig.name)
                val = decoded_vals.get(sig.name, "-")
                
                if val == "-":
                    sig_item.setText(7, "-")
                else:
                    if getattr(sig, 'choices', None) and isinstance(val, (int, float)) and int(val) in sig.choices:
                        val_str = f"{sig.choices[int(val)]} ({val})"
                    elif isinstance(val, float):
                        val_str = f"{round(val, 10):.10f}".rstrip('0')
                        if val_str.endswith('.'): val_str += '0'
                        if val_str == "-0.0": val_str = "0.0"
                    else:
                        val_str = str(val)
                        
                    unit = getattr(sig, 'unit', "")
                    sig_item.setText(7, f"{val_str} {unit}" if unit else val_str)
                
        action_widget = QWidget()
        h_layout = QHBoxLayout(action_widget)
        h_layout.setContentsMargins(2, 2, 2, 2)
        btn_send = QPushButton("Send" if data["cycle"] == 0 else "Start")
        btn_send.clicked.connect(lambda _, i=item, b=btn_send: self.on_action_clicked(i, b))
        h_layout.addWidget(btn_send)
        h_layout.addStretch()
        self.tree.setItemWidget(item, 11, action_widget)
        self._update_action_button(item)
        item.setExpanded(False) # 패킷 추가 시 기본적으로 접힌 상태로 시작
        
    def on_item_double_clicked(self, item, column):
        # 더블 클릭한 항목이 시그널(Child)인 경우 상위 패킷 노드를 가져오도록 함
        if not isinstance(item, TxPacketItem):
            item = item.parent()
            if not isinstance(item, TxPacketItem):
                return
                
        # 더블클릭 시 아이템의 확장 상태가 토글되는 기본 동작을 방지하기 위해, 현재 상태를 저장했다가 복원합니다.
        is_expanded = item.isExpanded()
                
        dlg = TxPacketDialog(self.db_messages, self)
        dlg.setWindowTitle("Edit Packet")
        dlg.set_packet_data(item.packet_data)
        
        if dlg.exec_() == QDialog.Accepted:
            was_running = item.is_running
            item.stop_timer()
            new_data = dlg.get_packet_data()
            new_data["count"] = item.packet_data["count"] # 전송 카운트는 기존 값 유지
            
            item.counter_states = {}
            item.packet_data = new_data
            item.update_ui()
            
            # 하위 시그널(Child) 트리 갱신 (삭제 후 재생성)
            item.takeChildren()
            bus_num = new_data["bus"]
            can_id = new_data["id"]
            if bus_num in self.db_messages and can_id in self.db_messages[bus_num]:
                db_msg = self.db_messages[bus_num][can_id]
                decoded_vals = {}
                try: # 디코딩 에러 방지를 위한 패딩 및 정렬 로직 추가
                    raw_data_list = new_data["data"][:new_data["length"]]
                    if len(raw_data_list) < db_msg.length:
                        raw_data_list.extend([0] * (db_msg.length - len(raw_data_list)))
                    raw_bytes = bytes(raw_data_list)
                    decoded_vals = db_msg.decode(raw_bytes, decode_choices=False)
                except Exception: pass
                
                try:
                    sorted_signals = sorted(db_msg.signals, key=lambda s: s.start_bit)
                except AttributeError:
                    sorted_signals = db_msg.signals

                for sig in sorted_signals:
                    sig_item = SortableTreeWidgetItem(item)
                    sig_item.setText(6, sig.name)
                    val = decoded_vals.get(sig.name, "-")
                    
                    if val == "-":
                        sig_item.setText(7, "-")
                    else:
                        if getattr(sig, 'choices', None) and isinstance(val, (int, float)) and int(val) in sig.choices:
                            val_str = f"{sig.choices[int(val)]} ({val})"
                        elif isinstance(val, float):
                            val_str = f"{round(val, 10):.10f}".rstrip('0')
                            if val_str.endswith('.'): val_str += '0'
                            if val_str == "-0.0": val_str = "0.0"
                        else:
                            val_str = str(val)
                        unit = getattr(sig, 'unit', "")
                        sig_item.setText(7, f"{val_str} {unit}" if unit else val_str)
            
            # 편집 후, 다이얼로그를 열기 전의 확장 상태를 그대로 복원합니다.
            item.setExpanded(is_expanded)
            if was_running and new_data['cycle'] > 0:
                item.start_timer()
            self._update_action_button(item)
            self.auto_save_packets()
            
    def on_action_clicked(self, item, btn):
        if item.packet_data["cycle"] == 0:
            # For one-shot sends, the check is inside send_packet
            item.send_packet()
        else:
            if item.is_running:
                item.stop_timer()
            else:
                # Check compatibility before starting timer
                d = item.packet_data
                bus_obj = self.buses.get(d["bus"])
                if not bus_obj:
                    return # Not connected, do nothing.
                bus_is_fd = self.main_window.bus_capabilities[d["bus"]].get('is_fd', False)
                # is_fd 플래그가 아닌, 실제 FD 전용 기능(DLC > 8) 사용 여부로 호환성 판단
                is_incompatible = d.get('length', 0) > 8
                if is_incompatible and not bus_is_fd:
                    return # Incompatible, do nothing.
                
                item.start_timer()
        
        self._update_action_button(item)
                
    def _update_action_button(self, item):
        action_widget = self.tree.itemWidget(item, 11)
        if not action_widget: return
        btn = action_widget.findChild(QPushButton)
        if not btn: return

        # 주기 전송이 실행 중일 때: 'Stop' 버튼, 주황색 배경
        if item.is_running:
            decorate_button(btn, 'stop', '이 패킷의 주기 송신을 정지합니다.', 'Stop')
            btn.setStyleSheet("background-color: #ffc107; color: black;") # 주황색 배경으로 '실행 중' 상태 표시
            return

        # 주기 전송이 중지 상태일 때: 'Start' 또는 'Send' 버튼
        if item.packet_data['cycle'] == 0:
            decorate_button(btn, 'start', '이 패킷을 한 번 송신합니다.', 'Send')
        else:
            decorate_button(btn, 'start', '이 패킷의 주기 송신을 시작합니다.', 'Start')
        
        bus_num = item.packet_data["bus"]
        is_connected = self.buses.get(bus_num) is not None
        
        # 호환성 검사: 채널이 FD를 지원하지 않는데 패킷 길이가 8을 초과하는 경우
        bus_is_fd = self.main_window.bus_capabilities.get(bus_num, {}).get('is_fd', False)
        is_incompatible = item.packet_data.get('length', 0) > 8
        
        if not is_connected or (is_incompatible and not bus_is_fd):
            btn.setToolTip('BUS 연결과 CAN FD 설정을 확인해야 송신할 수 있습니다.')
            btn.setStyleSheet("background-color: #dc3545; color: white;") # 빨간색 배경으로 '전송 불가' 상태 표시
        else:
            btn.setStyleSheet("") # 기본 스타일

    def on_save_packets(self):
        file_path, _ = QFileDialog.getSaveFileName(self, "Save XMT File", "", "XMT Files (*.xmt)")
        if not file_path: return
        
        lines = [
            ";$FORMATVERSION=5.1",
            f"; {file_path}",
            "; CAN messages saved by Universal CAN Monitor",
            ";", "; Columns descriptions:", "; ~~~~~~~~~~~~~~~~~~~~~",
            ";+Bus", ";|  +Message ID", ";|  |         +Reserved",
            ";|  |         |  +Cycle time in ms (0=manual)",
            ";|  |         |  |     +Length of message",
            ";|  |         |  |     |    +Frame type: D)ata or R)emote request",
            ";|  |         |  |     |    | +Message data", ";|  |         |  |     |    | |"
        ]
        
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            d = item.packet_data
            bus = d["bus"]
            msg_id = f"{d['id']:X}h"
            cycle = d["cycle"]
            total_length = d["length"]
            user_data = d["data"]
            data_hex = " ".join(f"{b:02X}h" for b in user_data)
            note = d["note"]

            crc_type = d.get("crc_type", "N/A")
            crc_note = f"CRC={crc_type}" if crc_type != "N/A" else ""

            is_brs = d.get("is_brs", False)
            brs_note = "BRS=On" if is_brs else ""

            counters = d.get("signal_counters", {})
            counter_note = "Counters=" + base64.urlsafe_b64encode(json.dumps(counters).encode("utf-8")).decode("ascii") if counters else ""

            all_notes = [note for note in (crc_note, brs_note, counter_note, note) if note]
            final_note = " ".join(all_notes)
            
            status = "Paused" if not item.is_running and cycle > 0 else ""
            line = f" {bus}  {msg_id:<10}-  {cycle:<5}{total_length:<5}D {data_hex}"
            if status: line += f"  {status}"
            if final_note: line += f" ; {final_note}"
            lines.append(line)
            
        try:
            with open(file_path, 'w', encoding='utf-8') as f: f.write("\n".join(lines))
            QMessageBox.information(self, "Saved", "성공적으로 저장되었습니다.")
        except Exception as e:
            show_error(self, "Error", f"저장 중 오류 발생:\n{e}")

    def on_load_packets(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Open XMT File", "", "XMT Files (*.xmt)")
        if not file_path: return
        
        try:
            lines = []
            for enc in ['utf-8-sig', 'cp949', 'cp1252', 'latin1']:
                try:
                    with open(file_path, 'r', encoding=enc) as f:
                        lines = f.readlines()
                    break
                except UnicodeDecodeError:
                    pass
            else:
                with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                    lines = f.readlines()
                    
            for line in lines:
                line = line.strip()
                if not line or line.startswith(";$"): continue
                
                note = ""
                crc_type = "N/A"
                is_brs = False
                signal_counters = {}
                if ';' in line and not line.startswith(';'):
                    parts = line.split(';', 1)
                    line = parts[0].strip()
                    note = parts[1].strip()
                    counter_match = re.search(r'\bCounters=(\S+)', note)
                    if counter_match:
                        signal_counters = json.loads(base64.urlsafe_b64decode(counter_match.group(1)).decode("utf-8"))
                        note = re.sub(r'\bCounters=\S+\s*', '', note, count=1).strip()
                    
                    crc_match = re.search(r'\bCRC=(\S+)\b', note)
                    if crc_match:
                        crc_val = crc_match.group(1)
                        if crc_val == 'Y': # 이전 버전 호환
                            crc_type = "Hyundai_CRC"
                        else:
                            crc_type = crc_val
                        note = re.sub(r'\bCRC=\S+\b\s*?', '', note, 1).strip()

                    if re.search(r'\bBRS=On\b', note, re.IGNORECASE):
                        is_brs = True
                        note = re.sub(r'\bBRS=On\b\s*?', '', note, re.IGNORECASE).strip()
                elif line.startswith(';'): continue
                    
                tokens = line.split()
                if not tokens: continue
                
                bus_num = 1
                can_id_str = tokens[0]
                is_type1 = False
                
                if len(tokens) > 1 and (tokens[1].lower().endswith('h') or '-' in tokens):
                    bus_num = int(tokens[0])
                    can_id_str = tokens[1]
                    is_type1 = True
                    
                try: can_id = int(can_id_str.lower().rstrip('h'), 16)
                except ValueError: continue
                    
                cycle, length = 0, 8
                data_bytes = []
                
                if is_type1:
                    idx = 2
                    if tokens[idx] == '-': idx += 1
                    try: cycle = int(tokens[idx])
                    except: pass
                    idx += 1
                    try: length = int(tokens[idx])
                    except: pass
                    idx += 2
                    for t in tokens[idx:]:
                        if t.lower() == 'paused': continue
                        try: data_bytes.append(int(t.lower().rstrip('h'), 16))
                        except: break
                else:
                    try: cycle = int(tokens[1])
                    except: pass
                    try: length = int(tokens[2])
                    except: pass
                    for t in tokens[4:]:
                        if t.lower() == 'paused': continue
                        try: data_bytes.append(int(t.lower().rstrip('h'), 16))
                        except: break
                        
                symbol = "N/A"
                if bus_num in self.db_messages and can_id in self.db_messages[bus_num]:
                    symbol = self.db_messages[bus_num][can_id].name
                    
                is_fd = length > 8 or is_brs
                data = {
                    "bus": bus_num, "id": can_id, "is_fd": is_fd, "is_brs": is_brs,
                    "length": length,
                    "signal_counters": signal_counters,
                    "data": data_bytes, "cycle": cycle, "note": note, "symbol": symbol, "count": 0, "crc_type": crc_type
                }
                self.add_packet_to_tree(data)
                
            self.auto_save_packets()
                
        except Exception as e:
            show_error(self, "Error", f"불러오기 중 오류 발생:\n{e}")

    def get_pk_path(self):
        if getattr(sys, 'frozen', False):
            base_dir = os.path.dirname(sys.executable)
        else:
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return os.path.join(base_dir, "PCAN_Viewer_JJ.pk")

    def auto_save_packets(self):
        session = getattr(self.main_window, 'session', None)
        if session is not None:
            # SessionManager owns persistent state, including DB contents and windows.
            session.autosave()
            return
        if getattr(self, '_is_loading', False):
            return
            
        # 뷰어 모드일 때는 기존 데이터(패킷/설정)를 덮어쓰지 않도록 저장을 생략합니다.
        if getattr(self.main_window, 'viewer_only', False):
            return
            
        try:
            path = self.get_pk_path()
            viewer_mode = False
            panel_only = False
            
            # 기존 파일에서 viewer_mode_only 상태 유지
            if os.path.exists(path):
                try:
                    with open(path, 'r', encoding='utf-8') as f:
                        old_data = json.load(f)
                        viewer_mode = old_data.get("viewer_mode_only", False)
                        panel_only = old_data.get('user_panel_only', False)
                except Exception:
                    pass
                    
            data_dict = {
                "viewer_mode_only": viewer_mode,
                "user_panel_only": panel_only,
                "tx_packets": [], 
                "db_files": {b: [] for b in BUS_IDS},
                "can_config": {}
            }
            
            for i in range(self.tree.topLevelItemCount()):
                item = self.tree.topLevelItem(i)
                data_dict["tx_packets"].append(item.packet_data)
                
            main_win = self.main_window
            if main_win and hasattr(main_win, 'list_db_files'):
                for bus_num, lw in main_win.list_db_files.items():
                    paths = []
                    for i in range(lw.count()):
                        p = lw.item(i).data(Qt.UserRole + 1)
                        if p: paths.append(p)
                    data_dict["db_files"][bus_num] = paths
                    
                for b in BUS_IDS:
                    data_dict["can_config"][str(b)] = {
                        "channel": main_win.combo_channels[b].currentText(),
                        "bitrate": main_win.combo_bitrate[b].currentText(),
                        "fd_iso": main_win.combo_fd_iso[b].currentText(),
                        "data_bitrate": main_win.combo_data_bitrate[b].currentText(),
                        "is_open": main_win.buses[b] is not None
                    }
                    
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(data_dict, f, indent=4)
        except Exception: pass

    def auto_load_packets(self):
        self._is_loading = True
        try:
            path = self.get_pk_path()
            if not os.path.exists(path): return
            with open(path, 'r', encoding='utf-8') as f: data_content = json.load(f)
            
            if isinstance(data_content, list):
                tx_packets = data_content
                db_files = {}
                can_config = {}
            else:
                tx_packets = data_content.get("tx_packets", [])
                db_files = data_content.get("db_files", {})
                can_config = data_content.get("can_config", {})
                
            main_win = self.main_window
            db_changed = False
            
            if main_win and hasattr(main_win, 'load_db_from_path'):
                # CAN 하드웨어 및 통신 속도 매칭 복구
                for b_str, config in can_config.items():
                    b = int(b_str)
                    channel_text = config.get("channel", "")
                    idx = main_win.combo_channels[b].findText(channel_text)
                    if idx >= 0:
                        main_win.combo_channels[b].setCurrentIndex(idx)
                        br_idx = main_win.combo_bitrate[b].findText(config.get("bitrate", ""))
                        if br_idx >= 0: main_win.combo_bitrate[b].setCurrentIndex(br_idx)
                        iso_idx = main_win.combo_fd_iso[b].findText(config.get("fd_iso", ""))
                        if iso_idx >= 0: main_win.combo_fd_iso[b].setCurrentIndex(iso_idx)
                        dbr_idx = main_win.combo_data_bitrate[b].findText(config.get("data_bitrate", ""))
                        if dbr_idx >= 0: main_win.combo_data_bitrate[b].setCurrentIndex(dbr_idx)
                        
                        if config.get("is_open", False):
                            main_win.open_can(b)
                        
                for bus_num_str, paths in db_files.items():
                    bus_num = int(bus_num_str)
                    for p in paths:
                        if os.path.exists(p):
                            main_win.load_db_from_path(p, bus_num)
                        else:
                            db_changed = True
                            
            for data in tx_packets:
                data["count"] = 0 # 프로그램 구동 시 전송 횟수는 0으로 초기화
                bus_num = data["bus"]
                can_id = data["id"]
                if bus_num in self.db_messages and can_id in self.db_messages[bus_num]:
                    data["symbol"] = self.db_messages[bus_num][can_id].name
                else:
                    data["symbol"] = "N/A"
                self.add_packet_to_tree(data)
                
        except Exception: pass
        finally:
            self._is_loading = False
            self.auto_save_packets()

    def refresh_db_symbols(self, bus_num=None):
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            d = item.packet_data
            if bus_num is None or d["bus"] == bus_num:
                b_num = d["bus"]
                c_id = d["id"]
                if b_num in self.db_messages and c_id in self.db_messages[b_num]:
                    db_msg = self.db_messages[b_num][c_id]
                    d["symbol"] = db_msg.name
                    item.update_ui()
                    item.takeChildren()
                    
                    decoded_vals = {}
                    try: # 디코딩 에러 방지를 위한 패딩 및 정렬 로직 추가
                        raw_data_list = d["data"][:d["length"]]
                        if len(raw_data_list) < db_msg.length:
                            raw_data_list.extend([0] * (db_msg.length - len(raw_data_list)))
                        raw_bytes = bytes(raw_data_list)
                        decoded_vals = db_msg.decode(raw_bytes, decode_choices=False)
                    except Exception: pass
                    
                    try:
                        sorted_signals = sorted(db_msg.signals, key=lambda s: s.start_bit)
                    except AttributeError:
                        sorted_signals = db_msg.signals

                    for sig in sorted_signals:
                        sig_item = SortableTreeWidgetItem(item)
                        sig_item.setText(6, sig.name)
                        val = decoded_vals.get(sig.name, "-")
                        
                        if val == "-":
                            sig_item.setText(7, "-")
                        else:
                            if getattr(sig, 'choices', None) and isinstance(val, (int, float)) and int(val) in sig.choices:
                                val_str = f"{sig.choices[int(val)]} ({val})"
                            elif isinstance(val, float):
                                val_str = f"{round(val, 10):.10f}".rstrip('0')
                                if val_str.endswith('.'): val_str += '0'
                                if val_str == "-0.0": val_str = "0.0"
                            else:
                                val_str = str(val)
                            unit = getattr(sig, 'unit', "")
                            sig_item.setText(7, f"{val_str} {unit}" if unit else val_str)
                else:
                    d["symbol"] = "N/A"
                    item.update_ui()
                    item.takeChildren()
