"""Ordered DBC signal selection and a receive-only grid control."""
import copy
from collections import defaultdict

from cantools.database.errors import DecodeError
from PyQt5.QtCore import Qt, QSize, QTimer, QEvent
from PyQt5.QtGui import QIcon, QFont, QFontMetrics
from PyQt5.QtWidgets import (
    QAbstractItemView, QApplication, QDialog, QDialogButtonBox, QHeaderView, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QSizePolicy, QTableWidget, QTableWidgetItem,
    QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget, QStyledItemDelegate,
)

from src.bus_config import BUS_IDS
from src.utils import get_resource_path

GRID_COLUMNS = (('bus', '버스'), ('symbol_name', '심볼명'), ('signal_name', '신호명'),
                ('user_name', '사용자이름'))
DEFAULT_GRID_COLUMNS = ['bus', 'symbol_name', 'signal_name']
BASE_GRID_FONT_SIZE = 11


def grid_value_text(value, unit='', decimals=3, choice=None):
    text = f'{value:.{decimals}f}'.rstrip('0').rstrip('.') if isinstance(value, float) else str(value)
    if text in ('', '-0'):
        text = '0'
    if unit:
        text += f' {unit}'
    return f'{choice} ({text})' if choice is not None else text


def visible_grid_columns(binding):
    selected = binding.get('grid_columns', DEFAULT_GRID_COLUMNS)
    return [key for key, _ in GRID_COLUMNS if key in selected] + ['value']


def grid_text_columns(cfg, db_messages):
    binding = cfg.get('binding', {})
    labels = dict(GRID_COLUMNS, value='값')
    texts = {key: [labels[key]] for key in visible_grid_columns(binding)}
    for entry in binding.get('grid_signals', []):
        bus, can_id, name = signal_key(entry)
        message = db_messages.get(bus, {}).get(can_id)
        values = dict(bus=f'BUS{bus}', symbol_name=message.name if message else entry.get('symbol_name', f'0x{can_id:X}'),
                      signal_name=name, user_name=entry.get('user_name') or name)
        unit = entry.get('unit', '')
        samples = ['-', '0' + (f' {unit}' if unit else '')]
        if message and any(signal.name == name for signal in message.signals):
            signal = message.get_signal_by_name(name)
            unit = signal.unit or ''
            samples.append('0' + (f' {unit}' if unit else ''))
            for value in (signal.minimum, signal.maximum):
                if value is not None:
                    samples.append(f'{value:g}' + (f' {unit}' if unit else ''))
            decimals = 6 if binding.get('value_precision_decimals') == 6 else 3
            samples.extend(grid_value_text(signal.conversion.raw_to_scaled(raw, decode_choices=False),
                                           unit, decimals, choice)
                           for raw, choice in (signal.choices or {}).items())
        for key in texts:
            texts[key].extend(samples if key == 'value' else [values[key]])
    return texts


def grid_metrics(texts, row_count, title, font):
    metrics = QFontMetrics(font)
    widths = {key: max(metrics.horizontalAdvance(text) for text in values) + 18
              for key, values in texts.items()}
    row_height = metrics.height() + 8
    header_height = metrics.height() + 10
    title_height = max(26, metrics.height() + 4)
    index_width = max(18, metrics.horizontalAdvance(str(max(1, row_count)))) + 12
    width = max(sum(widths.values()) + index_width + 2, metrics.horizontalAdvance(title) + 36)
    height = title_height + 2 + header_height + max(1, row_count) * row_height + 2
    return widths, row_height, header_height, title_height, index_width, QSize(width, height)


def grid_minimum_size(cfg, db_messages):
    font = QFont(QApplication.font())
    font.setPixelSize(BASE_GRID_FONT_SIZE)
    return grid_metrics(grid_text_columns(cfg, db_messages), len(cfg.get('binding', {}).get('grid_signals', [])),
                        cfg.get('title', 'Grid View'), font)[-1]


class UserNameDelegate(QStyledItemDelegate):
    def createEditor(self, parent, option, index):
        if index.column() == 4 and index.sibling(index.row(), 0).data(Qt.UserRole):
            return super().createEditor(parent, option, index)
        return None


def signal_key(entry):
    return int(entry['bus']), int(entry['can_id']), entry['signal_name']


