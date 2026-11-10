"""Graph configuration export/restore; samples and synchronization are transient."""
import copy
from PyQt5.QtCore import Qt, QRect
from PyQt5.QtGui import QColor, QIcon, QPixmap
from PyQt5.QtWidgets import QApplication, QListWidgetItem
import pyqtgraph as pg
from src.session_storage import compile_formula


def restore_geometry(window, values):
    """Restore client coordinates while keeping the whole window frame on screen."""
    if not values:
        return
    x, y, width, height = map(int, values)
    screens = QApplication.screens()
    rect = QRect(x, y, max(250, width), max(180, height))
    screen = next((s for s in screens if s.availableGeometry().contains(rect.center())),
                  QApplication.primaryScreen())
    area = screen.availableGeometry()
    # Staged session windows have not been shown yet. Create the native handle
    # so Qt can report their title bar and border margins before positioning.
    window.winId()
    margins = window.windowHandle().frameMargins()
    client_area = area.marginsRemoved(margins)
    rect.setWidth(min(rect.width(), client_area.width()))
    rect.setHeight(min(rect.height(), client_area.height()))
    rect.moveLeft(max(client_area.left(), min(rect.left(), client_area.right() - rect.width() + 1)))
    rect.moveTop(max(client_area.top(), min(rect.top(), client_area.bottom() - rect.height() + 1)))
    window.setGeometry(rect)


def graph_state(graph):
    legend = []
    for i in range(graph.legend_widget.count()):
        key = graph.legend_widget.item(i).data(Qt.UserRole)
        legend.append(dict(key=key, name=graph.display_names[key],
                           color=list(graph.curve_colors[key]), style=int(graph.curve_styles[key]),
                           visible=graph.curves[key].isVisible(), unit=graph.units.get(key, '')))
    vb = graph.plot_widget.getViewBox()
    return dict(signals=list(graph.signal_names), bindings=copy.deepcopy(graph.signal_bindings),
                title=graph._current_title, legend=legend,
                formulas={key: {k: v for k, v in value.items() if k != 'compiled'}
                          for key, value in graph.formulas.items()},
                geometry=graph.geometry().getRect(), sync=False,
                autoscroll=graph.btn_autoscroll.isChecked(), crosshair=graph.chk_crosshair.isChecked(),
                hover=graph.combo_hover_signal.currentData(), playing=graph.is_playing,
                y_range=list(vb.viewRange()[1]), y_auto=bool(vb.autoRangeEnabled()[1]),
                x_span=(graph.time_span if graph.btn_autoscroll.isChecked() else
                        max(0.01, vb.viewRange()[0][1] - vb.viewRange()[0][0])))


def restore_graph(graph, state):
    graph.signal_bindings = copy.deepcopy(state['bindings'])
    graph.time_span = float(state.get('x_span', 30))
    formulas = {}
    for key in ('Y1', 'Y2', 'Y3'):
        value = dict(state.get('formulas', {}).get(key, {}))
        value.setdefault('enabled', False)
        for field in ('expr', 'name', 'unit'):
            value.setdefault(field, '')
        value['compiled'] = compile_formula(value['expr'], len(graph.signal_names))
        formulas[key] = value
    graph.apply_formulas(formulas)
    graph._current_title = state['title']
    graph.plot_widget.setTitle(state['title'] or None)
    graph.setWindowTitle(graph.window_title_prefix + (': ' + state['title'] if state['title'] else ''))
    graph.legend_widget.clear()
    graph.combo_hover_signal.clear()
    for item in state['legend']:
        key = item['key']
        if key not in graph.curves:
            raise ValueError(f'그래프 범례 참조가 없습니다: {key}')
        graph.display_names[key] = item['name']
        graph.units[key] = item.get('unit', '')
        graph.curve_colors[key] = tuple(item['color'])
        graph.curve_styles[key] = Qt.PenStyle(item['style'])
        graph.curves[key].setPen(pg.mkPen(color=item['color'], style=Qt.PenStyle(item['style']),
                                        width=2 if key in formulas else 1))
        graph.curves[key].setVisible(item['visible'])
        pixmap = QPixmap(16, 16)
        pixmap.fill(QColor(*item['color']))
        row = QListWidgetItem(QIcon(pixmap), item['name'])
        row.setData(Qt.UserRole, key)
        row.setToolTip(item['name'])
        if not item['visible']:
            row.setForeground(QColor('gray'))
            font = row.font()
            font.setStrikeOut(True)
            row.setFont(font)
            row.setFlags(row.flags() & ~Qt.ItemIsSelectable)
        graph.legend_widget.addItem(row)
        graph.combo_hover_signal.addItem(item['name'], key)
    if graph.legend_widget.count() != len(graph.curves):
        raise ValueError('그래프 범례가 누락되었거나 중복됩니다.')
    graph.chk_sync.setChecked(False)
    graph.chk_crosshair.setChecked(state.get('crosshair', False))
    graph.btn_autoscroll.setChecked(state.get('autoscroll', True))
    graph._set_playing_state(state.get('playing', True), sync=False)
    graph.combo_hover_signal.setCurrentIndex(max(0, graph.combo_hover_signal.findData(state.get('hover'))))
    graph.plot_widget.setYRange(*state['y_range'], padding=0)
    graph.plot_widget.enableAutoRange(axis='y', enable=state.get('y_auto', True))
    # Absolute timestamps belong to the old capture, only preserve the time span.
    graph.plot_widget.setXRange(0, state.get('x_span', 30), padding=0)
    restore_geometry(graph, state.get('geometry'))
