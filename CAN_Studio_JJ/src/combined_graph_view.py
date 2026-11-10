from src.error_dialog import show_error
import sys
import os
import datetime
import re
from PyQt5.QtWidgets import *
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QIcon, QPixmap, QPainter, QColor
import pyqtgraph as pg
from src.graph_toolbar import graph_button, graph_toolbar
from src.utils import get_resource_path

class CombinedGraphView(QWidget):
    """동기화된 여러 그래프를 하나의 창에 모아서 보여주는 뷰어"""
    def __init__(self, ordered_graphs, parent_window):
        super().__init__()
        # Closing an individual graph removes it from the owner's sync list.
        self.graphs = list(ordered_graphs)
        self.parent_window = parent_window
        self.original_parents = {} # 그래프 컨테이너의 원래 부모 위젯을 저장
        self.proxies = [] # 마우스 이동 시그널 프록시를 저장하여 연결 해제에 사용
        self._xrange_slots = []
        self._syncing_xrange = False
        self.parent_is_closing = False # 부모 창이 닫히고 있는지 여부를 나타내는 플래그
        self._restore_individual_requested = False

        self.setWindowTitle("Combined Synchronized View")
        self.setWindowIcon(QIcon(get_resource_path(os.path.join("icon", "graph.png"))))
        self.resize(1200, 800)

        self.init_ui()
        self.reparent_graphs()

    def init_ui(self):
        main_layout = QVBoxLayout(self)

        # --- 상단 컨트롤 패널 ---
        
        self.btn_start = graph_button(self, "START", "start")
        self.btn_stop = graph_button(self, "STOP", "stop")
        self.btn_reset_zoom = graph_button(self, "Reset Zoom", "reset_zoom")
        self.btn_fit_all_data = graph_button(self, "Fit All Data", "fit_all")
        self.chk_crosshair = graph_button(self, "Show Crosshair", "crosshair", checkable=True)
        self.chk_crosshair.setChecked(True)
        self.btn_clear_tags = graph_button(self, "Clear All Tags", "clear_tags")
        self.btn_screenshot = graph_button(self, "Screenshot", "screenshot")
        self.btn_exit_combined = graph_button(self, "통합보기 종료", "exit_combined")
        self.btn_exit_combined.setToolTip("통합보기 종료 (낱개 창으로 전환)")
        self.btn_close_all = graph_button(self, "그래프 종료", "close_all")
        self.btn_close_all.setToolTip("그래프 종료 (통합된 모든 그래프 닫기)")

        groups = [
            [self.btn_reset_zoom, self.btn_fit_all_data],
            [self.chk_crosshair, self.btn_clear_tags],
            [self.btn_screenshot],
        ]
        if self._realtime_graphs():
            groups.insert(0, [self.btn_start, self.btn_stop])
        else:
            self.btn_start.hide()
            self.btn_stop.hide()
        top_ctrl_layout = graph_toolbar(self, groups)
        # graph_toolbar ends with a stretch; keep both exit actions at the right.
        top_ctrl_layout.addWidget(self.btn_exit_combined)
        top_ctrl_layout.addWidget(self.btn_close_all)
        main_layout.addLayout(top_ctrl_layout)

        # --- 그래프들을 담을 스크롤 영역 ---
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        self.scroll_content = QWidget()
        self.scroll_layout = QVBoxLayout(self.scroll_content)
        self.scroll_layout.setSpacing(0)
        self.scroll_layout.setContentsMargins(0, 0, 0, 0)
        scroll_area.setWidget(self.scroll_content)
        main_layout.addWidget(scroll_area)

        # --- 이벤트 연결 ---
        self.btn_start.clicked.connect(lambda: self.set_graphs_playing(True))
        self.btn_stop.clicked.connect(lambda: self.set_graphs_playing(False))
        self.update_playback_buttons()
        self.btn_reset_zoom.clicked.connect(self.do_reset_zoom)
        self.btn_fit_all_data.clicked.connect(self.do_fit_all_data)
        self.chk_crosshair.toggled.connect(self.do_toggle_crosshair)
        self.btn_clear_tags.clicked.connect(self.do_clear_tags)
        self.btn_screenshot.clicked.connect(self.do_screenshot)
        self.btn_exit_combined.clicked.connect(self.exit_combined_view)
        self.btn_close_all.clicked.connect(self.close_all_graphs)

    def _realtime_graphs(self):
        return [graph for graph in self.graphs
                if callable(getattr(graph, '_set_playing_state', None))]

    def update_playback_buttons(self):
        graphs = self._realtime_graphs()
        self.btn_start.setEnabled(any(not graph.is_playing for graph in graphs))
        self.btn_stop.setEnabled(any(graph.is_playing for graph in graphs))

    def set_graphs_playing(self, playing):
        for graph in self._realtime_graphs():
            graph._set_playing_state(playing, sync=False)
        self.update_playback_buttons()

    def reparent_graphs(self):
        """원본 그래프 창에서 그래프 위젯을 가져와 통합 뷰에 재배치합니다."""
        for graph in self.graphs:
            self.original_parents[graph] = graph.graph_container.parent()
            graph.hide() # 원본 창 숨기기
            
            graph.graph_container.setParent(self.scroll_content)
            self.scroll_layout.addWidget(graph.graph_container, stretch=1)
            graph.plot_widget.setLabel('bottom', '') # X축 "Time" 레이블 생략

            # Add flag and reference for context-aware event handling
            graph.is_in_combined_view = True
            graph.combined_view_ref = self

        if not self.graphs:
            return

        # --- 내부 그래프 간 X축 및 마우스 이동 동기화 설정 ---
        # Sync data coordinates directly; native XLink scales ranges by viewport geometry.
        for graph in self.graphs:
            view_box = graph.plot_widget.getViewBox()
            slot = lambda _view_box, xrange, source=graph: self._sync_xrange(source, xrange)
            view_box.sigXRangeChanged.connect(slot)
            self._xrange_slots.append((view_box, slot))

        # 2. 마우스 이동 연결 (십자선 동기화)
        for graph in self.graphs:
            proxy = pg.SignalProxy(graph.plot_widget.scene().sigMouseMoved, rateLimit=60, slot=self.on_mouse_moved)
            self.proxies.append(proxy)

    def on_mouse_moved(self, evt):
        """통합 뷰 내의 한 그래프에서 마우스가 움직이면 모든 그래프에 전파합니다."""
        pos = evt[0]
        source_graph = None
        # 마우스가 현재 어느 그래프 위에 있는지 찾습니다.
        for g in self.graphs:
            if g.graph_container.underMouse():
                source_graph = g
                break
        
        if source_graph:
            mouse_point = source_graph.plot_widget.plotItem.vb.mapSceneToView(pos)
            target_x = mouse_point.x()
            
            for g in self.graphs:
                g.process_hover(target_x)
        else:
            for g in self.graphs:
                g.vLine.setVisible(False)
                g.hLine.setVisible(False)

    def do_reset_zoom(self):
        realtime_graphs = self._realtime_graphs()
        if not realtime_graphs:
            self.do_fit_all_data()
            return
        latest_values = [graph.get_latest_time() for graph in realtime_graphs]
        latest_values = [value for value in latest_values if value is not None]
        if not latest_values:
            for graph in self.graphs:
                graph.set_auto_y(True)
            return
        latest = max(latest_values)
        x_min = latest - realtime_graphs[0].time_span
        for index, graph in enumerate(self.graphs):
            graph.set_time_range(x_min, latest, follow_latest=True, apply_x=index == 0)

    def do_fit_all_data(self):
        bounds = [graph.get_time_bounds() for graph in self.graphs]
        bounds = [span for span in bounds if span is not None]
        if not bounds:
            for graph in self.graphs:
                graph.set_auto_y(True)
            return
        x_min = min(start for start, _ in bounds)
        x_max = max(end for _, end in bounds)
        if x_min == x_max:
            realtime_graphs = self._realtime_graphs()
            x_min -= realtime_graphs[0].time_span if realtime_graphs else 1.0
        for index, graph in enumerate(self.graphs):
            graph.set_time_range(x_min, x_max, follow_latest=False, apply_x=index == 0)

    def _sync_xrange(self, source, xrange):
        if self._syncing_xrange:
            return
        self._syncing_xrange = True
        try:
            for graph in self.graphs:
                if graph is not source:
                    graph.plot_widget.setXRange(xrange[0], xrange[1], padding=0)
        finally:
            self._syncing_xrange = False

    def do_toggle_crosshair(self, checked):
        for graph in self.graphs:
            if graph.chk_crosshair.isChecked() != checked:
                 graph.chk_crosshair.setChecked(checked)

    def do_clear_tags(self):
        for graph in self.graphs:
            graph.clear_all_tags()

    def _capture_graph_pixmaps(self):
        # Snapshot every graph before changing any shared layout constraints.
        # Capturing/restoring one at a time redistributes the remaining heights.
        states = []
        for graph in self.graphs:
            legend = graph.legend_widget
            margins = legend.contentsMargins()
            legend_width = max(legend.width(), legend.sizeHintForColumn(0) + margins.left() + margins.right())
            legend_height = (sum(max(0, legend.sizeHintForRow(row)) for row in range(legend.count()))
                             + margins.top() + margins.bottom()
                             + 2 * legend.spacing() * legend.count())
            widgets = [(widget, widget.minimumSize(), widget.maximumSize(), widget.sizePolicy(), widget.size())
                       for widget in (graph.graph_container, graph.plot_widget, legend)]
            states.append(dict(
                graph=graph, widgets=widgets, legend_width=legend_width,
                height=max(graph.plot_widget.height(), legend_height),
                v_policy=legend.verticalScrollBarPolicy(), h_policy=legend.horizontalScrollBarPolicy(),
                elide_mode=legend.textElideMode(), selection=legend.selectedItems(),
                v_scroll=legend.verticalScrollBar().value(), h_scroll=legend.horizontalScrollBar().value(),
            ))
        if not states:
            return []

        height = max(state['height'] for state in states)
        legend_width = max(state['legend_width'] for state in states)
        width = max(state['graph'].graph_container.width() - state['graph'].legend_widget.width()
                    + legend_width for state in states)
        try:
            for state in states:
                graph = state['graph']
                legend = graph.legend_widget
                margins = graph.graph_container.layout().contentsMargins()
                graph.graph_container.setFixedSize(width, height + margins.top() + margins.bottom())
                graph.plot_widget.setFixedHeight(height)
                legend.setFixedSize(legend_width, height)
                legend.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
                legend.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
                legend.setTextElideMode(Qt.ElideNone)
                legend.clearSelection()
            # Settle all graphs together, keeping their capture sizes fixed.
            QApplication.processEvents()
            QApplication.processEvents()
            for state in states:
                state['graph'].legend_widget.scrollToTop()
                state['graph'].legend_widget.horizontalScrollBar().setValue(0)
            return [state['graph'].graph_container.grab() for state in states]
        finally:
            for state in states:
                legend = state['graph'].legend_widget
                for widget, minimum, maximum, policy, size in state['widgets']:
                    widget.setMinimumSize(minimum)
                    widget.setMaximumSize(maximum)
                    widget.setSizePolicy(policy)
                    widget.resize(size)
                legend.setVerticalScrollBarPolicy(state['v_policy'])
                legend.setHorizontalScrollBarPolicy(state['h_policy'])
                legend.setTextElideMode(state['elide_mode'])
                for item in state['selection']:
                    item.setSelected(True)
                legend.doItemsLayout()
            QApplication.processEvents()
            QApplication.processEvents()
            for state in states:
                legend = state['graph'].legend_widget
                legend.verticalScrollBar().setValue(state['v_scroll'])
                legend.horizontalScrollBar().setValue(state['h_scroll'])

    def do_screenshot(self):
        pixmaps = self._capture_graph_pixmaps()
        if not pixmaps: return

        total_width = max(p.width() for p in pixmaps)
        total_height = sum(p.height() for p in pixmaps)

        combined_pixmap = QPixmap(total_width, total_height)
        combined_pixmap.fill(QColor("black"))
        
        painter = QPainter(combined_pixmap)
        y_offset = 0
        for p in pixmaps:
            painter.drawPixmap(0, y_offset, p)
            y_offset += p.height()
        painter.end()

        if getattr(sys, 'frozen', False): base_dir = os.path.dirname(sys.executable)
        else: base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        
        screenshot_dir = os.path.join(base_dir, "Screenshot")
        os.makedirs(screenshot_dir, exist_ok=True)

        now = datetime.datetime.now()
        time_str = now.strftime('%y%m%d_%H%M%S') + f"{now.microsecond // 100000}"
        
        raw_title = self.graphs[0]._current_title.strip() if self.graphs else ""
        safe_title = re.sub(r'[\\/*?:"<>|]', "_", raw_title).strip(" _") or "Combined"
            
        filename = f"COMBINED_{time_str}_{safe_title}.png"
        filepath = os.path.join(screenshot_dir, filename)

        try:
            combined_pixmap.save(filepath, "PNG")
            QMessageBox.information(self, "Screenshot Saved", f"스크린샷이 성공적으로 저장되었습니다:\n{filepath}")
        except Exception as e:
            show_error(self, "Error", f"스크린샷 저장 중 오류 발생:\n{str(e)}")

    def exit_combined_view(self):
        """Leave the combined view and show its individual graph windows."""
        self._restore_individual_requested = True
        self.close()

    def close_all_graphs(self):
        """Close the combined view and its graphs without showing individual windows."""
        self._restore_individual_requested = False
        self.close()

    def closeEvent(self, event):
        """Closing disposes graphs unless the explicit uncombine action was used."""
        # 1. 마우스 이동 시그널 프록시 연결 해제
        for proxy in self.proxies:
            proxy.disconnect()
        self.proxies.clear()
        for view_box, slot in self._xrange_slots:
            try:
                view_box.sigXRangeChanged.disconnect(slot)
            except (TypeError, RuntimeError):
                pass
        self._xrange_slots.clear()

        # Parent shutdown already closes the source windows.
        if self.parent_is_closing:
            if self.parent_window:
                self.parent_window.combined_view_window = None
            event.accept()
            return

        # 2. X축 연결 해제 및 위젯 복원
        for graph in self.graphs:
            graph.plot_widget.setLabel('bottom', 'Time') # X축 "Time" 레이블 복원
            # Reset flag and reference
            graph.is_in_combined_view = False
            graph.combined_view_ref = None

            # 원래 부모로 위젯 복원
            original_parent = self.original_parents.get(graph)
            if original_parent:
                graph.graph_container.setParent(original_parent)
                graph.layout().addWidget(graph.graph_container, stretch=1)
                if self._restore_individual_requested:
                    # 원래 창으로 돌아갈 때 전체 데이터가 보이도록 리셋합니다.
                    graph.plot_widget.autoRange()
                    graph.show()
            if not self._restore_individual_requested:
                graph.close()
                graph.deleteLater()

        self.graphs.clear()
        self.original_parents.clear()
        
        if self.parent_window:
            self.parent_window.combined_view_window = None
            
        event.accept()
