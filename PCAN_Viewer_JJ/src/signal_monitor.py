"""RX signal statistics and receive-timeout notification.

Timeouts are notification only: the RX row is highlighted, the BUS log gets a
WARN/INFO entry and the status bar shows a message. Nothing is stopped or sent.
"""
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QBrush, QColor
from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem,
                             QHeaderView, QAbstractItemView, QPushButton, QLineEdit, QCheckBox,
                             QDoubleSpinBox, QLabel, QGroupBox)
from src.bus_config import BUS_IDS
from src.button_icons import decorate_button

DEFAULT_TIMEOUT = dict(enabled=True, factor=3.0, measured=True)
MIN_MEASURED_FRAMES = 5
MIN_LIMIT_MS = 20.0


def normalized_timeout_settings(value):
    settings = dict(DEFAULT_TIMEOUT)
    if isinstance(value, dict):
        if type(value.get('enabled')) is bool:
            settings['enabled'] = value['enabled']
        if type(value.get('measured')) is bool:
            settings['measured'] = value['measured']
        factor = value.get('factor')
        if isinstance(factor, (int, float)) and not isinstance(factor, bool) and 1.5 <= factor <= 20:
            settings['factor'] = float(factor)
    return settings


class TimeoutMonitor:
    """Detects RX messages that stopped arriving.

    The expected cycle is the DBC cycle time (GenMsgCycleTime) when present; otherwise,
    if ``measured`` is on, the average cycle measured since the message first appeared
    (after at least 5 frames). A message times out when nothing arrived for
    ``factor`` x cycle (at least 20 ms).
    """

    def __init__(self, settings=None):
        self.settings = normalized_timeout_settings(settings)
        self.timed_out = {}   # {(bus, can_id): (elapsed_ms, limit_ms)}
        self._first = {}      # {(bus, can_id): (first_time, first_count)}

    def configure(self, settings):
        self.settings = normalized_timeout_settings(settings)
        if not self.settings['enabled']:
            self.timed_out.clear()

    def forget(self, bus=None):
        for store in (self.timed_out, self._first):
            for key in [k for k in store if bus is None or k[0] == bus]:
                del store[key]

    def limit_ms(self, key, stats, db_cycle_ms):
        if db_cycle_ms:
            cycle = float(db_cycle_ms)
        elif self.settings['measured']:
            first = self._first.get(key)
            if first is not None and stats['count'] - first[1] >= MIN_MEASURED_FRAMES:
                cycle = (stats['last_time'] - first[0]) * 1000.0 / (stats['count'] - first[1])
            elif stats.get('count', 0) >= MIN_MEASURED_FRAMES and stats.get('cycle'):
                # Seen only after it already arrived several times: use the last interval.
                cycle = float(stats['cycle'])
            else:
                return None
        else:
            return None
        return max(MIN_LIMIT_MS, cycle * self.settings['factor']) if cycle > 0 else None

    def check(self, now, entries):
        """``entries``: iterable of ``(bus, can_id, stats, db_cycle_ms)`` for RX messages.

        Returns ``(new_timeouts, recovered)``; ``new_timeouts`` items are
        ``(bus, can_id, elapsed_ms, limit_ms)``, ``recovered`` items ``(bus, can_id)``.
        """
        if not self.settings['enabled']:
            return [], []
        new, recovered = [], []
        for bus, can_id, stats, db_cycle_ms in entries:
            key = (bus, can_id)
            last = stats.get('last_time')
            if last is None or stats.get('direction', 'Rx') != 'Rx':
                continue
            self._first.setdefault(key, (last, stats['count']))
            limit = self.limit_ms(key, stats, db_cycle_ms)
            elapsed = (now - last) * 1000.0
            if limit is not None and elapsed > limit:
                if key not in self.timed_out:
                    new.append((bus, can_id, elapsed, limit))
                self.timed_out[key] = (elapsed, limit)
            elif key in self.timed_out:
                del self.timed_out[key]
                recovered.append(key)
        return new, recovered


def _number(value):
    if isinstance(value, float):
        return f'{value:.6g}'
    return str(value)


