"""Scrollable group/tab contents using the same cell pitch as the panel canvas."""
from PyQt5.QtCore import QRect, QSize, QTimer
from PyQt5.QtWidgets import QGridLayout, QLayout, QScrollArea, QSizePolicy, QWidget


class FixedCellGrid(QGridLayout):
    def __init__(self, parent, cell_size):
        super().__init__(parent)
        self.cell_size = cell_size
        self.setContentsMargins(4, 4, 4, 4)
        self.setSpacing(0)
        self.setSizeConstraint(QLayout.SetNoConstraint)

    def cellRect(self, row, column):
        left, top, _, _ = self.getContentsMargins()
        origin = self.geometry().topLeft()
        return QRect(origin.x() + left + column * self.cell_size,
                     origin.y() + top + row * self.cell_size,
                     self.cell_size, self.cell_size)

    def setGeometry(self, rect):
        # QGridLayout's default engine compresses tracks when its parent is small.
        # Keep logical cells at the canvas pitch and let the scroll area clip them.
        QLayout.setGeometry(self, rect)
        for index in range(self.count()):
            item = self.itemAt(index)
            row, column, rows, columns = self.getItemPosition(index)
            cell = self.cellRect(row, column)
            item.setGeometry(QRect(cell.x(), cell.y(), columns * self.cell_size, rows * self.cell_size))

    def contentSize(self):
        rows = columns = 0
        for index in range(self.count()):
            row, column, row_span, col_span = self.getItemPosition(index)
            rows = max(rows, row + row_span)
            columns = max(columns, column + col_span)
        left, top, right, bottom = self.getContentsMargins()
        return QSize(columns * self.cell_size + left + right,
                     rows * self.cell_size + top + bottom)

    def minimumSize(self):
        return QSize(0, 0)

    def sizeHint(self):
        return self.contentSize()


class ContainerGrid(QScrollArea):
    def __init__(self, cell_size, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QScrollArea.NoFrame)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self.canvas = QWidget()
        self.grid = FixedCellGrid(self.canvas, cell_size)
        self.setWidget(self.canvas)
        self._saved_scroll = (0, 0)

    def refresh_extent(self):
        self.canvas.setMinimumSize(self.grid.contentSize())
        self.grid.invalidate()
        self.grid.activate()

    def scroll_position(self):
        return self.horizontalScrollBar().value(), self.verticalScrollBar().value()

    def restore_scroll(self, position):
        self._saved_scroll = position
        self._apply_saved_scroll()
        # Ranges settle after the new tab/group is laid out and shown.
        QTimer.singleShot(0, self._apply_saved_scroll)

    def _apply_saved_scroll(self):
        self.horizontalScrollBar().setValue(self._saved_scroll[0])
        self.verticalScrollBar().setValue(self._saved_scroll[1])
