"""Present existing detail editors inside the property pane."""
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QScrollArea


def show_inline_editor(host, editor, on_accept):
    def layout_widgets(layout):
        for index in range(layout.count()):
            item = layout.itemAt(index)
            if item.widget() is not None:
                yield item.widget()
            elif item.layout() is not None:
                yield from layout_widgets(item.layout())

    previous = [w for w in layout_widgets(host.layout()) if not w.isHidden()]
    for widget in previous:
        widget.hide()
    editor.setWindowFlags(Qt.Widget)
    editor._inline_editing = True
    scroll = QScrollArea(host)
    scroll.setWidgetResizable(True)
    scroll.setWidget(editor)
    host.layout().addWidget(scroll, 1)
    host._inline_editor = editor

    def finish(accepted):
        if accepted:
            on_accept()
        host.layout().removeWidget(scroll)
        scroll.hide()
        scroll.deleteLater()
        host._inline_editor = None
        for widget in previous:
            widget.show()

    editor.accepted.connect(lambda: finish(True))
    editor.rejected.connect(lambda: finish(False))
    editor.show()
    scroll.show()
