"""A bounded, ordered table for RX label color rules."""
import copy
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QHBoxLayout, QHeaderView, QLabel, QMessageBox, QPushButton, QStackedWidget,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)
from .color_picker import ColorPicker, RECOMMENDED_COLORS
from .label_colors import CONDITIONS, validate_color_rules
from .property_table import PROPERTY_TABLE_STYLE


class LabelColorRulesEditor(QWidget):
    rules_changed = pyqtSignal(list)

    def __init__(self, rules=None, enum_entries=None, parent=None):
        super().__init__(parent)
        self.enum_entries = list(enum_entries or [])
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        hint = QLabel('위쪽 규칙 우선 · 배경/글자 각각 첫 일치 적용 · 일치하는 규칙이 없으면 기본 색상\n'
                      'Enum은 RAW 값, 비교/범위는 Scale·Offset 적용 후 물리값입니다. 범위 안은 양 끝을 포함합니다.')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(['사용', '기준', '조건', '값 / min / Enum', 'max', '적용 대상', '색상'])
        self.table.setAlternatingRowColors(False)
        self.table.setStyleSheet(PROPERTY_TABLE_STYLE)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        for column, width in enumerate((48, 92, 195, 180, 110, 90, 190)):
            self.table.setColumnWidth(column, width)
        self.table.verticalHeader().setDefaultSectionSize(36)
        layout.addWidget(self.table, 1)
        buttons = QHBoxLayout()
        self.btn_add = QPushButton('조건 추가')
        self.btn_enum = QPushButton('Enum 일괄 추가')
        self.btn_delete = QPushButton('선택 삭제')
        self.btn_up = QPushButton('위로')
        self.btn_down = QPushButton('아래로')
        for button, callback in ((self.btn_add, lambda: self.add_rule()),
                                 (self.btn_enum, self.add_enums), (self.btn_delete, self.remove_selected),
                                 (self.btn_up, lambda: self.move_selected(-1)),
                                 (self.btn_down, lambda: self.move_selected(1))):
            buttons.addWidget(button)
            button.clicked.connect(callback)
        self.btn_enum.setEnabled(bool(self.enum_entries))
        self.btn_enum.setToolTip('현재 선택 신호의 Enum 값을 한 행씩 추가합니다.')
        layout.addLayout(buttons)
        self._rows = []
        self._loading = False
        self.set_rules(rules or [])
        self.table.itemChanged.connect(self._rules_changed)

    def set_rules(self, rules):
        self._loading = True
        self.table.setRowCount(0)
        self._rows.clear()
        for rule in rules:
            self.add_rule(rule)
        self._loading = False
        self._rules_changed()

    def _rules_changed(self, *_args):
        if not self._loading:
            self.rules_changed.emit(self.rules())

    @staticmethod
    def _number(value):
        spin = QDoubleSpinBox()
        spin.setDecimals(6)
        spin.setRange(-1e100, 1e100)
        spin.setValue(value)
        spin.setKeyboardTracking(False)
        return spin

    def add_rule(self, rule=None):
        if self.table.rowCount() >= 128:
            QMessageBox.warning(self, '색상 규칙', '최대 128개까지 등록할 수 있습니다.')
            return
        rule = rule or dict(enabled=True, source='value', op='ge', a=0, b=100,
                            target='background', color='#E8F1FF')
        row = self.table.rowCount()
        self.table.insertRow(row)
        enabled = QTableWidgetItem()
        enabled.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable)
        enabled.setCheckState(Qt.Checked if rule.get('enabled', True) else Qt.Unchecked)
        self.table.setItem(row, 0, enabled)
        source = QComboBox()
        source.addItem('물리값', 'value')
        if self.enum_entries or rule.get('source') == 'enum':
            source.addItem('Enum' if self.enum_entries else 'Enum (저장됨)', 'enum')
        source.setCurrentIndex(max(0, source.findData(rule['source'])))
        condition = QComboBox()
        for op, title in CONDITIONS:
            condition.addItem(title, op)
        condition.setCurrentIndex(max(0, condition.findData(rule.get('op', 'eq'))))
        a, b = self._number(rule.get('a', 0)), self._number(rule.get('b', 100))
        enum = QComboBox()
        enum.setMinimumContentsLength(12)
        enum.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        for raw, physical, title in self.enum_entries:
            enum.addItem(f'{title} · RAW {raw} / 값 {physical:g}', raw)
        if rule.get('source') == 'enum' and enum.findData(rule['raw']) < 0:
            enum.addItem(f"저장된 Enum RAW {rule['raw']} (현재 신호에 없음)", rule['raw'])
        enum.setCurrentIndex(max(0, enum.findData(rule.get('raw'))))
        stack = QStackedWidget()
        stack.addWidget(a)
        stack.addWidget(enum)
        target = QComboBox()
        target.addItem('배경색', 'background')
        target.addItem('글자색', 'text')
        target.setCurrentIndex(max(0, target.findData(rule['target'])))
        color = ColorPicker(rule['color'])
        for column, widget in enumerate((source, condition, stack, b, target, color), 1):
            self.table.setCellWidget(row, column, widget)
        fields = dict(enabled=enabled, source=source, condition=condition, stack=stack,
                      a=a, b=b, enum=enum, target=target, color=color)
        self._rows.append(fields)
        source.currentIndexChanged.connect(lambda: self._sync_row(fields))
        condition.currentIndexChanged.connect(lambda: self._sync_row(fields))
        for combo in (source, condition, enum, target):
            combo.currentIndexChanged.connect(self._rules_changed)
        a.valueChanged.connect(self._rules_changed)
        b.valueChanged.connect(self._rules_changed)
        color.textChanged.connect(self._rules_changed)
        self._sync_row(fields)
        self.table.setCurrentCell(row, 1)
        self._rules_changed()

    def _sync_row(self, fields):
        is_enum = fields['source'].currentData() == 'enum'
        fields['stack'].setCurrentIndex(1 if is_enum else 0)
        if is_enum:
            fields['condition'].setCurrentIndex(fields['condition'].findData('eq'))
        fields['condition'].setEnabled(not is_enum)
        fields['b'].setEnabled(not is_enum and fields['condition'].currentData() in ('between', 'outside'))

    def rules(self):
        rules = []
        for fields in self._rows:
            rule = dict(enabled=fields['enabled'].checkState() == Qt.Checked,
                        source=fields['source'].currentData(), target=fields['target'].currentData(),
                        color=fields['color'].text())
            if rule['source'] == 'enum':
                rule['raw'] = fields['enum'].currentData()
            else:
                rule.update(op=fields['condition'].currentData(), a=fields['a'].value(), b=fields['b'].value())
            rules.append(rule)
        return rules

    def add_enums(self):
        existing = {rule['raw'] for rule in self.rules() if rule['source'] == 'enum' and rule['target'] == 'background'}
        for index, (raw, _physical, _title) in enumerate(self.enum_entries):
            if raw not in existing:
                self.add_rule(dict(enabled=True, source='enum', raw=raw, target='background',
                                   color=RECOMMENDED_COLORS[index % len(RECOMMENDED_COLORS)][1]))
                if self.table.rowCount() >= 128:
                    break

    def remove_selected(self):
        selected = {index.row() for index in self.table.selectionModel().selectedRows()}
        self.set_rules([rule for row, rule in enumerate(self.rules()) if row not in selected])

    def move_selected(self, direction):
        selected = sorted(index.row() for index in self.table.selectionModel().selectedRows())
        if not selected:
            return
        if (direction < 0 and selected[0] == 0) or (direction > 0 and selected[-1] == self.table.rowCount() - 1):
            return
        rules = self.rules()
        for row in selected if direction < 0 else reversed(selected):
            rules[row], rules[row + direction] = rules[row + direction], rules[row]
        self.set_rules(rules)
        self.table.clearSelection()
        for row in selected:
            self.table.selectionModel().select(self.table.model().index(row + direction, 0),
                                              self.table.selectionModel().Select | self.table.selectionModel().Rows)


class LabelColorRulesDialog(QDialog):
    def __init__(self, rules=None, enum_entries=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle('수신 라벨 색상 규칙')
        self.resize(1080, 480)
        layout = QVBoxLayout(self)
        self.editor = LabelColorRulesEditor(copy.deepcopy(rules or []), enum_entries, self)
        layout.addWidget(self.editor)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def accept(self):
        try:
            validate_color_rules(self.editor.rules())
        except ValueError as exc:
            QMessageBox.warning(self, '색상 규칙 확인', str(exc))
            return
        super().accept()