class SignalStatsDialog(QDialog):
    COLUMNS = ['BUS', 'CAN ID', 'Message', 'Signal', '현재값', '최소', '최대', '평균', '샘플 수', '단위', '수신 상태']

    def __init__(self, main, parent=None):
        super().__init__(parent)
        self.main = main
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setWindowTitle('신호 통계 / 수신 타임아웃')
        self.resize(1060, 520)
        root = QVBoxLayout(self)

        top = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText('검색 (Message / Signal / ID)')
        self.search.setToolTip('메시지·신호 이름 또는 CAN ID로 표를 거릅니다.')
        top.addWidget(self.search, 1)
        self.btn_reset = QPushButton('통계 초기화')
        decorate_button(self.btn_reset, 'view_clear', '모든 BUS의 최소·최대·평균·샘플 수를 지웁니다.', 'Reset')
        self.btn_reset.clicked.connect(self.reset_stats)
        top.addWidget(self.btn_reset)
        root.addLayout(top)

        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        for column, width in enumerate((45, 90, 170, 190, 90, 90, 90, 90, 75, 60)):
            self.table.setColumnWidth(column, width)
        self.table.horizontalHeaderItem(10).setToolTip('수신 타임아웃 여부 (알림 전용)')
        root.addWidget(self.table, 1)

        group = QGroupBox('수신 타임아웃 알림')
        group.setToolTip('수신부 표시와 BUS 로그만 남기며 송수신에는 영향을 주지 않습니다.')
        row = QHBoxLayout(group)
        settings = main.timeout_monitor.settings
        self.chk_enabled = QCheckBox('사용')
        self.chk_enabled.setChecked(settings['enabled'])
        self.chk_enabled.setToolTip('수신이 끊긴 RX 메시지를 수신부에서 빨간 글씨로 표시하고 BUS 로그에 남깁니다.')
        row.addWidget(self.chk_enabled)
        row.addWidget(QLabel('기준: 주기 ×'))
        self.spin_factor = QDoubleSpinBox()
        self.spin_factor.setRange(1.5, 20.0)
        self.spin_factor.setSingleStep(0.5)
        self.spin_factor.setValue(settings['factor'])
        self.spin_factor.setToolTip('주기의 몇 배 동안 수신이 없으면 타임아웃으로 볼지 정합니다 (최소 20 ms).')
        row.addWidget(self.spin_factor)
        self.chk_measured = QCheckBox('DBC 주기가 없으면 측정 주기 사용')
        self.chk_measured.setChecked(settings['measured'])
        self.chk_measured.setToolTip('DBC에 GenMsgCycleTime이 없는 메시지는 수신 5회 이후의 평균 주기를 기준으로 합니다.\n'
                                     '해제하면 DBC 주기가 있는 메시지만 감시합니다.')
        row.addWidget(self.chk_measured)
        row.addStretch()
        root.addWidget(group)
        for widget in (self.chk_enabled, self.chk_measured):
            widget.toggled.connect(self.apply_settings)
        self.spin_factor.valueChanged.connect(self.apply_settings)

        buttons = QHBoxLayout()
        buttons.addStretch()
        close = QPushButton('닫기')
        close.clicked.connect(self.close)
        buttons.addWidget(close)
        root.addLayout(buttons)

        self._rows = {}
        self.search.textChanged.connect(self.refresh)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(500)
        self.refresh()

    def apply_settings(self):
        self.main.set_timeout_settings(dict(enabled=self.chk_enabled.isChecked(),
                                            factor=self.spin_factor.value(),
                                            measured=self.chk_measured.isChecked()))

    def reset_stats(self):
        for thread in self.main.rx_threads.values():
            if thread is not None:
                thread.signal_stats.clear()
        self.table.setRowCount(0)
        self._rows.clear()
        self.refresh()

    def _records(self):
        query = self.search.text().strip().lower()
        timed_out = self.main.timeout_monitor.timed_out
        for bus in BUS_IDS:
            thread = self.main.rx_threads.get(bus)
            if thread is None:
                continue
            for (can_id, signal), stat in list(thread.signal_stats.items()):
                message = self.main.db_messages[bus].get(can_id)
                name = message.name if message is not None else f'0x{can_id:X}'
                id_text = f'0x{can_id:03X}' if can_id <= 0x7FF else f'0x{can_id:X}'
                if query and not any(query in text.lower() for text in (name, signal, id_text)):
                    continue
                latest = thread.latest_data.get((can_id, signal))
                value, unit = (latest[0], latest[1]) if latest else ('-', '')
                state = timed_out.get((bus, can_id))
                status = f'TIMEOUT ({state[0]:.0f} ms)' if state else '정상'
                yield ((bus, can_id, signal),
                       [str(bus), id_text, name, signal, _number(value), _number(stat[0]),
                        _number(stat[1]), _number(stat[2] / stat[3]), str(stat[3]), unit, status],
                       bool(state))

    def refresh(self):
        records = sorted(self._records(), key=lambda record: record[0])
        keys = [record[0] for record in records]
        if keys != list(self._rows):
            self.table.setRowCount(len(records))
            self._rows = {key: row for row, key in enumerate(keys)}
        self.table.setUpdatesEnabled(False)
        try:
            for key, values, timeout in records:
                row = self._rows[key]
                for column, text in enumerate(values):
                    item = self.table.item(row, column)
                    if item is None:
                        item = QTableWidgetItem()
                        self.table.setItem(row, column, item)
                    if item.text() != text:
                        item.setText(text)
                    if column == 10:
                        item.setForeground(QBrush(QColor('#b3261e' if timeout else '#2e7d32')))
        finally:
            self.table.setUpdatesEnabled(True)

    def closeEvent(self, event):
        self.timer.stop()
        super().closeEvent(event)
