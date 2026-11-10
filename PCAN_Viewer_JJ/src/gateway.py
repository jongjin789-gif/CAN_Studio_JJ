"""BUS-to-BUS gateway: forward received frames to another BUS, with ID filter/remap.

Forwarding runs on the source BUS receiver thread (low latency) and writes to the
destination BUS under the same per-BUS lock the TX panels use. Only frames received
from the bus are forwarded (never this program's own TX), so A->B and B->A rules
cannot loop. The gateway always starts switched off when the program starts.
"""
import re
import time
from dataclasses import dataclass, field

import can
from PyQt5.QtCore import QObject, Qt, QTimer, pyqtSignal
from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem,
                             QHeaderView, QAbstractItemView, QPushButton, QComboBox, QCheckBox,
                             QLineEdit, QLabel, QMessageBox, QWidget)
from src.bus_config import BUS_IDS
from src.button_icons import decorate_button
from src.tx_schedule import bus_schedule

MODES = (('all', '전체 전달'), ('pass', '지정 ID만 전달'), ('block', '지정 ID 차단'))
MAX_ID = 0x1FFFFFFF


def _hex(token):
    token = token.strip()
    if not re.fullmatch(r'(0[xX])?[0-9A-Fa-f]{1,8}', token):
        raise ValueError(f"'{token}'은(는) 16진수 CAN ID가 아닙니다.")
    value = int(token, 16)
    if value > MAX_ID:
        raise ValueError(f"'{token}'은(는) 29bit CAN ID 범위를 벗어납니다.")
    return value


def parse_id_list(text):
    """``"100-1FF, 0x321"`` -> [(0x100, 0x1FF), (0x321, 0x321)]. IDs are hexadecimal."""
    ranges = []
    for part in re.split(r'[,\s;]+', text.strip()):
        if not part:
            continue
        if '-' in part:
            low, high = part.split('-', 1)
            low, high = _hex(low), _hex(high)
            if low > high:
                raise ValueError(f"'{part}': 시작 ID가 끝 ID보다 큽니다.")
        else:
            low = high = _hex(part)
        ranges.append((low, high))
    return ranges


def parse_remap(text):
    """``"100=200, 18DAF110=18DA10F1"`` -> {0x100: 0x200, ...}."""
    remap = {}
    for part in re.split(r'[,\s;]+', text.strip()):
        if not part:
            continue
        if '=' not in part:
            raise ValueError(f"'{part}': ID 변환은 '원래ID=새ID' 형식입니다.")
        old, new = part.split('=', 1)
        remap[_hex(old)] = _hex(new)
    return remap


def normalized_rules(value):
    """Rules from a session file; malformed entries are dropped."""
    rules = []
    for rule in value if isinstance(value, list) else []:
        if not isinstance(rule, dict):
            continue
        try:
            item = dict(enabled=rule.get('enabled') is True, src=int(rule['src']), dst=int(rule['dst']),
                        mode=rule.get('mode', 'all'), ids=str(rule.get('ids', '')),
                        remap=str(rule.get('remap', '')))
        except (KeyError, TypeError, ValueError):
            continue
        if item['mode'] in dict(MODES) and item['src'] in BUS_IDS and item['dst'] in BUS_IDS:
            rules.append(item)
    return rules[:32]


@dataclass
class CompiledRule:
    src: int
    dst: int
    mode: str
    ranges: list
    remap: dict
    forwarded: int = 0
    errors: int = 0
    last_error: str = ''
    _last_report: float = field(default=0.0, repr=False)

    def matches(self, can_id):
        if self.mode == 'all':
            return True
        hit = any(low <= can_id <= high for low, high in self.ranges)
        return hit if self.mode == 'pass' else not hit


def compile_rules(rules):
    """Validate rules; raises ValueError with the row number on the first problem."""
    compiled = []
    for row, rule in enumerate(rules, 1):
        try:
            if rule['src'] == rule['dst']:
                raise ValueError('From과 To BUS가 같습니다.')
            ranges = parse_id_list(rule['ids'])
            if rule['mode'] != 'all' and not ranges:
                raise ValueError('ID 목록을 입력하세요.')
            remap = parse_remap(rule['remap'])
        except ValueError as exc:
            raise ValueError(f'{row}행: {exc}') from None
        compiled.append(CompiledRule(rule['src'], rule['dst'], rule['mode'], ranges, remap)
                        if rule['enabled'] else None)
    return compiled