def validate_grid_signals(entries):
    if not isinstance(entries, list) or not entries:
        raise ValueError('그리드에 표시할 DBC 신호를 1개 이상 선택하세요.')
    seen = set()
    for entry in entries:
        if (not isinstance(entry, dict) or type(entry.get('bus')) is not int
                or entry['bus'] not in BUS_IDS or type(entry.get('can_id')) is not int
                or not 0 <= entry['can_id'] <= 0x1FFFFFFF
                or not isinstance(entry.get('signal_name'), str) or not entry['signal_name'].strip()):
            raise ValueError('그리드 신호의 BUS, CAN ID, 신호명을 확인하세요.')
        if 'user_name' in entry and not isinstance(entry['user_name'], str):
            raise ValueError('그리드 사용자이름은 문자열로 입력하세요.')
        key = signal_key(entry)
        if key in seen:
            raise ValueError('그리드에 같은 신호를 중복 선택할 수 없습니다.')
        seen.add(key)


class GridSignalDialog(QDialog):
    def __init__(self, db_messages, selected=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle('그리드 뷰 · DBC 신호 선택')
        self.resize(860, 560)
        self._selected = {signal_key(entry): copy.deepcopy(entry) for entry in selected or []}
        self.items = {}
        layout = QVBoxLayout(self)
        hint = QLabel('심볼을 체크하면 하위 신호 전체를 선택합니다. 개별 신호도 체크할 수 있습니다.\n'
                      '체크한 순서대로 행이 추가되며, 해제 후 다시 체크하면 마지막 행으로 이동합니다.\n'
                      '사용자이름 열을 더블클릭하여 별칭을 입력합니다. 비워두면 신호명을 표시합니다.')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.search = QLineEdit()
        self.search.setPlaceholderText('심볼 / 신호명 / CAN ID / BUS 검색')
        layout.addWidget(self.search)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(['심볼 / 신호명', '버스', 'CAN ID', '선택 순서', '사용자이름'])
        self.tree.setItemDelegate(UserNameDelegate(self.tree))
        self.tree.setAlternatingRowColors(True)
        self.tree.setColumnWidth(0, 360)
        self.tree.setSortingEnabled(False)
        layout.addWidget(self.tree, 1)
        for bus in BUS_IDS:
            for can_id, message in sorted(db_messages.get(bus, {}).items()):
                symbol = QTreeWidgetItem(self.tree, [message.name, f'BUS{bus}', f'0x{can_id:X}'])
                symbol.setFlags(symbol.flags() | Qt.ItemIsUserCheckable)
                for signal in message.signals:
                    self._add_signal(symbol, dict(bus=bus, can_id=can_id, symbol_name=message.name,
                                                  signal_name=signal.name, unit=signal.unit or ''))
        # Keep saved selections visible and removable even if their DBC is absent.
        missing = [entry for key, entry in self._selected.items() if key not in self.items]
        if missing:
            symbol = QTreeWidgetItem(self.tree, ['DBC에 없는 저장된 신호'])
            symbol.setFlags(symbol.flags() | Qt.ItemIsUserCheckable)
            symbol.setExpanded(True)
            for entry in missing:
                self._add_signal(symbol, entry)
        self.count_label = QLabel()
        footer = QHBoxLayout()
        clear = QPushButton('전체 선택 해제')
        clear.clicked.connect(self.clear_selection)
        footer.addWidget(clear)
        footer.addWidget(self.count_label)
        footer.addStretch()
        layout.addLayout(footer)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._sync_checks()
        self.tree.itemChanged.connect(self._item_changed)
        self.search.textChanged.connect(self._filter)

    def _add_signal(self, parent, entry):
        entry = copy.deepcopy(entry)
        saved = self._selected.get(signal_key(entry), {})
        if saved.get('user_name'):
            entry['user_name'] = saved['user_name']
        item = QTreeWidgetItem(parent, [entry['signal_name'], f"BUS{entry['bus']}",
                                       f"0x{entry['can_id']:X}", '', entry.get('user_name', '')])
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsEditable)
        item.setData(0, Qt.UserRole, copy.deepcopy(entry))
        self.items[signal_key(entry)] = item

    def selected_signals(self):
        return copy.deepcopy(list(self._selected.values()))

    def clear_selection(self):
        self._selected.clear()
        self._sync_checks()

    def _item_changed(self, item, column):
        if column == 4:
            entry = copy.deepcopy(item.data(0, Qt.UserRole))
            if entry:
                name = item.text(4).strip()
                if name:
                    entry['user_name'] = name
                else:
                    entry.pop('user_name', None)
                self.tree.blockSignals(True)
                try:
                    item.setData(0, Qt.UserRole, entry)
                finally:
                    self.tree.blockSignals(False)
                if signal_key(entry) in self._selected:
                    self._selected[signal_key(entry)] = copy.deepcopy(entry)
            return
        if column != 0:
            return
        checked = item.checkState(0) == Qt.Checked
        children = [item.child(i) for i in range(item.childCount())] if item.childCount() else [item]
        for child in children:
            entry = child.data(0, Qt.UserRole)
            if not entry:
                continue
            key = signal_key(entry)
            if checked:
                self._selected.setdefault(key, copy.deepcopy(entry))
            else:
                self._selected.pop(key, None)
        self._sync_checks()

    def _sync_checks(self):
        self.tree.blockSignals(True)
        try:
            positions = {key: str(i + 1) for i, key in enumerate(self._selected)}
            for key, item in self.items.items():
                item.setCheckState(0, Qt.Checked if key in positions else Qt.Unchecked)
                item.setText(3, positions.get(key, ''))
            for i in range(self.tree.topLevelItemCount()):
                symbol = self.tree.topLevelItem(i)
                count = sum(symbol.child(j).checkState(0) == Qt.Checked for j in range(symbol.childCount()))
                symbol.setCheckState(0, Qt.Unchecked if not count else
                                     Qt.Checked if count == symbol.childCount() else Qt.PartiallyChecked)
            self.count_label.setText(f'선택된 신호: {len(self._selected)}개')
        finally:
            self.tree.blockSignals(False)

    def _filter(self, text):
        text = text.strip().casefold()
        for i in range(self.tree.topLevelItemCount()):
            symbol = self.tree.topLevelItem(i)
            match = text in ' '.join(symbol.text(c) for c in range(3)).casefold()
            any_visible = False
            for j in range(symbol.childCount()):
                child = symbol.child(j)
                visible = match or text in ' '.join(child.text(c) for c in (0, 1, 2, 4)).casefold()
                child.setHidden(not visible)
                any_visible |= visible
            symbol.setHidden(not (match or any_visible))
            if text and any_visible:
                symbol.setExpanded(True)


