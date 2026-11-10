"""Shared, panel-style controls for realtime, log and combined graphs."""
from PyQt5.QtCore import QSize, Qt
from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import QHBoxLayout, QFrame, QToolButton

from src.utils import get_resource_path


def graph_button(parent, text, icon, *, checkable=False):
    button = QToolButton(parent)
    button.setText(text)
    button.setToolTip(text)
    button.setAccessibleName(text)
    button.setIcon(QIcon(get_resource_path(f'icon/graph_controls/{icon}.svg')))
    button.setToolButtonStyle(Qt.ToolButtonIconOnly)
    button.setIconSize(QSize(26, 26))
    button.setFixedSize(38, 38)
    button.setCheckable(checkable)
    button.setStyleSheet('''
        QToolButton { background: transparent; border: 1px solid transparent;
                      border-radius: 6px; padding: 2px; }
        QToolButton:hover { background: #e3f5f7; border-color: #b8c8ce; }
        QToolButton:checked { background: #bfe9ee; border: 1px solid #2194a4;
                              border-bottom: 3px solid #145b73; }
        QToolButton:pressed { background: #8fd0da; border-color: #145b73; }
        QToolButton:focus { border: 1px dashed #145b73; }
        QToolButton:checked:focus { border-bottom: 3px solid #145b73; }
        QToolButton:disabled { background: transparent; border-color: transparent; }
    ''')
    return button


def graph_toolbar(parent, groups):
    """One row with separators between functional groups."""
    row = QHBoxLayout()
    row.setSpacing(4)
    for index, group in enumerate(groups):
        if index:
            separator = QFrame(parent)
            separator.setFrameShape(QFrame.VLine)
            separator.setFixedSize(1, 24)
            separator.setStyleSheet('background: #b8c8ce; border: none;')
            row.addSpacing(4)
            row.addWidget(separator, 0, Qt.AlignVCenter)
            row.addSpacing(4)
        for button in group:
            row.addWidget(button)
    row.addStretch()
    return row