class Gateway(QObject):
    # dst bus, can id, data, is_extended, is_fd, timestamp
    forwarded = pyqtSignal(int, int, object, bool, bool, float)
    failed = pyqtSignal(int, int, str)  # rule row (1-based), dst bus, error

    def __init__(self, main):
        super().__init__(main)
        self.main = main
        self.rules = []          # editable rule dicts (saved in the session)
        self.enabled = False     # master switch, always off at program start
        self._compiled = ()      # read by receiver threads

    def configure(self, rules, enabled):
        compiled = compile_rules(rules)
        for rule in compiled:
            if rule is not None:
                bus_schedule(self.main, rule.dst)  # create per-BUS locks on the GUI thread
        self.rules = [dict(rule) for rule in rules]
        self.enabled = bool(enabled)
        self._compiled = tuple(compiled) if self.enabled else ()

    def statistics(self):
        return [(rule.forwarded, rule.errors, rule.last_error) if rule is not None else None
                for rule in self._compiled]

    def forward(self, src_bus, msg):
        """Receiver-thread hook; must stay cheap when nothing matches."""
        rules = self._compiled
        if not rules:
            return
        for row, rule in enumerate(rules, 1):
            if rule is None or rule.src != src_bus or not rule.matches(msg.arbitration_id):
                continue
            dest = self.main.buses.get(rule.dst)
            if dest is None:
                continue
            can_id = rule.remap.get(msg.arbitration_id, msg.arbitration_id)
            is_ext = bool(msg.is_extended_id or can_id > 0x7FF)
            frame = can.Message(arbitration_id=can_id, data=msg.data, is_extended_id=is_ext,
                                is_fd=msg.is_fd, bitrate_switch=msg.bitrate_switch)
            try:
                with bus_schedule(self.main, rule.dst).lock:
                    dest.send(frame)
            except Exception as exc:
                rule.errors += 1
                rule.last_error = str(exc)
                now = time.monotonic()
                if now - rule._last_report >= 5.0:
                    rule._last_report = now
                    self.failed.emit(row, rule.dst, str(exc))
                continue
            rule.forwarded += 1
            self.forwarded.emit(rule.dst, can_id, bytes(msg.data), is_ext, bool(msg.is_fd), time.time())


