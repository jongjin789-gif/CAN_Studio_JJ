"""Four fixed CAN rows, shared state, and on-demand diagnostic history."""
from PyQt5.QtCore import Qt, QTimer, pyqtSignal, QSize, QPoint
from PyQt5.QtGui import QPalette, QIcon, QPixmap, QPainter, QColor, QPolygon
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QTableWidget,
    QHeaderView, QAbstractItemView, QComboBox, QPushButton, QLabel, QStyle,
    QDialog, QPlainTextEdit, QApplication, QFileDialog, QGroupBox, QToolButton,
    QStylePainter, QStyleOptionComboBox, QSizePolicy)
from src.bus_config import BUS_IDS, bitrate_value
from src.button_icons import button_icon


class CenteredComboBox(QComboBox):
    def display_text(self, text):
        return text

    def paintEvent(self, event):
        option = QStyleOptionComboBox()
        self.initStyleOption(option)
        painter = QStylePainter(self)
        painter.drawComplexControl(QStyle.CC_ComboBox, option)
        rect = self.style().subControlRect(QStyle.CC_ComboBox, option, QStyle.SC_ComboBoxEditField, self)
        painter.setClipRect(rect)
        self.style().drawItemText(painter, rect, Qt.AlignCenter, option.palette,
                                  self.isEnabled(), self.display_text(option.currentText), QPalette.Text)


class RateComboBox(CenteredComboBox):
    """Compact painting leaves stored text, item data and popup values intact."""
    compact = False

    def display_text(self, text):
        if self.compact:
            try:
                value = bitrate_value(text)
                if value:
                    return f'{value // 1000000}M' if value % 1000000 == 0 else f'{value // 1000}K'
            except ValueError:
                pass
        return text


class CollapsibleGroup(QGroupBox):
    collapsed_changed = pyqtSignal(bool)

    def __init__(self, title, parent=None, icon=None):
        super().__init__(parent)
        self._section_icon = icon
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 3, 6, 3)
        root.setSpacing(3)
        self.header_row = QHBoxLayout()
        self.header_row.setContentsMargins(0, 0, 0, 0)
        self.header_row.setSpacing(4)
        self.header = QToolButton()
        self.header.setText(title)
        self.header.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.header.setCheckable(True)
        self.header.setChecked(True)
        self.header.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.header_row.addWidget(self.header)
        self.header_row.addStretch()
        root.addLayout(self.header_row)
        self.content = QWidget()
        root.addWidget(self.content)
        self.header.toggled.connect(self._toggle)
        self._toggle(True)

    def _toggle(self, expanded):
        if self._section_icon is None:
            self.header.setArrowType(Qt.DownArrow if expanded else Qt.RightArrow)
        else:
            pixmap = QPixmap(32, 16)
            pixmap.fill(Qt.transparent)
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor('#145b73'))
            points = ((3, 6), (11, 6), (7, 10)) if expanded else ((5, 4), (9, 8), (5, 12))
            painter.drawPolygon(QPolygon([QPoint(x, y) for x, y in points]))
            painter.drawPixmap(16, 0, self._section_icon.pixmap(16, 16))
            painter.end()
            self.header.setIcon(QIcon(pixmap))
            self.header.setIconSize(QSize(32, 16))
        self.header.setToolTip('Collapse section' if expanded else 'Expand section')
        self.content.setVisible(expanded)
        self.collapsed_changed.emit(not expanded)

    def is_collapsed(self):
        return not self.header.isChecked()

    def set_collapsed(self, collapsed):
        self.header.setChecked(not collapsed)


class BusLogDialog(QDialog):
    def __init__(self, main, bus, parent=None):
        super().__init__(parent)
        self.main, self.bus = main, bus
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setWindowTitle(f'BUS {bus} · 상태 / 상세 로그')
        self.resize(800, 460)
        root = QVBoxLayout(self)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        root.addWidget(self.text)
        buttons = QHBoxLayout()
        for title, callback in [('복사', self.copy), ('저장', self.save), ('닫기', self.close)]:
            button = QPushButton(title)
            button.clicked.connect(callback)
            buttons.addWidget(button)
        root.addLayout(buttons)
        self._rendered = None
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(400)
        self.refresh()

    def refresh(self):
        lines = []
        for entry in self.main.bus_events[self.bus]:
            suffix = (f" (반복 {entry['count']}회, 마지막 {entry.get('last_time', '')})"
                      if entry['count'] > 1 else '')
            lines.append(f"[{entry['time']}] {entry['level']} {entry['message']}{suffix}")
            if entry['details']:
                lines.append(entry['details'])
        rendered = '\n\n'.join(lines) or '아직 기록된 이벤트가 없습니다.'
        if rendered != self._rendered:
            scroll = self.text.verticalScrollBar()
            at_end = scroll.value() == scroll.maximum()
            old = scroll.value()
            self.text.setPlainText(rendered)
            scroll.setValue(scroll.maximum() if at_end else old)
            self._rendered = rendered

    def copy(self):
        QApplication.clipboard().setText(self.text.toPlainText())

    def save(self):
        path, _ = QFileDialog.getSaveFileName(self, 'BUS 로그 저장', f'BUS{self.bus}.log', 'Text (*.log *.txt)')
        if path:
            try:
                with open(path, 'w', encoding='utf-8') as stream:
                    stream.write(self.text.toPlainText())
            except OSError as exc:
                self.main.log_bus_event(self.bus, f'로그 저장 실패: {exc}', 'ERROR', issue=False)


