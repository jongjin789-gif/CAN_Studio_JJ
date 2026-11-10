"""Editable property/value rows using the existing field widgets."""
from PyQt5.QtCore import QSize
from PyQt5.QtWidgets import QAbstractItemView, QHeaderView, QLabel, QTableWidget, QTableWidgetItem

PROPERTY_TABLE_STYLE = '''
QTableWidget { background-color: #ffffff; alternate-background-color: #ffffff; gridline-color: #e2e5e9; }
QHeaderView::section { background-color: #f7f8fa; color: #334155; padding: 3px; border: 1px solid #e2e5e9; }
'''


class PropertyTable(QTableWidget):
    def __init__(self, parent=None):
        super().__init__(0, 2, parent)
        self.setHorizontalHeaderLabels(['항목', '설정값'])
        self.verticalHeader().hide()
        self.setAlternatingRowColors(False)
        self.setStyleSheet(PROPERTY_TABLE_STYLE)
        self.setSelectionMode(QAbstractItemView.NoSelection)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.horizontalHeader().setSectionResizeMode(0, QHeaderView.Interactive)
        self.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.setColumnWidth(0, 140)
        self.horizontalHeader().setMinimumSectionSize(100)
        self.setMinimumHeight(170)
        self._fields = {}

    def addRow(self, label, field):
        row = self.rowCount()
        self.insertRow(row)
        item = QTableWidgetItem(label)
        item.setToolTip(label)
        self.setItem(row, 0, item)
        if not isinstance(field, QLabel):
            field.setMaximumWidth(min(260, field.maximumWidth()))
        self.setCellWidget(row, 1, field)
        self._fields[field] = row
        self.setRowHeight(row, max(32, min(72, field.sizeHint().height() + 8)))

    def setRowVisible(self, field, visible):
        self.setRowHidden(self._fields[field], not visible)
        field.setVisible(visible)

    def sizeHint(self):
        height = self.horizontalHeader().height() + sum(
            self.rowHeight(row) for row in range(self.rowCount()) if not self.isRowHidden(row)) + 4
        return QSize(460, min(430, max(170, height)))
