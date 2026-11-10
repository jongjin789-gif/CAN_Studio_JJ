"""Click-to-select colors shared by the panel's drawing and container tools."""
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QColor, QIcon, QPixmap
from PyQt5.QtWidgets import (
    QColorDialog, QGridLayout, QLabel, QMenu, QPushButton, QToolButton,
    QVBoxLayout, QWidget, QWidgetAction,
)


RECOMMENDED_COLORS = (
    ('파란색', '#E8F1FF'), ('하늘색', '#E0F2FE'), ('청록색', '#CCFBF1'),
    ('초록색', '#DCFCE7'), ('노란색', '#FEF9C3'), ('주황색', '#FFEDD5'),
    ('분홍색', '#FCE7F3'), ('보라색', '#EDE9FE'), ('회색', '#F1F5F9'), ('흰색', '#FFFFFF'),
)
COLOR_CHART = (
    '#FFFFFF', '#F1F5F9', '#CBD5E1', '#94A3B8', '#64748B', '#475569', '#333333', '#000000',
    '#FEE2E2', '#FFEDD5', '#FEF9C3', '#DCFCE7', '#CCFBF1', '#E0F2FE', '#E8F1FF', '#EDE9FE',
    '#FECACA', '#FED7AA', '#FEF08A', '#BBF7D0', '#99F6E4', '#BAE6FD', '#BFDBFE', '#DDD6FE',
    '#F87171', '#FB923C', '#FACC15', '#4ADE80', '#2DD4BF', '#38BDF8', '#60A5FA', '#A78BFA',
    '#DC2626', '#EA580C', '#CA8A04', '#16A34A', '#0D9488', '#0284C7', '#2563EB', '#7C3AED',
    '#7F1D1D', '#7C2D12', '#713F12', '#14532D', '#134E4A', '#0C4A6E', '#1E3A8A', '#4C1D95',
)


class ColorPicker(QWidget):
    textChanged = pyqtSignal(str)
    colorSelected = pyqtSignal(str)

    def __init__(self, color, parent=None):
        super().__init__(parent)
        self._color = ''
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.button = QToolButton(self)
        self.button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.button.setPopupMode(QToolButton.InstantPopup)
        self.button.setAccessibleName('색상 선택')
        layout.addWidget(self.button)

        self.menu = QMenu(self.button)
        self.button.setMenu(self.menu)
        panel = QWidget(self.menu)
        colors = QVBoxLayout(panel)
        colors.addWidget(QLabel('추천 색상'))
        recommended = QGridLayout()
        colors.addLayout(recommended)
        self.recommended_buttons = []
        for index, (name, value) in enumerate(RECOMMENDED_COLORS):
            button = self._swatch(value, name, panel)
            recommended.addWidget(button, index // 5, index % 5)
            self.recommended_buttons.append(button)
        colors.addWidget(QLabel('색상표'))
        chart = QGridLayout()
        colors.addLayout(chart)
        self.chart_buttons = []
        for index, value in enumerate(COLOR_CHART):
            button = self._swatch(value, value, panel)
            chart.addWidget(button, index // 8, index % 8)
            self.chart_buttons.append(button)
        custom = QPushButton('사용자 지정 색상…', panel)
        custom.clicked.connect(self._choose_custom)
        colors.addWidget(custom)
        self.custom_button = custom
        action = QWidgetAction(self.menu)
        action.setDefaultWidget(panel)
        self.menu.addAction(action)
        self.setText(color)

    def _swatch(self, color, name, parent):
        button = QToolButton(parent)
        button.setFixedSize(26, 24)
        button.setToolTip(f'{name} ({color})' if name != color else color)
        button.setAccessibleName(button.toolTip())
        button.setStyleSheet(
            f'QToolButton {{ background-color: {color}; border: 1px solid #94A3B8; border-radius: 3px; }}'
            'QToolButton:hover, QToolButton:focus { border: 2px solid #2563EB; }'
        )
        button.clicked.connect(lambda _checked=False: self._select(color))
        return button

    def text(self):
        return self._color

    def setText(self, value):
        color = QColor(value)
        if not color.isValid():
            return
        value = color.name().upper()
        changed = value != self._color
        self._color = value
        swatch = QPixmap(18, 18)
        swatch.fill(color)
        self.button.setIcon(QIcon(swatch))
        name = next((name for name, code in RECOMMENDED_COLORS if code == value), value)
        self.button.setText(f'색 {name}')
        self.button.setAccessibleName(f'색상 선택: {name}')
        self.button.setToolTip(f'{name} ({value}) · 추천 색상 또는 색상표에서 선택')
        if changed:
            self.textChanged.emit(value)

    def _select(self, value):
        self.menu.hide()
        self.setText(value)
        self.colorSelected.emit(self._color)

    def _choose_custom(self):
        self.menu.hide()
        color = QColorDialog.getColor(QColor(self._color), self, '사용자 지정 색상')
        if color.isValid():
            self._select(color.name())
