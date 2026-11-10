"""A compact light theme matching the monitor's teal icon family."""
from PyQt5.QtCore import Qt, QRectF, pyqtSignal
from PyQt5.QtGui import QPainter, QColor
from PyQt5.QtWidgets import QWidget, QHBoxLayout


class PacketDirectionStrip(QWidget):
    """A narrow rotated label confined to its packet section."""

    double_clicked = pyqtSignal()

    def __init__(self, direction, parent=None):
        super().__init__(parent)
        self.direction = direction
        self.setFixedWidth(22)
        self.setToolTip('Receive packets' if direction == 'RX' else 'Transmit packets')
        self.setAccessibleName(self.toolTip())

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.double_clicked.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QColor('#a8bfce'))
        painter.setBrush(QColor('#dbeaf1'))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 4, 4)
        painter.translate(self.width() / 2, self.height() / 2)
        painter.rotate(-90)
        font = self.font()
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor('#145b73'))
        painter.drawText(QRectF(-self.height() / 2, -self.width() / 2,
                                self.height(), self.width()), Qt.AlignCenter, self.direction)


def packet_section(content, direction, tree=None):
    section = QWidget()
    layout = QHBoxLayout(section)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(1)
    strip = PacketDirectionStrip(direction, section)
    strip.setObjectName(f'{direction.lower()}DirectionStrip')
    if tree is not None:
        strip.setCursor(Qt.PointingHandCursor)
        label = '수신부' if direction == 'RX' else '송신부'
        strip.setToolTip(f'{label} 트리 전체 접기 / 펼치기 (더블클릭)')
        strip.setAccessibleName(strip.toolTip())

        def toggle_tree():
            expanded = any(tree.topLevelItem(i).childCount() and tree.topLevelItem(i).isExpanded()
                           for i in range(tree.topLevelItemCount()))
            if expanded:
                tree.collapseAll()
            else:
                tree.expandAll()

        strip.double_clicked.connect(toggle_tree)
    layout.addWidget(strip)
    layout.addWidget(content, 1)
    return section

MAIN_STYLE = """
QMainWindow#canMonitor, QWidget#monitorWorkspace {
    background: #cfd8e2; color: #243e4c;
}
QMenuBar { background: #c2d1dd; color: #244b5d; padding: 2px; }
QMenuBar::item { padding: 3px 8px; background: transparent; }
QMenuBar::item:selected { background: #cce6ec; border-radius: 3px; }
QMenu { background: #ffffff; color: #243e4c; border: 1px solid #ccdce5; }
QMenu::item { padding: 5px 20px; }
QMenu::item:selected { background: #dff0f3; color: #145b73; }
QMenu::item:disabled { color: #98a8b3; }
QMenu::separator { height: 1px; background: #e4edf2; margin: 3px 7px; }
QGroupBox { background: #ffffff; border: 1px solid #a8bfce; border-radius: 5px; }
QWidget#receiveSection, QWidget#transmitSection {
    background: #f9fbfd; border: 1px solid #a8bfce; border-radius: 5px;
}
QGroupBox#connectionSection { margin-top: 0; padding: 0; }
QGroupBox#databaseSection { margin-top: 0; padding: 0; }
QPushButton, QToolButton {
    color: #244b5d; background: #ffffff; border: 1px solid #bdd2dd;
    border-radius: 4px; padding: 2px 6px;
}
QPushButton:hover, QToolButton:hover { background: #e1f3f4; border-color: #68aeba; }
QPushButton:pressed, QToolButton:pressed { background: #c5e7eb; border-color: #298b9f; }
QPushButton:focus, QToolButton:focus { border-color: #298b9f; }
QPushButton:disabled, QToolButton:disabled {
    color: #95a6b2; background: #f3f6f8; border-color: #dce5ea;
}
QPushButton#databaseLoadButton {
    background: #c5e7eb; color: #12495d; border: 1px solid #68aeba;
}
QPushButton#databaseLoadButton:hover { background: #abdbe2; border-color: #298b9f; }
QPushButton#databaseLoadButton:pressed { background: #8acbd5; border-color: #145b73; }
QPushButton#databaseLoadButton:focus { border-color: #145b73; }
QPushButton#databaseLoadButton:disabled {
    background: #f3f6f8; color: #95a6b2; border-color: #dce5ea;
}
QToolButton#databaseToggle, QToolButton#connectionToggle { background: transparent; border: none; color: #145b73; padding: 2px 4px; }
QToolButton#databaseToggle:hover, QToolButton#connectionToggle:hover { background: #e1f3f4; }
QLineEdit, QComboBox {
    background: #ffffff; color: #243e4c; border: 1px solid #c6d8e2;
    border-radius: 3px; padding: 1px 4px;
}
QLineEdit:focus, QComboBox:focus { border-color: #298b9f; }
QComboBox:hover { border-color: #68aeba; }
QLineEdit:disabled, QComboBox:disabled { color: #91a3af; background: #f2f6f8; border-color: #dee7ec; }
QComboBox QAbstractItemView { background: white; color: #243e4c; selection-background-color: #d6edf1; selection-color: #145b73; }
QTreeWidget, QTableWidget, QListWidget {
    background: #ffffff; alternate-background-color: #f5f9fb; color: #243e4c;
    border: 1px solid #b7cbd8; border-radius: 3px; gridline-color: #e2ebf0;
    selection-background-color: #d5eef2; selection-color: #12495d;
}
QTreeWidget::item { border-bottom: 1px solid #edf2f5; }
QTreeWidget::item:selected, QListWidget::item:selected { background: #d5eef2; color: #12495d; }
QHeaderView::section {
    background: #dbeaf1; color: #234e62; border: none;
    border-right: 1px solid #c3d8e3; border-bottom: 1px solid #90b3c4;
    padding: 2px 4px; font-weight: 600;
}
QTableCornerButton::section { background: #dbeaf1; border: 1px solid #c3d8e3; }
QLabel { background: transparent; }
QSplitter::handle { background: #b0c3d2; height: 4px; }
QSplitter::handle:hover { background: #7bb9c5; }
QStatusBar { background: #c2d1dd; color: #365666; border-top: 1px solid #a9becd; }
QStatusBar::item { border: none; }
QScrollBar:vertical { background: #edf3f7; width: 12px; margin: 0; }
QScrollBar:horizontal { background: #edf3f7; height: 12px; margin: 0; }
QScrollBar::handle { background: #b6cdd9; border-radius: 4px; min-width: 22px; min-height: 22px; }
QScrollBar::handle:hover { background: #7fb3c2; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
"""


def apply_main_theme(main):
    main.setObjectName('canMonitor')
    main.centralWidget().setObjectName('monitorWorkspace')
    main.conn_group.setObjectName('connectionSection')
    main.conn_group.header.setObjectName('connectionToggle')
    main.db_group.setObjectName('databaseSection')
    main.db_group.header.setObjectName('databaseToggle')
    for button in main.btn_load_db.values():
        button.setObjectName('databaseLoadButton')
    receive = main.tree.parentWidget()
    receive.setObjectName('receiveSection')
    main.tx_panel.setObjectName('transmitSection')
    for section in (receive, main.tx_panel):
        section.setAttribute(Qt.WA_StyledBackground, True)
        section.layout().setContentsMargins(4, 4, 4, 4)
    main.tree.setStyleSheet('')
    main.tx_panel.tree.setStyleSheet('')
    main.setStyleSheet(MAIN_STYLE)
