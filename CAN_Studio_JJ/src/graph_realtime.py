from src.error_dialog import show_error
import sys
import os
import re
import datetime
import bisect
from collections import deque
from PyQt5.QtWidgets import *
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QIcon, QPixmap, QColor, QPainter, QFont
import pyqtgraph as pg
from src.graph_toolbar import graph_button, graph_toolbar
from src.utils import get_resource_path, TimeAxisItem, TagTextItem
from src.dialogs import LabelEditorDialog, FormulaDialog



class SignalGraphWindow(QWidget):
    """선택된 여러 시그널의 실시간 데이터를 3분간 누적하고, 최근 30초를 보여주는 다중 그래프 창"""
    def __init__(self, signal_names, main_window=None):
        super().__init__()
        self.signal_names = signal_names
        self.signal_bindings = {}
        self._bound_signals = {}
        self.time_span = 30.0
        self.main_window = main_window  # 다른 그래프 창들과의 통신을 위해 메인 윈도우 참조
        self.formulas = {}
        
        self.display_names = {name: name for name in signal_names}
        self.units = {name: "" for name in signal_names}
        self._current_title = ""
        self.window_title_prefix = "Real-time Graph"
        self.setWindowTitle(self.window_title_prefix)
        self.setWindowIcon(QIcon(get_resource_path(os.path.join("icon", "graph.png"))))
        self.resize(800, 600)
        layout = QVBoxLayout(self)

        # --- 상단 컨트롤 패널 ---
        top_ctrl_layout = QVBoxLayout()
        row2_layout = QHBoxLayout()
        
        self.btn_start = graph_button(self, "START", "start")
        self.btn_stop = graph_button(self, "STOP", "stop")
        
        self.btn_autoscroll = graph_button(self, "Auto-Scroll", "autoscroll")
        self.btn_autoscroll.setCheckable(True)
        self.btn_autoscroll.setChecked(True)
        self.btn_reset_zoom = graph_button(self, "Reset Zoom", "reset_zoom")
        self.chk_auto_y = graph_button(self, "Auto Y", "auto_y", checkable=True)
        self.chk_auto_y.setChecked(True)

        self.btn_combined_view = graph_button(self, "Combined View", "combined")
        
        self.chk_sync = graph_button(self, "Sync X-Axis", "sync_x", checkable=True)
        self.chk_sync.setChecked(False)
        
        self.chk_crosshair = graph_button(self, "Show Crosshair", "crosshair", checkable=True)
        self.chk_crosshair.setChecked(True)
        
        self.btn_clear_tags = graph_button(self, "Clear Tags", "clear_tags")
        
        self.btn_edit_labels = graph_button(self, "Edit Labels", "edit_labels")
        self.btn_edit_labels.clicked.connect(self.open_label_editor)

        self.btn_formula = graph_button(self, "Formula", "formula")
        self.btn_formula.clicked.connect(self.open_formula_editor)

        self.btn_screenshot = graph_button(self, "Screenshot", "screenshot")
        self.btn_screenshot.clicked.connect(self.take_screenshot)
        
        self.combo_hover_signal = QComboBox()
        self.combo_hover_signal.setToolTip("Hover signal")
        for name in signal_names:
            self.combo_hover_signal.addItem(name, name)
        
        self.label_info = QLabel("Hover over graph...")
        self.label_info.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        font = self.label_info.font()
        font.setBold(True)
        self.label_info.setFont(font)
        
        row1_layout = graph_toolbar(self, [
            [self.btn_start, self.btn_stop],
            [self.btn_autoscroll, self.btn_reset_zoom, self.chk_auto_y],
            [self.chk_sync, self.btn_combined_view],
            [self.chk_crosshair, self.btn_clear_tags],
            [self.btn_edit_labels, self.btn_formula, self.btn_screenshot],
        ])

        row2_layout.addStretch()
        row2_layout.addWidget(self.combo_hover_signal)
        row2_layout.addWidget(self.label_info)
        
        top_ctrl_layout.addLayout(row1_layout)
        top_ctrl_layout.addLayout(row2_layout)
        layout.addLayout(top_ctrl_layout)

        # --- 그래프 및 우측 범례 영역 (캡처를 위해 QWidget으로 묶기) ---
        self.graph_container = QWidget()
        graph_layout = QHBoxLayout(self.graph_container)
        graph_layout.setContentsMargins(0, 0, 0, 0)
        graph_layout.setSpacing(0)  # 그래프와 범례 위젯 사이의 간격을 없애서 자연스럽게 붙임
        
        time_axis = TimeAxisItem(orientation='bottom')
        self.plot_widget = pg.PlotWidget(title=None, axisItems={'bottom': time_axis})
        self.plot_widget.setLabel('bottom', 'Time')
        self.plot_widget.setLabel('left', 'Value')
        # 그래프 내부(PlotItem) 좌/우/상/하 여백을 주어, X축과 Y축 글자가 창 모서리에 붙지 않도록 공간 확보
        self.plot_widget.plotItem.setContentsMargins(15, 10, 10, 15) 
        self.plot_widget.showGrid(x=True, y=True)
        
        # 커스텀 범례 패널 (고정 너비로 설정하여 창 크기가 같으면 그래프 공간이 완벽하게 일치함)
        self.legend_widget = QListWidget()
        self.legend_widget.setFixedWidth(200) 
        self.legend_widget.setSelectionMode(QListWidget.MultiSelection) # 다중 선택 가능하도록 변경 (클릭으로 토글)
        self.legend_widget.setTextElideMode(Qt.ElideRight) # 긴 글자 깔끔하게 말줄임 처리(...)
        
        # 범례 배경색을 그래프 영역과 일치시키고, 패널 내부의 글자(아이템)들에 여백(padding)을 추가하여 답답함 해소
        self.legend_widget.setStyleSheet(
            "QListWidget { background-color: #000000; color: #e0e0e0; border: 1px solid #333; padding: 10px; outline: 0; }\n"
            "QListWidget::item { padding: 4px 0px; border: 1px solid transparent; }\n"
            "QListWidget::item:selected { background-color: #000000; color: #e0e0e0; border: 1px solid #0078d7; border-radius: 2px; }"
        )
        
        graph_layout.addWidget(self.plot_widget, stretch=1)
        graph_layout.addWidget(self.legend_widget)
        layout.addWidget(self.graph_container, stretch=1)
        
        # 십자선(Crosshair) 설정
        self.vLine = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen(color='gray', style=Qt.DashLine))
        self.hLine = pg.InfiniteLine(angle=0, movable=False, pen=pg.mkPen(color='gray', style=Qt.DashLine))
        self.plot_widget.addItem(self.vLine, ignoreBounds=True)
        self.plot_widget.addItem(self.hLine, ignoreBounds=True)
        self.chk_crosshair.toggled.connect(self.toggle_crosshair)

        self.curves = {}
        self.curve_colors = {}
        self.curve_styles = {}
        self.times = {name: deque() for name in signal_names}
        self.values = {name: deque() for name in signal_names}
        self.active_tags = {} # {(signal_name, t_val): (marker, tag)} 딕셔너리로 태그 관리
        
        # 가독성 있는 색상 팔레트
        colors = [
            (255, 85, 85),     # Bright Red
            (85, 255, 85),     # Bright Green
            (85, 170, 255),    # Light Blue
            (255, 255, 85),    # Bright Yellow
            (255, 170, 0),     # Orange
            (255, 85, 255),    # Bright Magenta
            (85, 255, 255),    # Bright Cyan
            (170, 170, 255),   # Soft Blue
            (255, 170, 170),   # Soft Pink
            (170, 255, 170),   # Soft Green
        ]
        
        for i, name in enumerate(signal_names):
            color = colors[i % len(colors)]
            self.curve_colors[name] = color
            self.curve_styles[name] = Qt.SolidLine
            self.curves[name] = self.plot_widget.plot(pen=pg.mkPen(color=color, width=1, style=self.curve_styles[name]), name=name)
            
            # 우측 범례 패널에 항목 추가 (색상 박스 아이콘 + 이름)
            pixmap = QPixmap(16, 16)
            pixmap.fill(QColor(*color))
            item = QListWidgetItem(QIcon(pixmap), name)
            item.setToolTip(name) # 이름이 길어 잘릴 경우 마우스를 올리면 전체 이름 표시
            item.setData(Qt.UserRole, name)
            self.legend_widget.addItem(item)

        # 상태 변수
        self.is_playing = True
        self.btn_start.setEnabled(False)
        self.is_in_combined_view = False
        self.combined_view_ref = None
        
        # X축 강제 갱신 중 무한 루프(Event Recursion) 방지용 플래그
        self._updating_xrange = False
        self._updating_size = False
        
        # 이벤트 연결
        self.btn_start.clicked.connect(self.start_graph)
        self.btn_stop.clicked.connect(self.stop_graph)
        self.btn_autoscroll.clicked.connect(self.on_autoscroll_clicked)
        self.btn_reset_zoom.clicked.connect(self.reset_zoom)
        self.chk_auto_y.toggled.connect(self.set_auto_y)
        self.chk_sync.toggled.connect(self.notify_sync_toggle)
        self.btn_combined_view.clicked.connect(self.request_combined_view)
        self.btn_clear_tags.clicked.connect(self.on_clear_tags_clicked)
        
        # 마우스 이벤트 캡처 (데이터 위치 확인용)
        self.proxy = pg.SignalProxy(self.plot_widget.scene().sigMouseMoved, rateLimit=60, slot=self.mouse_moved)
        
        # 사용자가 마우스로 뷰를 조작하면 오토스크롤 비활성화
        self.plot_widget.getViewBox().sigRangeChangedManually.connect(self.disable_autoscroll)
        # X축 범위가 변경될 때 동기화 이벤트를 위해 연결
        self.plot_widget.getViewBox().sigXRangeChanged.connect(self.on_xrange_changed)
        
        # 범례 더블클릭 이벤트 연결 (그래프 숨기기/보이기)
        self.legend_widget.itemDoubleClicked.connect(self.on_legend_double_clicked)
        
        # Hover Target 콤보박스 변경 이벤트 연결
        self.combo_hover_signal.currentIndexChanged.connect(self.on_hover_target_changed)
        # 초기 생성 시 현재 선택된 항목에 하이라이트 적용
        self.on_hover_target_changed()
        
        # 마우스 클릭 이벤트 (원하는 데이터 지점에 태그/마커 고정)
        self.plot_widget.scene().sigMouseClicked.connect(self.on_mouse_clicked)
        self.set_auto_y(True)
        # Register new windows in the owner's ordered sync list by default.
        self.chk_sync.setChecked(True)

    def get_parent_window(self):
        """상위 부모 윈도우(메인 또는 뷰어)를 반환합니다."""
        return getattr(self, 'main_window', None) or getattr(self, 'viewer_window', None)

    def notify_sync_toggle(self, checked):
        """Sync 체크박스 상태가 변경될 때 부모 윈도우에 알립니다."""
        parent = self.get_parent_window()
        if parent and hasattr(parent, 'handle_sync_toggle'):
            parent.handle_sync_toggle(self, checked)

    def request_combined_view(self):
        """'Combined View' 버튼 클릭 시 부모 윈도우에 통합 뷰 생성을 요청합니다."""
        parent = self.get_parent_window()
        if parent and hasattr(parent, 'open_combined_view'):
            parent.open_combined_view()

    def closeEvent(self, event):
        parent = self.get_parent_window()
        for name in ('active_graphs', 'synced_graphs_ordered'):
            graphs = getattr(parent, name, [])
            if self in graphs:
                graphs.remove(self)
        event.accept()

    def _get_enum_string(self, sig_name, val):
        """시그널 이름과 값을 받아 해당하는 Enum 텍스트를 반환합니다."""
        if sig_name in self._bound_signals:
            choices = self._bound_signals[sig_name][1].choices
            try:
                return str(choices[int(val)]) if choices and int(val) in choices else None
            except (ValueError, OverflowError):
                return None
        if getattr(self, 'main_window', None) and hasattr(self.main_window, 'signal_choices'):
            if sig_name.startswith("B"):
                parts = sig_name.split(":", 1)
                if len(parts) == 2:
                    try:
                        b_num = int(parts[0][1:])
                        s_name = parts[1]
                        choices = self.main_window.signal_choices.get((b_num, s_name))
                        if choices and int(val) in choices:
                            return str(choices[int(val)])
                    except:
                        pass
        return None

    def open_label_editor(self):
        # 1. Gather current data for all signals in the current order
        signals_data = []
        for i in range(self.legend_widget.count()):
            item = self.legend_widget.item(i)
            orig_name = item.data(Qt.UserRole)
            signals_data.append({
                'orig_name': orig_name,
                'disp_name': self.display_names.get(orig_name, orig_name),
                'color': self.curve_colors.get(orig_name, (255, 255, 255)),
                'style': self.curve_styles.get(orig_name, Qt.SolidLine)
            })

        dialog = LabelEditorDialog(self._current_title, signals_data, self)
        if dialog.exec_() == QDialog.Accepted:
            new_title, new_signals_data = dialog.get_values()

            # 2. Update graph title
            self._current_title = new_title
            if new_title:
                self.plot_widget.setTitle(new_title)
                self.setWindowTitle(f"{self.window_title_prefix}: {new_title}")
            else:
                self.plot_widget.setTitle(None)
                self.setWindowTitle(self.window_title_prefix)
            
            # 3. Clear existing legend and hover combo to re-populate in new order
            self.legend_widget.clear()
            self.combo_hover_signal.clear()

            # 4. Apply new properties and re-populate UI elements
            for data in new_signals_data:
                orig_name = data['orig_name']
                
                # Update internal dictionaries
                self.display_names[orig_name] = data['disp_name']
                self.curve_colors[orig_name] = data['color']
                self.curve_styles[orig_name] = data['style']
                
                # Update curve pen
                if orig_name in self.curves:
                    pen = self.curves[orig_name].opts['pen']
                    pen.setColor(QColor(*data['color']))
                    pen.setStyle(data['style'])
                    self.curves[orig_name].setPen(pen)

                # Re-add to legend widget
                pixmap = QPixmap(16, 16)
                pixmap.fill(QColor(*data['color']))
                item = QListWidgetItem(QIcon(pixmap), data['disp_name'])
                item.setToolTip(data['disp_name'])
                item.setData(Qt.UserRole, orig_name)
                
                # Restore visibility state
                if orig_name in self.curves and not self.curves[orig_name].isVisible():
                    item.setForeground(QColor("gray"))
                    font = item.font()
                    font.setStrikeOut(True)
                    item.setFont(font)
                    item.setFlags(item.flags() & ~Qt.ItemIsSelectable)
                
                self.legend_widget.addItem(item)
                
                # Re-add to hover combo box
                self.combo_hover_signal.addItem(data['disp_name'], orig_name)
                
            # 5. Update existing tags
            for (sig_name, t_val), (marker, tag) in self.active_tags.items():
                if tag and sig_name in self.display_names:
                    disp_name = self.display_names[sig_name]
                    unit = self.units.get(sig_name, "")
                    unit_str = f" {unit}" if unit else ""
                    try: t_val_str = datetime.datetime.fromtimestamp(t_val).strftime('%H:%M:%S.%f')[:-3]
                    except: t_val_str = f"{t_val:.3f}"
                    y_val = getattr(marker, 'y_val', 0.0)

                    enum_str = self._get_enum_string(sig_name, y_val)
                    val_display = f"{enum_str} ({y_val:.3f})" if enum_str else f"{y_val:.3f}"
                    tag.setText(f"{disp_name}\n{t_val_str}\n{val_display}{unit_str}")
                    
                    # Update tag color
                    new_color = self.curve_colors.get(sig_name)
                    if new_color:
                        color_with_alpha = QColor(*new_color)
                        color_with_alpha.setAlpha(220)
                        tag.fill = color_with_alpha
                        marker.setBrush(pg.mkBrush(new_color))

            # 6. Refresh hover target highlight
            self.on_hover_target_changed()

    def open_formula_editor(self):
        legend_items = []
        for i in range(self.legend_widget.count()):
            legend_items.append(self.legend_widget.item(i).data(Qt.UserRole))
            
        dialog = FormulaDialog(self.formulas, legend_items, self)
        if dialog.exec_() == QDialog.Accepted:
            new_formulas = dialog.get_formulas()
            self.apply_formulas(new_formulas)

    def apply_formulas(self, new_formulas):
        self.formulas = new_formulas
        
        colors = {
            'Y1': (255, 255, 255),
            'Y2': (255, 170, 255),
            'Y3': (85, 255, 255)
        }
        
        self.evaluate_formulas_retroactive()
        
        for y_name in ['Y1', 'Y2', 'Y3']:
            data = self.formulas[y_name]
            if data['enabled']:
                display_name = data['name'] if data['name'] else f"{y_name} ({data['expr']})"
                unit = data['unit']
                if y_name not in self.times:
                    self.times[y_name] = deque()
                    self.values[y_name] = deque()
                    
                if y_name not in self.curves:
                    color = colors[y_name]
                    self.curve_colors[y_name] = color
                    self.curve_styles[y_name] = Qt.DashLine
                    self.curves[y_name] = self.plot_widget.plot(list(self.times[y_name]), list(self.values[y_name]), pen=pg.mkPen(color=color, width=2, style=self.curve_styles[y_name]), name=y_name)
                    self.display_names[y_name] = display_name
                    self.units[y_name] = unit
                    
                    pixmap = QPixmap(16, 16)
                    pixmap.fill(QColor(*color))
                    item = QListWidgetItem(QIcon(pixmap), self.display_names[y_name])
                    item.setToolTip(self.display_names[y_name])
                    item.setData(Qt.UserRole, y_name)
                    self.legend_widget.addItem(item)
                    self.combo_hover_signal.addItem(self.display_names[y_name], y_name)
                else:
                    self.display_names[y_name] = display_name
                    self.units[y_name] = unit
                    
                    pen = self.curves[y_name].opts['pen']
                    pen.setStyle(self.curve_styles[y_name])
                    self.curves[y_name].setPen(pen)
                    self.curves[y_name].setData(list(self.times[y_name]), list(self.values[y_name]))
                    for i in range(self.legend_widget.count()):
                        item = self.legend_widget.item(i)
                        if item.data(Qt.UserRole) == y_name:
                            item.setText(self.display_names[y_name])
                            item.setToolTip(self.display_names[y_name])
                            break
                    idx = self.combo_hover_signal.findData(y_name)
                    if idx >= 0:
                        self.combo_hover_signal.setItemText(idx, self.display_names[y_name])
                        
                for (sig_name, t_val), (marker, tag) in self.active_tags.items():
                    if sig_name == y_name and tag:
                        unit_str = f" {unit}" if unit else ""
                        try: t_val_str = datetime.datetime.fromtimestamp(t_val).strftime('%H:%M:%S.%f')[:-3]
                        except: t_val_str = f"{t_val:.3f}"
                        y_val = getattr(marker, 'y_val', 0.0)

                        enum_str = self._get_enum_string(y_name, y_val)
                        val_display = f"{enum_str} ({y_val:.3f})" if enum_str else f"{y_val:.3f}"
                        tag.setText(f"{display_name}\n{t_val_str}\n{val_display}{unit_str}")
            else:
                if y_name in self.curves:
                    self.plot_widget.removeItem(self.curves[y_name])
                    del self.curves[y_name]
                    del self.curve_colors[y_name]
                    del self.curve_styles[y_name]
                    del self.times[y_name]
                    del self.values[y_name]
                    if y_name in self.display_names:
                        del self.display_names[y_name]
                    
                    for i in range(self.legend_widget.count()):
                        item = self.legend_widget.item(i)
                        if item.data(Qt.UserRole) == y_name:
                            self.legend_widget.takeItem(i)
                            break
                            
                    idx = self.combo_hover_signal.findData(y_name)
                    if idx >= 0:
                        self.combo_hover_signal.removeItem(idx)
                        
                    keys_to_remove = [k for k in self.active_tags.keys() if k[0] == y_name]
                    for k in keys_to_remove:
                        marker, tag = self.active_tags[k]
                        self.remove_tag(k[0], k[1], tag, marker)

    def evaluate_formulas_retroactive(self):
        if not getattr(self, 'formulas', None):
            return
            
        x_mapping = {}
        x_count = 0
        for i in range(self.legend_widget.count()):
            sig = self.legend_widget.item(i).data(Qt.UserRole)
            if sig not in ['Y1', 'Y2', 'Y3']:
                x_count += 1
                x_mapping[f"X{x_count}"] = sig
                
        all_times = set()
        for sig in x_mapping.values():
            if sig in self.times:
                all_times.update(self.times[sig])
        
        sorted_times = sorted(list(all_times))
        if not sorted_times: return
        
        env_arrays = {x_var: [] for x_var in x_mapping}
        for x_var, sig in x_mapping.items():
            if sig in self.times:
                t_list = list(self.times[sig])
                v_list = list(self.values[sig])
                for t in sorted_times:
                    idx = bisect.bisect_right(t_list, t) - 1
                    if idx < 0: env_arrays[x_var].append(None)
                    else: env_arrays[x_var].append(v_list[idx])
            else:
                env_arrays[x_var] = [None] * len(sorted_times)
                
        for y_name in ['Y1', 'Y2', 'Y3']:
            f_data = self.formulas.get(y_name)
            if f_data and f_data['enabled'] and f_data['compiled']:
                y_times, y_values = deque(), deque()
                env_arrays[y_name] = []
                for i, t in enumerate(sorted_times):
                    env = {}
                    for x_var in x_mapping:
                        val = env_arrays[x_var][i]
                        if val is not None:
                            env[x_var] = val
                            
                    if 'Y1' in env_arrays and len(env_arrays['Y1']) > i and env_arrays['Y1'][i] is not None: env['Y1'] = env_arrays['Y1'][i]
                    if 'Y2' in env_arrays and len(env_arrays['Y2']) > i and env_arrays['Y2'][i] is not None: env['Y2'] = env_arrays['Y2'][i]
                    if 'Y3' in env_arrays and len(env_arrays['Y3']) > i and env_arrays['Y3'][i] is not None: env['Y3'] = env_arrays['Y3'][i]
                    
                    val_out = None
                    try:
                        val_out = float(eval(f_data['compiled'], {"__builtins__": None}, env))
                        y_times.append(t)
                        y_values.append(val_out)
                    except Exception: pass
                    env_arrays[y_name].append(val_out)
                    
                self.times[y_name] = y_times
                self.values[y_name] = y_values

    def evaluate_formulas(self, timestamp):
        if not getattr(self, 'formulas', None):
            return
            
        x_mapping = {}
        x_count = 0
        for i in range(self.legend_widget.count()):
            sig = self.legend_widget.item(i).data(Qt.UserRole)
            if sig not in ['Y1', 'Y2', 'Y3']:
                x_count += 1
                x_mapping[f"X{x_count}"] = sig
                
        env = {}
        for x_var, sig_name in x_mapping.items():
            if sig_name in self.values and len(self.values[sig_name]) > 0:
                env[x_var] = self.values[sig_name][-1]
                
        for y_name in ['Y1', 'Y2', 'Y3']:
            f_data = self.formulas.get(y_name)
            if f_data and f_data['enabled'] and f_data['compiled']:
                try:
                    val = eval(f_data['compiled'], {"__builtins__": None}, env)
                    env[y_name] = val
                    
                    t_deque = self.times[y_name]
                    v_deque = self.values[y_name]
                    
                    if t_deque and t_deque[-1] == timestamp: v_deque[-1] = float(val)
                    else: t_deque.append(timestamp); v_deque.append(float(val))
                    while t_deque and (timestamp - t_deque[0]) > 180.0: t_deque.popleft(); v_deque.popleft()
                    self.curves[y_name].setData(list(t_deque), list(v_deque))
                except Exception: pass

    def take_screenshot(self):
        is_sync = self.chk_sync.isChecked()
        graphs_to_capture = []
        
        if is_sync and getattr(self, 'main_window', None):
            for graph in self.main_window.active_graphs:
                if graph.isVisible() and getattr(graph, 'chk_sync', None) and graph.chk_sync.isChecked():
                    graphs_to_capture.append(graph)
        else:
            graphs_to_capture.append(self)

        if not graphs_to_capture:
            return

        # 지정된 그래프 파트(그래프+범례+태그 등) 픽스맵 캡처
        pixmaps = []

        for g in graphs_to_capture:
            # 범례와 플롯의 원래 상태 저장
            legend_state = {
                'min_w': g.legend_widget.minimumWidth(), 'max_w': g.legend_widget.maximumWidth(),
                'min_h': g.legend_widget.minimumHeight(), 'max_h': g.legend_widget.maximumHeight(),
                'v_policy': g.legend_widget.verticalScrollBarPolicy(), 'h_policy': g.legend_widget.horizontalScrollBarPolicy(),
                'elide_mode': g.legend_widget.textElideMode(),
                'size_policy_h': g.legend_widget.sizePolicy().horizontalPolicy(),
                'size_policy_v': g.legend_widget.sizePolicy().verticalPolicy(),
                'fixed_width': g.legend_widget.width(), # Store current fixed width (should be 200)
            }
            plot_state = {
                'min_h': g.plot_widget.minimumHeight(), 'max_h': g.plot_widget.maximumHeight(),
                'size_policy_h': g.plot_widget.sizePolicy().horizontalPolicy(),
                'size_policy_v': g.plot_widget.sizePolicy().verticalPolicy(),
                'current_height': g.plot_widget.height(), # Store current height of plot
            }

            # 스크린샷을 위해 범례 위젯 임시 수정 (스크롤바 제거 및 전체 내용 표시)
            g.legend_widget.setMinimumSize(0, 0)
            g.legend_widget.setMaximumSize(16777215, 16777215)
            g.legend_widget.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            g.legend_widget.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            g.legend_widget.setTextElideMode(Qt.ElideNone)
            g.legend_widget.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred) # Allow sizeHint to be accurate

            # 레이아웃 업데이트를 강제하여 sizeHint가 정확한 값을 반환하도록 함
            ideal_size = g.legend_widget.sizeHint()
            
            # 범례의 이상적인 높이와 현재 플롯의 높이 중 더 큰 값으로 두 위젯의 높이를 맞춤
            target_height = max(plot_state['current_height'], ideal_size.height())

            g.legend_widget.setFixedSize(ideal_size.width(), target_height) # 너비는 이상적인 너비, 높이는 맞춘 높이
            g.plot_widget.setFixedHeight(target_height) # 플롯 위젯 높이도 맞춤
            
            # QApplication.processEvents()를 호출하여 레이아웃 변경이 화면에 반영되도록 함
            QApplication.processEvents()
            QApplication.processEvents() # 한 번 더 호출하여 안정성 확보

            # 선택 해제 및 GUI 업데이트 후 스크린샷 캡처
            saved_selection = g.legend_widget.selectedItems()
            g.legend_widget.clearSelection()
            QApplication.processEvents()
            pixmaps.append(g.graph_container.grab())

            # 원래 상태로 완벽하게 복원
            for item in saved_selection:
                item.setSelected(True)
            
            g.legend_widget.setMinimumSize(legend_state['min_w'], legend_state['min_h'])
            g.legend_widget.setMaximumSize(legend_state['max_w'], legend_state['max_h'])
            g.legend_widget.setVerticalScrollBarPolicy(legend_state['v_policy'])
            g.legend_widget.setHorizontalScrollBarPolicy(legend_state['h_policy'])
            g.legend_widget.setTextElideMode(legend_state['elide_mode'])
            g.legend_widget.setSizePolicy(legend_state['size_policy_h'], legend_state['size_policy_v'])
            g.legend_widget.setFixedWidth(legend_state['fixed_width']) # Restore fixed width

            # Restore plot widget's original size policy and remove fixed height constraint
            g.plot_widget.setSizePolicy(plot_state['size_policy_h'], plot_state['size_policy_v'])
            g.plot_widget.setMinimumHeight(0)
            g.plot_widget.setMaximumHeight(16777215)
        
        total_width = max(p.width() for p in pixmaps)
        total_height = sum(p.height() for p in pixmaps)

        combined_pixmap = QPixmap(total_width, total_height)
        combined_pixmap.fill(QColor("black")) # 여백은 기본적으로 검은색 처리
        
        painter = QPainter(combined_pixmap)
        y_offset = 0
        for p in pixmaps:
            painter.drawPixmap(0, y_offset, p)
            y_offset += p.height()
        painter.end()

        if getattr(sys, 'frozen', False):
            base_dir = os.path.dirname(sys.executable)
        else:
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        
        screenshot_dir = os.path.join(base_dir, "Screenshot")
        os.makedirs(screenshot_dir, exist_ok=True)

        now = datetime.datetime.now()
        # 0.1초 단위(100ms)만 남기기 위해 100,000으로 나눔 (0~9)
        time_str = now.strftime('%y%m%d_%H%M%S') + f"{now.microsecond // 100000}"
        
        raw_title = self._current_title.strip()
        # 특수문자를 언더바(_)로 치환하고 양끝의 공백 및 언더바 제거
        safe_title = re.sub(r'[\\/*?:"<>|]', "_", raw_title).strip(" _")
        if not safe_title:
            safe_title = "Untitled"
            
        filename = f"RT_{time_str}_{safe_title}.png"
        filepath = os.path.join(screenshot_dir, filename)

        try:
            combined_pixmap.save(filepath, "PNG")
            QMessageBox.information(self, "Screenshot Saved", f"스크린샷이 성공적으로 저장되었습니다:\n{filepath}")
        except Exception as e:
            show_error(self, "Error", f"스크린샷 저장 중 오류 발생:\n{str(e)}")

    def start_graph(self):
        self.is_playing = True
        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self._set_playing_state(True, sync=True)

    def stop_graph(self):
        self.is_playing = False
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self._set_playing_state(False, sync=True)
        
    def _set_playing_state(self, playing, sync=True):
        self.is_playing = playing
        self.btn_start.setEnabled(not playing)
        self.btn_stop.setEnabled(playing)
        
        # 동기화 옵션이 켜져 있으면 다른 그래프 창에도 같은 상태 전달
        if sync and self.chk_sync.isChecked() and self.main_window:
            for other in self.main_window.active_graphs:
                if other is not self and other.chk_sync.isChecked() and other.isVisible():
                    other._set_playing_state(playing, sync=False)

    def get_time_bounds(self):
        bounds = [(series[0], series[-1]) for series in self.times.values() if len(series)]
        if not bounds:
            return None
        return min(start for start, _ in bounds), max(end for _, end in bounds)

    def get_latest_time(self):
        latest = None
        for t_deque in self.times.values():
            if t_deque:
                if latest is None or t_deque[-1] > latest:
                    latest = t_deque[-1]
        return latest

    def on_autoscroll_clicked(self, checked):
        if checked:
            latest = self.get_latest_time()
            if latest is not None:
                self.plot_widget.setXRange(latest - 30.0, latest, padding=0)
                if self.chk_auto_y.isChecked():
                    self._apply_auto_y(True)

    def _apply_auto_y(self, enabled):
        view_box = self.plot_widget.getViewBox()
        view_box.setAutoVisible(y=enabled)
        view_box.enableAutoRange(axis='y', enable=enabled)
        if enabled:
            view_box.updateAutoRange()

    def set_auto_y(self, enabled):
        self._apply_auto_y(bool(enabled))

    def set_time_range(self, x_min, x_max, follow_latest=False, apply_x=True):
        was_blocked = self.btn_autoscroll.blockSignals(True)
        self.btn_autoscroll.setChecked(bool(follow_latest))
        self.btn_autoscroll.blockSignals(was_blocked)
        if apply_x:
            self.plot_widget.setXRange(float(x_min), float(x_max), padding=0)
        was_blocked = self.chk_auto_y.blockSignals(True)
        self.chk_auto_y.setChecked(True)
        self.chk_auto_y.blockSignals(was_blocked)
        self._apply_auto_y(True)

    def reset_zoom(self, latest=None):
        if latest is None:
            latest = self.get_latest_time()
        if latest is None:
            self.set_auto_y(True)
            return
        self.set_time_range(latest - self.time_span, latest, follow_latest=True)

    def disable_autoscroll(self):
        self.btn_autoscroll.setChecked(False)

    def on_xrange_changed(self, viewbox, xrange):
        """자신의 X축 범위가 변경되었을 때, 동기화가 켜진 다른 그래프들에 범위를 전송"""
        if self._updating_xrange:
            return
        if not self.chk_sync.isChecked():
            return
        if self.main_window:
            is_auto = self.btn_autoscroll.isChecked()
            for other in self.main_window.active_graphs:
                if other is not self and other.chk_sync.isChecked() and other.isVisible():
                    other.apply_xrange(xrange, is_auto)

    def apply_xrange(self, xrange, is_auto):
        """다른 그래프로부터 수신한 X축(시간) 범위 적용 (Y축은 각 데이터 스케일에 맞춤)"""
        if self._updating_xrange: return
        self._updating_xrange = True
        if not is_auto: self.btn_autoscroll.setChecked(False)
        self.plot_widget.setXRange(xrange[0], xrange[1], padding=0)
        self._updating_xrange = False

    def resizeEvent(self, event):
        """창 크기가 변경될 때 발생하는 이벤트를 가로채어 다른 동기화된 창들의 크기도 조절"""
        super().resizeEvent(event)
        if self._updating_size:
            return
        # UI 위젯이 전부 생성되기 전이거나, Sync 체크가 안 되어있으면 리턴
        if not hasattr(self, 'chk_sync') or not self.chk_sync.isChecked():
            return
        if self.main_window:
            new_size = event.size()
            for other in self.main_window.active_graphs:
                if other is not self and other.chk_sync.isChecked() and other.isVisible():
                    other.apply_size(new_size)

    def apply_size(self, new_size):
        if self._updating_size or self.size() == new_size: return
        self._updating_size = True
        self.resize(new_size)
        self._updating_size = False
        
    def toggle_crosshair(self, checked):
        if not checked:
            self.vLine.setVisible(False)
            self.hLine.setVisible(False)
            
    def on_legend_double_clicked(self, item):
        """범례 항목을 더블클릭하면 해당 그래프의 표시 여부를 토글합니다."""
        sig_name = item.data(Qt.UserRole)
        if sig_name in self.curves:
            curve = self.curves[sig_name]
            is_visible = curve.isVisible()
            curve.setVisible(not is_visible)
            
            if is_visible: # 숨기기 적용
                item.setForeground(QColor("gray"))
                font = item.font()
                font.setStrikeOut(True)
                item.setFont(font)
                item.setSelected(False) # 테두리 색 변경(선택) 즉시 해제
                item.setFlags(item.flags() & ~Qt.ItemIsSelectable) # 선택 불가 처리로 태그 출력 원천 차단
                
                # 태그 자동 삭제 로직 추가
                keys_to_remove = [k for k in self.active_tags.keys() if k[0] == sig_name]
                for k in keys_to_remove:
                    marker, tag = self.active_tags[k]
                    self.remove_tag(k[0], k[1], tag, marker)
            else: # 보이기 적용
                item.setForeground(QColor("#e0e0e0"))
                font = item.font()
                font.setStrikeOut(False)
                item.setFont(font)
                item.setFlags(item.flags() | Qt.ItemIsSelectable) # 선택 가능 복구
                item.setSelected(False) # 숨김 해제 시 기본적으로 선택(태그 활성화) 해제 상태 유지

    def on_hover_target_changed(self, index=None):
        """선택된 Hover Target의 선을 두껍게 하고 맨 앞으로 가져오기"""
        selected_signal = self.combo_hover_signal.currentData()
        for name, curve in self.curves.items():
            color = self.curve_colors[name]
            if name == selected_signal:
                curve.setPen(pg.mkPen(color=color, width=2, style=self.curve_styles.get(name, Qt.SolidLine)))  # 선 두껍게 강조
                curve.setZValue(10)                           # 레이어 맨 앞으로 배치
            else:
                curve.setPen(pg.mkPen(color=color, width=1, style=self.curve_styles.get(name, Qt.SolidLine)))  # 얇은 선 유지
                curve.setZValue(0)                            # 기본 레이어로 복귀
                
        # 태그(마커, 텍스트)도 함께 Z-value 조정
        for (sig_name, t_val), (marker, tag) in self.active_tags.items():
            z_val = 30 if sig_name == selected_signal else 20
            if marker: marker.setZValue(z_val)
            if tag: tag.setZValue(z_val)

    def on_mouse_clicked(self, evt):
        try:
            if evt.button() != Qt.LeftButton:
                return
                
            pos = evt.scenePos()
            if not self.plot_widget.sceneBoundingRect().contains(pos):
                return
                
            mouse_point = self.plot_widget.plotItem.vb.mapSceneToView(pos)
            
            # --- Determine graphs to apply action to ---
            graphs_to_tag = []
            if self.is_in_combined_view and self.combined_view_ref:
                graphs_to_tag = self.combined_view_ref.graphs
            else:
                graphs_to_tag.append(self)
                if self.chk_sync.isChecked():
                    parent_win = self.get_parent_window()
                    if parent_win:
                        for other in parent_win.active_graphs:
                            if other is not self and other.chk_sync.isChecked() and other.isVisible():
                                graphs_to_tag.append(other)

            # --- Logic to handle tag removal ---
            # Check the source graph (self) for a clicked tag first.
            source_target_signals = [item.data(Qt.UserRole) for item in self.legend_widget.selectedItems()]
            if not source_target_signals:
                selected_signal = self.combo_hover_signal.currentData()
                if selected_signal:
                    source_target_signals = [selected_signal]

            pixel_width = self.plot_widget.plotItem.vb.viewPixelSize()[0]
            tolerance_x = pixel_width * 15
            
            clicked_tag = None
            items = self.plot_widget.scene().items(pos)
            for item in items:
                if isinstance(item, TagTextItem):
                    clicked_tag = item
                    break
                elif isinstance(item, pg.ScatterPlotItem) and hasattr(item, 'tag'):
                    clicked_tag = getattr(item, 'tag', None)
                    break
                    
            closest_tag_key = None
            min_dist = float('inf')
            
            if clicked_tag:
                closest_tag_key = (clicked_tag.signal_name, clicked_tag.t_val)
            else:
                for (sig_name, t_val), (marker, tag) in list(self.active_tags.items()):
                    if sig_name in source_target_signals:
                        dist = abs(t_val - mouse_point.x())
                        if dist < min_dist:
                            min_dist = dist
                            closest_tag_key = (sig_name, t_val)
                            
            if closest_tag_key is not None and (clicked_tag or min_dist <= tolerance_x):
                marker, tag = self.active_tags.get(closest_tag_key, (None, None))
                if tag:
                    remove_group_id = getattr(tag, 'group_id', None)
                    if remove_group_id is not None:
                        # Remove this group from all target graphs
                        for g in graphs_to_tag:
                            g.remove_tags_by_group(remove_group_id)
                    else:
                        self.remove_tag(closest_tag_key[0], closest_tag_key[1], tag, marker)
                evt.accept()
                return

            # --- Logic to handle tag addition ---
            target_x = mouse_point.x()
            group_id = target_x
            
            for graph in graphs_to_tag:
                # For each graph, find its selected signals
                selected_items = [item.data(Qt.UserRole) for item in graph.legend_widget.selectedItems()]
                if not selected_items:
                    selected_signal = graph.combo_hover_signal.currentData()
                    if selected_signal:
                        selected_items = [selected_signal]
                
                target_signals_for_graph = [sig for sig in selected_items if sig in graph.curves and graph.curves[sig].isVisible()]

                for sig in target_signals_for_graph:
                    graph.add_tag_for_signal(sig, target_x, group_id)

            evt.accept()
            
        except Exception as e:
            self.label_info.setText(f"Click Error: {str(e)}")
                
    def on_clear_tags_clicked(self):
        self.clear_all_tags()
        if self.chk_sync.isChecked() and self.main_window:
            for other in self.main_window.active_graphs:
                if other is not self and other.chk_sync.isChecked() and other.isVisible():
                    other.clear_all_tags()

    def clear_all_tags(self):
        for marker, tag in self.active_tags.values():
            try:
                if tag: self.plot_widget.removeItem(tag)
                if marker: self.plot_widget.removeItem(marker)
            except Exception: pass
        self.active_tags.clear()

    def remove_tag(self, signal_name, t_val, tag_item, marker_item):
        """특정 태그와 마커를 안전하게 삭제"""
        try:
            if tag_item:
                self.plot_widget.removeItem(tag_item)
            if marker_item:
                self.plot_widget.removeItem(marker_item)
        except Exception:
            pass
            
        tag_key = (signal_name, float(t_val))
        if tag_key in self.active_tags:
            del self.active_tags[tag_key]

    def remove_tags_by_group(self, group_id):
        to_remove = []
        for (sig_name, t_val), (marker, tag) in self.active_tags.items():
            if getattr(tag, 'group_id', None) == group_id:
                to_remove.append((sig_name, t_val, tag, marker))
        for s, t, tg, m in to_remove:
            self.remove_tag(s, t, tg, m)

    def add_tag_for_signal(self, sig_name, target_x, group_id=None):
        if sig_name not in self.curves or not self.curves[sig_name].isVisible():
            return
            
        x_data, y_data = self.curves[sig_name].getData()
        if x_data is None or len(x_data) == 0:
            return
            
        if hasattr(x_data, 'tolist'): x_list = x_data.tolist()
        else: x_list = list(x_data)
        
        if hasattr(y_data, 'tolist'): y_list = y_data.tolist()
        else: y_list = list(y_data)
        
        idx = bisect.bisect_left(x_list, target_x)
        if idx == 0: closest_idx = 0
        elif idx == len(x_list): closest_idx = len(x_list) - 1
        else: closest_idx = idx if abs(x_list[idx] - target_x) < abs(x_list[idx-1] - target_x) else idx - 1
                
        t_val = float(x_list[closest_idx])
        y_val = float(y_list[closest_idx])
        
        tag_key = (sig_name, t_val)
        if tag_key in self.active_tags:
            return

        # --- Y-축 자동 확장 로직 (태그 가시성 확보) ---
        view_box = self.plot_widget.getViewBox()
        y_range = view_box.viewRange()[1]
        y_min, y_max = y_range[0], y_range[1]

        # 유효한 범위이고, 데이터 포인트가 상단에 가까울 때만 확장
        if y_max > y_min:
            # 데이터 포인트가 Y축 가시 범위의 상위 15% 내에 위치할 경우
            if (y_val - y_min) / (y_max - y_min) > 0.85:
                # Y축 범위를 현재 범위의 20%만큼 위로 확장
                new_y_max = y_max + (y_max - y_min) * 0.2
                view_box.setYRange(y_min, new_y_max, padding=0)
            
        try: t_val_str = datetime.datetime.fromtimestamp(t_val).strftime('%H:%M:%S.%f')[:-3]
        except: t_val_str = f"{t_val:.3f}"
            
        color = self.curve_colors[sig_name]
        marker = pg.ScatterPlotItem(x=[t_val], y=[y_val], size=12, pen=pg.mkPen('w', width=1), brush=pg.mkBrush(color))
        marker.signal_name = sig_name
        marker.t_val = t_val
        marker.y_val = y_val
        
        selected_signal = self.combo_hover_signal.currentData()
        z_val = 30 if sig_name == selected_signal else 20
        marker.setZValue(z_val)
        
        disp_name = self.display_names.get(sig_name, sig_name)
        unit = self.units.get(sig_name, "")
        unit_str = f" {unit}" if unit else ""

        enum_str = self._get_enum_string(sig_name, y_val)
        val_display = f"{enum_str} ({y_val:.3f})" if enum_str else f"{y_val:.3f}"
        tag_text = f"{disp_name}\n{t_val_str}\n{val_display}{unit_str}"
        tag = TagTextItem(tag_text, color, sig_name, t_val, marker)
        tag.setPos(t_val, y_val)
        tag.setZValue(z_val)
        
        tag.group_id = group_id if group_id is not None else target_x
        marker.group_id = tag.group_id
        
        self.plot_widget.addItem(marker)
        self.plot_widget.addItem(tag)
        
        self.active_tags[tag_key] = (marker, tag)

    def process_hover(self, target_x, target_y=None):
        try: mouse_x_str = datetime.datetime.fromtimestamp(target_x).strftime('%H:%M:%S.%f')[:-3]
        except: mouse_x_str = f"{target_x:.3f}"
        
        selected_signal = self.combo_hover_signal.currentData()
        if not selected_signal or selected_signal not in self.curves or not self.curves[selected_signal].isVisible():
            y_str = f"{target_y:.3f}" if target_y is not None else "N/A"
            self.label_info.setText(f"Time: {mouse_x_str}, Y: {y_str}")
            self.vLine.setVisible(False)
            self.hLine.setVisible(False)
            return
            
        x_data, y_data = self.curves[selected_signal].getData()
        if x_data is not None and len(x_data) > 0:
            idx = bisect.bisect_left(x_data, target_x)
            if idx == 0:
                closest_idx = 0
            elif idx == len(x_data):
                closest_idx = len(x_data) - 1
            else:
                if abs(x_data[idx] - target_x) < abs(x_data[idx-1] - target_x):
                    closest_idx = idx
                else:
                    closest_idx = idx - 1
                    
            t_val = x_data[closest_idx]
            y_val = y_data[closest_idx]
            
            try:
                t_val_str = datetime.datetime.fromtimestamp(t_val).strftime('%H:%M:%S.%f')[:-3]
            except:
                t_val_str = f"{t_val:.3f}"
        
            unit = self.units.get(selected_signal, "")
            unit_str = f" {unit}" if unit else ""

            enum_str = self._get_enum_string(selected_signal, y_val)
            val_display = f"{enum_str} ({y_val:.3f})" if enum_str else f"{y_val:.3f}"
            self.label_info.setText(f"Time: {t_val_str}, Val: {val_display}{unit_str}")
                
            if self.chk_crosshair.isChecked():
                self.vLine.setPos(t_val)
                self.hLine.setPos(y_val)
                self.vLine.setVisible(True)
                self.hLine.setVisible(True)
            else:
                self.vLine.setVisible(False)
                self.hLine.setVisible(False)
        else:
            y_str = f"{target_y:.3f}" if target_y is not None else "N/A"
            self.label_info.setText(f"Time: {mouse_x_str}, Y: {y_str}")
            self.vLine.setVisible(False)
            self.hLine.setVisible(False)

    def mouse_moved(self, evt):
        pos = evt[0]
        if self.plot_widget.sceneBoundingRect().contains(pos):
            mouse_point = self.plot_widget.plotItem.vb.mapSceneToView(pos)
            target_x = mouse_point.x()
            self.process_hover(target_x, mouse_point.y())
            
            if self.chk_sync.isChecked() and getattr(self, 'main_window', None):
                for other in self.main_window.active_graphs:
                    if other is not self and other.chk_sync.isChecked() and other.isVisible():
                        other.process_hover(target_x, None)
        else:
            self.vLine.setVisible(False)
            self.hLine.setVisible(False)
            if self.chk_sync.isChecked() and getattr(self, 'main_window', None):
                for other in self.main_window.active_graphs:
                    if other is not self and other.chk_sync.isChecked() and other.isVisible():
                        other.vLine.setVisible(False)
                        other.hLine.setVisible(False)

    def update_data(self, signal_name, timestamp, value, unit=""):
        if not self.is_playing:
            return

        if signal_name not in self.signal_names:
            return
            
        self.units[signal_name] = unit
            
        t_deque = self.times[signal_name]
        v_deque = self.values[signal_name]

        if t_deque and t_deque[-1] == timestamp:
            return 
            
        try:
            if hasattr(value, 'value'):
                float_val = float(value.value)
            elif isinstance(value, str):
                parts = signal_name.split(':', 1)
                if len(parts) == 2 and getattr(self, 'main_window', None) and hasattr(self.main_window, 'signal_choices'):
                    try:
                        b_num = int(parts[0][1:])
                        s_name = parts[1]
                        choices = self.main_window.signal_choices.get((b_num, s_name))
                        if choices:
                            num_val = next((k for k, v in choices.items() if str(v) == value), None)
                            if num_val is not None:
                                float_val = float(num_val)
                            else: return
                        else: return
                    except Exception: return
                else: return
            else:
                float_val = float(value)
        except (ValueError, TypeError):
            return

        t_deque.append(timestamp)
        v_deque.append(float_val)

        # 3분(180초)이 지난 데이터 큐에서 제거
        while t_deque and (timestamp - t_deque[0]) > 180.0:
            t_deque.popleft()
            v_deque.popleft()

        self.curves[signal_name].setData(list(t_deque), list(v_deque))
        
        # --- Evaluate formulas ---
        self.evaluate_formulas(timestamp)
        
        # 오토스크롤 활성화 시 최근 30초 구간 유지
        if self.btn_autoscroll.isChecked():
            latest = self.get_latest_time()
            if latest is not None:
                self.plot_widget.setXRange(latest - self.time_span, latest, padding=0)
        if self.chk_auto_y.isChecked():
            self._apply_auto_y(True)
