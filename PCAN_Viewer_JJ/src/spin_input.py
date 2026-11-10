"""Number inputs that are easy to type into.

A QSpinBox suffix/prefix is part of the editable text, so long explanations there get in
the way of typing. Keep only the number in the box, show the unit/explanation in a label
next to it, and select the whole value on focus so typing replaces it.
"""
from PyQt5.QtCore import QEvent, QObject, QTimer
from PyQt5.QtWidgets import QHBoxLayout, QLabel, QWidget


class _SelectAllOnFocus(QObject):
    def eventFilter(self, obj, event):
        if event.type() == QEvent.FocusIn:
            # After the click/Tab has been processed, otherwise it moves the cursor again.
            QTimer.singleShot(0, obj.selectAll)
        return False


def select_all_on_focus(spin):
    # The spin box itself receives the focus event (its line edit is a focus proxy).
    spin.installEventFilter(_SelectAllOnFocus(spin))
    return spin


def spin_with_hint(spin, hint, tooltip=None):
    """Return a widget with ``spin`` and a hint label; the label is ``widget.hint``."""
    select_all_on_focus(spin)
    holder = QWidget()
    layout = QHBoxLayout(holder)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(6)
    layout.addWidget(spin, 1)
    label = QLabel(hint)
    label.setStyleSheet('color: #555;')
    layout.addWidget(label)
    if tooltip:
        spin.setToolTip(tooltip)
        label.setToolTip(tooltip)
    holder.hint = label
    return holder