class RxGridControl(QWidget):
    def __init__(self, cfg, db_messages, open_graph, parent=None):
        super().__init__(parent)
        self.entries = copy.deepcopy(cfg.get('binding', {}).get('grid_signals', []))
        self.decimals = 6 if cfg.get('binding', {}).get('value_precision_decimals') == 6 else 3
        self._rows = defaultdict(list)
        self._last_payloads = {}
        self.columns = visible_grid_columns(cfg.get('binding', {}))
        self._text_columns = grid_text_columns(cfg, db_messages)
        self._title = cfg.get('title', 'Grid View')
        self._fit_pending = False
        self._fitting = False
        self._value_min_width = 1
        self._base_font = QFont(self.font())
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        header = QHBoxLayout()
        self.title_label = QLabel(cfg.get('title', 'Grid View'))
        self.title_label.setObjectName('grid_title')
        self.title_label.setToolTip(self.title_label.text())
        self.title_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        header.addWidget(self.title_label, 1)
        self.graph_button = QToolButton()
        self.graph_button.setObjectName('grid_graph')
        self.graph_button.setIcon(QIcon(get_resource_path('icon/main_controls/graph.svg')))
        self.graph_button.setIconSize(QSize(22, 22))
        self.graph_button.setFixedSize(28, 26)
        self.graph_button.setToolTip('그리드 신호의 실시간 그래프 보기')
        self.graph_button.setAccessibleName('실시간 그래프 보기')
        self.graph_button.setEnabled(bool(self.entries))
        self.graph_button.clicked.connect(open_graph)
        header.addWidget(self.graph_button, 0, Qt.AlignRight)
        layout.addLayout(header)
        self.table = QTableWidget(len(self.entries), len(self.columns))
        labels = dict(GRID_COLUMNS, value='값')
        self.table.setHorizontalHeaderLabels([labels[key] for key in self.columns])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(False)
        self.table.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self.table.verticalHeader().setMinimumSectionSize(1)
        self.table.verticalHeader().setSectionResizeMode(QHeaderView.Fixed)
        self.table.horizontalHeader().setMinimumSectionSize(1)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Fixed)
        for row, entry in enumerate(self.entries):
            bus, can_id, name = signal_key(entry)
            self._rows[(bus, can_id)].append((row, entry))
            message = db_messages.get(bus, {}).get(can_id)
            symbol = message.name if message else entry.get('symbol_name', f'0x{can_id:X}')
            values = dict(bus=f'BUS{bus}', symbol_name=symbol, signal_name=name,
                          user_name=entry.get('user_name') or name, value='-')
            for col, key in enumerate(self.columns):
                text = values[key]
                item = QTableWidgetItem(text)
                item.setToolTip(f'{text}\nBUS{bus} / 0x{can_id:X} / {symbol}.{name}')
                self.table.setItem(row, col, item)
        layout.addWidget(self.table, 1)
        self.table.viewport().installEventFilter(self)
        self._schedule_fit()

    def eventFilter(self, watched, event):
        if watched is self.table.viewport() and event.type() == QEvent.Resize:
            value_column = len(self.columns) - 1
            remaining = event.size().width() - sum(self.table.columnWidth(c) for c in range(value_column))
            width = max(self._value_min_width, remaining)
            if self.table.columnWidth(value_column) != width:
                self.table.setColumnWidth(value_column, width)
        return super().eventFilter(watched, event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._schedule_fit()

    def _schedule_fit(self):
        if not self._fit_pending:
            self._fit_pending = True
            QTimer.singleShot(0, self._fit_layout)

    def _fit_layout(self):
        self._fit_pending = False
        if self._fitting:
            return
        self._fitting = True
        try:
            texts = {key: list(values) for key, values in self._text_columns.items()}
            value_column = len(self.columns) - 1
            texts['value'].extend(self.table.item(row, value_column).text() for row in range(len(self.entries)))
            font = QFont(self._base_font)

            def measure(size):
                font.setPixelSize(size)
                return grid_metrics(texts, len(self.entries), self._title, font)

            low, high = BASE_GRID_FONT_SIZE, max(BASE_GRID_FONT_SIZE, self.height())
            while low < high:
                middle = (low + high + 1) // 2
                needed = measure(middle)[-1]
                if needed.width() <= self.width() and needed.height() <= self.height():
                    low = middle
                else:
                    high = middle - 1
            widths, row_h, head_h, title_h, index_w, _ = measure(low)
            self._value_min_width = widths['value']
            self.table.setFont(font)
            self.title_label.setFont(font)
            self.title_label.setFixedHeight(title_h)
            self.table.horizontalHeader().setFont(font)
            self.table.horizontalHeader().setFixedHeight(head_h)
            self.table.verticalHeader().setFont(font)
            self.table.verticalHeader().setFixedWidth(index_w)
            self.table.verticalHeader().setDefaultSectionSize(row_h)
            self.layout().activate()
            for column, key in enumerate(self.columns[:-1]):
                self.table.setColumnWidth(column, widths[key])
            remaining = self.table.viewport().width() - sum(widths[key] for key in self.columns[:-1])
            self.table.setColumnWidth(value_column, max(widths['value'], remaining))
        finally:
            self._fitting = False

    def _set_value(self, row, value, unit='', choice=None):
        text = grid_value_text(value, unit, self.decimals, choice)
        item = self.table.item(row, len(self.columns) - 1)
        if item.text() != text:
            item.setText(text)
            item.setToolTip(text)
            if QFontMetrics(self.table.font()).horizontalAdvance(text) + 18 > self.table.columnWidth(len(self.columns) - 1):
                self._schedule_fit()

    def update_message(self, db_messages, bus, can_id, payload):
        rows = self._rows.get((bus, can_id))
        if not rows:
            return
        message = db_messages.get(bus, {}).get(can_id)
        if message is None:
            return
        marker = id(message), bytes(payload)
        if self._last_payloads.get((bus, can_id)) == marker:
            return
        try:
            decoded = message.decode(payload, decode_choices=False, scaling=False)
        except (DecodeError, ValueError, KeyError):
            return
        self._last_payloads[(bus, can_id)] = marker
        for row, entry in rows:
            name = entry['signal_name']
            if name in decoded:
                signal = message.get_signal_by_name(name)
                raw = decoded[name]
                value = signal.conversion.raw_to_scaled(raw, decode_choices=False)
                choice = signal.choices.get(raw) if signal.choices else None
                self._set_value(row, value, signal.unit or '', choice)
            else:
                self.table.item(row, len(self.columns) - 1).setText('-')

    def simulate_value(self, value):
        self._last_payloads.clear()
        for row, entry in enumerate(self.entries):
            self._set_value(row, value, entry.get('unit', ''))