class GatewayDialog(QDialog):
    COLUMNS = ['사용', 'From', 'To', '동작', 'ID 목록 (16진수)', 'ID 변환 (원래=새)', '전달 / 실패']

    def __init__(self, main, parent=None):
        super().__init__(parent)
        self.main = main
        self.gateway = main.gateway
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setWindowTitle('BUS 게이트웨이')
        self.resize(900, 380)
        root = QVBoxLayout(self)

        top = QHBoxLayout()
        self.chk_enabled = QCheckBox('게이트웨이 사용')
        self.chk_enabled.setChecked(self.gateway.enabled)
        self.chk_enabled.setToolTip('체크 후 적용하면 아래의 사용 규칙대로 수신 프레임을 다른 BUS로 전달합니다.\n'
                                    '프로그램을 시작할 때는 항상 꺼져 있습니다.')
        top.addWidget(self.chk_enabled)
        top.addStretch()
        self.btn_add = QPushButton('규칙 추가')
        decorate_button(self.btn_add, 'packet_add', '전달 규칙을 한 줄 추가합니다.', 'Add')
        self.btn_add.clicked.connect(lambda: self.add_row())
        top.addWidget(self.btn_add)
        self.btn_remove = QPushButton('규칙 삭제')
        decorate_button(self.btn_remove, 'trash', '선택한 전달 규칙을 삭제합니다.', 'Delete')
        self.btn_remove.clicked.connect(self.remove_rows)
        top.addWidget(self.btn_remove)
        root.addLayout(top)

        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.verticalHeader().setDefaultSectionSize(26)
        header = self.table.horizontalHeader()
        for column, width in enumerate((45, 70, 70, 130, 230, 230)):
            self.table.setColumnWidth(column, width)
        header.setStretchLastSection(True)
        header.setSectionResizeMode(QHeaderView.Interactive)
        tips = ('이 규칙을 사용합니다.', '프레임을 받는 BUS', '프레임을 보낼 BUS',
                '전체 전달 / ID 목록의 ID만 전달 / ID 목록의 ID만 빼고 전달',
                '예: 100-1FF, 321, 18DAF110 (0x 생략 가능)',
                '보낼 때 ID를 바꿉니다. 예: 100=200, 18DAF110=18DA10F1',
                '이 규칙으로 전달한 프레임 수와 송신 실패 수')
        for column, tip in enumerate(tips):
            self.table.horizontalHeaderItem(column).setToolTip(tip)
        root.addWidget(self.table, 1)

        note = QLabel('· 장치에서 수신한 프레임만 전달합니다(이 프로그램이 보낸 TX는 전달하지 않아 순환되지 않음). '
                      '전달한 프레임은 To BUS의 TX로 표시·기록됩니다.\n'
                      '· 두 BUS가 모두 연결되어 있어야 전달합니다. CAN FD 프레임은 To BUS도 FD로 연결해야 보낼 수 있습니다.')
        note.setWordWrap(True)
        note.setStyleSheet('color: #555;')
        root.addWidget(note)

        buttons = QHBoxLayout()
        buttons.addStretch()
        for text, callback in (('적용', self.apply), ('확인', self.accept_and_apply), ('닫기', self.close)):
            button = QPushButton(text)
            button.clicked.connect(callback)
            buttons.addWidget(button)
        root.addLayout(buttons)

        for rule in self.gateway.rules:
            self.add_row(rule)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh_counters)
        self.timer.start(500)
        self.refresh_counters()

    def add_row(self, rule=None):
        rule = rule or dict(enabled=True, src=1, dst=2, mode='all', ids='', remap='')
        row = self.table.rowCount()
        self.table.insertRow(row)
        enabled = QCheckBox()
        enabled.setChecked(rule['enabled'])
        holder = QWidget()
        layout = QHBoxLayout(holder)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setAlignment(Qt.AlignCenter)
        layout.addWidget(enabled)
        self.table.setCellWidget(row, 0, holder)
        for column, key in ((1, 'src'), (2, 'dst')):
            combo = QComboBox()
            for bus in BUS_IDS:
                combo.addItem(f'BUS {bus}', bus)
            combo.setCurrentIndex(max(0, combo.findData(rule[key])))
            self.table.setCellWidget(row, column, combo)
        mode = QComboBox()
        for key, label in MODES:
            mode.addItem(label, key)
        mode.setCurrentIndex(max(0, mode.findData(rule['mode'])))
        self.table.setCellWidget(row, 3, mode)
        for column, key in ((4, 'ids'), (5, 'remap')):
            edit = QLineEdit(rule[key])
            edit.setPlaceholderText('100-1FF, 321' if key == 'ids' else '100=200')
            self.table.setCellWidget(row, column, edit)
        counter = QTableWidgetItem('-')
        counter.setFlags(Qt.ItemIsEnabled)
        self.table.setItem(row, 6, counter)

    def remove_rows(self):
        for row in sorted({index.row() for index in self.table.selectedIndexes()}, reverse=True):
            self.table.removeRow(row)

    def rules(self):
        rules = []
        for row in range(self.table.rowCount()):
            check = self.table.cellWidget(row, 0).findChild(QCheckBox)
            rules.append(dict(enabled=check.isChecked(),
                              src=self.table.cellWidget(row, 1).currentData(),
                              dst=self.table.cellWidget(row, 2).currentData(),
                              mode=self.table.cellWidget(row, 3).currentData(),
                              ids=self.table.cellWidget(row, 4).text().strip(),
                              remap=self.table.cellWidget(row, 5).text().strip()))
        return rules

    def apply(self):
        try:
            self.main.set_gateway(self.rules(), self.chk_enabled.isChecked())
        except ValueError as exc:
            QMessageBox.warning(self, 'BUS 게이트웨이', str(exc))
            return False
        return True

    def accept_and_apply(self):
        if self.apply():
            self.close()

    def refresh_counters(self):
        stats = self.gateway.statistics()
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 6)
            value = stats[row] if row < len(stats) else None
            if not self.gateway.enabled:
                text, tip = '꺼짐', '게이트웨이가 꺼져 있습니다.'
            elif value is None:
                text, tip = '-', '사용하지 않는 규칙이거나 아직 적용하지 않았습니다.'
            else:
                text, tip = f'{value[0]:,} / {value[1]:,}', value[2] or '실패 없음'
            if item.text() != text:
                item.setText(text)
            item.setToolTip(tip)

    def closeEvent(self, event):
        self.timer.stop()
        super().closeEvent(event)