# Per-BUS selectors in column order. 'backend' is the CAN communication method (PCAN, SocketCAN ...).
FIELD_KEYS = ('backend', 'channels', 'bitrate', 'fd_iso', 'data_bitrate')
REFRESH_COLUMN, CONNECT_COLUMN, STATE_COLUMN = 5, 6, 7


class ConnectionGrid(QTableWidget):
    def __init__(self, main, parent=None, mirror=False, before_action=None):
        super().__init__(len(BUS_IDS), 8, parent)
        self.main, self.mirror, self.before_action = main, mirror, before_action
        font = main.tx_panel.tree.font() if hasattr(main, 'tx_panel') else main.font()
        font.setPointSizeF(max(8.0, min(9.0, font.pointSizeF() - 1.0)))
        self.setFont(font)
        self.fields, self.buttons, self.states = {}, {}, {}
        self._syncing = False
        self.setMinimumWidth(960)
        self.setHorizontalHeaderLabels(['CAN 방식', 'Channel', 'Baudrate', 'FD ISO', 'Data Bitrate', 'Refresh', 'Open / Close', 'State'])
        self.setVerticalHeaderLabels([str(b) for b in BUS_IDS])
        self.setSortingEnabled(False)
        self.setDragDropMode(QAbstractItemView.NoDragDrop)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setSelectionMode(QAbstractItemView.NoSelection)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        header = self.horizontalHeader()
        header.setSectionsMovable(False)
        self.verticalHeader().setSectionsMovable(False)
        header.setSectionResizeMode(QHeaderView.Fixed)
        header.setSectionResizeMode(STATE_COLUMN, QHeaderView.Stretch)
        metrics = self.fontMetrics()
        row_height = max(22, metrics.height() + 6)
        header.setFixedHeight(max(20, metrics.height() + 4))
        self._full_widths = (
            max(95, metrics.horizontalAdvance('SocketCAN') + 32),
            250,
            max(120, metrics.horizontalAdvance('500 kBit/s') + 32),
            max(100, metrics.horizontalAdvance('Non-ISO') + 32),
            max(120, metrics.horizontalAdvance('1 MBit/s') + 32, metrics.horizontalAdvance('Data Bitrate') + 12),
            max(70, metrics.horizontalAdvance('Refresh') + 12),
            max(100, metrics.horizontalAdvance('Open / Close') + 12))
        for column, width in enumerate(self._full_widths):
            self.setColumnWidth(column, width)
        # Keep the "CAN 방식" column narrow (the State column needs the room): its combo uses a
        # smaller font when a method name (e.g. candleLight) would not fit the column.
        method_font = self._fit_font(font, self._method_names(main), self._full_widths[0] - 30)
        for row, bus in enumerate(BUS_IDS):
            for column, key in enumerate(FIELD_KEYS):
                field = (RateComboBox() if key in ('bitrate', 'data_bitrate')
                         else CenteredComboBox() if key in ('backend', 'fd_iso') else QComboBox())
                if key == 'backend':
                    field.setFont(method_font)
                field.setMinimumWidth(0)
                field.setMinimumContentsLength(1)
                field.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
                field.setFixedHeight(row_height)
                self.fields[bus, key] = field
                self.setCellWidget(row, column, field)
                if mirror:
                    field.currentIndexChanged.connect(lambda index, b=bus, k=key: self.change(b, k, index))
                if key == 'backend':
                    # Picking a CAN method is a user-started search: offer a driver setup if needed.
                    field.activated.connect(lambda index, b=bus: getattr(main, 'offer_driver_setup', lambda bus: None)(b))
            refresh = QPushButton()
            refresh.setIcon(button_icon('refresh'))
            refresh.setToolTip(f'BUS {bus}: Refresh / 장치 검색')
            refresh.setAccessibleName(f'BUS {bus} 장치 검색')
            refresh.clicked.connect(lambda checked=False, b=bus: self.perform(
                lambda: getattr(main, 'refresh_channels', main.search_can_channels)(b)))
            connect = QPushButton('Open')
            refresh.setFixedHeight(row_height)
            connect.setFixedHeight(row_height)
            connect.clicked.connect(lambda checked=False, b=bus: self.perform(lambda: main.toggle_can(b)))
            self.buttons[bus, 'refresh'], self.buttons[bus, 'connect'] = refresh, connect
            self.setCellWidget(row, REFRESH_COLUMN, refresh)
            self.setCellWidget(row, CONNECT_COLUMN, connect)
            state = QWidget()
            layout = QHBoxLayout(state)
            layout.setContentsMargins(5, 0, 2, 0)
            layout.setSpacing(2)
            label = QLabel('미연결')
            label.setMinimumWidth(0)
            label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            layout.addWidget(label, 1)
            details = QPushButton('…')
            details.setFixedWidth(28)
            details.setFixedHeight(row_height - 2)
            details.setToolTip(f'BUS {bus}: 상세 로그')
            details.setAccessibleName(f'BUS {bus} 상세 로그')
            details.clicked.connect(lambda checked=False, b=bus: self.main.show_bus_log(b, self.window()))
            layout.addWidget(details)
            self.states[bus] = label
            self.setCellWidget(row, STATE_COLUMN, state)
            self.setRowHeight(row, row_height)
        self.setFixedHeight(header.height() + sum(self.rowHeight(r) for r in range(self.rowCount())) + 4)
        self.horizontalScrollBar().rangeChanged.connect(self.fit_height)
        if mirror:
            self.timer = QTimer(self)
            self.timer.timeout.connect(self.refresh)
            self.timer.start(200)
        main.bus_state_changed.connect(lambda bus: self.refresh())

    @staticmethod
    def _method_names(main):
        devices = getattr(main, 'devices', None)
        names = [backend.label for backend in devices.selectable()] if devices is not None else []
        return names + ['ALL', 'SocketCAN']

    @staticmethod
    def _fit_font(font, texts, room, smallest=6.5):
        """``font`` reduced in 0.5 pt steps until every text fits ``room`` pixels."""
        from PyQt5.QtGui import QFont, QFontMetrics
        fitted = QFont(font)
        size = fitted.pointSizeF()
        while size > smallest:
            if max(QFontMetrics(fitted).horizontalAdvance(text) for text in texts) <= room:
                break
            size -= 0.5
            fitted.setPointSizeF(size)
        return fitted

    def perform(self, callback):
        if self.before_action:
            self.before_action()
        callback()
        self.refresh()
        session = getattr(self.main, 'session', None)
        if session:
            session.autosave()

    def change(self, bus, key, index):
        if self._syncing or index < 0:
            return
        source = getattr(self.main, 'combo_' + key)[bus]
        if source.isEnabled() and self.main.buses[bus] is None:
            self.perform(lambda: source.setCurrentIndex(index))

    def refresh(self):
        self._syncing = True
        try:
            for bus in BUS_IDS:
                connected = self.main.buses.get(bus) is not None
                busy = bus in self.main._bus_busy
                for key in FIELD_KEYS:
                    source = getattr(self.main, 'combo_' + key, {}).get(bus)
                    field = self.fields[bus, key]
                    if source is None:
                        continue
                    if self.mirror:
                        values = [(source.itemText(i), source.itemData(i)) for i in range(source.count())]
                        current = [(field.itemText(i), field.itemData(i)) for i in range(field.count())]
                        if current != values:
                            field.clear()
                            for text, data in values:
                                field.addItem(text, data)
                        field.setCurrentIndex(source.currentIndex())
                        field.setEnabled(source.isEnabled() and not connected and not busy)
                    field.setToolTip(source.currentText())
                    if isinstance(field, CenteredComboBox):
                        for index in range(field.count()):
                            if field.itemData(index, Qt.TextAlignmentRole) != Qt.AlignCenter:
                                field.setItemData(index, Qt.AlignCenter, Qt.TextAlignmentRole)
                button = self.buttons[bus, 'connect']
                button.setText('Close' if connected else 'Open')
                button.setIcon(button_icon('stop' if connected else 'start'))
                button.setToolTip(f"BUS {bus}: {'연결 해제' if connected else '연결'}")
                button.setEnabled(not busy and (connected or self.main.can_open(bus)))
                self.buttons[bus, 'refresh'].setEnabled(not busy and not connected)
                summary, level = self.main.bus_summary(bus)
                load_text = getattr(self.main, 'bus_load_text', lambda b: '')(bus)
                if load_text:
                    summary = f'{summary} · {load_text}'
                label = self.states[bus]
                label.setText(label.fontMetrics().elidedText(summary, Qt.ElideRight, max(0, label.width())))
                label.setToolTip(summary)
                label.setStyleSheet('color: #b3261e;' if level == 'ERROR' else 'color: #9a6700;' if level == 'WARN' else '')
        finally:
            self._syncing = False

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, '_full_widths'):
            compact = self.viewport().width() - sum(self._full_widths) < 220
            for column in (2, 4):  # Baudrate, Data Bitrate
                title = self.horizontalHeaderItem(column).text()
                width = max(75, self.fontMetrics().horizontalAdvance(title) + 12) if compact else self._full_widths[column]
                self.setColumnWidth(column, width)
            for (bus, key), field in self.fields.items():
                if isinstance(field, RateComboBox):
                    field.compact = compact
                    field.update()
        if hasattr(self.main, 'bus_events'):
            self.refresh()

    def fit_height(self, minimum, maximum):
        scroll_height = self.horizontalScrollBar().sizeHint().height() if maximum > minimum else 0
        self.setFixedHeight(self.horizontalHeader().height()
                            + sum(self.rowHeight(r) for r in range(self.rowCount())) + 4 + scroll_height)
