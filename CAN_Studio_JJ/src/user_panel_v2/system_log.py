"""Bounded system events shared by panel controls and CAN operations."""
from datetime import datetime
import time
from PyQt5.QtGui import QTextCursor
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QPlainTextEdit, QFileDialog, QApplication


class SystemLog(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._last = None
        self._count = 0
        self._time = 0
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        bar = QHBoxLayout()
        bar.addWidget(QLabel('시스템 로그'))
        bar.addStretch()
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setMaximumBlockCount(2000)
        for title, callback in [('복사', self.copy_all), ('저장', self.save), ('지우기', self.clear)]:
            button = QPushButton(title)
            button.clicked.connect(callback)
            bar.addWidget(button)
        layout.addLayout(bar)
        layout.addWidget(self.text)

    def append(self, message, level='INFO'):
        key = (level, str(message))
        now = time.monotonic()
        if key == self._last and now - self._time < 2:
            self._count += 1
            block = self.text.document().lastBlock()
            cursor = QTextCursor(block)
            cursor.movePosition(QTextCursor.EndOfBlock, QTextCursor.KeepAnchor)
            cursor.insertText(self._line + f' (반복 {self._count}회)')
            self.text.setTextCursor(cursor)
            return
        self._last, self._time, self._count = key, now, 1
        stamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        self._line = f'[{stamp}] {level} {message}'
        self.text.appendPlainText(self._line)

    def copy_all(self):
        QApplication.clipboard().setText(self.text.toPlainText())

    def clear(self):
        self.text.clear()
        self._last = None

    def save(self):
        path, _ = QFileDialog.getSaveFileName(self, '시스템 로그 저장', '', 'Text (*.log *.txt)')
        if path:
            try:
                with open(path, 'w', encoding='utf-8') as stream:
                    stream.write(self.text.toPlainText())
                self.append(f'로그 저장: {path}')
            except OSError as exc:
                self.append(f'로그 저장 실패: {exc}', 'ERROR')
