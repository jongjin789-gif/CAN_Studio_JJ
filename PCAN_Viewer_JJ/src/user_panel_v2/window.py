from src.bus_config import normalized_signature
from src.bus_config import BUS_IDS
from src.error_dialog import show_error
import copy
import json
import math
import time
import uuid
import queue
from types import SimpleNamespace
from contextlib import ExitStack
from .periodic_sender import PeriodicSender
from PyQt5.QtCore import Qt, QTimer, QRect, QEvent, pyqtSignal, QPoint, QSize
from PyQt5.QtGui import QColor, QFontMetrics, QKeySequence, QPainter, QPen, QIcon
from PyQt5.QtWidgets import (
    QAction,
    QAbstractItemView,
    QApplication,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLineEdit,
    QInputDialog,
    QTabBar,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QMenuBar,
    QPushButton,
    QToolButton,
    QDoubleSpinBox,
    QProgressBar,
    QSizePolicy,
    QShortcut,
    QSpinBox,
    QSlider,
    QScrollArea,
    QRubberBand,
    QSplitter,
    QStatusBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .config_dialog import WidgetConfigDialog, minimum_tool_spans, tab_names
from .container_grid import ContainerGrid
from .rx_grid import RxGridControl
from .sequence import SequenceControl, validate_steps
from .mode_security import verify_edit_password, verify_communication_password
from .system_log import SystemLog
from .dbc_identity import database_signature
from src.utils import get_resource_path
from .storage import PACKAGE_EXT, load_bundle, save_bundle
from .styles import TOOL_STYLE, lamp_style, apply_container_style, apply_drawing_style
from .label_colors import inferred_raw, label_color_style, validate_color_rules
from .properties import ToolProperties
from .binding import reconcile_binding, unpack_raw, matching_signal, bit_positions, quantize_value
from .commands import command_bindings
from .packets import PacketRegistryDialog, PacketRuntime, find_packet, bind_packet, validate_tool, validate_packet, connected_packet_bus


_CURRENT_PARENT = object()
_PACKAGE_FILTER = f"패널 + DBC/SYM 패키지 (*{PACKAGE_EXT})"


class FittingLabel(QLabel):
    def __init__(self, text="", parent=None, base_pixel_size=11, min_pixel_size=8):
        self._full_text = str(text)
        self._base_pixel_size = base_pixel_size
        self._min_pixel_size = min_pixel_size
        super().__init__(parent)
        font = self.font()
        font.setPixelSize(base_pixel_size)
        self.setFont(font)
        self._fit_text()

    def setText(self, text):
        self._full_text = str(text)
        self._fit_text()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_text()

    def _fit_text(self):
        available_width = max(1, self.contentsRect().width())
        font = self.font()
        for pixel_size in range(self._base_pixel_size, self._min_pixel_size - 1, -1):
            font.setPixelSize(pixel_size)
            if QFontMetrics(font).horizontalAdvance(self._full_text) <= available_width:
                break

        metrics = QFontMetrics(font)
        display_text = metrics.elidedText(self._full_text, Qt.ElideRight, available_width)
        self.setFont(font)
        QLabel.setText(self, display_text)
        self.setToolTip(
            self._full_text
            if display_text != self._full_text or font.pixelSize() < self._base_pixel_size
            else ""
        )


class FittingProgressBar(QProgressBar):
    def setFormat(self, text):
        super().setFormat(text)
        self.setToolTip(text)
        self._fit_text()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_text()

    def _fit_text(self):
        font = self.font()
        for size in range(12, 7, -1):
            font.setPixelSize(size)
            if QFontMetrics(font).horizontalAdvance(self.text()) <= max(1, self.contentsRect().width() - 8):
                break
        self.setFont(font)


class FittingGroupBox(QGroupBox):
    def __init__(self, title, parent=None):
        self._full_title = str(title)
        super().__init__(self._full_title, parent)
        font = self.font()
        font.setPixelSize(11)
        self.setFont(font)
        self._fit_title()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_title()

    def _fit_title(self):
        available_width = max(1, self.width() - 24)
        font = self.font()
        for pixel_size in range(11, 7, -1):
            font.setPixelSize(pixel_size)
            if QFontMetrics(font).horizontalAdvance(self._full_title) <= available_width:
                break

        display_title = QFontMetrics(font).elidedText(
            self._full_title, Qt.ElideRight, available_width
        )
        self.setFont(font)
        self.setTitle(display_title)
        self.setToolTip(
            self._full_title
            if display_title != self._full_title or font.pixelSize() < 11
            else ""
        )


class GridCanvas(QWidget):
    def __init__(self, owner, parent=None):
        super().__init__(parent)
        self.owner = owner
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def paintEvent(self, event):
        super().paintEvent(event)
        cell_size = max(1, int(getattr(self.owner, "grid_cell_size", 16)))
        w = max(1, self.width())
        h = max(1, self.height())
        rows = max(1, int(getattr(self.owner, "grid_rows", 12)))
        cols = max(1, int(getattr(self.owner, "grid_cols", 12)))

        p = QPainter(self)
        c0 = QColor("#F8FAFC")
        c1 = QColor("#F4F7FB")

        for r in range(rows):
            for c in range(cols):
                p.fillRect(
                    c * cell_size,
                    r * cell_size,
                    cell_size,
                    cell_size,
                    c0 if ((r + c) % 2 == 0) else c1,
                )

        p.setPen(QPen(QColor("#E5EAF1"), 1))
        for r in range(rows + 1):
            y = r * cell_size
            p.drawLine(0, y, w, y)
        for c in range(cols + 1):
            x = c * cell_size
            p.drawLine(x, 0, x, h)

        p.end()


class UserPanelWindow(QWidget):
    request_tx_value = pyqtSignal(dict, float)
    HISTORY_LIMIT = 50

    def __init__(self, main_window, db_messages, parent=None, security_config=None):
        super().__init__(parent)
        self.main_window = main_window
        self.db_messages = db_messages
        self.security_config = security_config or {}
        self.dbc_signature = database_signature(main_window)

        self.setWindowTitle("User Panel")
        self.setWindowIcon(QIcon(get_resource_path('icon/user_panel.svg')))
        self.resize(1180, 760)

        self.grid_rows = 36
        self.grid_cols = 36
        self.grid_cell_size = 16
        self.mode = "edit"

        self.widgets_config = []
        self.pages = [dict(id='default', name='페이지 1')]
        self.active_page_id = 'default'
        self.tx_packets = []
        self.init_steps = []
        self._init_running = False
        self._bus_snapshot = None
        self._paused_packets = set()
        self._packet_runtimes = {}
        self.widget_frames = {}
        self.widget_controls = {}
        self.widget_child_hosts = {}
        self._container_pages = {}
        self.selected_widget_id = None
        self.selected_widget_ids = set()
        self._tool_clipboard = []
        self._tool_clipboard_is_cut = False
        self._tool_clipboard_tabs = {}
        self._paste_offset = 0
        self._ctrl_drag_copy = False
        self._ctrl_click_selected = False
        self.channel_settings = {}
        self._undo_stack = []
        self._redo_stack = []
        self._history_current = None
        self._history_suspended = False
        self.latest_raw_by_msg = {}
        self.latest_value_by_signal = {}
        self._tool_list_syncing = False
        self._frame_timers = {}
        self._periodic_senders = {}
        self._periodic_records = queue.Queue(maxsize=20000)
        self._periodic_errors = queue.SimpleQueue()
        self._periodic_reported_missed = {}
        self._periodic_dropped_records = 0
        self._periodic_monitor = QTimer(self)
        self._periodic_monitor.setInterval(20)
        self._periodic_monitor.timeout.connect(self._drain_periodic_events)
        self.draw_mode = None
        self.draw_start_cell = None
        self._shape_counter = 1
        self._drag_target_id = None
        self._drag_start_global = None
        self._drag_offset_global = None
        self._drag_origin_cell = None
        self._drag_origin_span = None
        self._drag_origin_parent = None
        self._drag_resize_mode = False
        self._drag_preview_band = None
        self._grid_resize_pending = False
        self._prop_syncing = False
        self._overlap_map = {}
        self._conflict_cursor = {}
        self._sim_phase = 0.0
        self._sim_timer = QTimer(self)
        self._sim_timer.setInterval(200)
        self._sim_timer.timeout.connect(self._on_sim_timer)

        self.request_tx_value.connect(self._tx_value_throttled)
        self._last_tx_sent = {}

        self._build_ui()
        self.refresh_mode_ui()
        self._reset_history()
        self._bus_monitor = QTimer(self)
        self._bus_monitor.setInterval(400)
        self._bus_monitor.timeout.connect(self._refresh_bus_status)
        self._bus_monitor.start()
        self._refresh_bus_status()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        content = QWidget(self)
        outer.addWidget(content, 1)
        root = QVBoxLayout(content)
        self.setFocusPolicy(Qt.StrongFocus)

        self.menu_bar = QMenuBar(self)
        root.addWidget(self.menu_bar)

        def tool_button(text, icon):
            button = QToolButton(self)
            button.setText(text)
            button.setToolTip(text)
            button.setAccessibleName(text)
            button.setIcon(QIcon(get_resource_path(f'icon/panel/{icon}.svg')))
            button.setToolButtonStyle(Qt.ToolButtonIconOnly)
            button.setIconSize(QSize(26, 26))
            button.setFixedSize(38, 38)
            button.setAutoRaise(True)
            button.setStyleSheet('QToolButton { border-radius: 6px; } '
                                'QToolButton:hover { background: #e3f5f7; } '
                                'QToolButton:checked { background: #bfe9ee; border: 1px solid #2194a4; }')
            return button

        self.btn_mode_edit = tool_button("Mode: EDIT", 'edit')
        self.btn_mode_standby = tool_button("Mode: STANDARD", 'standard')
        self.btn_mode_run = tool_button("Mode: RUN", 'run')
        self.btn_mode_edit.clicked.connect(lambda: self.set_mode("edit"))
        self.btn_mode_standby.clicked.connect(lambda: self.set_mode("standby"))
        self.btn_mode_run.clicked.connect(lambda: self.set_mode("run"))

        self.btn_add_tx = tool_button("Add TX Tool", 'add_tx')
        self.btn_packets = tool_button("TX 패킷 등록 / 관리", 'packets')
        self.btn_packets.clicked.connect(self.manage_tx_packets)
        self.btn_init = tool_button('RUN Init 시퀀스 설정', 'init')
        self.btn_init.clicked.connect(self.edit_init_sequence)
        self.btn_add_rx = tool_button("Add RX Tool", 'add_rx')
        self.btn_add_misc = tool_button("Add Group/Shape", 'add_shape')

        self.btn_add_tx.clicked.connect(lambda: self.add_widget("tx"))
        self.btn_add_rx.clicked.connect(lambda: self.add_widget("rx"))
        self.btn_add_misc.clicked.connect(lambda: self.add_widget("none"))

        controls = QHBoxLayout()
        controls.setSpacing(4)
        def add_separator():
            separator = QFrame(self)
            separator.setFrameShape(QFrame.VLine)
            separator.setFrameShadow(QFrame.Plain)
            separator.setFixedSize(1, 24)
            separator.setStyleSheet('background: #b8c8ce; border: none;')
            controls.addSpacing(6)
            controls.addWidget(separator, 0, Qt.AlignVCenter)
            controls.addSpacing(6)
        controls.addWidget(self.btn_mode_edit)
        controls.addWidget(self.btn_mode_standby)
        controls.addWidget(self.btn_mode_run)
        add_separator()
        self.btn_communication = tool_button('통신 설정 / DBC', 'connection')
        self.btn_communication.clicked.connect(self.open_communication)
        controls.addWidget(self.btn_communication)
        controls.addWidget(self.btn_packets)
        controls.addWidget(self.btn_init)
        add_separator()
        controls.addWidget(self.btn_add_tx)
        controls.addWidget(self.btn_add_rx)
        controls.addWidget(self.btn_add_misc)
        add_separator()
        self.clear_buttons = {}
        for key, text, icon in [('packets', 'Packet clear', 'clear_packets'),
                                ('tools', 'Tool clear', 'clear_tools'),
                                ('all', 'All clear', 'clear_all')]:
            button = tool_button(text, icon)
            button.clicked.connect(lambda checked=False, scope=key: self.clear_panel(scope))
            self.clear_buttons[key] = button
            controls.addWidget(button)
        controls.addStretch()
        root.addLayout(controls)
        for button in (self.btn_mode_edit, self.btn_mode_standby, self.btn_mode_run):
            button.setCheckable(True)
            button.clicked.connect(self._sync_toolbar_modes)

        self._setup_menu_actions()

        self.label_mode = QLabel()
        self.label_mode.setAlignment(Qt.AlignCenter)
        self.label_io = QLabel()
        self.label_bus_states = {bus: QLabel() for bus in BUS_IDS}
        self.init_control = SequenceControl(self, dict(title='RUN Init', is_init=True, binding={}))
        self.init_control.button.setEnabled(False)
        self.init_control.finished.connect(self._init_finished)
        self.init_control.hide()

        split = QSplitter(Qt.Horizontal)

        left = QWidget()
        self.tool_list_panel = left
        left.setMinimumWidth(180)
        left.setMaximumWidth(340)
        left_lay = QVBoxLayout(left)
        left_lay.setContentsMargins(0, 0, 0, 0)
        left_lay.addWidget(QLabel("Tool List"))
        self.list_tools = QListWidget()
        self.list_tools.setMinimumWidth(170)
        self.list_tools.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.list_tools.itemSelectionChanged.connect(self._on_tool_list_selection_changed)
        self.list_tools.itemDoubleClicked.connect(self._on_tool_list_double_clicked)
        self.list_tools.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list_tools.customContextMenuRequested.connect(self._show_tool_context_menu)
        left_lay.addWidget(self.list_tools)

        geom_box = QGroupBox("Selected Tool Geometry", left)
        geom_form = QFormLayout(geom_box)

        self.spin_sel_row = QSpinBox()
        self.spin_sel_row.setRange(0, self.grid_rows - 1)
        self.spin_sel_col = QSpinBox()
        self.spin_sel_col.setRange(0, self.grid_cols - 1)
        self.spin_sel_row_span = QSpinBox()
        self.spin_sel_row_span.setRange(1, self.grid_rows)
        self.spin_sel_col_span = QSpinBox()
        self.spin_sel_col_span.setRange(1, self.grid_cols)

        geom_form.addRow("Row", self.spin_sel_row)
        geom_form.addRow("Col", self.spin_sel_col)
        geom_form.addRow("Row Span", self.spin_sel_row_span)
        geom_form.addRow("Col Span", self.spin_sel_col_span)

        self.spin_sel_row.valueChanged.connect(self._on_geom_editor_changed)
        self.spin_sel_col.valueChanged.connect(self._on_geom_editor_changed)
        self.spin_sel_row_span.valueChanged.connect(self._on_geom_editor_changed)
        self.spin_sel_col_span.valueChanged.connect(self._on_geom_editor_changed)

        geom_box.hide()  # Geometry is edited in the right-hand property inspector.

        sim_box = QWidget()
        self.display_test_tab = sim_box
        sim_layout = QVBoxLayout(sim_box)
        sim_row = QHBoxLayout()
        self.spin_sim_value = QDoubleSpinBox()
        self.spin_sim_value.setDecimals(3)
        self.spin_sim_value.setRange(-1000000.0, 1000000.0)
        self.spin_sim_value.setValue(1.0)
        self.spin_sim_value.setToolTip("수신 도구에 표시할 시험값입니다. 소수점 셋째 자리까지 입력할 수 있습니다.")
        self.btn_sim_selected = QPushButton("선택 RX 적용")
        self.btn_sim_auto = QPushButton("자동 표시 테스트: OFF")
        self.btn_sim_auto.setCheckable(True)
        self.label_sim_help = QLabel(
            "CAN 통신 없이 수신 도구의 표시를 시험합니다. 선택 RX 적용은 현재 선택 도구 1개에 "
            "시험값을 적용합니다. 자동 테스트는 모든 RX 도구의 "
            "설정된 최소·최대 범위에서 값을 변화시킵니다. 실제 수신 및 DBC 해석은 검증하지 않습니다."
        )
        self.label_sim_help.setWordWrap(True)
        self.label_sim_help.setStyleSheet("color:#555;")

        self.btn_sim_selected.clicked.connect(self.simulate_selected_rx)
        self.btn_sim_auto.toggled.connect(self._toggle_auto_sim)

        sim_row.addWidget(QLabel("시험값"))
        sim_row.addWidget(self.spin_sim_value)
        sim_layout.addLayout(sim_row)
        sim_layout.addWidget(self.label_sim_help)
        sim_layout.addWidget(self.btn_sim_selected)
        sim_layout.addWidget(self.btn_sim_auto)
        sim_layout.addStretch()

        right = QWidget()
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(0, 0, 0, 0)

        self.canvas = GridCanvas(self)
        self.canvas.setFocusPolicy(Qt.StrongFocus)
        self._setup_shortcuts()
        self.canvas_layout = QGridLayout(self.canvas)
        # Keep canvas grid and widget placement perfectly aligned to cell borders.
        self.canvas_layout.setContentsMargins(0, 0, 0, 0)
        self.canvas_layout.setHorizontalSpacing(0)
        self.canvas_layout.setVerticalSpacing(0)
        self._sync_canvas_size()
        self.canvas.mousePressEvent = self._on_canvas_mouse_press
        self.canvas.mouseReleaseEvent = self._on_canvas_mouse_release

        self.canvas_scroll = QScrollArea()
        self.canvas_scroll.setWidgetResizable(True)
        self.canvas_scroll.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.canvas_scroll.setWidget(self.canvas)
        self.canvas_scroll.viewport().installEventFilter(self)
        page_row = QHBoxLayout()
        self.page_tabs = QTabBar()
        self.page_tabs.setExpanding(False)
        self.page_tabs.setUsesScrollButtons(True)
        self.page_tabs.currentChanged.connect(self._switch_page)
        self.page_tabs.tabBarDoubleClicked.connect(self.rename_page)
        self.btn_add_page = QPushButton('+ 탭')
        self.btn_add_page.clicked.connect(self.add_page)
        self.btn_rename_page = QPushButton('탭 이름 변경')
        self.btn_rename_page.clicked.connect(lambda: self.rename_page(self.page_tabs.currentIndex()))
        self.btn_delete_page = QPushButton('탭 삭제')
        self.btn_delete_page.clicked.connect(lambda: self.delete_page(self.page_tabs.currentIndex()))
        page_row.addWidget(self.page_tabs, 1)
        page_row.addWidget(self.btn_add_page)
        page_row.addWidget(self.btn_rename_page)
        page_row.addWidget(self.btn_delete_page)
        right_lay.addLayout(page_row)
        right_lay.addWidget(self.canvas_scroll)
        self._sync_page_tabs()

        split.addWidget(left)
        split.addWidget(right)
        self.properties = ToolProperties(self)
        self.inspector_tabs = QTabWidget()
        self.property_edit_panel = self.inspector_tabs
        self.inspector_tabs.addTab(self.properties, "속성")
        self.display_test_tab.setParent(self.inspector_tabs)
        self.display_test_tab.hide()
        self.inspector_tabs.tabBar().hide()
        split.addWidget(self.inspector_tabs)
        split.setStretchFactor(1, 1)
        split.setSizes([210, 630, 340])

        vertical = QSplitter(Qt.Vertical)
        self.workspace_splitter = vertical
        vertical.setHandleWidth(6)
        vertical.setStretchFactor(0, 0)
        vertical.addWidget(self.init_control)
        vertical.addWidget(split)
        self.system_log = SystemLog(self)
        vertical.addWidget(self.system_log)
        vertical.setCollapsible(0, True)
        vertical.setStretchFactor(1, 1)
        vertical.setStretchFactor(2, 0)
        vertical.setSizes([150, 530, 140])
        root.addWidget(vertical, 1)
        self.status_bar = QStatusBar(self)
        self.status_bar.setSizeGripEnabled(True)
        self.status_bar.setMinimumHeight(26)
        self.status_bar.setStyleSheet(
            'QStatusBar { background: #f2f4f6; border-top: 1px solid #b8c8ce; } '
            'QStatusBar::item { border: none; } '
            'QStatusBar QLabel { padding: 2px 10px; border-right: 1px solid #c6cfd5; }')
        for bus, label in self.label_bus_states.items():
            label.setMinimumWidth(label.fontMetrics().horizontalAdvance(f'BUS {bus}: 연결 Classic') + 24)
            self.status_bar.addWidget(label)
        self.status_bar.addWidget(QWidget(), 1)
        self.status_bar.addPermanentWidget(self.label_mode)
        self.status_bar.addPermanentWidget(self.label_io)
        outer.addWidget(self.status_bar)

        self._sync_geom_editor_from_selection()

    def new_screen(self):
        if self.mode != 'edit':
            self.set_mode('edit')
            if self.mode != 'edit':
                return
        self._sim_timer.stop()
        self._cancel_draw_mode(refresh=False)
        self._load_panel_data(dict(grid=dict(rows=36, cols=36, cell_size=16)))
        self._tool_clipboard.clear()
        self._tool_clipboard_is_cut = False
        self._tool_clipboard_tabs.clear()
        self._packet_clipboard = []
        self._paste_offset = 0
        self._paused_packets.clear()
        self.latest_raw_by_msg.clear()
        self.latest_value_by_signal.clear()
        self._overlap_map.clear()
        self._conflict_cursor.clear()
        self._sim_phase = 0.0
        self._init_running = False
        self.init_control.cfg['binding'] = dict(sequence_steps=[])
        self.init_control.log.clear()
        self.init_control.hide()
        self.system_log.clear()
        self._reset_history()
        self.log_system('새 패널 · 도구와 TX 패킷 초기화 완료')
        session = getattr(self.main_window, 'session', None)
        if session:
            session.autosave()

    def rename_panel(self):
        title, accepted = QInputDialog.getText(self, 'Rename Panel', 'Window title:', text=self.windowTitle())
        if accepted:
            self.setWindowTitle(title.strip() or 'User Panel')
            self.log_system(f'창 제목 변경: {self.windowTitle()}')
            session = getattr(self.main_window, 'session', None)
            if session:
                session.autosave()

    def clear_panel(self, scope):
        if self.mode != 'edit' or scope not in ('packets', 'tools', 'all'):
            return
        descriptions = {
            'packets': '등록 TX 패킷, INIT, 시퀀스 도구의 실행/실패 처리 명령을 비웁니다. 도구 배치는 유지합니다.',
            'tools': '모든 페이지의 도구를 비웁니다. 등록 TX 패킷과 INIT은 유지합니다.',
            'all': '패널의 모든 도구, 등록 TX 패킷, INIT 및 페이지를 초기화합니다.',
        }
        if QMessageBox.question(self, '패널 클리어', descriptions[scope] + '\nDBC와 버스 연결은 유지됩니다. 진행할까요?',
                                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        self.stop_panel_commands('패널 클리어')
        self._packet_runtimes.clear()
        if scope in ('packets', 'all'):
            self.tx_packets.clear()
            self.init_steps.clear()
            self.init_control.hide()
            for cfg in self.widgets_config:
                for binding in command_bindings(cfg):
                    binding.pop('packet_id', None)
                if cfg.get('widget_type') == 'sequence':
                    cfg['binding']['sequence_steps'] = []
                    cfg['binding']['sequence_failure_steps'] = []
        if scope in ('tools', 'all'):
            self.widgets_config.clear()
            self.selected_widget_id = None
            self.selected_widget_ids.clear()
        if scope == 'all':
            self.pages = [dict(id='default', name='페이지 1')]
            self.active_page_id = 'default'
            self._sync_page_tabs()
            self._tool_clipboard.clear()
            self._tool_clipboard_is_cut = False
            self._tool_clipboard_tabs.clear()
        self.rebuild_grid()
        self.log_system(f'클리어 완료: {scope}')
        session = getattr(self.main_window, 'session', None)
        if session:
            session.autosave()

    def _sync_toolbar_modes(self):
        for button, active in ((self.btn_mode_edit, self.mode == 'edit'),
                               (self.btn_mode_standby, self.mode == 'standby'),
                               (self.btn_mode_run, self.mode == 'run')):
            button.setChecked(active)

    def log_system(self, message, level='INFO'):
        if hasattr(self, 'system_log'):
            self.system_log.append(message, level)

    def _update_mode_status(self):
        if self.mode == 'edit':
            mode, activity = 'EDIT', '송신 정지 / 수신 표시 정지'
        elif self.mode != 'run':
            mode, activity = 'STANDARD', '송신 정지 / 수신'
        elif self._init_running:
            mode, activity = 'RUN · INIT', 'INIT 실행 / 일반 송신 대기'
        else:
            mode, activity = 'RUN', '송수신'
        self.label_mode.setText(mode)
        self.label_io.setText(activity)

    def _refresh_bus_status(self):
        main = self.main_window
        snapshot = tuple((bus, getattr(main, 'buses', {}).get(bus) is not None,
                          bool(getattr(main, 'bus_capabilities', {}).get(bus, {}).get('is_fd')))
                         for bus in BUS_IDS)
        for bus, opened, fd in snapshot:
            self.label_bus_states[bus].setText(f"BUS {bus}: {'연결 ' + ('FD' if fd else 'Classic') if opened else '미연결'}")
        if snapshot != self._bus_snapshot:
            self.log_system(' | '.join(label.text() for label in self.label_bus_states.values()))
            self._bus_snapshot = snapshot

    def _authorize_communication(self):
        password = str(getattr(self.main_window, 'communication_password', '1234'))
        accepted = verify_communication_password(self, password)
        self.log_system('통신 권한 인증 성공' if accepted else '통신 권한 인증 취소/실패',
                        'INFO' if accepted else 'WARN')
        return accepted

    def open_communication(self):
        if not self._authorize_communication():
            return
        from .connection_dialog import ConnectionDialog
        dialog = ConnectionDialog(self)
        dialog.exec_()
        dialog.deleteLater()
        if normalized_signature(self.dbc_signature) != normalized_signature(database_signature(self.main_window)):
            self.main_window.open_user_panel()

    def _check_init_connections(self):
        for index, step in enumerate(self.init_steps, 1):
            packets = []
            if step['kind'] in ('CMD', 'RCV'):
                packets = [step['packet']]
            elif step['kind'] == 'START':
                packets = [p for p in self.tx_packets if step['target_packet_id'] in ('*', p['packet_id'])
                           and (p['bus'] or step['target_packet_id'] != '*')]
            for packet in packets:
                try:
                    connected_packet_bus(self.main_window, packet)
                except ValueError as exc:
                    raise ValueError(f'INIT {index}단계 {step["kind"]}: {exc}') from exc

    def _setup_menu_actions(self):
        menu_file = self.menu_bar.addMenu("File")
        self.act_new_panel = QAction('New Panel', self)
        self.act_new_panel.triggered.connect(self.new_screen)
        menu_file.addAction(self.act_new_panel)
        self.act_rename_panel = QAction('Rename Panel…', self)
        self.act_rename_panel.triggered.connect(self.rename_panel)
        menu_file.addAction(self.act_rename_panel)
        menu_file.addSeparator()
        menu_edit = self.menu_bar.addMenu("Edit")
        self.act_undo = QAction("Undo", self)
        self.act_redo = QAction("Redo", self)
        self.act_undo.setShortcut(QKeySequence("Ctrl+Z"))
        self.act_redo.setShortcut(QKeySequence("Ctrl+Y"))
        self.act_undo.triggered.connect(self.undo_edit)
        self.act_redo.triggered.connect(self.redo_edit)
        menu_edit.addAction(self.act_undo)
        menu_edit.addAction(self.act_redo)
        menu_edit.addSeparator()
        self.act_cut = QAction('Cut\tCtrl+X', self)
        self.act_copy = QAction('Copy\tCtrl+C', self)
        self.act_paste = QAction('Paste\tCtrl+V', self)
        for action, callback in ((self.act_cut, self.cut_selected_tools),
                                 (self.act_copy, self.copy_selected_tools),
                                 (self.act_paste, self.paste_tools)):
            action.triggered.connect(callback)
            menu_edit.addAction(action)
        menu_draw = self.menu_bar.addMenu("Draw")
        menu_arrange = self.menu_bar.addMenu("Arrange")
        menu_tools = self.menu_bar.addMenu("Tools")
        menu_diag = self.menu_bar.addMenu("Diagnostics")

        self.act_save_panel = QAction("Save Panel", self)
        self.act_load_panel = QAction("Load Panel", self)
        self.act_save_panel.setToolTip('패널과 DBC/SYM을 하나의 .pjjupkg 파일로 저장합니다.')
        self.act_load_panel.setToolTip('.pjjupkg 파일에서 패널과 DBC/SYM을 함께 불러옵니다.')
        self.act_save_panel.triggered.connect(self.save_panel_to_file)
        self.act_load_panel.triggered.connect(self.load_panel_from_file)
        menu_file.addAction(self.act_save_panel)
        menu_file.addAction(self.act_load_panel)

        menu_comm = self.menu_bar.addMenu("Communication")
        self.act_open_communication = QAction("CAN / DBC Settings…", self)
        self.act_open_communication.triggered.connect(self.open_communication)
        menu_comm.addAction(self.act_open_communication)

        self.act_draw_rect = QAction("Draw Rect", self)
        self.act_draw_line = QAction("Draw Line", self)
        self.act_draw_cancel = QAction("Cancel Draw", self)
        self.act_draw_rect.triggered.connect(lambda: self._start_draw_mode("shape_rect"))
        self.act_draw_line.triggered.connect(lambda: self._start_draw_mode("shape_line"))
        self.act_draw_cancel.triggered.connect(self._cancel_draw_mode)
        menu_draw.addAction(self.act_draw_rect)
        menu_draw.addAction(self.act_draw_line)
        menu_draw.addAction(self.act_draw_cancel)

        self.act_front = QAction("Bring Front", self)
        self.act_back = QAction("Send Back", self)
        self.act_forward = QAction("Forward", self)
        self.act_backward = QAction("Backward", self)
        self.act_front.triggered.connect(self.bring_to_front)
        self.act_back.triggered.connect(self.send_to_back)
        self.act_forward.triggered.connect(self.move_forward)
        self.act_backward.triggered.connect(self.move_backward)
        menu_arrange.addAction(self.act_front)
        menu_arrange.addAction(self.act_back)
        menu_arrange.addAction(self.act_forward)
        menu_arrange.addAction(self.act_backward)

        self.act_resize_w_plus = QAction("Width +1", self)
        self.act_resize_w_minus = QAction("Width -1", self)
        self.act_resize_h_plus = QAction("Height +1", self)
        self.act_resize_h_minus = QAction("Height -1", self)
        self.act_resize_w_plus.triggered.connect(lambda: self.resize_selected_span(1, 0))
        self.act_resize_w_minus.triggered.connect(lambda: self.resize_selected_span(-1, 0))
        self.act_resize_h_plus.triggered.connect(lambda: self.resize_selected_span(0, 1))
        self.act_resize_h_minus.triggered.connect(lambda: self.resize_selected_span(0, -1))
        menu_tools.addAction(self.act_resize_w_plus)
        menu_tools.addAction(self.act_resize_w_minus)
        menu_tools.addAction(self.act_resize_h_plus)
        menu_tools.addAction(self.act_resize_h_minus)

        self.act_check_overlap = QAction("Check TX Overlap", self)
        self.act_focus_conflict = QAction("Focus Conflict", self)
        self.act_check_overlap.triggered.connect(self.check_tx_overlap)
        self.act_focus_conflict.triggered.connect(self.focus_next_conflict)
        menu_diag.addAction(self.act_check_overlap)
        menu_diag.addAction(self.act_focus_conflict)

        self.act_sim_selected = QAction("선택 RX 적용", self)
        self.act_sim_auto = QAction("자동 표시 테스트", self)
        self.act_sim_auto.setCheckable(True)
        self.act_sim_selected.triggered.connect(self.simulate_selected_rx)
        self.act_sim_auto.toggled.connect(self._toggle_auto_sim)

        self._edit_mode_actions = [
            self.act_cut,
            self.act_copy,
            self.act_paste,
            *self.clear_buttons.values(),
            self.btn_add_tx,
            self.btn_add_rx,
            self.btn_add_misc,
            self.act_draw_rect,
            self.act_draw_line,
            self.act_draw_cancel,
            self.act_front,
            self.act_back,
            self.act_forward,
            self.act_backward,
            self.act_resize_w_plus,
            self.act_resize_w_minus,
            self.act_resize_h_plus,
            self.act_resize_h_minus,
            self.act_check_overlap,
            self.act_focus_conflict,
        ]
        menu_clear = self.menu_bar.addMenu('Clear')
        for key, button in self.clear_buttons.items():
            action = QAction(button.icon(), button.text(), self)
            action.triggered.connect(lambda checked=False, scope=key: self.clear_panel(scope))
            menu_clear.addAction(action)
            self._edit_mode_actions.append(action)

    def _setup_shortcuts(self):
        self._shortcuts = []

        def _add_shortcut(keyseq, callback):
            sc = QShortcut(QKeySequence(keyseq), self.canvas)
            sc.setContext(Qt.WidgetWithChildrenShortcut)
            sc.setEnabled(self.mode == 'edit')
            sc.activated.connect(callback)
            self._shortcuts.append(sc)

        _add_shortcut(Qt.Key_Up, lambda: self.nudge_selected(0, -1))
        _add_shortcut(Qt.Key_Down, lambda: self.nudge_selected(0, 1))
        _add_shortcut(Qt.Key_Left, lambda: self.nudge_selected(-1, 0))
        _add_shortcut(Qt.Key_Right, lambda: self.nudge_selected(1, 0))
        _add_shortcut("Shift+Up", lambda: self.resize_selected_span(0, -1))
        _add_shortcut("Shift+Down", lambda: self.resize_selected_span(0, 1))
        _add_shortcut("Shift+Left", lambda: self.resize_selected_span(-1, 0))
        _add_shortcut("Shift+Right", lambda: self.resize_selected_span(1, 0))
        _add_shortcut(Qt.Key_Delete, self.delete_selected_widget)
        for host in (self.canvas, self.list_tools):
            for key, callback in [('Ctrl+X', self.cut_selected_tools),
                                  ('Ctrl+C', self.copy_selected_tools), ('Ctrl+V', self.paste_tools)]:
                shortcut = QShortcut(QKeySequence(key), host)
                shortcut.setContext(Qt.WidgetWithChildrenShortcut)
                shortcut.activated.connect(callback)
                self._shortcuts.append(shortcut)
        delete_from_list = QShortcut(QKeySequence(Qt.Key_Delete), self.list_tools)
        delete_from_list.setContext(Qt.WidgetWithChildrenShortcut)
        delete_from_list.activated.connect(self.delete_selected_widget)
        self._shortcuts.append(delete_from_list)

    def _sync_page_tabs(self):
        self.page_tabs.blockSignals(True)
        while self.page_tabs.count():
            self.page_tabs.removeTab(0)
        for page in self.pages:
            self.page_tabs.addTab(page['name'])
        index = next((i for i, p in enumerate(self.pages) if p['id'] == self.active_page_id), 0)
        self.active_page_id = self.pages[index]['id']
        self.page_tabs.setCurrentIndex(index)
        self.page_tabs.blockSignals(False)
        self.btn_delete_page.setEnabled(self.mode == 'edit' and len(self.pages) > 1)

    def _page_for(self, cfg):
        # Nested tools belong to their root container's page.
        seen = set()
        while cfg.get('parent_id') and cfg['parent_id'] not in seen:
            seen.add(cfg['parent_id'])
            parent = self._cfg_by_id(cfg['parent_id'])
            if parent is None:
                break
            cfg = parent
        return cfg.get('page_id', self.pages[0]['id'])

    def _apply_page_visibility(self):
        for cfg in self.widgets_config:
            frame = self.widget_frames.get(cfg['id'])
            if frame is not None:
                frame.setVisible(self._page_for(cfg) == self.active_page_id)

    def _switch_page(self, index):
        if not 0 <= index < len(self.pages):
            return
        self._cancel_draw_mode(refresh=False)
        self._hide_drag_preview()
        self._drag_target_id = None
        self.active_page_id = self.pages[index]['id']
        self.selected_widget_id = None
        self.selected_widget_ids.clear()
        # Keep controls and packet runtimes alive across tab switches.
        self._apply_page_visibility()
        self._refresh_selection_ui()
        self._refresh_tool_list()

    def add_page(self):
        if self.mode != 'edit':
            return
        name, ok = QInputDialog.getText(self, '탭 추가', '탭 이름:', text=f'페이지 {len(self.pages)+1}')
        if not ok or not name.strip():
            return
        self.pages.append(dict(id=str(uuid.uuid4()), name=name.strip()))
        self.active_page_id = self.pages[-1]['id']
        self._sync_page_tabs()
        self._switch_page(len(self.pages)-1)
        self._record_history()

    def rename_page(self, index):
        if self.mode != 'edit' or not 0 <= index < len(self.pages):
            return
        name, ok = QInputDialog.getText(self, '탭 이름 변경', '탭 이름:', text=self.pages[index]['name'])
        if ok and name.strip():
            self.pages[index]['name'] = name.strip()
            self._sync_page_tabs()
            self._record_history()

    def delete_page(self, index):
        if self.mode != 'edit' or len(self.pages) <= 1 or not 0 <= index < len(self.pages):
            return
        page = self.pages[index]
        ids = {cfg['id'] for cfg in self.widgets_config if self._page_for(cfg) == page['id']}
        reply = QMessageBox.question(
            self, '탭 삭제',
            f'"{page["name"]}" 탭을 삭제하시겠습니까?\n포함된 도구 {len(ids)}개도 함께 삭제됩니다.\n실행 취소(Ctrl+Z)로 복원할 수 있습니다.',
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        self._history_current = self._history_snapshot()
        self._cancel_draw_mode(refresh=False)
        self.widgets_config = [cfg for cfg in self.widgets_config if cfg['id'] not in ids]
        self.pages.pop(index)
        if self.active_page_id == page['id']:
            self.active_page_id = self.pages[min(index, len(self.pages) - 1)]['id']
        self.selected_widget_id = None
        self.selected_widget_ids.clear()
        self._sync_page_tabs()
        self.rebuild_grid()

    def _selected_tool_tree_ids(self):
        ids = {cfg['id'] for cfg in self.selected_configs()}
        # Include every nested group/tab child once, including inactive tabs.
        while True:
            expanded = ids | {cfg['id'] for cfg in self.widgets_config if cfg.get('parent_id') in ids}
            if expanded == ids:
                return ids
            ids = expanded

    def _copy_tool_snapshot(self):
        ids = self._selected_tool_tree_ids()
        return copy.deepcopy([cfg for cfg in self.widgets_config if cfg['id'] in ids])

    def _tool_tab_indices(self, snapshot):
        return {cfg['id']: self.widget_controls[cfg['id']].currentIndex()
                for cfg in snapshot if isinstance(self.widget_controls.get(cfg['id']), QTabWidget)}

    def copy_selected_tools(self):
        if self.mode != 'edit':
            return
        snapshot = self._copy_tool_snapshot()
        if snapshot:
            self._tool_clipboard = snapshot
            self._tool_clipboard_is_cut = False
            self._tool_clipboard_tabs = self._tool_tab_indices(snapshot)
            self._paste_offset = 0

    def cut_selected_tools(self):
        if self.mode != 'edit':
            return
        snapshot = self._copy_tool_snapshot()
        if not snapshot:
            return
        self._tool_clipboard = snapshot
        self._tool_clipboard_is_cut = True
        self._tool_clipboard_tabs = self._tool_tab_indices(snapshot)
        self._paste_offset = 0
        ids = {cfg['id'] for cfg in snapshot}
        self.widgets_config = [cfg for cfg in self.widgets_config if cfg['id'] not in ids]
        self.selected_widget_id = None
        self.selected_widget_ids.clear()
        self.rebuild_grid()

    def paste_tools(self):
        if self.mode != 'edit' or not self._tool_clipboard:
            return
        if self._tool_clipboard_is_cut:
            self._duplicate_tools(self._tool_clipboard, 0, 0, move=True,
                                  tab_indices=self._tool_clipboard_tabs)
            self._tool_clipboard = []
            self._tool_clipboard_is_cut = False
            self._tool_clipboard_tabs.clear()
            self._paste_offset = 0
            return
        self._paste_offset += 1
        self._duplicate_tools(self._tool_clipboard, self._paste_offset, self._paste_offset,
                              tab_indices=self._tool_clipboard_tabs)

    def _duplicate_tools(self, snapshot, row_delta, col_delta, *, move=False, tab_indices=None):
        if self.mode != 'edit' or not snapshot:
            return
        if tab_indices is None:
            tab_indices = self._tool_tab_indices(snapshot)
        clones = copy.deepcopy(snapshot)
        existing_ids = {cfg['id'] for cfg in self.widgets_config}
        # Undo may have restored the cut source. Paste a copy in that case.
        move = move and existing_ids.isdisjoint(cfg['id'] for cfg in clones)
        mapping = {cfg['id']: cfg['id'] if move else str(uuid.uuid4()) for cfg in clones}
        roots = [cfg for cfg in clones if cfg.get('parent_id') not in mapping]
        # One shared offset preserves the selected tools' relative positions.
        def bounded_delta(key, span, limit, wanted):
            low = max(-int(cfg.get(key, 0)) for cfg in roots)
            high = min(limit - int(cfg.get(key, 0)) - int(cfg.get(span, 1)) for cfg in roots)
            if wanted > 0 and high <= 0 and low < 0:
                wanted = -wanted
            return max(low, min(high, wanted))
        dr = bounded_delta('row', 'row_span', self.grid_rows, row_delta)
        dc = bounded_delta('col', 'col_span', self.grid_cols, col_delta)
        z = self._next_z()
        for index, cfg in enumerate(clones):
            original_id = cfg['id']
            parent = cfg.get('parent_id')
            cfg['id'] = mapping[original_id]
            cfg['page_id'] = self.active_page_id
            if not move:
                cfg['title'] = cfg.get('title', 'Widget') + ' (복사)'
            cfg['z_index'] = z + index
            if parent in mapping:
                cfg['parent_id'] = mapping[parent]
            else:
                cfg['parent_id'] = parent if parent in existing_ids and self._page_for(self._cfg_by_id(parent)) == self.active_page_id else None
                cfg['row'] = int(cfg.get('row', 0)) + dr
                cfg['col'] = int(cfg.get('col', 0)) + dc
        self.widgets_config.extend(clones)
        self.selected_widget_ids = {cfg['id'] for cfg in clones}
        self.selected_widget_id = clones[0]['id']
        self.rebuild_grid()
        for original_id, index in tab_indices.items():
            control = self.widget_controls.get(mapping.get(original_id))
            if isinstance(control, QTabWidget):
                control.setCurrentIndex(max(0, min(control.count() - 1, index)))
        self.properties.refresh(force=True)
        self.canvas.setFocus()

    def manage_tx_packets(self):
        if self.mode != 'edit':
            return
        dialog = PacketRegistryDialog(self)
        if dialog.exec_() != dialog.Accepted:
            return
        configs = copy.deepcopy(self.widgets_config)
        init_steps = copy.deepcopy(self.init_steps)
        try:
            validate_tool(dialog.packets, dict(behavior='tx', widget_type='sequence', binding=dict(sequence_steps=init_steps)))
            for cfg in configs:
                validate_tool(dialog.packets, cfg)
        except ValueError as exc:
            QMessageBox.warning(self, 'TX 패킷', str(exc))
            return
        self.tx_packets = dialog.packets
        self.widgets_config = configs
        self.init_steps = init_steps
        self._packet_runtimes.clear()
        self.rebuild_grid()
        self.properties.refresh(force=True)

    def edit_init_sequence(self):
        if self.mode != 'edit':
            return
        from .sequence_dialog import SequenceDialog
        dialog = SequenceDialog(self.db_messages, self.init_steps, self, self.tx_packets,
                                actions_only=True, allow_empty=True, allow_failure=False)
        dialog.setWindowTitle('RUN Init 시퀀스 (비우면 사용 안함)')
        if dialog.exec_() == dialog.Accepted:
            self.init_steps = copy.deepcopy(dialog.steps)
            if not self.init_steps:
                self.init_control.hide()
            self._record_history()

    def _init_finished(self, success):
        if not self._init_running:
            return
        self._init_running = False
        if not success:
            self.log_system('INIT 실패/중단 — STANDARD 복귀, 일반 송신 시작 안 함', 'ERROR')
            self.mode = 'standby'
            self.refresh_mode_ui()
            self.label_mode.setToolTip('Init NG / 중단: STANDARD로 전환했습니다. 로그를 확인하세요.')
            return
        self._update_mode_status()
        self._set_init_tool_state()
        self._sync_frame_timers_from_configs()

    def _set_init_tool_state(self):
        for cfg in self.widgets_config:
            if cfg.get('behavior') == 'tx':
                ctrl = self.widget_controls.get(cfg.get('id'))
                if ctrl:
                    ctrl.setEnabled(not self._init_running)

    def set_packet_transmission(self, packet_id, enabled):
        if self.mode != 'run':
            raise ValueError('RUN에서만 주기 전송을 제어할 수 있습니다.')
        targets = {p['packet_id'] for p in self.tx_packets}
        if packet_id != '*':
            if packet_id not in targets:
                raise ValueError('시작/정지 대상 패킷이 등록되어 있지 않습니다.')
            targets = {packet_id}
        if enabled:
            self._paused_packets.difference_update(targets)
        else:
            self._paused_packets.update(targets)
            for target in targets:
                timer = self._frame_timers.pop(target, None)
                if timer:
                    timer.stop()
                    timer.deleteLater()
        self._sync_frame_timers_from_configs()

    def _report_packet_error(self, exc):
        self.log_system(f'TX 패킷 오류: {exc}', 'ERROR')
        self.label_mode.setToolTip(f'TX 패킷 오류: {exc}')
        if hasattr(self.main_window, 'statusBar'):
            self.main_window.statusBar().showMessage(f'User panel TX: {exc}', 6000)

    def _prepare_registered_packets(self):
        validate_steps(self.init_steps, allow_empty=True)
        validate_tool(self.tx_packets, dict(behavior='tx', widget_type='sequence', binding=dict(sequence_steps=self.init_steps)))
        for cfg in self.widgets_config:
            validate_tool(self.tx_packets, cfg)
        runtimes = {}
        retained = set()
        keys = set()
        for p in self.tx_packets:
            validate_packet(p, self.db_messages)
            key = (p['bus'], p['id'])
            if (p['bus'] and key in keys) or not 0 <= int(p.get('cycle', -1)) <= 600000:
                raise ValueError('등록 패킷의 BUS/ID 중복 또는 딜레이 설정을 확인하세요.')
            keys.add(key)
            previous = self._packet_runtimes.get(p['packet_id'])
            if previous is not None and previous.packet == p:
                previous.db_messages = self.db_messages
                runtimes[p['packet_id']] = previous
                retained.add(p['packet_id'])
            else:
                runtimes[p['packet_id']] = PacketRuntime(p, self.db_messages, self.main_window)
        # Initialize from the displayed tool values before the first periodic send.
        def is_checked_toggle(cfg):
            ctrl = self.widget_controls.get(cfg.get('id'))
            return cfg.get('widget_type') == 'toggle' and ctrl is not None and ctrl.isChecked()
        for cfg in sorted(self.widgets_config, key=is_checked_toggle):
            if cfg.get('behavior') != 'tx' or cfg.get('widget_type') == 'sequence':
                continue
            binding = cfg['binding']
            ctrl = self.widget_controls.get(cfg['id'])
            kind = cfg.get('widget_type')
            if kind == 'slider':
                slider = ctrl.findChild(QSlider) if ctrl else None
                low, high = binding.get('min', 0), binding.get('max', 100)
                value = low + (high - low) * slider.value() / max(1, slider.maximum()) if slider else binding.get('tx_initial_value', low)
            elif kind == 'spinbox':
                value = ctrl.value() if ctrl else binding.get('min', 0)
            elif kind == 'toggle':
                value = binding.get('tx_on_value', 1) if ctrl and ctrl.isChecked() else binding.get('tx_off_value', 0)
            elif kind == 'button':
                value = binding.get('tx_release_value', binding.get('min', 0))
            else:
                continue
            for target in command_bindings(cfg, enabled_only=True):
                if target['packet_id'] in retained:
                    continue
                target_value = value
                if kind == 'toggle':
                    target_value = target.get('tx_on_value', 1) if ctrl and ctrl.isChecked() else target.get('tx_off_value', 0)
                elif kind == 'button':
                    target_value = target.get('tx_release_value', target.get('min', 0))
                runtimes[target['packet_id']].stage(target, target_value)
        self._packet_runtimes = runtimes

    def set_mode(self, new_mode):
        if hasattr(self, 'properties'):
            self.properties.cancel_color_preview()
        if new_mode == 'standard':
            new_mode = 'standby'
        if new_mode == self.mode:
            return

        if new_mode == "edit" and self.mode != "edit":
            enabled = bool(self.security_config.get("enabled", False))
            password = str(self.security_config.get("password", ""))
            if not verify_edit_password(self, enabled, password):
                QMessageBox.warning(self, "Denied", "Invalid password for EDIT mode.")
                return

        self.stop_panel_commands("모드 변경")
        if new_mode == 'run':
            try:
                validate_steps(self.init_steps, actions_only=True, allow_empty=True)
                self._prepare_registered_packets()
                self._check_init_connections()
            except ValueError as exc:
                self.mode = 'standby'
                self.refresh_mode_ui()
                self.label_mode.setToolTip(f'RUN 시작 불가 / STANDARD: {exc}')
                self.log_system(f'RUN 시작 불가 / STANDARD: {exc}', 'ERROR')
                return
        self.mode = new_mode
        self.log_system(f"모드: {new_mode.upper() if new_mode != 'standby' else 'STANDARD'}")
        if new_mode == 'run':
            self._paused_packets.clear()
            self._init_running = bool(self.init_steps)
            self.init_control.setVisible(bool(self.init_steps))
        self.refresh_mode_ui()
        if new_mode == 'run' and self.init_steps:
            self.init_control.cfg['binding'] = dict(sequence_steps=copy.deepcopy(self.init_steps))
            self.init_control.show()
            self._update_mode_status()
            self.init_control.toggle()

    def stop_panel_commands(self, reason="패널 정지"):
        if self.mode == 'run':
            self.log_system(f'송신 정지: {reason}')
        self._init_running = False
        if hasattr(self, 'init_control'):
            self.init_control.stop(reason)
        self._stop_all_frame_timers()
        for ctrl in self.widget_controls.values():
            if isinstance(ctrl, SequenceControl):
                ctrl.stop(reason)
            for timer in ctrl.findChildren(QTimer, "panel_hold_timer"):
                timer.stop()
                timer.deleteLater()

    def showEvent(self, event):
        super().showEvent(event)
        if hasattr(self, '_periodic_senders'):
            self._sync_frame_timers_from_configs()
        if hasattr(self, '_bus_monitor'):
            self._bus_monitor.start()
            self._refresh_bus_status()

    def closeEvent(self, event):
        if hasattr(self, 'properties'):
            self.properties.cancel_color_preview()
        main = self.main_window
        if (getattr(main, 'user_panel_only', False) and getattr(main, 'user_panel_window', None) is self
                and not getattr(main, '_is_closing', False) and not getattr(main.session, 'busy', False)):
            main.close()
            event.accept()
            return
        self.stop_panel_commands("패널 닫힘")
        self._bus_monitor.stop()
        self._sim_timer.stop()
        self.mode = "standby"
        super().closeEvent(event)

    def on_sequence_receive(self, ts, bus, can_id, data, extended, fd, brs=False):
        if self.mode == "run" and self.isVisible():
            if self._init_running:
                self.init_control.receive(ts, bus, can_id, data, extended, fd, brs)
            for ctrl in self.widget_controls.values():
                if isinstance(ctrl, SequenceControl):
                    ctrl.receive(ts, bus, can_id, data, extended, fd, brs)

    def refresh_mode_ui(self):
        self._sync_toolbar_modes()
        is_edit = self.mode == "edit"
        if self.mode != "run":
            self.stop_panel_commands("패널 상태 변경")
        self._update_mode_status()
        self.label_mode.setToolTip('')

        if self.mode != "run":
            self._stop_all_frame_timers()
        if self.mode != "edit" and self._sim_timer.isActive():
            self._sim_timer.stop()
            self.btn_sim_auto.blockSignals(True)
            self.btn_sim_auto.setChecked(False)
            self.btn_sim_auto.setText("자동 표시 테스트: OFF")
            self.btn_sim_auto.blockSignals(False)
            self.act_sim_auto.blockSignals(True)
            self.act_sim_auto.setChecked(False)
            self.act_sim_auto.blockSignals(False)

        for item in getattr(self, "_edit_mode_actions", []):
            item.setEnabled(is_edit)
        for shortcut in getattr(self, '_shortcuts', []):
            shortcut.setEnabled(is_edit)
        for shortcut in getattr(self, '_shortcuts', []):
            shortcut.setEnabled(is_edit)
        self.btn_packets.setEnabled(is_edit)
        self.btn_add_page.setEnabled(is_edit)
        self.btn_rename_page.setEnabled(is_edit)
        self.btn_delete_page.setEnabled(is_edit and len(self.pages) > 1)
        self.btn_add_page.setVisible(is_edit)
        self.btn_rename_page.setVisible(is_edit)
        self.btn_delete_page.setVisible(is_edit)
        self.btn_init.setEnabled(is_edit)
        self.init_control.setVisible(self.mode == 'run' and bool(self.init_steps))
        self.tool_list_panel.setVisible(is_edit)
        self.property_edit_panel.setVisible(is_edit)
        self._set_init_tool_state()

        self.act_sim_auto.blockSignals(True)
        self.act_sim_auto.setChecked(self.btn_sim_auto.isChecked())
        self.act_sim_auto.blockSignals(False)

        # RUN/STANDBY 모드에서는 위젯 선택 기능을 비활성화합니다.
        if not is_edit:
            self._cancel_draw_mode(refresh=False)
            self.selected_widget_id = None
            self.selected_widget_ids.clear()
            self.list_tools.clearSelection()

        self.list_tools.setEnabled(is_edit)
        self._refresh_selection_ui()

        self._sync_frame_timers_from_configs()
        self._refresh_history_actions()

    def _history_snapshot(self):
        return dict(widgets=copy.deepcopy(self.widgets_config),
                    pages=copy.deepcopy(self.pages), active_page_id=self.active_page_id,
                    tx_packets=copy.deepcopy(self.tx_packets),
                    init_steps=copy.deepcopy(self.init_steps),
                    selected=self.selected_widget_id, ids=set(self.selected_widget_ids))

    def _reset_history(self):
        self._undo_stack.clear()
        self._redo_stack.clear()
        self._history_current = self._history_snapshot()
        self._refresh_history_actions()

    def _refresh_history_actions(self):
        if not hasattr(self, "act_undo"):
            return
        self.act_undo.setEnabled(self.mode == "edit" and bool(self._undo_stack))
        self.act_redo.setEnabled(self.mode == "edit" and bool(self._redo_stack))
        self.act_undo.setText(f"Undo ({len(self._undo_stack)})")
        self.act_redo.setText(f"Redo ({len(self._redo_stack)})")

    def _record_history(self):
        if self._history_suspended:
            return
        current = self._history_snapshot()
        if self._history_current is not None and any(current[key] != self._history_current.get(key) for key in ('widgets', 'pages', 'tx_packets', 'init_steps')):
            self._undo_stack.append(self._history_current)
            del self._undo_stack[:-self.HISTORY_LIMIT]
            self._redo_stack.clear()
        self._history_current = current
        self._refresh_history_actions()

    def _restore_history(self, source, destination):
        if self.mode != "edit" or not source or self._history_suspended:
            return
        destination.append(self._history_snapshot())
        state = source.pop()
        self._history_suspended = True
        try:
            self._cancel_draw_mode(refresh=False)
            self._drag_target_id = None
            self.widgets_config = copy.deepcopy(state["widgets"])
            self.pages = copy.deepcopy(state['pages'])
            self.active_page_id = state['active_page_id']
            self._sync_page_tabs()
            self.tx_packets = copy.deepcopy(state.get('tx_packets', []))
            self.init_steps = copy.deepcopy(state.get('init_steps', []))
            self._packet_runtimes.clear()
            for cfg in self.widgets_config:
                for binding in command_bindings(cfg):
                    reconcile_binding(self.db_messages, binding)
            self.selected_widget_id = state["selected"]
            self.selected_widget_ids = set(state["ids"])
            self.rebuild_grid()
        finally:
            self._history_suspended = False
        self._history_current = self._history_snapshot()
        self._refresh_history_actions()

    def undo_edit(self):
        self._restore_history(self._undo_stack, self._redo_stack)

    def redo_edit(self):
        self._restore_history(self._redo_stack, self._undo_stack)

    def _start_draw_mode(self, shape_type):
        if self.mode != "edit":
            QMessageBox.information(self, "Info", "Shape draw mode is available in EDIT mode only.")
            return
        self.draw_mode = shape_type
        self.draw_start_cell = None
        self.label_mode.setToolTip(
            f"EDIT: draw mode active ({shape_type}). Drag on empty canvas area to create shape."
        )

    def _cancel_draw_mode(self, refresh=True):
        self.draw_mode = None
        self.draw_start_cell = None
        if refresh:
            self.refresh_mode_ui()

    def _canvas_cell_from_pos(self, pos):
        if self.grid_rows <= 0 or self.grid_cols <= 0:
            return None
        if self.canvas.width() <= 0 or self.canvas.height() <= 0:
            return None

        x = max(0, min(self.canvas.width() - 1, int(pos.x())))
        y = max(0, min(self.canvas.height() - 1, int(pos.y())))
        col = min(self.grid_cols - 1, x // self.grid_cell_size)
        row = min(self.grid_rows - 1, y // self.grid_cell_size)
        return row, col

    def _cell_index_from_coord(self, coord, size, count):
        size = max(1, int(size))
        count = max(1, int(count))
        c = max(0, min(size - 1, int(coord)))

        for i in range(count):
            end = int(round((i + 1) * size / float(count)))
            if c < end:
                return i
        return count - 1

    def _on_canvas_mouse_press(self, event):
        if self.draw_mode is None or self.mode != "edit":
            return
        if event.button() != Qt.LeftButton:
            return
        self.draw_start_cell = self._canvas_cell_from_pos(event.pos())

    def _on_canvas_mouse_release(self, event):
        if self.draw_mode is None or self.mode != "edit":
            return
        if event.button() != Qt.LeftButton:
            return
        if self.draw_start_cell is None:
            return

        end_cell = self._canvas_cell_from_pos(event.pos())
        if end_cell is None:
            self.draw_start_cell = None
            return

        r1, c1 = self.draw_start_cell
        r2, c2 = end_cell
        row = min(r1, r2)
        col = min(c1, c2)
        row_span = max(1, abs(r2 - r1) + 1)
        col_span = max(1, abs(c2 - c1) + 1)

        shape_type = self.draw_mode
        cfg = {
            "id": str(uuid.uuid4()),
            "widget_type": shape_type,
            "title": f"Shape {self._shape_counter}",
            "row": row,
            "col": col,
            "row_span": row_span,
            "col_span": col_span,
            "parent_id": None,
            "z_index": self._next_z(),
            "behavior": "none",
            "binding": {},
        }
        self._shape_counter += 1

        self._normalize_config(cfg)

        if shape_type == "shape_line":
            dr = abs(r2 - r1)
            dc = abs(c2 - c1)
            if dc >= dr:
                cfg["binding"]["shape_line_direction"] = "horizontal"
                cfg["row_span"] = 1
                cfg["col_span"] = max(1, col_span)
            else:
                cfg["binding"]["shape_line_direction"] = "vertical"
                cfg["col_span"] = 1
                cfg["row_span"] = max(1, row_span)

        self._upsert_widget_config(cfg)
        self.selected_widget_id = cfg.get("id")
        self.rebuild_grid()
        self.draw_start_cell = None

    def add_widget(self, behavior=None):
        if behavior == 'tx' and not self.tx_packets:
            QMessageBox.information(self, 'TX 패킷', '먼저 TX 패킷 등록 / 관리에서 패킷을 등록하세요.')
            return
        dlg = WidgetConfigDialog(
            self.db_messages,
            self,
            fixed_behavior=behavior,
            parent_candidates=self._group_parent_candidates(),
            live_preview_default=False,
            grid_rows=self.grid_rows,
            grid_cols=self.grid_cols,
            grid_cell_size=self.grid_cell_size,
            tx_packets=self.tx_packets,
        )
        if behavior == "tx":
            dlg.combo_widget_type.setCurrentText("button")
        elif behavior == "rx":
            dlg.combo_widget_type.setCurrentText("status_lamp")

        preview_id = dlg._config_id
        preview_applied = False

        def _preview(cfg):
            nonlocal preview_applied
            preview_applied = True
            self._normalize_config(cfg)
            self._upsert_widget_config(cfg)
            self.selected_widget_id = cfg.get("id")
            self.rebuild_grid()

        dlg.config_changed.connect(_preview)
        self._history_suspended = True
        try:
            result = dlg.exec_()
        finally:
            self._history_suspended = False
        if result != dlg.Accepted:
            if preview_applied:
                self.widgets_config = [x for x in self.widgets_config if x.get("id") != preview_id]
                self.selected_widget_id = None
                self.rebuild_grid()
            return

        cfg = dlg.get_config(strict=True)
        self._normalize_config(cfg)
        self._upsert_widget_config(cfg)
        self.selected_widget_id = cfg.get("id")
        self.rebuild_grid()

    def edit_selected_widget(self):
        cfg = self._get_selected_config()
        if not cfg:
            QMessageBox.information(self, "Info", "Select a widget first.")
            return

        if self.mode != "edit":
            return
        self.properties.refresh(force=True)
        self.inspector_tabs.setCurrentWidget(self.properties)
        self.properties.show()
        self.properties.setFocus()

    def refresh_dbc_bindings(self):
        if normalized_signature(self.dbc_signature) != normalized_signature(database_signature(self.main_window)):
            if self.mode == 'run':
                self.set_mode('standby')
            self.log_system('DBC/SYM 변경 감지: 패널을 다시 열 때 기존 이력 사용 여부를 확인합니다.', 'WARN')
            return
        for cfg in self.widgets_config:
            for binding in command_bindings(cfg):
                reconcile_binding(self.db_messages, binding)
        self.properties.refresh(force=True)
        self._history_current = self._history_snapshot()

    def delete_selected_widget(self):
        if self.mode != "edit":
            return
        cfg = self._get_selected_config()
        if not cfg:
            QMessageBox.information(self, "Info", "Select a widget first.")
            return

        ids = {c["id"] for c in self.selected_configs()}
        self.widgets_config = [x for x in self.widgets_config if x.get("id") not in ids]
        for item in self.widgets_config:
            if item.get("parent_id") in ids:
                item["parent_id"] = None
        self.selected_widget_id = None
        self.selected_widget_ids.clear()
        self.rebuild_grid()

    def _upsert_widget_config(self, cfg, tab_index_map=None):
        target_id = cfg.get("id")
        if tab_index_map:
            control = self.widget_controls.get(target_id)
            if isinstance(control, QTabWidget):
                control.setCurrentIndex(tab_index_map[max(0, min(len(tab_index_map) - 1, control.currentIndex()))])
            for child in self.widgets_config:
                if child.get('parent_id') == target_id:
                    old_index = max(0, min(len(tab_index_map) - 1, int(child.get('tab_index', 0))))
                    child['tab_index'] = tab_index_map[old_index]
        for i, old in enumerate(self.widgets_config):
            if old.get("id") == target_id:
                cfg["z_index"] = old.get("z_index", cfg.get("z_index", 0))
                self.widgets_config[i] = cfg
                return
        cfg["z_index"] = cfg.get("z_index", self._next_z())
        self.widgets_config.append(cfg)

    def _next_z(self):
        if not self.widgets_config:
            return 0
        return max(int(c.get("z_index", 0)) for c in self.widgets_config) + 1

    def _get_selected_config(self):
        if not self.selected_widget_id:
            return None
        for cfg in self.widgets_config:
            if cfg.get("id") == self.selected_widget_id:
                return cfg
        return None

    def selected_configs(self):
        ids = self.selected_widget_ids & {c["id"] for c in self.widgets_config}
        if self.selected_widget_id and self.selected_widget_id not in ids:
            ids = {self.selected_widget_id}
        if not self.selected_widget_id:
            ids = set()
        self.selected_widget_ids = ids
        return [c for c in self.widgets_config if c["id"] in ids]

    def _minimum_tool_row_span(self, widget_type, config=None):
        return minimum_tool_spans(
            widget_type, self.grid_cell_size, self.grid_rows, self.grid_cols, config, self.db_messages
        )[0]

    def _minimum_tool_col_span(self, widget_type, config=None):
        return minimum_tool_spans(
            widget_type, self.grid_cell_size, self.grid_rows, self.grid_cols, config, self.db_messages
        )[1]

    def _normalize_config(self, cfg):
        existing = self._cfg_by_id(cfg.get('id'))
        cfg.setdefault('page_id', existing.get('page_id', self.active_page_id) if existing else self.active_page_id)
        cfg.setdefault("id", str(uuid.uuid4()))
        cfg.setdefault("widget_type", "label")
        cfg.setdefault("title", "Widget")
        cfg.setdefault("title_align", "center")
        cfg.setdefault("row", 0)
        cfg.setdefault("col", 0)
        cfg.setdefault("row_span", 1)
        cfg.setdefault("col_span", 1)
        cfg.setdefault("parent_id", None)
        cfg.setdefault("z_index", 0)
        cfg.setdefault("behavior", "none")

        binding = cfg.setdefault("binding", {})
        validate_color_rules(binding.get('label_color_rules', []))
        binding.setdefault("bus", 1)
        binding.setdefault("can_id", 0)
        binding.setdefault("signal_name", None)
        binding.setdefault("dlc", 8)
        binding.setdefault("start_bit", 0)
        binding.setdefault("bit_length", 8)
        binding.setdefault("scale", 1.0)
        binding.setdefault("offset", 0.0)
        binding.setdefault("signed", False)
        binding.setdefault("byte_order", "little_endian")
        binding.setdefault("min", 0.0)
        binding.setdefault("max", 100.0)
        binding.setdefault("tx_resolution", 1.0)
        binding.pop("tx_cycle_mode", None)
        binding.pop("tx_cycle_ms", None)
        binding.setdefault("tx_press_value", binding.get("max", 100.0))
        binding.setdefault("tx_release_value", binding.get("min", 0.0))
        binding.setdefault("tx_on_value", 1.0)
        binding.setdefault("tx_off_value", 0.0)
        binding.setdefault("tx_hold_period_ms", 80)
        binding.setdefault("rx_on_op", "ge")
        binding.setdefault("rx_on_a", binding.get("max", 100.0))
        binding.setdefault("rx_on_b", binding.get("max", 100.0))
        binding.setdefault("rx_off_op", "lt")
        binding.setdefault("rx_off_a", binding.get("max", 100.0))
        binding.setdefault("rx_off_b", binding.get("max", 100.0))
        binding.setdefault("shape_kind", "line")
        binding.setdefault("shape_line_direction", "horizontal")
        binding.setdefault("stroke_color", "#333333")
        binding.setdefault("stroke_width", 2)
        binding.setdefault("stroke_style", "solid")
        binding.setdefault("fill", False)
        binding.setdefault("fill_color", "#E8F1FF")
        binding.setdefault("corner_radius", 0)
        binding.setdefault("unit", "")

        if cfg.get("parent_id") == cfg.get("id"):
            cfg["parent_id"] = None

        wtype = cfg.get("widget_type", "label")
        min_row_span = self._minimum_tool_row_span(wtype, cfg)
        min_col_span = self._minimum_tool_col_span(wtype, cfg)

        cfg["row"] = max(0, min(self.grid_rows - 1, int(cfg.get("row", 0))))
        cfg["col"] = max(0, min(self.grid_cols - 1, int(cfg.get("col", 0))))
        cfg["row_span"] = (
            min_row_span if wtype == "slider"
            else max(min_row_span, min(self.grid_rows, int(cfg.get("row_span", 1))))
        )
        cfg["col_span"] = max(min_col_span, min(self.grid_cols, int(cfg.get("col_span", 1))))

    def _group_parent_candidates(self, exclude_id=None):
        out = []
        for cfg in self.widgets_config:
            if self._page_for(cfg) != self.active_page_id:
                continue
            if cfg.get("id") == exclude_id:
                continue
            if cfg.get("widget_type") in ("group_box", "tab_container"):
                out.append(
                    {
                        "id": cfg.get("id"),
                        "title": cfg.get("title", "Group"),
                        "widget_type": cfg.get("widget_type", "group_box"),
                        "tabs": tab_names(cfg),
                    }
                )
        return out

    def _refresh_tool_list(self):
        overlap_ids, overlap_map = self._collect_overlap_details()
        self._overlap_map = overlap_map

        self._tool_list_syncing = True
        self.list_tools.clear()
        sorted_cfg = sorted(self.widgets_config, key=lambda c: int(c.get("z_index", 0)))
        for cfg in sorted_cfg:
            if self._page_for(cfg) != self.active_page_id:
                continue
            binding = cfg.get("binding", {})
            parent_id = cfg.get("parent_id")
            parent_text = "ROOT" if not parent_id else f"P:{str(parent_id)[:8]}"
            text = f"{cfg.get('title', 'Widget')} [{cfg.get('widget_type', '-')}] [{cfg.get('behavior', '-').upper()}] B{binding.get('bus', 1)} 0x{int(binding.get('can_id', 0)):X}"
            text = f"{text} [{parent_text}]"
            it = QListWidgetItem(text)
            wid = cfg.get("id")
            it.setData(Qt.UserRole, wid)
            if wid in overlap_ids:
                it.setForeground(QColor("#C62828"))
                targets = sorted(list(overlap_map.get(wid, set())))
                if targets:
                    it.setData(Qt.UserRole + 1, targets[0])
                    it.setToolTip(f"TX overlap with {len(targets)} tool(s). Double-click to focus conflict.")
            self.list_tools.addItem(it)
            it.setSelected(wid in self.selected_widget_ids)
        self._tool_list_syncing = False

    def _cfg_by_id(self, widget_id):
        for cfg in self.widgets_config:
            if cfg.get("id") == widget_id:
                return cfg
        return None

    def _is_descendant(self, maybe_child_id, maybe_parent_id):
        current_id = maybe_child_id
        guard = 0
        while current_id and guard < 200:
            guard += 1
            cfg = self._cfg_by_id(current_id)
            if not cfg:
                return False
            pid = cfg.get("parent_id")
            if not pid:
                return False
            if pid == maybe_parent_id:
                return True
            current_id = pid
        return False

    def _hit_group_parent_from_global(self, global_pos, exclude_id=None):
        selected_id = exclude_id
        for cfg in sorted(self.widgets_config, key=lambda x: int(x.get("z_index", 0)), reverse=True):
            if self._page_for(cfg) != self.active_page_id:
                continue
            wid = cfg.get("id")
            if wid == selected_id:
                continue
            if cfg.get("widget_type") not in ("group_box", "tab_container"):
                continue
            frame = self.widget_frames.get(wid)
            if frame is None or not frame.isVisible():
                continue
            local = frame.mapFromGlobal(global_pos)
            if frame.rect().contains(local):
                if selected_id and self._is_descendant(wid, selected_id):
                    continue
                return wid
        return None

    def _child_host_layout(self, parent_id):
        control = self.widget_controls.get(parent_id)
        if isinstance(control, QTabWidget):
            return control.currentWidget().grid
        layout = self.widget_child_hosts.get(parent_id)
        return layout if layout is not None else self.canvas_layout

    def _cell_from_global_in_parent(self, parent_id, global_pos):
        layout = self._child_host_layout(parent_id)
        host = layout.parentWidget()
        if host is not self.canvas:
            layout.activate()
            local = host.mapFromGlobal(global_pos)
            # Track sizes include layout margins, spacing and tool minimum sizes;
            # they are not necessarily equal fractions of the container size.
            columns = [layout.cellRect(0, col) for col in range(self.grid_cols)]
            rows = [layout.cellRect(row, 0) for row in range(self.grid_rows)]
            col = next((i for i in range(len(columns) - 1)
                        if local.x() < columns[i + 1].left()), len(columns) - 1)
            row = next((i for i in range(len(rows) - 1)
                        if local.y() < rows[i + 1].top()), len(rows) - 1)
            return row, col

        local = self.canvas.mapFromGlobal(global_pos)
        x = max(0, min(max(0, self.canvas.width() - 1), int(local.x())))
        y = max(0, min(max(0, self.canvas.height() - 1), int(local.y())))
        col = min(self.grid_cols - 1, x // self.grid_cell_size)
        row = min(self.grid_rows - 1, y // self.grid_cell_size)
        return row, col

    def _drag_move_cell(self, cfg, parent_id, global_pos):
        row, col = self._cell_from_global_in_parent(parent_id, global_pos - self._drag_offset_global)
        return (max(0, min(self.grid_rows - int(cfg.get('row_span', 1)), row)),
                max(0, min(self.grid_cols - int(cfg.get('col_span', 1)), col)))

    def _on_tool_list_selection_changed(self, *_args):
        if self._tool_list_syncing:
            return
        items = self.list_tools.selectedItems()
        self.selected_widget_ids = {i.data(Qt.UserRole) for i in items}
        current = self.list_tools.currentItem()
        self.selected_widget_id = (current.data(Qt.UserRole) if current and current.isSelected()
                                   else (items[-1].data(Qt.UserRole) if items else None))
        self._refresh_selection_ui()

    def _on_tool_list_double_clicked(self, item):
        if item is None:
            return
        wid = item.data(Qt.UserRole)
        target = item.data(Qt.UserRole + 1)
        if not wid or not target:
            self.edit_selected_widget()
            return
        self.selected_widget_id = wid
        self.focus_next_conflict()

    def _on_geom_editor_changed(self, _value):
        if self._prop_syncing:
            return
        if self.mode != "edit":
            return

        cfg = self._get_selected_config()
        if not cfg:
            return

        cfg["row"] = int(self.spin_sel_row.value())
        cfg["col"] = int(self.spin_sel_col.value())
        cfg["row_span"] = int(self.spin_sel_row_span.value())
        cfg["col_span"] = int(self.spin_sel_col_span.value())
        self.rebuild_grid()

    def _sync_geom_editor_from_selection(self):
        self._prop_syncing = True
        try:
            cfg = self._get_selected_config()
            enabled = bool(cfg) and self.mode == "edit"
            self.spin_sel_row.setEnabled(enabled)
            self.spin_sel_col.setEnabled(enabled)
            self.spin_sel_row_span.setEnabled(enabled)
            self.spin_sel_col_span.setEnabled(enabled)

            self.spin_sel_row.setRange(0, self.grid_rows - 1)
            self.spin_sel_col.setRange(0, self.grid_cols - 1)

            if not cfg:
                # 선택된 위젯이 없을 경우: 기본 최소값으로 범위를 설정하고 값을 초기화합니다.
                min_row_span = self._minimum_tool_row_span("button")
                min_col_span = self._minimum_tool_col_span("button")
                self.spin_sel_row_span.setRange(min_row_span, self.grid_rows)
                self.spin_sel_col_span.setRange(min_col_span, self.grid_cols)
                self.spin_sel_row.setValue(0)
                self.spin_sel_col.setValue(0)
                self.spin_sel_row_span.setValue(min_row_span)
                self.spin_sel_col_span.setValue(min_col_span)
                return

            # 선택된 위젯이 있을 경우: 위젯 타입에 맞는 최소 크기 제약을 적용합니다.
            wtype = cfg.get("widget_type")
            min_row_span = self._minimum_tool_row_span(wtype, cfg)
            min_col_span = self._minimum_tool_col_span(wtype, cfg)

            self.spin_sel_row_span.setRange(
                min_row_span,
                min_row_span if wtype == "slider" else self.grid_rows,
            )
            self.spin_sel_col_span.setRange(min_col_span, self.grid_cols)

            self.spin_sel_row.setValue(max(0, min(self.grid_rows - 1, int(cfg.get("row", 0)))))
            self.spin_sel_col.setValue(max(0, min(self.grid_cols - 1, int(cfg.get("col", 0)))))
            self.spin_sel_row_span.setValue(max(min_row_span, min(self.grid_rows, int(cfg.get("row_span", 1)))))
            self.spin_sel_col_span.setValue(max(min_col_span, min(self.grid_cols, int(cfg.get("col_span", 1)))))
        finally:
            self._prop_syncing = False

    def _show_tool_context_menu(self, pos):
        item = self.list_tools.itemAt(pos)
        if item is not None:
            self.selected_widget_id = item.data(Qt.UserRole)
            if self.selected_widget_id not in self.selected_widget_ids:
                self.selected_widget_ids = {self.selected_widget_id}
            self._refresh_selection_ui()

        menu = QMenu(self)
        act_edit = menu.addAction("Edit")
        menu.addAction(self.act_cut)
        menu.addAction(self.act_copy)
        menu.addAction(self.act_paste)
        act_delete = menu.addAction("Delete")
        menu.addSeparator()
        act_front = menu.addAction("Bring Front")
        act_back = menu.addAction("Send Back")
        act_forward = menu.addAction("Forward")
        act_backward = menu.addAction("Backward")
        menu.addSeparator()
        act_conflict = menu.addAction("Focus Conflict")

        chosen = menu.exec_(self.list_tools.mapToGlobal(pos))
        if chosen == act_edit:
            self.edit_selected_widget()
        elif chosen == act_delete:
            self.delete_selected_widget()
        elif chosen == act_front:
            self.bring_to_front()
        elif chosen == act_back:
            self.send_to_back()
        elif chosen == act_forward:
            self.move_forward()
        elif chosen == act_backward:
            self.move_backward()
        elif chosen == act_conflict:
            self.focus_next_conflict()

    def _sync_canvas_size(self):
        self.canvas.setFixedSize(
            self.grid_cols * self.grid_cell_size,
            self.grid_rows * self.grid_cell_size,
        )
        # Clear obsolete tracks when loading a panel with fewer rows/columns.
        for r in range(max(self.grid_rows, self.canvas_layout.rowCount())):
            self.canvas_layout.setRowStretch(r, 0)
            self.canvas_layout.setRowMinimumHeight(r, self.grid_cell_size if r < self.grid_rows else 0)
        for c in range(max(self.grid_cols, self.canvas_layout.columnCount())):
            self.canvas_layout.setColumnStretch(c, 0)
            self.canvas_layout.setColumnMinimumWidth(c, self.grid_cell_size if c < self.grid_cols else 0)
        self.canvas.update()

    def eventFilter(self, watched, event):
        if (
            hasattr(self, "canvas_scroll")
            and watched is self.canvas_scroll.viewport()
            and event.type() == QEvent.Resize
            and not self._grid_resize_pending
        ):
            self._grid_resize_pending = True
            QTimer.singleShot(0, self._expand_grid_to_viewport)
        return super().eventFilter(watched, event)

    def _expand_grid_to_viewport(self):
        self._grid_resize_pending = False
        viewport = self.canvas_scroll.viewport()
        required_cols = max(1, math.ceil(viewport.width() / self.grid_cell_size))
        required_rows = max(1, math.ceil(viewport.height() / self.grid_cell_size))
        new_cols = max(self.grid_cols, required_cols)
        new_rows = max(self.grid_rows, required_rows)
        if (new_rows, new_cols) == (self.grid_rows, self.grid_cols):
            return

        self.grid_rows = new_rows
        self.grid_cols = new_cols
        self._sync_canvas_size()
        self.canvas_layout.activate()
        # Child cells keep their pixel pitch independently of canvas dimensions.
        self._refresh_selection_ui()

    def rebuild_grid(self):
        if hasattr(self, 'properties'):
            self.properties.cancel_color_preview()
        self.stop_panel_commands("패널 도구 갱신")
        tab_indices = {wid: control.currentIndex() for wid, control in self.widget_controls.items()
                       if isinstance(control, QTabWidget)}
        scroll_positions = {key: page.scroll_position() for key, page in self._container_pages.items()}
        while self.canvas_layout.count():
            item = self.canvas_layout.takeAt(0)
            w = item.widget()
            if w:
                w.hide()
                w.setParent(None)
                w.deleteLater()

        self.widget_frames.clear()
        self.widget_controls.clear()
        self.widget_child_hosts.clear()
        self._container_pages.clear()
        self._hide_drag_preview()
        self._sync_canvas_size()

        sorted_cfg = sorted(self.widgets_config, key=lambda c: int(c.get("z_index", 0)))

        for cfg in sorted_cfg:
            frame = QFrame()
            frame.setFrameShape(QFrame.StyledPanel)
            frame.setObjectName(cfg.get("id"))
            frame.setProperty("panelCard", True)
            frame.setStyleSheet(TOOL_STYLE)

            frame_layout = QVBoxLayout(frame)
            frame_margin = max(2, min(6, self.grid_cell_size // 4))
            frame_layout.setContentsMargins(
                frame_margin, frame_margin, frame_margin, frame_margin
            )
            frame_layout.setSpacing(4)

            wtype = cfg.get("widget_type")
            title_widget_for_binding = None

            # group_box와 tab_container는 자체적으로 제목을 표시하므로,
            # 중복되는 외부 라벨을 생성하지 않습니다.
            if wtype not in ("group_box", "tab_container", "slider", "grid_view"):
                behavior = str(cfg.get("behavior", "none")).upper()
                title = FittingLabel(f"[{behavior}] {cfg.get('title', 'Widget')}")
                title.setProperty("panelTitle", True)
                # 라벨이 수직으로 늘어나는 것을 방지하고 컨트롤 위젯이 공간을 차지하도록 합니다.
                title.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
                title_align_str = str(cfg.get("title_align", "center")).lower()
                if title_align_str == "left":
                    align_flag = Qt.AlignLeft | Qt.AlignVCenter
                elif title_align_str == "right":
                    align_flag = Qt.AlignRight | Qt.AlignVCenter
                else:
                    align_flag = Qt.AlignCenter
                title.setAlignment(align_flag)
                frame_layout.addWidget(title)
                title_widget_for_binding = title

            ctrl, child_host = self._create_runtime_widget(cfg)
            if wtype in ('group_box', 'tab_container'):
                pages = [page for (ident, _index), page in self._container_pages.items() if ident == cfg['id']]
                apply_container_style(frame, ctrl, pages, cfg.get('binding', {}))
            if isinstance(ctrl, QTabWidget):
                index = tab_indices.get(cfg['id'], 0)
                ctrl.setCurrentIndex(max(0, min(ctrl.count() - 1, index)))
            if wtype == "slider":
                title_widget_for_binding = ctrl.findChild(QLabel, "slider_title")
            elif wtype == 'grid_view':
                title_widget_for_binding = ctrl.title_label
            # Stretch factor 1 allows the control to expand and fill available vertical space.
            frame_layout.addWidget(ctrl, 1)

            wid = cfg.get("id")
            self.widget_frames[wid] = frame
            self.widget_controls[wid] = ctrl
            self.widget_child_hosts[wid] = child_host

            self._bind_select(frame, wid)
            if title_widget_for_binding:
                self._bind_select(title_widget_for_binding, wid)

        for cfg in sorted_cfg:
            wid = cfg.get("id")
            frame = self.widget_frames.get(wid)
            if frame is None:
                continue

            parent_id = cfg.get("parent_id")
            if parent_id and parent_id == wid:
                parent_id = None

            row = int(cfg.get("row", 0))
            col = int(cfg.get("col", 0))
            row_span = max(1, int(cfg.get("row_span", 1)))
            col_span = max(1, int(cfg.get("col_span", 1)))

            if parent_id and parent_id in self.widget_child_hosts and self.widget_child_hosts[parent_id] is not None:
                # Child size hints must not redistribute grid tracks when the
                # tool moves, otherwise the drop jumps away from its preview.
                frame.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
                frame.layout().setSizeConstraint(QLayout.SetNoConstraint)
                frame.setMinimumSize(0, 0)
                host_layout = self.widget_child_hosts[parent_id]
                parent_ctrl = self.widget_controls.get(parent_id)
                if isinstance(parent_ctrl, QTabWidget):
                    tab_index = max(0, min(parent_ctrl.count() - 1, int(cfg.get("tab_index", 0))))
                    host_layout = parent_ctrl.widget(tab_index).grid
                host_layout.addWidget(frame, row, col, row_span, col_span)
            else:
                self.canvas_layout.addWidget(frame, row, col, row_span, col_span)

            frame.raise_()

        for page in self._container_pages.values():
            page.refresh_extent()
        self._apply_page_visibility()
        for key, position in scroll_positions.items():
            if key in self._container_pages:
                self._container_pages[key].restore_scroll(position)
        self._refresh_selection_ui()
        self._refresh_tool_list()
        self._sync_frame_timers_from_configs()
        self._record_history()

    def _bind_select(self, widget, widget_id):
        widget.setMouseTracking(True)

        def _update_cursor(event):
            if self.mode != "edit" or self.draw_mode is not None:
                widget.unsetCursor()
                return

            cfg = self._get_selected_config()
            if not cfg or cfg.get("id") != widget_id:
                widget.setCursor(Qt.ArrowCursor)
                return

            local = event.pos() if event is not None else None
            if local is None:
                widget.setCursor(Qt.ArrowCursor)
                return

            if local.x() >= (max(1, widget.width()) - 16) and local.y() >= (max(1, widget.height()) - 16):
                widget.setCursor(Qt.SizeFDiagCursor)
            else:
                widget.setCursor(Qt.OpenHandCursor)

        def _on_press(event):
            # RUN/STANDBY 모드에서는 위젯을 클릭해도 선택되지 않습니다.
            if self.mode != "edit":
                return
            self.canvas.setFocus()

            self._ctrl_drag_copy = bool(event is not None and event.modifiers() & Qt.ControlModifier)
            self._ctrl_click_selected = False
            if self._ctrl_drag_copy:
                ids = {c["id"] for c in self.selected_configs()}
                self._ctrl_click_selected = widget_id in ids
                ids.add(widget_id)
                self.selected_widget_ids = ids
            elif widget_id not in self.selected_widget_ids:
                self.selected_widget_ids = {widget_id}
            self.selected_widget_id = widget_id
            self._refresh_selection_ui()

            if self.draw_mode is None and event is not None and event.button() == Qt.LeftButton:
                try:
                    self._drag_target_id = widget_id
                    self._drag_start_global = event.globalPos()

                    # 드래그 대상인 전체 프레임(widget_frames[widget_id])을 기준으로 오프셋을 계산합니다.
                    # 이렇게 하면 제목 라벨이나 다른 내부 위젯 중 어느 것을 클릭해도
                    # 항상 전체 프레임의 좌상단을 기준으로 상대 위치가 유지됩니다.
                    frame_widget = self.widget_frames.get(widget_id)
                    self._drag_offset_global = event.globalPos() - frame_widget.mapToGlobal(QPoint(0, 0))
                    self._drag_origin_parent = self._get_selected_config().get("parent_id") if self._get_selected_config() else None
                    cfg = self._get_selected_config()
                    if cfg:
                        self._drag_origin_cell = (int(cfg.get("row", 0)), int(cfg.get("col", 0)))
                        self._drag_origin_span = (int(cfg.get("row_span", 1)), int(cfg.get("col_span", 1)))

                    local = event.pos()
                    w = max(1, widget.width())
                    h = max(1, widget.height())
                    self._drag_resize_mode = not self._ctrl_drag_copy and (local.x() >= (w - 16) and local.y() >= (h - 16))
                except Exception:
                    self._drag_target_id = None
                    self._drag_start_global = None
                    self._drag_offset_global = None
                    self._drag_origin_cell = None
                    self._drag_origin_span = None
                    self._drag_resize_mode = False

        def _on_move(event):
            _update_cursor(event)
            if self.mode != "edit" or self.draw_mode is not None:
                return
            if self._drag_target_id != widget_id:
                return
            if self._drag_start_global is None or event is None:
                return
            if not (event.buttons() & Qt.LeftButton):
                return

            cfg = self._get_selected_config()
            if not cfg:
                return

            end_pos = event.globalPos()

            if self._drag_resize_mode:
                parent_id = cfg.get("parent_id")
                r_end, c_end = self._cell_from_global_in_parent(parent_id, end_pos)
                r_start = int(cfg.get("row", 0))
                c_start = int(cfg.get("col", 0))
                preview_row_span = max(1, r_end - r_start + 1)
                preview_col_span = max(1, c_end - c_start + 1)
                self._show_drag_preview(cfg, preview_row_span, preview_col_span, resize_mode=True)
            else:
                new_parent = cfg.get('parent_id') if self._ctrl_drag_copy or len(self.selected_configs()) > 1 else self._hit_group_parent_from_global(end_pos, exclude_id=widget_id)
                # 위젯의 좌상단이 위치할 목표 지점을 오프셋을 적용하여 계산합니다.
                preview_row, preview_col = self._drag_move_cell(cfg, new_parent, end_pos)
                self._show_drag_preview(cfg, preview_row, preview_col, parent_id=new_parent, resize_mode=False)

        def _on_release(event):
            if self.mode != "edit" or self.draw_mode is not None:
                return
            if self._drag_target_id != widget_id:
                return
            if self._drag_start_global is None or self._drag_origin_cell is None:
                return
            if event is None:
                return

            try:
                end_pos = event.globalPos()

                # 마우스가 거의 움직이지 않았다면(클릭), 드래그로 처리하지 않고 종료합니다.
                if (end_pos - self._drag_start_global).manhattanLength() < QApplication.startDragDistance():
                    if self._ctrl_drag_copy and self._ctrl_click_selected:
                        self.selected_widget_ids.discard(widget_id)
                        self.selected_widget_id = next(iter(self.selected_widget_ids), None)
                        self._refresh_selection_ui()
                    return

                cfg = self._get_selected_config()
                if not cfg:
                    return

                if self._ctrl_drag_copy:
                    adjusted_pos = end_pos - self._drag_offset_global
                    row_new, col_new = self._cell_from_global_in_parent(cfg.get('parent_id'), adjusted_pos)
                    snapshot = self._copy_tool_snapshot()
                    self._duplicate_tools(snapshot, row_new - int(cfg.get('row', 0)), col_new - int(cfg.get('col', 0)))
                elif self._drag_resize_mode:
                    parent_id = cfg.get("parent_id")
                    r_end, c_end = self._cell_from_global_in_parent(parent_id, end_pos)
                    r_start = int(cfg.get("row", 0))
                    c_start = int(cfg.get("col", 0))

                    wtype = cfg.get("widget_type")
                    min_row_span = self._minimum_tool_row_span(wtype, cfg)
                    min_col_span = self._minimum_tool_col_span(wtype, cfg)
                    cfg["row_span"] = (
                        min_row_span if wtype == "slider"
                        else max(min_row_span, r_end - r_start + 1)
                    )
                    cfg["col_span"] = max(min_col_span, c_end - c_start + 1)
                    self.rebuild_grid()
                elif len(self.selected_configs()) > 1:
                    adjusted_pos = end_pos - self._drag_offset_global
                    row_new, col_new = self._cell_from_global_in_parent(cfg.get('parent_id'), adjusted_pos)
                    self._move_selected(col_new - self._drag_origin_cell[1],
                                        row_new - self._drag_origin_cell[0])
                    self.rebuild_grid()
                else:
                    new_parent = self._hit_group_parent_from_global(end_pos, exclude_id=widget_id)
                    # 최종 위치를 오프셋을 적용하여 계산합니다.
                    row_new, col_new = self._drag_move_cell(cfg, new_parent, end_pos)

                    cfg["parent_id"] = new_parent
                    parent_ctrl = self.widget_controls.get(new_parent)
                    if isinstance(parent_ctrl, QTabWidget):
                        cfg['tab_index'] = parent_ctrl.currentIndex()
                    cfg["row"] = row_new
                    cfg["col"] = col_new
                    self.rebuild_grid()
            finally:
                self._drag_target_id = None
                self._drag_start_global = None
                self._drag_offset_global = None
                self._drag_origin_cell = None
                self._drag_origin_span = None
                self._drag_origin_parent = None
                self._drag_resize_mode = False
                self._ctrl_drag_copy = False
                self._hide_drag_preview()

        widget.mousePressEvent = _on_press
        widget.mouseMoveEvent = _on_move
        widget.mouseReleaseEvent = _on_release

    def _refresh_selection_ui(self):
        ids = {c["id"] for c in self.selected_configs()}
        for wid, frame in self.widget_frames.items():
            selected = wid in ids
            frame.setProperty("selected", selected)
            frame.style().unpolish(frame)
            frame.style().polish(frame)
            frame.update()

        self._tool_list_syncing = True
        for i in range(self.list_tools.count()):
            it = self.list_tools.item(i)
            it.setSelected(it.data(Qt.UserRole) in ids)
        self._tool_list_syncing = False

        self._sync_geom_editor_from_selection()
        if hasattr(self, "properties"):
            self.properties.refresh()
        self._refresh_display_test_tab()
        if self._history_current is not None and self._history_current["widgets"] == self.widgets_config:
            self._history_current["selected"] = self.selected_widget_id
            self._history_current["ids"] = set(self.selected_widget_ids)

    def _stroke_qt_style(self, style_name):
        if style_name == "dash":
            return "dashed"
        if style_name == "dot":
            return "dotted"
        return "solid"

    def _open_grid_graph(self, cfg):
        entries = cfg.get('binding', {}).get('grid_signals', [])
        try:
            if not hasattr(self.main_window, 'open_realtime_graph'):
                raise ValueError('실시간 그래프를 열 수 있는 메인창이 필요합니다.')
            return self.main_window.open_realtime_graph(entries)
        except ValueError as exc:
            QMessageBox.warning(self, '그리드 실시간 그래프', str(exc))

    def _create_runtime_widget(self, cfg):
        wtype = cfg.get("widget_type", "label")
        behavior = cfg.get("behavior", "none")
        binding = cfg.get("binding", {})

        min_v = float(binding.get("min", 0.0))
        max_v = float(binding.get("max", 100.0))
        if max_v <= min_v:
            max_v = min_v + 1.0

        if wtype == "sequence":
            return SequenceControl(self, cfg), None

        if wtype == "button":
            btn = QPushButton("Send")
            btn.setProperty("panelPrimary", True)
            btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            push_value = float(binding.get("tx_press_value", max_v))
            pull_value = float(binding.get("tx_release_value", min_v))
            def _pressed():
                if self.mode != "run":
                    return
                self._emit_tool_action(cfg, push_value, 'tx_press_value')

            def _released():
                self._emit_tool_action(cfg, pull_value, 'tx_release_value')

            btn.pressed.connect(_pressed)
            btn.released.connect(_released)
            return btn, None

        if wtype == "toggle":
            btn = QPushButton("OFF")
            btn.setCheckable(True)
            btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            on_value = float(binding.get("tx_on_value", max_v))
            off_value = float(binding.get("tx_off_value", min_v))

            def _toggle(checked):
                btn.setText("ON" if checked else "OFF")
                if checked and behavior == "tx":
                    self._uncheck_signal_toggles(cfg)
                self._emit_tool_action(cfg, on_value if checked else off_value,
                                       'tx_on_value' if checked else 'tx_off_value')

            btn.toggled.connect(_toggle)
            return btn, None

        if wtype == "slider":
            container = QWidget()
            lay = QGridLayout(container)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setHorizontalSpacing(max(1, self.grid_cell_size // 2))
            lay.setVerticalSpacing(0)
            slider = QSlider(Qt.Horizontal)
            slider.setObjectName("slider")
            slider.setFocusPolicy(Qt.StrongFocus)
            slider.setSingleStep(1)
            slider.setInvertedControls(False)
            slider.setLayoutDirection(Qt.LeftToRight)
            steps = self._slider_steps(binding, min_v, max_v)
            slider.setRange(0, steps)
            initial = max(min_v, min(max_v, float(binding.get("tx_initial_value", min_v))))
            initial_step = int(round((initial - min_v) / (max_v - min_v) * steps))
            slider.setValue(initial_step)
            initial = min_v + (max_v - min_v) * slider.value() / steps
            display_initial = quantize_value(binding, initial) if behavior == "tx" else initial
            value_label = FittingLabel(
                self._format_slider_value(binding, display_initial),
                base_pixel_size=max(9, min(16, self.grid_cell_size - 6)),
            )
            value_label.setObjectName("value_label")
            value_label.setAlignment(Qt.AlignCenter)
            value_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            value_label.setFixedHeight(value_label.fontMetrics().height() + 1)
            slider_area = QWidget()
            slider_area_layout = QVBoxLayout(slider_area)
            slider_area_layout.setContentsMargins(0, 0, 0, 0)
            slider_area_layout.setSpacing(0)
            slider_area_layout.addStretch(1)
            slider.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            slider.setFixedHeight(max(
                18,
                min(28, self.grid_cell_size * 7 // 8),
                slider.sizeHint().height() + 4,
            ))
            slider_area_layout.addWidget(slider)
            slider_area_layout.addWidget(value_label)
            slider_area_layout.addStretch(1)
            slider_area.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            title = FittingLabel(
                f"[{str(behavior).upper()}] {cfg.get('title', 'Widget')}"
            )
            title.setObjectName("slider_title")
            title.setProperty("panelTitle", True)
            title.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            title.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
            home_width = self.grid_cell_size * 3
            lay.setColumnMinimumWidth(0, home_width)
            lay.addWidget(title, 0, 0)
            lay.addWidget(slider_area, 1, 1, 2, 1)
            value_input = QLineEdit(self._format_slider_value(binding, display_initial))
            value_input.setObjectName("slider_value_input")
            value_input.setMinimumWidth(28)
            value_font = value_input.font()
            value_font.setPixelSize(max(8, min(10, self.grid_cell_size * 5 // 8)))
            value_input.setFont(value_font)
            value_font_metrics = QFontMetrics(value_font)
            widest_input_text = max(
                self._format_slider_value(binding, min_v),
                self._format_slider_value(binding, max_v),
                self._format_slider_value(binding, display_initial),
            )
            value_input.setFixedWidth(max(
                44,
                min(64, value_font_metrics.horizontalAdvance(widest_input_text) + 4),
            ))
            value_input.setFixedHeight(20)
            value_input.setToolTip("입력값은 유지되며 Set을 눌러야 적용됩니다. 숫자가 아니면 Home 값으로 복귀합니다.")
            set_button = QPushButton("Set")
            set_button.setObjectName("slider_set")
            compact_font = set_button.font()
            compact_font.setPixelSize(max(8, min(10, self.grid_cell_size // 2)))
            set_button.setFont(compact_font)
            set_button.setFixedWidth(max(30, self.grid_cell_size * 2))
            set_button.setFixedHeight(20)
            set_button.setStyleSheet("padding: 0px 2px;")
            input_row = QHBoxLayout()
            input_row.setSpacing(4)
            input_row.addWidget(value_input, 1)
            input_row.addWidget(set_button)
            lay.addLayout(input_row, 0, 1, Qt.AlignRight)
            home = QPushButton("Home")
            home.setObjectName("slider_home")
            home.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
            home_font = home.font()
            home_font.setPixelSize(max(10, min(11, self.grid_cell_size * 5 // 8)))
            home.setFont(home_font)
            home.setFixedWidth(home_width)
            home.setStyleSheet("padding: 1px 2px;")
            home.setFixedHeight(max(
                home.sizeHint().height(),
                self.grid_cell_size * 2,
            ))
            home.setToolTip("Return to Initial Value")
            slider_area.setFixedHeight(max(
                slider.height() + value_label.height(),
                home.height(),
            ))
            lay.addWidget(home, 1, 0, 2, 1, Qt.AlignLeft | Qt.AlignTop)
            lay.setColumnStretch(1, 1)
            lay.setRowStretch(1, 1)
            container.setFixedHeight(input_row.sizeHint().height() + slider_area.height())

            def _on_changed(v):
                ratio = 0.0 if steps <= 0 else (v / float(steps))
                phys = min_v + ((max_v - min_v) * ratio)
                command_value = phys
                if behavior == "tx":
                    phys = quantize_value(binding, phys)
                value_label.setText(self._format_slider_value(binding, phys))
                if behavior != "tx":
                    value_input.setText(self._format_slider_value(binding, phys))
                if behavior == "tx":
                    self._emit_tx(cfg, command_value if cfg.get('tx_commands') else phys)

            slider.valueChanged.connect(_on_changed)
            def _home():
                was_blocked = slider.blockSignals(True)
                try:
                    slider.setValue(initial_step)
                finally:
                    slider.blockSignals(was_blocked)
                value_label.setText(self._format_slider_value(binding, display_initial))
                if behavior != "tx":
                    value_input.setText(self._format_slider_value(binding, display_initial))
                if behavior == "tx":
                    self._emit_tx(dict(cfg, _slider_home=True), initial)
            home.clicked.connect(_home)
            def _set_value():
                try:
                    value = float(value_input.text())
                    if not math.isfinite(value):
                        raise ValueError('Non-finite value')
                except ValueError:
                    if slider.value() != initial_step:
                        slider.setValue(initial_step)
                    else:
                        _on_changed(initial_step)
                    value_input.setText(self._format_slider_value(binding, display_initial))
                    return
                value = max(min_v, min(max_v, value))
                value_input.setText(self._format_slider_value(binding, value))
                target = int(round((value - min_v) / (max_v - min_v) * steps))
                if slider.value() == target:
                    _on_changed(target)
                else:
                    slider.setValue(target)
            set_button.clicked.connect(_set_value)
            return container, None

        if wtype == "spinbox":
            spin = QDoubleSpinBox()
            spin.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
            spin_font = spin.font()
            spin_font.setPixelSize(max(8, min(14, self.grid_cell_size * 5 // 8)))
            spin.setFont(spin_font)
            spin.setRange(min_v, max_v)
            step = float(binding.get("tx_resolution", max((max_v - min_v) / 100.0, 0.001)))
            step = max(step, 0.000001)
            spin.setSingleStep(step)
            spin.setDecimals(self._resolution_decimals(step))
            if behavior == "tx":
                spin.valueChanged.connect(lambda v: self._emit_tx(cfg, float(v)))
            return spin, None

        if wtype == 'grid_view':
            return RxGridControl(cfg, self.db_messages, lambda: self._open_grid_graph(cfg)), None

        if wtype == "progress":
            bar = FittingProgressBar()
            bar.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
            bar.setRange(0, 1000)
            bar.setValue(0)
            bar.setFormat(self._format_progress_text(binding, None, 0))
            return bar, None

        if wtype == "status_lamp":
            lamp = QLabel("OFF")
            lamp.setAlignment(Qt.AlignCenter)
            lamp.setStyleSheet(lamp_style(False))
            return lamp, None

        if wtype == "group_box":
            grp = FittingGroupBox(cfg.get("title", "Group"))
            page = ContainerGrid(self.grid_cell_size)
            self._container_pages[(cfg['id'], 0)] = page
            grp_lay = QVBoxLayout(grp)
            grp_lay.setContentsMargins(6, 6, 6, 6)
            grp_lay.addWidget(page, 1)
            return grp, page.grid

        if wtype == "tab_container":
            tabs = QTabWidget()
            for index, name in enumerate(tab_names(cfg)):
                page = ContainerGrid(self.grid_cell_size)
                self._container_pages[(cfg['id'], index)] = page
                tabs.addTab(page, str(name))
            return tabs, tabs.widget(0).grid

        if wtype == "shape_line":
            line = QFrame()
            apply_drawing_style(line, wtype, binding)
            return line, None

        if wtype == "shape_rect":
            rect = QLabel("")
            apply_drawing_style(rect, wtype, binding)
            return rect, None

        label = FittingLabel("-", base_pixel_size=16)
        label.setProperty("panelValue", True)
        label.setAlignment(Qt.AlignCenter)
        return label, None

    def _apply_tool_color_preview(self, widget_id, binding):
        """Repaint an existing control without changing saved config or history."""
        cfg = self._cfg_by_id(widget_id)
        ctrl = self.widget_controls.get(widget_id)
        frame = self.widget_frames.get(widget_id)
        if cfg is None or ctrl is None or frame is None:
            return
        kind = cfg.get('widget_type')
        if kind in ('group_box', 'tab_container'):
            pages = [page for (ident, _index), page in self._container_pages.items() if ident == widget_id]
            apply_container_style(frame, ctrl, pages, binding)
        elif kind in ('shape_line', 'shape_rect'):
            apply_drawing_style(ctrl, kind, binding)
        elif kind == 'label' and cfg.get('behavior') == 'rx':
            sample = getattr(ctrl, '_rx_color_sample', None)
            if sample is None:
                original = cfg.get('binding', {})
                payload = self.latest_raw_by_msg.get((original.get('bus', 1), original.get('can_id', 0)))
                raw = self._extract_raw_value(payload, original) if payload is not None else None
                if raw is not None:
                    sample = (raw * float(original.get('scale', 1)) + float(original.get('offset', 0)), raw)
            ctrl.setStyleSheet(label_color_style(binding, *sample) if sample is not None else '')

    def _preview_parent_widget(self, cfg, parent_id=_CURRENT_PARENT):
        target_parent_id = cfg.get('parent_id') if parent_id is _CURRENT_PARENT else parent_id
        return self._child_host_layout(target_parent_id).parentWidget()

    def _show_drag_preview(self, cfg, row_or_span, col_or_span, parent_id=_CURRENT_PARENT, resize_mode=False):
        parent_widget = self._preview_parent_widget(cfg, parent_id=parent_id)
        if parent_widget is None:
            return

        if resize_mode:
            row = int(cfg.get("row", 0))
            col = int(cfg.get("col", 0))
            row_span = min(int(row_or_span), self.grid_rows - row)
            col_span = min(int(col_or_span), self.grid_cols - col)
            if cfg.get('widget_type') == 'grid_view':
                row_span = max(row_span, self._minimum_tool_row_span('grid_view', cfg))
                col_span = max(col_span, self._minimum_tool_col_span('grid_view', cfg))
        else:
            row_span = max(1, int(cfg.get("row_span", 1)))
            col_span = max(1, int(cfg.get("col_span", 1)))
            row = max(0, min(self.grid_rows - row_span, int(row_or_span)))
            col = max(0, min(self.grid_cols - col_span, int(col_or_span)))

        if parent_widget is self.canvas:
            x = col * self.grid_cell_size
            y = row * self.grid_cell_size
            w = col_span * self.grid_cell_size
            h = row_span * self.grid_cell_size
        else:
            layout = parent_widget.layout()
            cell_rect = layout.cellRect(row, col) if isinstance(layout, QGridLayout) else QRect()
            last_cell = (
                layout.cellRect(row + row_span - 1, col + col_span - 1)
                if isinstance(layout, QGridLayout)
                else QRect()
            )
            if not cell_rect.isEmpty() and not last_cell.isEmpty():
                x = cell_rect.x()
                y = cell_rect.y()
                w = last_cell.right() - x + 1
                h = last_cell.bottom() - y + 1
            else:
                parent_w = max(1, parent_widget.width())
                parent_h = max(1, parent_widget.height())
                cell_w = float(parent_w) / max(1, self.grid_cols)
                cell_h = float(parent_h) / max(1, self.grid_rows)
                x = int(round(col * cell_w))
                y = int(round(row * cell_h))
                x2 = int(round((col + col_span) * cell_w))
                y2 = int(round((row + row_span) * cell_h))
                w = max(1, x2 - x)
                h = max(1, y2 - y)

        if self._drag_preview_band is None or self._drag_preview_band.parent() is not parent_widget:
            self._hide_drag_preview()
            self._drag_preview_band = QRubberBand(QRubberBand.Rectangle, parent_widget)
            self._drag_preview_band.setStyleSheet("border: 2px dashed #0078D7; background: rgba(0,120,215,30);")

        self._drag_preview_band.setGeometry(QRect(x, y, w, h))
        self._drag_preview_band.show()
        self._drag_preview_band.raise_()

    def _hide_drag_preview(self):
        if self._drag_preview_band is not None:
            try:
                self._drag_preview_band.hide()
                self._drag_preview_band.deleteLater()
            except Exception:
                pass
            self._drag_preview_band = None

    def bring_to_front(self):
        cfg = self._get_selected_config()
        if not cfg:
            return
        cfg["z_index"] = self._next_z()
        self.rebuild_grid()

    def send_to_back(self):
        cfg = self._get_selected_config()
        if not cfg:
            return
        min_z = min((int(c.get("z_index", 0)) for c in self.widgets_config), default=0)
        cfg["z_index"] = min_z - 1
        self.rebuild_grid()

    def move_forward(self):
        cfg = self._get_selected_config()
        if not cfg:
            return
        cfg["z_index"] = int(cfg.get("z_index", 0)) + 1
        self.rebuild_grid()

    def move_backward(self):
        cfg = self._get_selected_config()
        if not cfg:
            return
        cfg["z_index"] = int(cfg.get("z_index", 0)) - 1
        self.rebuild_grid()

    def _move_selected(self, dx, dy):
        configs = self.selected_configs()
        ids = {cfg['id'] for cfg in configs}
        roots = [cfg for cfg in configs
                 if not any(self._is_descendant(cfg['id'], wid) for wid in ids if wid != cfg['id'])]
        if not roots:
            return
        # Clamp one shared offset so boundary contact preserves relative positions.
        dx = max(-min(int(c.get('col', 0)) for c in roots),
                 min(int(dx), min(self.grid_cols - 1 - int(c.get('col', 0)) for c in roots)))
        dy = max(-min(int(c.get('row', 0)) for c in roots),
                 min(int(dy), min(self.grid_rows - 1 - int(c.get('row', 0)) for c in roots)))
        for cfg in roots:
            cfg['col'] = int(cfg.get('col', 0)) + dx
            cfg['row'] = int(cfg.get('row', 0)) + dy

    def nudge_selected(self, dx, dy):
        if self.mode != "edit":
            return
        cfg = self._get_selected_config()
        if not cfg:
            return

        self._move_selected(dx, dy)
        self.rebuild_grid()

    def resize_selected_span(self, dcol_span, drow_span):
        if self.mode != "edit":
            return
        cfg = self._get_selected_config()
        if not cfg:
            return

        wtype = cfg.get("widget_type")
        min_row_span = self._minimum_tool_row_span(wtype, cfg)
        min_col_span = self._minimum_tool_col_span(wtype, cfg)

        col_span = int(cfg.get("col_span", 1)) + int(dcol_span)
        row_span = (
            min_row_span if wtype == "slider"
            else int(cfg.get("row_span", 1)) + int(drow_span)
        )
        cfg["col_span"] = max(min_col_span, min(self.grid_cols, col_span))
        cfg["row_span"] = max(min_row_span, min(self.grid_rows, row_span))
        self.rebuild_grid()

    def check_tx_overlap(self):
        overlap_ids, overlap_map, issues = self._collect_overlap_issue_lines()
        self._overlap_map = overlap_map

        if not issues:
            QMessageBox.information(self, "TX Overlap Check", "No overlapping TX bit ranges found.")
            self._refresh_tool_list()
            return

        self._refresh_tool_list()
        QMessageBox.warning(
            self,
            "TX Overlap Check",
            "Overlapping TX bit ranges found:\n\n" + "\n".join(issues[:30]) +
            ("\n\n..." if len(issues) > 30 else "")
        )

    def _collect_overlap_details(self):
        ids, mapping, _ = self._collect_overlap_issue_lines()
        return ids, mapping

    def _collect_overlap_issue_lines(self):
        frames = {}
        ids, mapping, issues = set(), {}, []
        for cfg in self.widgets_config:
            if cfg.get("behavior") != "tx" or cfg.get("widget_type") == "sequence":
                continue
            binding = cfg.get("binding", {})
            key = self._frame_key_from_binding(binding)
            try:
                bits = set(bit_positions(binding, key[2]))
            except ValueError:
                continue
            frames.setdefault(key, []).append((cfg, bits))
        for key, items in frames.items():
            for index, (a, a_bits) in enumerate(items):
                for b, b_bits in items[index + 1:]:
                    shared = a_bits & b_bits
                    if shared:
                        ids.update((a["id"], b["id"]))
                        mapping.setdefault(a["id"], set()).add(b["id"])
                        mapping.setdefault(b["id"], set()).add(a["id"])
                        issues.append(f"Frame B{key[0]} 0x{key[1]:X} DLC {key[2]}: "
                                      f"{a.get('title', 'Widget')} <-> {b.get('title', 'Widget')} "
                                      f"(bits {', '.join(map(str, sorted(shared)))})")
        return ids, mapping, issues

    def focus_next_conflict(self):
        if not self.selected_widget_id:
            QMessageBox.information(self, "Conflict Focus", "Select a tool first.")
            return

        targets = sorted(list(self._overlap_map.get(self.selected_widget_id, set())))
        if not targets:
            QMessageBox.information(self, "Conflict Focus", "No conflict target for selected tool.")
            return

        idx = int(self._conflict_cursor.get(self.selected_widget_id, 0))
        idx = idx % len(targets)
        target = targets[idx]
        self._conflict_cursor[self.selected_widget_id] = idx + 1

        target_page = self._page_for(self._cfg_by_id(target))
        self.page_tabs.setCurrentIndex(next(i for i, p in enumerate(self.pages) if p['id'] == target_page))
        self.selected_widget_id = target
        self._refresh_selection_ui()

    def _tx_value_throttled(self, binding, value):
        self._emit_tx(dict(behavior="tx", binding=binding), value)

    def _uncheck_signal_toggles(self, selected_cfg):
        def signal_key(binding):
            return (
                int(binding.get("bus", 1)),
                int(binding.get("can_id", 0)),
                int(binding.get("start_bit", 0)),
                int(binding.get("bit_length", 8)),
                str(binding.get("byte_order", "little_endian")),
            )

        selected_keys = {signal_key(b) for b in command_bindings(selected_cfg, enabled_only=True)}
        for cfg in self.widgets_config:
            if cfg.get("id") == selected_cfg.get("id"):
                continue
            if cfg.get("widget_type") != "toggle" or cfg.get("behavior") != "tx":
                continue
            if not selected_keys.intersection(signal_key(b) for b in command_bindings(cfg, enabled_only=True)):
                continue
            ctrl = self.widget_controls.get(cfg.get("id"))
            if isinstance(ctrl, QPushButton) and ctrl.isCheckable():
                # Deselect visually without sending OFF over the selected ON value.
                was_blocked = ctrl.blockSignals(True)
                try:
                    ctrl.setChecked(False)
                    ctrl.setText("OFF")
                finally:
                    ctrl.blockSignals(was_blocked)

    def _emit_tool_action(self, cfg, value, action):
        if cfg.get('tx_commands'):
            cfg = dict(cfg, _tx_action=action)
        self._emit_tx(cfg, value)

    def _emit_tx(self, cfg, value):
        with ExitStack() as locks:
            for key in sorted(self._packet_runtimes):
                locks.enter_context(self._packet_runtimes[key].lock)
            self._emit_tx_locked(cfg, value)

    def _emit_tx_locked(self, cfg, value):
        if self.mode != "run" or self._init_running or not self.isVisible() or cfg.get("behavior") != "tx":
            return
        try:
            # Prepare all overlays before committing so invalid commands cannot
            # leave a partially updated set of periodic packets.
            from .binding import pack_value
            pending = {}
            for binding in command_bindings(cfg, enabled_only=True):
                packet = find_packet(self.tx_packets, binding)
                if packet is None:
                    raise ValueError("Register the TX packet first.")
                key = packet['packet_id']
                runtime = self._packet_runtimes[key]
                command_value = binding.get(cfg['_tx_action'], value) if cfg.get('_tx_action') else value
                if cfg.get('widget_type') == 'slider':
                    primary = cfg.get('binding', {})
                    low, high = float(primary.get('min', 0)), float(primary.get('max', 100))
                    target_low = float(binding.get('min', low))
                    target_high = float(binding.get('max', high))
                    if high <= low or target_high <= target_low:
                        raise ValueError('Slider Max must be greater than Min.')
                    if cfg.get('_slider_home'):
                        command_value = max(target_low, min(target_high,
                            float(binding.get('tx_initial_value', target_low))))
                    elif binding.get('slider_mapping', 'direct') == 'direct':
                        command_value = max(target_low, min(target_high, float(value)))
                    else:
                        ratio = max(0.0, min(1.0, (float(value) - low) / (high - low)))
                        command_value = target_low + ratio * (target_high - target_low)
                pending[key] = pack_value(pending.get(key, runtime.overlay), binding, command_value)
            changed = []
            for key, overlay in pending.items():
                runtime = self._packet_runtimes[key]
                if overlay != runtime.overlay:
                    previous = bytearray(runtime.overlay)
                    runtime.overlay = overlay
                    changed.append((runtime, previous))
            errors = []
            failed = set()
            for runtime, previous in changed:
                if runtime.packet['cycle'] == 0:
                    try:
                        runtime.send()
                    except Exception as exc:
                        # Failed immediate commands are not queued as sent values.
                        # A later explicit action with the same value can retry.
                        runtime.overlay = previous
                        failed.add(runtime.packet['packet_id'])
                        packet = runtime.packet
                        errors.append(f"BUS {packet['bus']} / 0x{packet['id']:X}: {exc}")
            if cfg.get('widget_type') == 'slider':
                self._sync_tx_sliders(cfg, set(pending) - failed)
            if errors:
                self._report_packet_error(' | '.join(errors))
        except Exception as exc:
            self._report_packet_error(exc)

    def _sync_tx_sliders(self, source, applied_packets):
        """Reflect command overlays without sending from another control."""
        from .binding import bit_positions, unpack_raw

        def field_key(binding):
            packet = find_packet(self.tx_packets, binding)
            if packet is None or packet['packet_id'] not in applied_packets:
                return None
            return (packet['packet_id'], tuple(bit_positions(binding, packet['length'])))

        source_bindings = command_bindings(source, enabled_only=True)
        touched = {field_key(binding) for binding in source_bindings} - {None}
        source_is_multi = len(source_bindings) > 1
        for cfg in self.widgets_config:
            if (cfg.get('id') == source.get('id') or cfg.get('behavior') != 'tx'
                    or cfg.get('widget_type') != 'slider'):
                continue
            bindings = command_bindings(cfg, enabled_only=True)
            matched = [binding for binding in bindings if field_key(binding) in touched]
            if not matched:
                continue
            if len(bindings) > 1:
                if not source_is_multi:
                    primary = cfg.get('binding', {})
                    home = max(float(primary.get('min', 0)), min(float(primary.get('max', 100)),
                               float(primary.get('tx_initial_value', primary.get('min', 0)))))
                    self._update_widget_value(cfg, home)
                continue
            binding = matched[0]
            packet = find_packet(self.tx_packets, binding)
            payload = self._packet_runtimes[packet['packet_id']].overlay
            raw = unpack_raw(payload, binding)
            value = raw * float(binding.get('scale', 1)) + float(binding.get('offset', 0))
            self._update_widget_value(cfg, value)

    def _frame_key_from_binding(self, binding):
        return (
            int(binding.get("bus", 1)),
            int(binding.get("can_id", 0)),
            max(1, min(64, int(binding.get("dlc", 8)))),
        )

    def _flush_frame(self, packet_id):
        if self.mode != "run" or self._init_running or packet_id in self._paused_packets or not self.isVisible():
            return
        try:
            self._packet_runtimes[packet_id].send()
        except Exception as exc:
            timer = self._frame_timers.pop(packet_id, None)
            if timer:
                timer.stop()
                timer.deleteLater()
            self._report_packet_error(exc)

    def _stop_all_frame_timers(self):
        for timer in self._frame_timers.values():
            try:
                timer.stop()
                timer.deleteLater()
            except Exception:
                pass
        self._frame_timers.clear()
        for sender in self._periodic_senders.values():
            sender.close()
        self._periodic_senders.clear()
        self._drain_periodic_events()
        self._periodic_reported_missed.clear()

    def hideEvent(self, event):
        if hasattr(self, '_periodic_senders'):
            self._stop_all_frame_timers()
        super().hideEvent(event)

    def stop_periodic_bus(self, bus):
        for key, task in list(self._frame_timers.items()):
            if task.runtime.packet['bus'] == bus:
                task.stop()
                self._frame_timers.pop(key, None)
                self._paused_packets.add(key)
        self._drain_periodic_events()

    def _periodic_completed(self, task, payload, timestamp):
        # Worker callback: no Qt widgets, decoding, file I/O or UI logging here.
        try:
            self._periodic_records.put_nowait((task, payload, timestamp, task.missed,
                                               task.max_lateness_ms))
        except queue.Full:
            self._periodic_dropped_records += 1

    def _drain_periodic_events(self):
        while not self._periodic_errors.empty():
            task, error = self._periodic_errors.get()
            key = task.runtime.packet['packet_id']
            if self._frame_timers.get(key) is task:
                self._frame_timers.pop(key)
                self._paused_packets.add(key)
                self._report_packet_error(error)
        for _ in range(500 if self._frame_timers else 20000):
            try:
                task, payload, timestamp, missed, max_lateness_ms = self._periodic_records.get_nowait()
            except queue.Empty:
                break
            packet = task.runtime.packet
            reported_missed = self._periodic_reported_missed.get(task, 0)
            if missed > reported_missed:
                self._periodic_reported_missed[task] = missed
                self.log_system(
                    f"TX 주기 지연: BUS {packet['bus']} / 0x{packet['id']:X}, "
                    f"누락 {missed - reported_missed}회 (누적 {missed}), "
                    f"최대 지연 {max_lateness_ms:.1f} ms",
                    'WARN')
            if hasattr(self.main_window, 'record_tx_activity'):
                self.main_window.record_tx_activity(packet['bus'], packet['id'], payload,
                                                    packet['is_fd'], timestamp,
                                                    is_ext=packet.get('is_extended_id', packet['id'] > 0x7FF))
        if self._periodic_dropped_records:
            self.log_system(f'화면 처리 지연으로 TX 기록 {self._periodic_dropped_records}개 생략 (송신은 계속됨)', 'WARN')
            self._periodic_dropped_records = 0
        if not self._frame_timers and self._periodic_records.empty():
            self._periodic_monitor.stop()

    def _sync_frame_timers_from_configs(self):
        if self.mode != "run" or self._init_running or not self.isVisible():
            self._stop_all_frame_timers()
            return
        for packet in self.tx_packets:
            key = packet['packet_id']
            if not packet['bus']:
                continue
            if packet['cycle'] <= 0 or key in self._paused_packets or key in self._frame_timers:
                continue
            bus = packet['bus']
            if bus not in self._periodic_senders:
                self._periodic_senders[bus] = PeriodicSender(
                    bus, self._periodic_completed,
                    lambda task, error: self._periodic_errors.put((task, error)))
            self._frame_timers[key] = self._periodic_senders[bus].add(self._packet_runtimes[key])
            self._periodic_monitor.start()

    def _slider_steps(self, binding, min_v, max_v):
        resolution = float(binding.get("tx_resolution", 1.0))
        resolution = max(resolution, 0.000001)
        steps = int(round((max_v - min_v) / resolution))
        return max(1, min(steps, 5000))

    def _format_slider_value(self, binding, value):
        decimals = self._resolution_decimals(binding.get("tx_resolution", 1.0))
        msg = self.db_messages.get(int(binding.get("bus", 1)), {}).get(int(binding.get("can_id", 0)))
        if msg and binding.get("signal_name"):
            try:
                sig = msg.get_signal_by_name(binding["signal_name"])
                scale = float(getattr(sig, "scale", binding.get("scale", 1.0)))
                offset = float(getattr(sig, "offset", binding.get("offset", 0.0)))
                decimals = max(decimals, self._resolution_decimals(scale), self._resolution_decimals(offset))
                if (not getattr(sig, "is_float", False)
                        and scale.is_integer()
                        and offset.is_integer()):
                    decimals = 0
            except KeyError:
                pass
        return f"{float(value):.{decimals}f}"

    def _resolution_decimals(self, step):
        text = f"{float(step):.10f}".rstrip("0")
        if "." not in text:
            return 0
        return min(6, len(text.split(".", 1)[1]))

    def _format_display_value(self, value):
        try:
            num = round(float(value), 3)
        except Exception:
            return str(value)

        if abs(num - round(num)) < 0.0005:
            return str(int(round(num)))

        text = f"{num:.3f}".rstrip("0").rstrip(".")
        return text if text else "0"

    def _format_rx_label(self, binding, value, raw_value=None, received_unit=None):
        signal = matching_signal(self.db_messages, binding, require_name=True)
        choice = None
        if signal is not None and signal.choices:
            raw_value = inferred_raw(binding, value, raw_value)
            if raw_value is not None:
                choice = signal.choices.get(raw_value)
        text = str(choice) if choice is not None else self._format_display_value(value)
        unit = binding.get('unit') or (signal.unit if signal is not None else None) or received_unit
        return f'{text} {unit}' if unit else text

    def _format_progress_text(self, binding, value, percent):
        mode = binding.get('progress_text_mode', 'percent')
        if mode not in ('value', 'both'):
            return f'{percent}%'
        if value is None:
            actual = '-'
        else:
            decimals = 6 if binding.get('value_precision_decimals') == 6 else 3
            actual = f'{value:.{decimals}f}'.rstrip('0').rstrip('.')
            if actual in ('', '-0'):
                actual = '0'
            unit = binding.get('unit', '')
            if unit:
                actual += f' {unit}'
        return f'{percent}% | {actual}' if mode == 'both' else actual

    def _eval_condition(self, value, op, a, b):
        if op == "gt":
            return value > a
        if op == "ge":
            return value >= a
        if op == "lt":
            return value < a
        if op == "le":
            return value <= a
        if op == "eq":
            return value == a
        if op == "ne":
            return value != a
        lo = min(a, b)
        hi = max(a, b)
        if op == "between":
            return lo <= value <= hi
        if op == "outside":
            return value < lo or value > hi
        return False

    def on_message_update(self, bus, can_id, data_bytes, ts):
        self.latest_raw_by_msg[(bus, can_id)] = bytes(data_bytes)

        for cfg in self.widgets_config:
            if cfg.get("behavior") != "rx":
                continue
            if cfg.get('widget_type') == 'grid_view':
                control = self.widget_controls.get(cfg['id'])
                if self.mode != 'edit' and isinstance(control, RxGridControl):
                    control.update_message(self.db_messages, bus, can_id, data_bytes)
                continue
            binding = cfg.get("binding", {})
            if int(binding.get("bus", -1)) != int(bus):
                continue
            if int(binding.get("can_id", -1)) != int(can_id):
                continue

            raw = self._extract_raw_value(bytes(data_bytes), binding)
            if raw is None:
                continue
            phys = raw * float(binding.get("scale", 1.0)) + float(binding.get("offset", 0.0))
            self._update_widget_value(cfg, phys, raw_value=raw)

    def on_signal_update(self, bus, sig_name, value, unit, ts, *, can_id=None):
        self.latest_value_by_signal[(bus, sig_name)] = value

        for cfg in self.widgets_config:
            if cfg.get("behavior") != "rx":
                continue
            if cfg.get('widget_type') == 'grid_view':
                continue  # The message decoder distinguishes identical names on different IDs.
            binding = cfg.get("binding", {})
            if int(binding.get("bus", -1)) != int(bus):
                continue
            if binding.get("signal_name") != sig_name:
                continue
            if can_id is not None and int(binding.get('can_id', -1)) != int(can_id):
                continue
            if (cfg.get('widget_type') == 'label'
                    and (int(bus), int(binding.get('can_id', 0))) in self.latest_raw_by_msg):
                continue  # Preserve the label decoded from its exact frame and RAW value.
            signal = matching_signal(self.db_messages, binding)
            if signal is None:
                continue
            if (float(binding.get("scale", 1)) != float(signal.scale)
                    or float(binding.get("offset", 0)) != float(signal.offset)
                    or bool(binding.get("signed", False)) != bool(signal.is_signed)):
                continue
            self._update_widget_value(cfg, value, received_unit=unit)

    def _update_widget_value(self, cfg, value, force=False, *, raw_value=None, received_unit=None):
        if self.mode == "edit" and not force:
            return

        wid = cfg.get("id")
        ctrl = self.widget_controls.get(wid)
        if not ctrl:
            return

        wtype = cfg.get("widget_type")
        binding = cfg.get("binding", {})
        min_v = float(binding.get("min", 0.0))
        max_v = float(binding.get("max", 100.0))
        if max_v <= min_v:
            max_v = min_v + 1.0

        try:
            v = float(value)
        except Exception:
            return

        if wtype == 'grid_view' and isinstance(ctrl, RxGridControl):
            ctrl.simulate_value(v)
            return

        if wtype == "progress":
            ratio = 0.0 if max_v == min_v else (v - min_v) / (max_v - min_v)
            ratio = min(1.0, max(0.0, ratio))
            ctrl.setValue(int(ratio * 1000.0))
            ctrl.setFormat(self._format_progress_text(binding, v, ctrl.value() // 10))
            return

        if wtype == "slider":
            if isinstance(ctrl, QWidget):
                slider = ctrl.findChild(QSlider, "slider")
                value_label = ctrl.findChild(QLabel, "value_label")
                value_input = ctrl.findChild(QLineEdit, "slider_value_input")
                if slider is not None:
                    steps = max(1, int(slider.maximum()))
                    ratio = 0.0 if max_v == min_v else (v - min_v) / (max_v - min_v)
                    ratio = min(1.0, max(0.0, ratio))
                    slider.blockSignals(True)
                    slider.setValue(int(round(ratio * steps)))
                    slider.blockSignals(False)
                    if value_label is not None:
                        value_label.setText(self._format_slider_value(binding, v))
                    if value_input is not None and cfg.get("behavior") != "tx":
                        value_input.setText(self._format_slider_value(binding, v))
            return

        if wtype == "spinbox":
            if isinstance(ctrl, QDoubleSpinBox):
                ctrl.blockSignals(True)
                ctrl.setValue(v)
                ctrl.blockSignals(False)
            return

        if wtype == "toggle":
            if isinstance(ctrl, QPushButton) and ctrl.isCheckable():
                on_value = float(binding.get("tx_on_value", max_v))
                off_value = float(binding.get("tx_off_value", min_v))
                pivot = (on_value + off_value) / 2.0
                is_on = v >= pivot
                ctrl.blockSignals(True)
                ctrl.setChecked(is_on)
                ctrl.setText("ON" if is_on else "OFF")
                ctrl.blockSignals(False)
            return

        if wtype == "status_lamp":
            on_op = str(binding.get("rx_on_op", "ge"))
            on_a = float(binding.get("rx_on_a", max_v))
            on_b = float(binding.get("rx_on_b", max_v))
            off_op = str(binding.get("rx_off_op", "lt"))
            off_a = float(binding.get("rx_off_a", max_v))
            off_b = float(binding.get("rx_off_b", max_v))

            on_match = self._eval_condition(v, on_op, on_a, on_b)
            off_match = self._eval_condition(v, off_op, off_a, off_b)

            if on_match and not off_match:
                is_on = True
            elif off_match and not on_match:
                is_on = False
            else:
                is_on = v > ((min_v + max_v) / 2.0)

            ctrl.setText("ON" if is_on else "OFF")
            ctrl.setStyleSheet(lamp_style(is_on))
            return

        if wtype == "label":
            ctrl.setText(self._format_rx_label(binding, v, raw_value, received_unit))
            if cfg.get('behavior') == 'rx':
                ctrl._rx_color_sample = (v, raw_value)
                properties = getattr(self, 'properties', None)
                color_binding = properties.color_binding(wid, binding) if properties is not None else binding
                style = label_color_style(color_binding, v, raw_value)
                if ctrl.styleSheet() != style:
                    ctrl.setStyleSheet(style)
            return

    def _extract_raw_value(self, payload, binding):
        try:
            return unpack_raw(payload, binding)
        except (ValueError, TypeError, IndexError):
            return None

    def _rx_widgets(self):
        return [cfg for cfg in self.widgets_config if cfg.get("behavior") == "rx"]

    def _can_display_test(self):
        cfg = self._get_selected_config()
        return self.mode == 'edit' and bool(cfg and cfg.get('behavior') == 'rx')

    def _refresh_display_test_tab(self):
        if not hasattr(self, 'inspector_tabs'):
            return
        enabled = self._can_display_test()
        index = self.inspector_tabs.indexOf(self.display_test_tab)
        if enabled and index < 0:
            self.inspector_tabs.addTab(self.display_test_tab, '표시 테스트')
        elif not enabled:
            self._toggle_auto_sim(False)
            if index >= 0:
                self.inspector_tabs.removeTab(index)
                self.display_test_tab.hide()
        self.inspector_tabs.tabBar().setVisible(enabled)
        for action in (self.act_sim_selected, self.act_sim_auto):
            action.setEnabled(enabled)

    def _simulate_apply_to_cfg(self, cfg, value):
        try:
            v = float(value)
        except Exception:
            return
        self._update_widget_value(cfg, v, force=True)

    def simulate_selected_rx(self):
        if not self._can_display_test():
            return
        cfg = self._get_selected_config()
        if not cfg or cfg.get("behavior") != "rx":
            QMessageBox.information(self, "표시 테스트", "수신 도구를 먼저 선택하세요.")
            return
        self._simulate_apply_to_cfg(cfg, self.spin_sim_value.value())

    def _toggle_auto_sim(self, checked):
        checked = bool(checked and self._can_display_test())
        self.btn_sim_auto.blockSignals(True)
        self.btn_sim_auto.setChecked(checked)
        self.btn_sim_auto.blockSignals(False)
        self.act_sim_auto.blockSignals(True)
        self.act_sim_auto.setChecked(bool(checked))
        self.act_sim_auto.blockSignals(False)

        if checked:
            self._sim_phase = 0.0
            self._sim_timer.start()
            self.btn_sim_auto.setText("자동 표시 테스트: ON")
        else:
            self._sim_timer.stop()
            self.btn_sim_auto.setText("자동 표시 테스트: OFF")

    def _on_sim_timer(self):
        if not self._can_display_test():
            self._toggle_auto_sim(False)
            return
        rx_items = self._rx_widgets()
        if not rx_items:
            return

        self._sim_phase += 0.25
        for idx, cfg in enumerate(rx_items):
            binding = cfg.get("binding", {})
            min_v = float(binding.get("min", 0.0))
            max_v = float(binding.get("max", 100.0))
            if max_v <= min_v:
                max_v = min_v + 1.0

            phase = self._sim_phase + (idx * 0.35)
            ratio = (math.sin(phase) + 1.0) * 0.5
            value = min_v + (max_v - min_v) * ratio
            self._simulate_apply_to_cfg(cfg, value)

    def _panel_data(self):
        return {
            "version": 5,
            "title": self.windowTitle(),
            "dbc_signature": copy.deepcopy(self.dbc_signature),
            "pages": copy.deepcopy(self.pages),
            "active_page_id": self.active_page_id,
            "tx_packets": copy.deepcopy(self.tx_packets),
            "init_steps": copy.deepcopy(self.init_steps),
            "grid": {"rows": self.grid_rows, "cols": self.grid_cols, "cell_size": self.grid_cell_size},
            "mode": self.mode,
            "widgets": self.widgets_config,
            "channel_settings": copy.deepcopy(self.channel_settings),
        }

    def save_panel_to_file(self):
        path, _ = QFileDialog.getSaveFileName(self, "유저 패널 저장", "", _PACKAGE_FILTER)
        if not path:
            return
        self._save_package_file(path)

    def load_panel_from_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "유저 패널 불러오기", "", _PACKAGE_FILTER)
        if not path:
            return
        self._load_package_file(path)

    @staticmethod
    def _validate_panel_data(data):
        """패널 상태를 바꾸기 전에 파일 내용을 먼저 검증합니다(실패 시 기존 상태 유지)."""
        if not isinstance(data, dict):
            raise ValueError("Invalid panel file")
        for field in ('init_steps', 'widgets', 'tx_packets'):
            entries = data.get(field, [])
            if not isinstance(entries, list) or any(not isinstance(entry, dict) for entry in entries):
                raise ValueError(f'Invalid panel {field}')
        validate_steps(copy.deepcopy(data.get('init_steps', [])), allow_empty=True)
        pages = data.get('pages') or [dict(id='default', name='페이지 1')]
        if not isinstance(pages, list) or any(not isinstance(p, dict) or 'id' not in p for p in pages):
            raise ValueError('Invalid panel pages')
        page_ids = [p['id'] for p in pages]
        if (any(not isinstance(pid, str) or not pid for pid in page_ids)
                or len(set(page_ids)) != len(page_ids)
                or any(not isinstance(p.get('name'), str) or not p['name'].strip() for p in pages)):
            raise ValueError('Invalid panel pages')
        for field, id_key in (('widgets', 'id'), ('tx_packets', 'packet_id')):
            ids = [entry[id_key] for entry in data.get(field, []) if id_key in entry]
            if any(not isinstance(ident, str) or not ident for ident in ids) or len(set(ids)) != len(ids):
                raise ValueError(f'Invalid panel {field} IDs')
        try:
            for packet in data.get('tx_packets', []):
                # Counter/CRC signal compatibility depends on the bundled DB;
                # validate the frame itself before replacing any live state.
                frame = copy.deepcopy(packet)
                frame.pop('signal_counters', None)
                validate_packet(frame, {})
            for cfg in data.get('widgets', []):
                if not isinstance(cfg.get('binding', {}), dict):
                    raise ValueError('Invalid panel binding')
                for binding in command_bindings(cfg):
                    if not isinstance(binding, dict):
                        raise ValueError('Invalid panel command binding')
                if cfg.get('widget_type') == 'sequence':
                    binding = cfg.get('binding', {})
                    validate_steps(binding.get('sequence_steps', []), allow_empty=True)
                    validate_steps(binding.get('sequence_failure_steps', []), actions_only=True, allow_empty=True)
        except (KeyError, TypeError, AttributeError, OverflowError) as exc:
            raise ValueError('Invalid panel packet or widget') from exc
        grid = data.get("grid", {})
        if not isinstance(grid, dict) or not isinstance(data.get('channel_settings', {}), dict):
            raise ValueError('Invalid panel settings')
        for key in ('rows', 'cols', 'cell_size'):
            if key in grid:
                int(grid[key])

    def _preflight_panel_data(self, data):
        """실제 UI 구성까지 격리된 창에서 검증해 기존 패널/DB를 보호합니다."""
        self._validate_panel_data(data)
        from src.session_storage import validate_panel
        # Parent references and cycles must be rejected before creating Qt widgets.
        structural = copy.deepcopy(data)
        for cfg in structural.get('widgets', []):
            cfg.setdefault('id', str(uuid.uuid4()))
        validate_panel(structural)
        proxy = SimpleNamespace(buses={}, database_content_signature=data.get('dbc_signature'))
        staged = UserPanelWindow(proxy, {})
        try:
            staged._load_panel_data(copy.deepcopy(data))
        finally:
            staged.close()
            staged.deleteLater()

    def _load_panel_data(self, data):
        self._validate_panel_data(data)
        self.setWindowTitle(str(data.get('title') or 'User Panel'))
        self.dbc_signature = normalized_signature(copy.deepcopy(data.get('dbc_signature', database_signature(self.main_window))))

        self.stop_panel_commands('패널 불러오기')
        self._packet_runtimes.clear()
        self.tx_packets = copy.deepcopy(data.get('tx_packets', []))
        self.init_steps = copy.deepcopy(data.get('init_steps', []))
        validate_steps(self.init_steps, allow_empty=True)
        for packet in self.tx_packets:
            packet.setdefault('packet_id', str(uuid.uuid4()))

        self.grid_rows = max(1, int(data.get("grid", {}).get("rows", 12)))
        self.grid_cols = max(1, int(data.get("grid", {}).get("cols", 12)))
        saved_cell_size = max(1, int(data.get("grid", {}).get("cell_size", 32)))
        self.grid_cell_size = max(16, (saved_cell_size + 1) // 2)
        self.widgets_config = list(data.get("widgets", []))
        self.pages = copy.deepcopy(data.get('pages') or [dict(id='default', name='페이지 1')])
        page_ids = [p['id'] for p in self.pages]
        if len(set(page_ids)) != len(page_ids) or any(not isinstance(p.get('name'), str) or not p['name'].strip() for p in self.pages):
            raise ValueError('Invalid panel pages')
        self.active_page_id = data.get('active_page_id', page_ids[0])
        self._sync_page_tabs()
        for cfg in self.widgets_config:
            if cfg.get('page_id') not in page_ids:
                cfg['page_id'] = page_ids[0]
        self.channel_settings = copy.deepcopy(data.get("channel_settings", {}))
        self.selected_widget_ids.clear()
        for cfg in self.widgets_config:
            self._normalize_config(cfg)
            for binding in command_bindings(cfg):
                if cfg.get('behavior') == 'tx' and cfg.get('widget_type') != 'sequence':
                    packet = find_packet(self.tx_packets, binding)
                    if packet:
                        bind_packet(binding, packet)
                if normalized_signature(self.dbc_signature) == normalized_signature(database_signature(self.main_window)):
                    reconcile_binding(self.db_messages, binding)
        self.selected_widget_id = next((c['id'] for c in self.widgets_config if self._page_for(c) == self.active_page_id), None)

        shape_count = sum(1 for c in self.widgets_config if str(c.get("widget_type", "")).startswith("shape_"))
        self._shape_counter = max(1, shape_count + 1)

        self.mode = 'edit'

        self._reset_history()
        self.rebuild_grid()
        self._expand_grid_to_viewport()
        self.refresh_mode_ui()

    def _save_package_file(self, path):
        if not hasattr(self.main_window, "get_db_file_paths_by_bus"):
            QMessageBox.warning(self, "Not Supported", "Main window does not support DB bundle export.")
            return

        try:
            db_paths = self.main_window.get_db_file_paths_by_bus()
            out_path = save_bundle(path, self._panel_data(), db_paths)
            self.log_system(f'패널 패키지 저장: {out_path}')
            QMessageBox.information(self, "Saved", f"Package saved:\n{out_path}")
        except Exception as e:
            self.log_system(f'패널 패키지 저장 실패: {e}', 'ERROR')
            show_error(self, "Error", f"Package save failed:\n{e}")

    def _load_package_file(self, path):
        if not path.lower().endswith(PACKAGE_EXT):
            QMessageBox.warning(self, '지원하지 않는 파일', '.pjjupkg 패널 패키지를 선택해주세요.')
            return
        if not self._authorize_communication():
            return
        if not hasattr(self.main_window, "replace_db_files_by_bus"):
            QMessageBox.warning(self, "Not Supported", "Main window does not support DB bundle import.")
            return

        try:
            panel_data, db_paths_by_bus = load_bundle(path)
            # DB를 교체하기 전에 패널 내용을 검증해, 손상된 파일로 DB만 바뀌는 상황을 막습니다.
            self._preflight_panel_data(panel_data)
            self.set_mode('standby')
            self.main_window.replace_db_files_by_bus(db_paths_by_bus)
            self._load_panel_data(panel_data)
            self.set_mode('standby')
            self.log_system(f'패널/DBC 패키지 불러오기: {path}')
            QMessageBox.information(self, "Loaded", "Package loaded successfully.")
        except Exception as e:
            self.log_system(f'패널 패키지 불러오기 실패: {e}', 'ERROR')
            show_error(self, "Error", f"Package load failed:\n{e}")
