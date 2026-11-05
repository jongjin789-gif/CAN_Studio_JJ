from src.bus_config import BUS_IDS
from src.error_dialog import show_error, diagnostic_details
import sys, os, re
import platform
import time
from collections import deque
from datetime import datetime
import can, cantools
from src.db_frame_format import load_sym_with_fd, frame_is_extended
from src.can_devices import (DeviceRegistry, DeviceOpenCancelled, DeviceOpenError, OpenSettings,
                             DEFAULT_NOMINAL_INDEX)
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QGroupBox, QHBoxLayout, QLabel, 
                             QComboBox, QPushButton, QListWidget, QListWidgetItem, QShortcut, QLineEdit,
                             QTreeWidget, QSplitter, QMessageBox, QFileDialog, QTreeWidgetItemIterator, QInputDialog)
from PyQt5.QtCore import Qt, QTimer, pyqtSignal, QSize
from PyQt5.QtGui import QFont, QIcon, QKeySequence
from src.utils import get_resource_path, ReceiveTreeWidgetItem
from src.can_threads import CANReceiverThread
from src.graph_realtime import SignalGraphWindow
from src.graph_labels import update_realtime_labels
from src.graph_lifecycle import close_graph_windows
from src.record_window import RecordWindow
from src.log_viewer import LogViewerWindow
from src.tx_panel import TxPanel
from src.user_panel import UserPanelWindow
from src.session_manager import SessionManager
from src.main_menu import install_main_menu
from src.connection_controls import ConnectionGrid, CollapsibleGroup, BusLogDialog, FIELD_KEYS
from src.bus_config import normalized_signature
from src.session_manager import device_identity
from src.button_icons import decorate_button, button_icon
from src.main_theme import apply_main_theme, packet_section


def _signal_display_decimals(signal):
    def decimal_places(value):
        try:
            text = f"{float(value):.12f}".rstrip("0").rstrip(".")
        except (TypeError, ValueError):
            return 0
        return len(text.split(".", 1)[1]) if "." in text else 0

    return max(3, decimal_places(getattr(signal, "scale", 1.0)),
               decimal_places(getattr(signal, "offset", 0.0)))


class UniversalCANMonitor(QMainWindow):
    bus_state_changed = pyqtSignal(int)

    def __init__(self, viewer_only=False, user_panel_security=None, user_panel_only=False, communication_password='1234'):
        super().__init__()
        self.user_panel_only = bool(user_panel_only)
        self.viewer_only = bool(viewer_only and not user_panel_only)
        self.communication_password = communication_password
        self._pending_panel_events = []
        self.user_panel_security = user_panel_security or {"enabled": False, "password": ""}
        
        app_version = self.get_app_version()
        version_str = f" - {app_version}" if app_version else ""
        
        if self.viewer_only:
            self.setWindowTitle(f'Log Viewer{version_str}')
        else:
            self.setWindowTitle(f'PCAN Monitoring{version_str}')
        self.setWindowIcon(QIcon(get_resource_path(os.path.join("icon", "viewer.png"))))
        self.resize(600, 200) if self.viewer_only else self.resize(1280, 800)
        
        # CAN 통신 방식별 장치 모듈(src/can_devices/device_*.py). 연결 그리드보다 먼저 만들어야 합니다.
        self.devices = DeviceRegistry(self)
        self.buses = {b: None for b in BUS_IDS}
        self.bus_capabilities = {b: {'is_fd': False} for b in BUS_IDS}
        self._bus_busy = set()
        self._bus_generation = {b: 0 for b in BUS_IDS}
        self.bus_events = {b: deque(maxlen=500) for b in BUS_IDS}
        self.bus_issues = {}
        self._bus_log_windows = {}
        self.rx_threads = {b: None for b in BUS_IDS}
        self.db_messages = {b: {} for b in BUS_IDS} # {bus_num: {can_id: cantools.message.Message}}
        self.signal_tree_items = {} # {(bus_num, can_id, signal_name): QTreeWidgetItem}
        self.signal_choices = {} # Legacy name-only graph fallback; RX uses its exact message definition.
        self.signal_display_decimals = {}
        self.msg_tree_items = {} # {(bus_num, can_id): QTreeWidgetItem}
        self.active_graphs = [] # [SignalGraphWindow, ...]
        self.synced_graphs_ordered = []
        self.combined_view_window = None
        self.record_window = None
        self.log_viewers = [] # [LogViewerWindow, ...]
        self.user_panel_window = None
        self.user_tx_cache = {}
        self.user_frame_properties = {} # User Panel 프레임 속성(BRS 등) 캐시
        self._session_generation = 0
        self._graph_pending_frames = {}
        self._panel_pending_frames = {}
        
        self.init_ui()
        self.statusBar().messageChanged.connect(lambda message: self.panel_system_event(message) if message else None)
        if not self.viewer_only:
            self.search_can_channels()
        
        # UI 갱신 타이머 (GUI 프리징 방지를 위해 50ms마다 렌더링) - 자동 연결(Open) 전에 미리 생성되어야 함
        self.ui_update_timer = QTimer(self)
        self.ui_update_timer.timeout.connect(self.update_ui_data)
        self.panel_update_timer = QTimer(self)
        self.panel_update_timer.setSingleShot(True)
        self.panel_update_timer.setInterval(50)
        self.panel_update_timer.timeout.connect(self.flush_panel_messages)
        self.bus_status_timer = QTimer(self)
        self.bus_status_timer.setInterval(1000)
        self.bus_status_timer.timeout.connect(self.poll_bus_status)
        if not self.viewer_only:
            self.bus_status_timer.start()
        
        self.session = SessionManager(self)
        install_main_menu(self)
        self.session.start()

    def can_open(self, bus):
        data = self.combo_channels[bus].currentData()
        return (bus not in self._bus_busy and self.buses[bus] is None
                and isinstance(data, dict) and data.get('handle') is not None)

    def bus_summary(self, bus):
        if bus in self._bus_busy:
            return ('연결 / 해제 처리 중', 'INFO')
        if bus in self.bus_issues:
            return self.bus_issues[bus]
        if self.buses[bus] is not None:
            mode = 'FD' if self.bus_capabilities[bus].get('is_fd') else 'Classic'
            return (f'연결됨 · {mode}', 'INFO')
        return ('미연결', 'INFO')

    def poll_bus_status(self):
        """Device warnings may be logged by python-can without a receive exception."""
        for bus in BUS_IDS:
            device = self.buses[bus]
            cfg = self.combo_channels[bus].currentData() or {}
            if device is None or bus in self._bus_busy or not isinstance(cfg, dict):
                continue
            backend = self.devices.for_bustype(cfg.get('bustype'))
            try:
                result = backend.poll_status(device)
            except Exception as exc:
                self.log_bus_event(bus, f'장치 상태 조회 오류: {exc}', 'ERROR')
                continue
            if result is None:
                continue
            state, detail = result
            if state == 'busoff':
                self.log_bus_event(bus, 'Bus-Off · 오류로 연결 해제', 'ERROR', detail)
                self.close_can(bus)
            elif state == 'warn':
                self.log_bus_event(bus, '연결됨 · 경고: CAN 버스 오류', 'WARN', detail)
            elif state == 'ok' and self.bus_issues.get(bus, ('', ''))[0] == '연결됨 · 경고: CAN 버스 오류':
                self.bus_issues.pop(bus, None)
                self.log_bus_event(bus, 'CAN 버스 경고 해소')

    def log_bus_event(self, bus, message, level='INFO', details='', issue=True):
        events = self.bus_events[bus]
        now = time.monotonic()
        if (events and events[-1]['message'] == message and events[-1]['level'] == level
                and now - events[-1]['monotonic'] < 2):
            events[-1]['count'] += 1
            events[-1]['monotonic'] = now
        else:
            events.append(dict(time=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                               monotonic=now, message=str(message), level=level,
                               details=str(details), count=1))
        if issue and level in ('ERROR', 'WARN'):
            if level == 'ERROR' or self.bus_issues.get(bus, ('', ''))[1] != 'ERROR':
                self.bus_issues[bus] = (' '.join(str(message).split()), level)
        self.panel_system_event(f'BUS {bus}: {message}', level)
        self.bus_state_changed.emit(bus)

    def show_bus_log(self, bus, parent=None):
        from PyQt5 import sip
        dialog = self._bus_log_windows.get(bus)
        if dialog is None or sip.isdeleted(dialog):
            dialog = BusLogDialog(self, bus, parent or self)
            self._bus_log_windows[bus] = dialog
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def stop_bus_activity(self, bus):
        if hasattr(self, 'tx_panel'):
            for row in range(self.tx_panel.tree.topLevelItemCount()):
                item = self.tx_panel.tree.topLevelItem(row)
                if item.packet_data['bus'] == bus:
                    item.stop_timer()
        panel = self.user_panel_window
        if panel is not None:
            from src.user_panel_v2.sequence import SequenceControl
            from src.user_panel_v2.properties import ToolProperties
            panel.stop_periodic_bus(bus)
            controls = [panel.init_control, *panel.widget_controls.values()]
            for control in controls:
                if isinstance(control, SequenceControl) and bus in getattr(control, 'required_buses', set()):
                    control.stop(f'BUS {bus} 연결 해제')
                    if control is panel.init_control:
                        panel._init_running = False
            for cfg in panel.widgets_config:
                if bus in ToolProperties.bound_buses(cfg):
                    control = panel.widget_controls.get(cfg['id'])
                    if control:
                        for timer in control.findChildren(QTimer, 'panel_hold_timer'):
                            timer.stop()
                            timer.deleteLater()
        # A disconnected replay BUS can otherwise fall back to another BUS.
        for viewer in self.log_viewers:
            viewer.stop_sending()

    def toggle_can(self, bus):
        if bus in self._bus_busy:
            return
        if self.buses[bus] is None:
            self.open_can(bus)
        else:
            self._bus_busy.add(bus)
            self.bus_state_changed.emit(bus)
            try:
                self.close_can(bus)
            finally:
                self._bus_busy.discard(bus)
                self.bus_state_changed.emit(bus)

    def open_can(self, bus):
        if bus in self._bus_busy or self.buses[bus] is not None:
            return
        desired = device_identity(self.combo_channels[bus].currentData())
        for other in BUS_IDS:
            if (other != bus and self.buses[other] is not None and desired is not None
                    and desired == device_identity(self.combo_channels[other].currentData())):
                self.log_bus_event(bus, f'연결 실패: BUS {other}에서 같은 장치를 사용 중입니다.', 'ERROR')
                return
        self._bus_busy.add(bus)
        self._active_bus_operation = bus
        self.bus_state_changed.emit(bus)
        self.log_bus_event(bus, '연결 시도', details='\n'.join([
            f'장치: {self.combo_channels[bus].currentText()}',
            f'Baudrate: {self.combo_bitrate[bus].currentText()}',
            f'FD ISO: {self.combo_fd_iso[bus].currentText()}',
            f'Data Bitrate: {self.combo_data_bitrate[bus].currentText()}']))
        try:
            self._bus_generation[bus] += 1
            self._open_can(bus)
            if self.buses[bus] is not None:
                self.bus_issues.pop(bus, None)
                self.log_bus_event(bus, '연결 성공')
            elif bus not in self.bus_issues:
                self.log_bus_event(bus, '연결 실패: 장치와 통신 설정을 확인하세요.', 'ERROR')
        finally:
            self._active_bus_operation = None
            self._bus_busy.discard(bus)
            self.bus_state_changed.emit(bus)
            if hasattr(self, 'session'):
                self.session.autosave()

    def panel_system_event(self, message, level='INFO'):
        panel = self.user_panel_window
        if panel is not None:
            panel.log_system(message, level)
        else:
            self._pending_panel_events.append((message, level))
            self._pending_panel_events = self._pending_panel_events[-200:]

    def notify_connection_error(self, title, message):
        bus = getattr(self, '_active_bus_operation', None)
        if bus is not None:
            self.log_bus_event(bus, message, 'ERROR', diagnostic_details(title))
        self.panel_system_event(message, 'ERROR')
        session = getattr(self, 'session', None)
        if session is not None and session.errors is not None:
            session.errors.append(message)
        else:
            show_error(self.user_panel_window or self, title, message,
                       icon=QMessageBox.Warning)
        
    def get_app_version(self):
        """build_exe.py 파일 또는 실행 파일명에서 APP_VERSION을 추출하여 타이틀에 표시합니다."""
        if getattr(sys, 'frozen', False):
            try:
                exe_name = os.path.basename(sys.executable)
                match = re.search(r'Viewer_JJ_(.+?)_(?:win|linux|mac)', exe_name, re.IGNORECASE)
                if match:
                    return match.group(1)
            except Exception:
                pass
        else:
            try:
                base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                build_script = os.path.join(base_dir, 'build_exe.py')
                if os.path.exists(build_script):
                    with open(build_script, 'r', encoding='utf-8') as f:
                        for line in f:
                            if line.startswith('APP_VERSION') and '=' in line:
                                return line.split('=')[1].strip().strip('"\' ')
            except Exception:
                pass
        return ""

    def init_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        if self.viewer_only:
            main_layout.setContentsMargins(5, 5, 5, 5) # 뷰어 모드일 때 불필요한 테두리 여백 축소
        
        # ---------------------------------------------------------
        # 1. 상단 컨트롤 패널 (PCAN 채널 선택 및 연결)
        # ---------------------------------------------------------
        conn_group = self.conn_group = CollapsibleGroup('CAN Settings',
            icon=QIcon(get_resource_path('icon/panel/connection.svg')))
        conn_group.collapsed_changed.connect(lambda collapsed: self.session.autosave() if hasattr(self, 'session') else None)
        conn_layout = QVBoxLayout(conn_group.content)
        conn_layout.setContentsMargins(0, 0, 0, 0)
        conn_layout.setSpacing(0)
        self.connection_grid = ConnectionGrid(self, conn_group.content)
        for key in FIELD_KEYS:
            setattr(self, 'combo_' + key, {b: self.connection_grid.fields[b, key] for b in BUS_IDS})
        self.btn_refresh = {b: self.connection_grid.buttons[b, 'refresh'] for b in BUS_IDS}
        self.btn_connect = {b: self.connection_grid.buttons[b, 'connect'] for b in BUS_IDS}
        for bus in BUS_IDS:
            for backend in self.devices.selectable():
                self.combo_backend[bus].addItem(backend.label, backend.key)
            self.combo_backend[bus].setToolTip('CAN 통신 방식')
            self.combo_backend[bus].currentIndexChanged.connect(lambda idx, b=bus: self.on_backend_changed(b))
            self.combo_fd_iso[bus].addItems(['ISO', 'Non-ISO'])
            self.combo_channels[bus].currentIndexChanged.connect(lambda idx, b=bus: self.on_channel_changed(b))
            for key in FIELD_KEYS:
                getattr(self, 'combo_' + key)[bus].currentIndexChanged.connect(lambda idx, b=bus: self.bus_state_changed.emit(b))
            self.on_channel_changed(bus)
        conn_layout.addWidget(self.connection_grid)
        main_layout.addWidget(conn_group)
        conn_group.setVisible(not self.viewer_only)

        # ---------------------------------------------------------
        # 2. 데이터베이스 파일 로드 패널
        # ---------------------------------------------------------
        db_group = self.db_group = CollapsibleGroup("Database Files (.dbc, .sym)", icon=button_icon('database_load'))
        db_group.collapsed_changed.connect(lambda collapsed: self.session.autosave() if hasattr(self, "session") else None)
        db_layout = QVBoxLayout(db_group.content)
        db_layout.setContentsMargins(0, 0, 0, 0)
        db_layout.setSpacing(3)
        
        db_cols_layout = QHBoxLayout()
        self.btn_load_db = {}
        self.list_db_files = {}
        self.shortcut_delete_db = {}
        
        for i in BUS_IDS:
            col_layout = QVBoxLayout()
            self.btn_load_db[i] = QPushButton(f"Load DBC/SYM (Bus {i})")
            decorate_button(self.btn_load_db[i], 'database_load', f'BUS {i}의 DBC/SYM 파일을 등록합니다.')
            self.btn_load_db[i].clicked.connect(lambda checked=False, b=i: self.load_database_file(b))
            col_layout.addWidget(self.btn_load_db[i])
            
            self.list_db_files[i] = QListWidget()
            if not self.viewer_only:
                self.list_db_files[i].setMaximumHeight(50)
            self.list_db_files[i].itemDoubleClicked.connect(lambda item, b=i: self.remove_database_file(item, b))
            col_layout.addWidget(self.list_db_files[i])
            
            # Delete 키 입력 시 삭제 이벤트 연결
            self.shortcut_delete_db[i] = QShortcut(QKeySequence(Qt.Key_Delete), self.list_db_files[i])
            self.shortcut_delete_db[i].setContext(Qt.WidgetWithChildrenShortcut)
            self.shortcut_delete_db[i].activated.connect(lambda b=i: self.remove_selected_db_file(b))
            
            db_cols_layout.addLayout(col_layout)
            
        db_layout.addLayout(db_cols_layout)
        
        self.btn_clear_all_db = QPushButton("Clear All DB Files")
        decorate_button(self.btn_clear_all_db, 'trash', '모든 BUS의 DBC/SYM 등록을 해제합니다.', 'DBC Clear')
        self.btn_clear_all_db.clicked.connect(self.clear_all_database_files)
        db_group.header_row.addWidget(self.btn_clear_all_db)
        
        self.btn_open_log = QPushButton("Open CAN Log Viewer")
        decorate_button(self.btn_open_log, 'document_open', 'CAN 로그 파일을 열어 확인합니다.', 'Log View')
        self.btn_open_log.clicked.connect(self.open_log_viewer)
        self.btn_open_log.setEnabled(False)
        db_group.header_row.addWidget(self.btn_open_log)
        
        main_layout.addWidget(db_group)
        
        # ---------------------------------------------------------
        # 3. 트리 뷰 (모니터링 데이터 표시)
        # ---------------------------------------------------------
        tree_group = QWidget()
        tree_layout = QVBoxLayout(tree_group)
        tree_layout.setContentsMargins(0, 0, 0, 0)
        
        btn_layout = QHBoxLayout()
        self.btn_view_graph = QPushButton("Graph View")
        decorate_button(self.btn_view_graph, 'graph', '선택한 신호의 실시간 그래프를 엽니다.')
        self.btn_view_graph.clicked.connect(self.open_combined_graph)
        btn_layout.addWidget(self.btn_view_graph)
        
        self.btn_uncheck_all = QPushButton("Uncheck All")
        decorate_button(self.btn_uncheck_all, 'uncheck', '수신 신호의 체크를 모두 해제합니다.')
        self.btn_uncheck_all.clicked.connect(self.uncheck_all_signals)
        btn_layout.addWidget(self.btn_uncheck_all)
        
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search (Message/Signal Name or ID)")
        self.search_input.textChanged.connect(self.filter_tree_items)
        btn_layout.addWidget(self.search_input)
        
        btn_layout.addStretch()

        self.btn_close_graphs = QPushButton("Close Graphs")
        decorate_button(self.btn_close_graphs, 'graph_close', '이 메인창에서 연 모든 실시간 그래프를 닫습니다.')
        self.btn_close_graphs.clicked.connect(self.close_graphs)
        btn_layout.addWidget(self.btn_close_graphs)
        
        self.btn_clear_data = QPushButton("Clear Data")
        decorate_button(self.btn_clear_data, 'view_clear', '수신 패킷 화면과 통계를 비웁니다.')
        self.btn_clear_data.clicked.connect(self.clear_monitoring_data)
        btn_layout.addWidget(self.btn_clear_data)
        
        self.btn_record = QPushButton("Record")
        decorate_button(self.btn_record, 'record', 'CAN 로그 기록창을 엽니다.')
        self.btn_record.clicked.connect(self.open_record_window)
        self.btn_record.setEnabled(False)
        btn_layout.addWidget(self.btn_record)
        
        tree_layout.addLayout(btn_layout)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(['Bus', 'Type', 'CAN ID', 'Message / Signal', 'Dir.', 'Value', 'Unit', 'Cycle (ms)', 'Count'])
        self.tree.setColumnWidth(0, 40)  # Bus
        self.tree.setColumnWidth(1, 50)  # Type
        self.tree.setColumnWidth(2, 90)  # CAN ID
        self.tree.setColumnWidth(3, 270) # Message / Signal
        self.tree.setColumnWidth(4, 60)  # Direction
        self.tree.setColumnWidth(5, 200) # Value
        self.tree.setColumnWidth(6, 80)  # Unit
        self.tree.setColumnWidth(7, 100) # Cycle
        self.tree.setColumnWidth(8, 80)  # Count
        self.tree.setSortingEnabled(True)
        self.tree.sortByColumn(0, Qt.AscendingOrder) # Bus, then numeric CAN ID within each bus.
        self.tree.itemChanged.connect(self.on_tree_item_changed)
        tree_layout.addWidget(self.tree)
        
        # ---------------------------------------------------------
        # 4. 송신부 (Tx 패널) 적용 및 QSplitter 구성
        # ---------------------------------------------------------
        self.tx_panel = TxPanel(self.buses, self.db_messages, self)
        self.btn_user_panel = QPushButton("User Panel")
        decorate_button(self.btn_user_panel, 'panel', '사용자 패널을 엽니다.', 'Panel')
        self.btn_user_panel.clicked.connect(self.open_user_panel)
        self.tx_panel.toolbar.addWidget(self.btn_user_panel)
        
        split_view = self.packet_splitter = QSplitter(Qt.Vertical)
        split_view.addWidget(packet_section(tree_group, 'RX', self.tree))
        split_view.addWidget(packet_section(self.tx_panel, 'TX', self.tx_panel.tree))
        split_view.setSizes([400, 200]) # 상단 수신부와 하단 송신부의 초기 비율
        # Keep the user's TX height; RX absorbs changes above the splitter.
        split_view.setStretchFactor(0, 1)
        split_view.setStretchFactor(1, 0)
        main_layout.addWidget(split_view, 1)  # stretch 인자를 1로 주어 가능한 많은 공간 할당
        split_view.setVisible(not self.viewer_only)

        # 가독성 향상을 위해 행마다 배경색을 교차로 표시하고, 구분선을 추가합니다.
        self.tree.setAlternatingRowColors(True)
        self.tree.setStyleSheet(
            "QTreeView::item { border-bottom: 1px solid #E0E0E0; }"
            "QTreeView::item:selected { background-color: #0078D7; color: white; }"
        )
        
        # 상태바 초기화
        self.statusBar().showMessage("Ready")
        apply_main_theme(self)
        
    def on_tree_item_changed(self, item, column):
        """트리 아이템의 체크 상태가 변경될 때 호출됩니다."""
        # 'Message / Signal' 컬럼(3)의 체크박스 변경에만 반응합니다.
        if column != 3:
            return

        # --- 재귀 호출 방지를 위해 시그널 처리 중단 ---
        self.tree.blockSignals(True)

        try:
            check_state = item.checkState(3)
            
            # 1. 부모 아이템(메시지)이 변경된 경우 -> 모든 자식(시그널)의 상태를 동기화
            if item.childCount() > 0:
                if check_state != Qt.PartiallyChecked:
                    for i in range(item.childCount()):
                        child = item.child(i)
                        child.setCheckState(3, check_state)
            
            # 2. 자식 아이템(시그널)이 변경된 경우 -> 부모의 상태를 갱신
            else:
                parent = item.parent()
                if parent:
                    checked_count = sum(1 for i in range(parent.childCount()) if parent.child(i).checkState(3) == Qt.Checked)
                    
                    if checked_count == 0:
                        parent.setCheckState(3, Qt.Unchecked)
                    elif checked_count == parent.childCount():
                        parent.setCheckState(3, Qt.Checked)
                    else:
                        parent.setCheckState(3, Qt.PartiallyChecked)
        finally:
            self.tree.blockSignals(False)

    def selected_backend(self, bus_num):
        """CAN 방식 콤보에서 고른 장치 모듈 (없으면 OS 기본값)."""
        combo = getattr(self, 'combo_backend', {}).get(bus_num)
        key = combo.currentData() if combo is not None else None
        return self.devices.get(key) or self.devices.get(self.devices.default_key())

    def channel_backend(self, bus_num):
        """선택된 채널을 실제로 여는 장치 모듈 (채널 데이터의 bustype 기준)."""
        data = self.combo_channels[bus_num].currentData()
        if isinstance(data, dict) and data.get('bustype'):
            return self.devices.for_bustype(data['bustype'])
        return self.selected_backend(bus_num)

    def on_backend_changed(self, bus_num):
        if self.buses[bus_num] is not None or bus_num in self._bus_busy:
            return
        if not getattr(self, 'viewer_only', False):
            self.search_can_channels(bus_num)

    def on_channel_changed(self, bus_num):
        """선택된 채널(장치 모듈)에 맞게 통신 속도(Bitrate) 목록을 갱신합니다."""
        self.combo_bitrate[bus_num].clear()
        self.combo_data_bitrate[bus_num].clear()
        channel_data = self.combo_channels[bus_num].currentData()
        backend = self.channel_backend(bus_num)

        for text, rate in backend.nominal_bitrates(channel_data):
            self.combo_bitrate[bus_num].addItem(text, rate)
        self.combo_bitrate[bus_num].setCurrentIndex(DEFAULT_NOMINAL_INDEX) # Default 500 kBit/s

        data_rates = backend.data_bitrates(channel_data)
        if data_rates:
            self.combo_fd_iso[bus_num].setEnabled(True)
            self.combo_data_bitrate[bus_num].setEnabled(True)
            for text, data_rate in data_rates:
                self.combo_data_bitrate[bus_num].addItem(text, data_rate)
            self.combo_data_bitrate[bus_num].setCurrentIndex(0) # Default Off (N/A)
        else:
            self.combo_fd_iso[bus_num].setEnabled(False)
            self.combo_data_bitrate[bus_num].setEnabled(False)
            self.combo_data_bitrate[bus_num].addItem("N/A", None)

    def filter_tree_items(self, text):
        """검색어에 따라 트리 뷰의 메시지/시그널을 필터링합니다."""
        search_text = text.lower()
        for i in range(self.tree.topLevelItemCount()):
            msg_item = self.tree.topLevelItem(i)
            # 메시지 이름 또는 CAN ID (HEX) 매칭 확인
            msg_match = search_text in msg_item.text(3).lower() or search_text in msg_item.text(2).lower()
            
            any_child_match = False
            for j in range(msg_item.childCount()):
                sig_item = msg_item.child(j)
                sig_match = search_text in sig_item.text(3).lower()
                
                if msg_match or search_text == "":
                    sig_item.setHidden(False)
                    any_child_match = True
                elif sig_match:
                    sig_item.setHidden(False)
                    any_child_match = True
                else:
                    sig_item.setHidden(True)
                    
            if msg_match or any_child_match or search_text == "":
                msg_item.setHidden(False)
                if search_text != "" and (msg_match or any_child_match):
                    msg_item.setExpanded(True)
            else:
                msg_item.setHidden(True)

    def open_combined_graph(self):
        """선택된 여러 시그널을 하나의 그래프 창에 띄웁니다"""
        selected_signals = []
        bindings = {}

        # 기존: self.signal_tree_items 딕셔너리 순회 -> 데이터 수신 순서로 범례 생성
        # 변경: 화면에 보이는 Tree 위젯을 직접 순회 -> 화면에 정렬된 순서대로 범례 생성
        iterator = QTreeWidgetItemIterator(self.tree)
        while iterator.value():
            item = iterator.value()
            # 자식 아이템(시그널)이고, 체크된 경우에만 리스트에 추가
            if item.parent() is not None and item.checkState(3) == Qt.Checked:
                parent = item.parent()
                if parent:
                    try:
                        bus_num_str = parent.text(0)
                        sig_name = item.text(3).strip()
                        bus_num = int(bus_num_str)
                        
                        can_id = int(parent.text(2), 16)
                        full_sig_name, binding = self.session.bind_signal(bus_num, can_id, sig_name)
                        selected_signals.append(full_sig_name)
                        bindings[full_sig_name] = binding
                    except (ValueError, IndexError):
                        continue # 데이터 파싱 오류 시 해당 시그널은 무시
            iterator += 1

        if not selected_signals:
            QMessageBox.warning(self, "Warning", "그래프를 확인할 시그널을 트리 체크박스에서 1개 이상 선택해주세요.")
            return
            
        graph = self._show_realtime_graph(selected_signals, bindings)
        self.uncheck_all_signals()
        return graph

    def open_realtime_graph(self, entries):
        """Open panel-selected signals through the main graph routing/session path."""
        bindings = {}
        for entry in entries:
            key, binding = self.session.bind_signal(entry['bus'], entry['can_id'], entry['signal_name'])
            bindings[key] = binding
        if not bindings:
            raise ValueError('그래프로 표시할 신호를 선택하세요.')
        return self._show_realtime_graph(list(bindings), bindings)

    def _show_realtime_graph(self, selected_signals, bindings):
        bound_signals = self.resolve_graph_bindings(bindings)
        graph = SignalGraphWindow(selected_signals, main_window=self)
        graph.signal_bindings = bindings
        graph._bound_signals = bound_signals
        update_realtime_labels(graph)
        
        # 이미 열려있는 그래프 창이 있다면, 가장 최근 창의 크기를 가져와서 똑같이 맞춰줌
        visible_graphs = [g for g in self.active_graphs if g.isVisible()]
        if visible_graphs:
            graph.resize(visible_graphs[-1].size())
            
        self.active_graphs.append(graph)
        graph.show()
        return graph
        
    def close_graphs(self):
        close_graph_windows(self)

    def handle_sync_toggle(self, graph_window, is_checked):
        """그래프 창의 Sync 체크 상태 변경을 처리하여 순서가 있는 리스트를 관리합니다."""
        if is_checked:
            if graph_window not in self.synced_graphs_ordered:
                self.synced_graphs_ordered.append(graph_window)
        else:
            if graph_window in self.synced_graphs_ordered:
                self.synced_graphs_ordered.remove(graph_window)

    def open_combined_view(self):
        """동기화된 그래프들을 하나의 창에 모아보는 통합 뷰를 엽니다."""
        from src.combined_graph_view import CombinedGraphView
        if not self.synced_graphs_ordered:
            QMessageBox.information(self, "정보", "통합 보기 기능을 사용하려면 먼저 동기화할 그래프를 1개 이상 선택해주세요.")
            return

        try:
            if self.combined_view_window and self.combined_view_window.isVisible():
                self.combined_view_window.activateWindow()
                return
        except RuntimeError:
            self.combined_view_window = None

        self.combined_view_window = CombinedGraphView(self.synced_graphs_ordered, self)
        self.combined_view_window.show()
        
    def uncheck_all_signals(self):
        """트리에서 체크된 모든 시그널의 체크를 해제합니다."""
        # Walk the tree: different CAN IDs can contain the same signal name.
        blocked = self.tree.blockSignals(True)
        try:
            for index in range(self.tree.topLevelItemCount()):
                message = self.tree.topLevelItem(index)
                message.setCheckState(3, Qt.Unchecked)
                for row in range(message.childCount()):
                    message.child(row).setCheckState(3, Qt.Unchecked)
        finally:
            self.tree.blockSignals(blocked)
            
    def clear_monitoring_data(self):
        """실시간 모니터링 중인 트리 데이터와 수집된 통계를 초기화합니다."""
        for bus_num in self.buses:
            self.reset_tree_values(bus_num)
            
    def open_record_window(self):
        """녹화 창 열기 및 뷰어 띄우기"""
        if not any(self.buses.values()):
            QMessageBox.warning(self, "Warning", "At least one CAN channel must be connected.")
            return
            
        # C++ 객체가 닫혀서 삭제된 경우 RuntimeError가 발생하므로 이를 처리하여 새 창을 생성하도록 함
        try:
            if self.record_window is not None and self.record_window.isVisible():
                self.record_window.raise_()
                self.record_window.activateWindow()
                return
        except RuntimeError:
            self.record_window = None

        # 메인 창과 별개의 독립된 창으로 띄우기 위해 parent를 None으로 설정 (closeEvent에서 자동 종료 처리됨)
        bitrates = {b: self.combo_bitrate[b].currentText() for b in BUS_IDS if self.buses[b]}
        try:
            self.record_window = RecordWindow(None, bitrates=bitrates)
        except OSError as exc:
            # 임시 기록 파일을 만들 수 없으면(디스크/권한) 기록을 시작하지 않습니다.
            self.record_window = None
            show_error(self, "Record 시작 실패", f"임시 기록 파일을 만들 수 없습니다:\n{exc}")
            return
        self.record_window.show()
            
    def route_raw_msg_to_record(self, ts, can_id, data, is_ext, is_err, is_fd, is_rx, bus_num, is_brs=False):
        if not is_err:
            self._graph_pending_frames[(bus_num, can_id, is_ext)] = (ts, bytes(data))
            self.queue_panel_message(bus_num, can_id, data, ts)
        panel = getattr(self, "user_panel_window", None)
        if is_rx and not is_err and panel is not None and panel.isVisible():
            panel.on_sequence_receive(ts, bus_num, can_id, data, is_ext, is_fd, is_brs)
        try:
            if getattr(self, 'record_window', None) and self.record_window.isVisible():
                self.record_window.add_log_entry(ts, can_id, data, is_ext, is_err, is_fd, is_rx, bus_num,
                                                 preserve_timestamp=not is_rx)
        except RuntimeError:
            self.record_window = None

    def queue_panel_message(self, bus_num, can_id, data, timestamp):
        """Display valid RX and locally sent TX frames without requiring a driver echo."""
        panel = self.user_panel_window
        try:
            if panel is None or not panel.isVisible():
                return
        except RuntimeError:
            self.user_panel_window = None
            return
        key = (bus_num, can_id)
        pending = self._panel_pending_frames.get(key)
        if pending is None or timestamp >= pending[0]:
            self._panel_pending_frames[key] = (timestamp, bytes(data))
        if not self.panel_update_timer.isActive():
            self.panel_update_timer.start()

    def flush_panel_messages(self):
        self.panel_update_timer.stop()
        pending, self._panel_pending_frames = self._panel_pending_frames, {}
        panel = self.user_panel_window
        try:
            if panel is not None and panel.isVisible():
                for (bus_num, can_id), (timestamp, data) in pending.items():
                    panel.on_message_update(bus_num, can_id, data, timestamp)
        except RuntimeError:
            self.user_panel_window = None

    def record_tx_activity(self, bus_num, can_id, data, is_fd, timestamp=None, is_ext=None):
        """Rx 스레드를 거치지 않는 수동 Tx(로그 재생/Tx 패널)도 메인 트리에 즉시 반영합니다."""
        now = time.time() if timestamp is None else timestamp
        payload = bytes(data)
        is_ext = frame_is_extended(can_id, is_ext,
                                   getattr(self, 'db_messages', {}).get(bus_num, {}).get(can_id))
        rx_thread = self.rx_threads.get(bus_num)
        if not rx_thread:
            self.route_raw_msg_to_record(now, can_id, payload, is_ext,
                                         False, bool(is_fd), False, bus_num)
            return

        stats = rx_thread.latest_msg_stats.get(
            can_id,
            {"count": 0, "cycle": 0.0, "last_time": None, "data": b"", "is_fd": False, "direction": 'Tx'}
        )
        stats["count"] += 1
        if stats['last_time'] is not None and now < stats['last_time']:
            # A periodic record may arrive after a newer immediate CMD record.
            # Keep latest-value/cycle displays monotonic, but retain the older TX in the trace.
            rx_thread.latest_msg_stats[can_id] = stats
            recorder = getattr(self, 'record_window', None)
            if recorder is not None and recorder.isVisible():
                recorder.add_log_entry(now, can_id, payload, is_ext, False,
                                       bool(is_fd), False, bus_num, preserve_timestamp=True)
            return
        if stats["last_time"] is not None:
            stats["cycle"] = (now - stats["last_time"]) * 1000.0
        stats["last_time"] = now
        stats["data"] = payload
        stats["is_fd"] = bool(is_fd)
        stats["direction"] = 'Tx'
        rx_thread.latest_msg_stats[can_id] = stats

        # 상위 메시지 행뿐 아니라 하위 시그널 값도 갱신되도록 Tx 데이터도 즉시 디코딩합니다.
        db_msg = self.db_messages.get(bus_num, {}).get(can_id)
        if db_msg:
            try:
                decoded_data = db_msg.decode(payload, decode_choices=False)
                for sig_name, sig_val in decoded_data.items():
                    signal_def = db_msg.get_signal_by_name(sig_name)
                    unit = signal_def.unit if signal_def and signal_def.unit else ""
                    rx_thread.latest_data[(can_id, sig_name)] = (sig_val, unit, now)
            except Exception:
                pass

        self.route_raw_msg_to_record(
            now,
            can_id,
            payload,
            is_ext,
            False,
            bool(is_fd),
            False,
            bus_num
        )

    def _pack_signal_to_payload(self, payload, binding, phys_value):
        """Encode saved Intel or Motorola bit fields without requiring a DBC."""
        from .user_panel_v2.binding import pack_value
        return pack_value(payload, binding, phys_value)

    def send_user_panel_value(self, binding, phys_value):
        """User Panel에서 전달된 값을 CAN 프레임으로 인코딩하여 단발 전송합니다."""
        bus_num, can_id, dlc, _key = self.stage_user_panel_value(binding, phys_value)
        self.flush_user_panel_frame(bus_num, can_id, dlc)

    def stage_user_panel_value(self, binding, phys_value):
        """User Panel 값을 내부 CAN 프레임 캐시에만 반영하고 즉시 송신하지 않습니다."""
        bus_num = int(binding.get("bus", 1))
        can_id = int(binding.get("can_id", 0))
        dlc = int(binding.get("dlc", 8))
        dlc = max(1, min(64, dlc))

        key = (bus_num, can_id, dlc)
        payload = bytearray(self.user_tx_cache.get(key, bytes([0] * dlc)))
        if len(payload) != dlc:
            payload = bytearray([0] * dlc)

        # BRS와 같은 프레임 레벨 속성을 캐시에 저장합니다.
        # 여러 도구가 동일 프레임에 다른 BRS 설정을 가질 경우, 마지막에 업데이트한 도구의 설정이 적용됩니다.
        is_brs = bool(binding.get("brs", False))
        is_fd = bool(binding.get("is_fd", dlc > 8 or is_brs))
        if not is_fd and (dlc > 8 or is_brs):
            raise ValueError("Classic CAN requires at most 8 bytes and BRS disabled.")
        payload = self._pack_signal_to_payload(payload, binding, phys_value)
        self.user_tx_cache[key] = bytes(payload)
        if key not in self.user_frame_properties:
            self.user_frame_properties[key] = {}
        self.user_frame_properties[key]['brs'] = is_brs
        self.user_frame_properties[key]['is_fd'] = is_fd

        return bus_num, can_id, dlc, key

    def flush_user_panel_frame(self, bus_num, can_id, dlc):
        """내부 캐시에 저장된 User Panel 프레임을 송신합니다."""
        dlc = max(1, min(64, int(dlc)))
        key = (int(bus_num), int(can_id), dlc)
        payload = bytes(self.user_tx_cache.get(key, bytes([0] * dlc)))

        bus_obj = self.buses.get(int(bus_num))
        if bus_obj is None:
            raise RuntimeError(f"Bus {bus_num} is not connected.")

        # 프레임 속성 캐시에서 BRS 설정값을 가져옵니다.
        frame_props = self.user_frame_properties.get(key, {})
        is_brs = frame_props.get('brs', False)

        is_fd = bool(frame_props.get('is_fd', dlc > 8 or is_brs))
        if is_fd and not self.bus_capabilities[int(bus_num)].get('is_fd', False):
            raise RuntimeError(f"Bus {bus_num} is not connected in CAN FD mode.")

        msg = can.Message(
            arbitration_id=int(can_id),
            data=payload,
            is_extended_id=frame_is_extended(
                int(can_id), frame_props.get('is_extended_id'),
                getattr(self, 'db_messages', {}).get(int(bus_num), {}).get(int(can_id))),
            is_fd=is_fd,
            bitrate_switch=is_brs
        )
        bus_obj.send(msg)
        self.record_tx_activity(int(bus_num), int(can_id), payload, is_fd, is_ext=msg.is_extended_id)

    def choose_panel_history(self):
        box = QMessageBox(self.user_panel_window if self.user_panel_window.isVisible() else self)
        box.setWindowTitle('유저 패널 DBC 확인')
        box.setIcon(QMessageBox.Question)
        box.setText('이전 패널의 DBC/SYM 내용 또는 BUS 배치가 현재 메인창과 다릅니다.')
        box.setInformativeText('이전 패널을 현재 DBC로 사용할까요? 새 패널을 선택하면 이전 패널은 파일로 백업합니다.')
        reuse = box.addButton('이전 패널 사용', QMessageBox.AcceptRole)
        fresh = box.addButton('새 패널', QMessageBox.DestructiveRole)
        cancel = box.addButton(QMessageBox.Cancel)
        box.setDefaultButton(cancel)
        box.exec_()
        return 'reuse' if box.clickedButton() is reuse else 'new' if box.clickedButton() is fresh else 'cancel'

    def archive_user_panel(self):
        import json
        from .session_storage import atomic_write
        path = self.session.directory / 'panel_history' / f'panel_{time.time_ns()}.upp.json'
        atomic_write(path, json.dumps(self.user_panel_window._panel_data(), ensure_ascii=False, indent=2).encode('utf-8'))
        return path

    def open_user_panel(self):
        self._panel_open_cancelled = False
        if self.viewer_only:
            QMessageBox.information(self, "Info", "User panel is disabled in viewer-only mode.")
            return

        try:
            if self.user_panel_window is not None:
                from .user_panel_v2.dbc_identity import database_signature
                current = database_signature(self)
                panel = self.user_panel_window
                if normalized_signature(panel.dbc_signature) != normalized_signature(current):
                    choice = self.choose_panel_history()
                    if choice == 'cancel':
                        self._panel_open_cancelled = True
                        return False
                    panel.set_mode('standby')
                    if choice == 'new':
                        try:
                            backup = self.archive_user_panel()
                        except OSError as exc:
                            QMessageBox.warning(panel, '패널 백업 실패', str(exc))
                            self._panel_open_cancelled = True
                            return False
                        panel._load_panel_data({'dbc_signature': current})
                        panel.set_mode('standby')
                        panel.log_system(f'새 패널 시작 / 이전 패널 백업: {backup}')
                    else:
                        panel.dbc_signature = current
                        panel.log_system('DBC 변경 확인: 이전 패널을 현재 DBC/SYM으로 사용합니다.', 'WARN')
                self.user_panel_window.refresh_dbc_bindings()
                if not self.user_panel_window.isVisible():
                    self.user_panel_window.set_mode('standby')
                self.user_panel_window.show()
                self.user_panel_window.raise_()
                self.user_panel_window.activateWindow()
                self.session.autosave()
                return True
        except RuntimeError:
            self.user_panel_window = None

        self.user_panel_window = UserPanelWindow(
            self,
            self.db_messages,
            None,
            security_config=self.user_panel_security,
        )
        self.user_panel_window.set_mode('standby')
        for message, level in self._pending_panel_events:
            self.user_panel_window.log_system(message, level)
        self._pending_panel_events.clear()
        self.user_panel_window.show()
        return True

    def get_db_file_paths_by_bus(self):
        result = {b: [] for b in BUS_IDS}
        for bus_num in BUS_IDS:
            lw = self.list_db_files.get(bus_num)
            if not lw:
                continue
            for i in range(lw.count()):
                item = lw.item(i)
                raw = item.data(Qt.UserRole + 2)
                if raw is not None:
                    # Legacy UserPanel packages require paths: materialize the captured
                    # contents, not a possibly changed/deleted source file.
                    from src.session_storage import atomic_write
                    import hashlib
                    ident = hashlib.sha256(bytes(raw)).hexdigest()
                    p = self.session.directory / 'db_cache' / ident / item.text()
                    if not p.exists():
                        atomic_write(p, bytes(raw))
                    result[bus_num].append(str(p))
        return result

    def replace_db_files_by_bus(self, db_paths_by_bus):
        # 열린 그래프는 DB 기준이 바뀌면 무효가 되므로 먼저 닫습니다.
        for g in list(self.active_graphs):
            try:
                g.close()
            except Exception:
                pass
        self.active_graphs = []

        for bus_num in BUS_IDS:
            self.list_db_files[bus_num].clear()
            self.db_messages[bus_num].clear()
            self.reset_tree_values(bus_num)

        for bus_num in BUS_IDS:
            for p in db_paths_by_bus.get(bus_num, []):
                if os.path.exists(p):
                    self.load_db_from_path(p, bus_num, auto_save=False)

        self.btn_open_log.setEnabled(any(self.db_messages.values()))

        if hasattr(self, 'tx_panel') and not self.viewer_only:
            for bus_num in BUS_IDS:
                self.tx_panel.refresh_db_symbols(bus_num)
            self.tx_panel.auto_save_packets()

    def search_can_channels(self, bus_num=None):
        """OS에 따라 CAN 채널 탐색 로직을 분기합니다."""
        targets = [b for b in ([bus_num] if bus_num else BUS_IDS)
                   if self.buses[b] is None and b not in self._bus_busy]
        if not targets:
            return
        previous = {b: (device_identity(self.combo_channels[b].currentData()),
                       self.combo_bitrate[b].currentText(), self.combo_fd_iso[b].currentText(),
                       self.combo_data_bitrate[b].currentText()) for b in targets}
        if platform.system() == 'Linux':
            self.search_linux_socketcan_channels(bus_num)
        else:
            self.search_windows_can_channels(bus_num)
        for bus in targets:
            desired, bitrate, iso, data_bitrate = previous[bus]
            combo = self.combo_channels[bus]
            index = next((i for i in range(combo.count())
                          if desired is not None and device_identity(combo.itemData(i)) == desired), -1)
            if desired is None:
                index = next((i for i in range(combo.count())
                              if device_identity(combo.itemData(i)) is not None), -1)
            if desired is None:
                index = next((i for i in range(combo.count())
                              if device_identity(combo.itemData(i)) is not None), -1)
            if combo.isEnabled():
                combo.setCurrentIndex(index)
            if index >= 0:
                for field, text in ((self.combo_bitrate[bus], bitrate), (self.combo_fd_iso[bus], iso),
                                    (self.combo_data_bitrate[bus], data_bitrate)):
                    match = field.findText(text)
                    if match >= 0:
                        field.setCurrentIndex(match)
            self.log_bus_event(bus, '장치 검색 완료')

    def search_windows_can_channels(self, bus_num=None):
        """Windows: BUS별로 선택된 CAN 방식(PCAN 등)의 채널을 검색합니다."""
        self._populate_channels(bus_num)

    def search_linux_socketcan_channels(self, bus_num=None):
        """Linux: SocketCAN(can0~, vcan0~) 채널을 검색합니다."""
        self._populate_channels(bus_num)

    def _populate_channels(self, bus_num=None):
        buses_to_update = [b for b in ([bus_num] if bus_num else BUS_IDS) if self.buses[b] is None and b not in self._bus_busy]
        found = {}  # 같은 방식은 한 번만 검색합니다.
        for b in buses_to_update:
            backend = self.selected_backend(b)
            if backend.key not in found:
                found[backend.key] = backend.list_channels()
            combo = self.combo_channels[b]
            combo.clear()
            combo.setEnabled(True)
            self.combo_bitrate[b].setEnabled(True)
            self.btn_refresh[b].setEnabled(True)
            for text, data in found[backend.key]:
                combo.addItem(text, dict(data))
            if combo.count() == 0:
                combo.addItem(backend.empty_text, None)
                combo.setEnabled(False)
            else:
                self.on_channel_changed(b) # 초기화 시 FD UI 갱신

    def _open_can(self, bus_num):
        """선택된 채널의 장치 모듈(src/can_devices)로 채널을 엽니다."""
        session = getattr(self, 'session', None)
        if session is not None and not session.busy and bus_num in session.pending_can:
            session.capture_can(bus_num)
            if bus_num in session.pending_can:
                self.notify_connection_error('CAN 설정 확인',
                    f'BUS {bus_num}: 불러온 설정과 장치가 호환되지 않습니다. 장치/통신 설정을 변경한 뒤 연결하세요.')
                return
        channel_data = self.combo_channels[bus_num].currentData()
        if not isinstance(channel_data, dict) or channel_data.get('handle') is None:
            self.notify_connection_error("Warning", f"Please select a valid CAN channel for Bus {bus_num}.")
            return
        self._open_device(bus_num, channel_data)

    def _open_settings(self, bus_num):
        return OpenSettings(
            bitrate_text=self.combo_bitrate[bus_num].currentText(),
            bitrate_data=self.combo_bitrate[bus_num].currentData(),
            data_enabled=self.combo_data_bitrate[bus_num].isEnabled(),
            data_text=self.combo_data_bitrate[bus_num].currentText(),
            data_data=self.combo_data_bitrate[bus_num].currentData(),
            fd_iso=self.combo_fd_iso[bus_num].currentText())

    def _open_device(self, bus_num, channel_data):
        """장치 모듈로 Bus를 연 뒤, 수신 스레드/화면 상태는 장치와 무관하게 공통 처리합니다."""
        backend = self.devices.for_bustype(channel_data.get('bustype'))
        channel_handle = channel_data.get('handle')
        try:
            self.reset_tree_values(bus_num)
            try:
                bus_obj, is_fd = backend.open(channel_data, self._open_settings(bus_num))
            except DeviceOpenCancelled:
                return
            except DeviceOpenError as exc:
                self.notify_connection_error(exc.title, exc.message)
                return
            self.bus_capabilities[bus_num]['is_fd'] = bool(is_fd)
            self.buses[bus_num] = bus_obj

            self.rx_threads[bus_num] = CANReceiverThread(self.buses[bus_num], self.db_messages[bus_num])
            self.rx_threads[bus_num].error_signal.connect(lambda err, b=bus_num, generation=self._bus_generation[bus_num]: self.handle_rx_error(err, b) if generation == self._bus_generation[b] else None)
            self.rx_threads[bus_num].raw_msg_signal.connect(
                lambda ts, cid, data, ext, err, fd, is_rx, brs, b=bus_num, generation=self._bus_generation[bus_num]: self.route_raw_msg_to_record(ts, cid, data, ext, err, fd, is_rx, b, brs) if generation == self._bus_generation[b] else None
            )
            self.rx_threads[bus_num].start()

            if not self.ui_update_timer.isActive():
                self.ui_update_timer.start(50)

            self.combo_backend[bus_num].setEnabled(False)
            self.combo_channels[bus_num].setEnabled(False)
            self.combo_bitrate[bus_num].setEnabled(False)
            self.combo_fd_iso[bus_num].setEnabled(False)
            self.combo_data_bitrate[bus_num].setEnabled(False)
            self.btn_record.setEnabled(True)

            if hasattr(self, 'tx_panel'):
                self.tx_panel.update_all_action_buttons()
                self.tx_panel.auto_save_packets()

            self.statusBar().showMessage(
                f"Bus {bus_num} Opened: {channel_handle} ({backend.status_label(channel_data)})", 5000)

        except Exception as e:
            self.close_can(bus_num)
            self.log_bus_event(bus_num, f"연결 실패: {e}", "ERROR", diagnostic_details(backend.diag_context))
            self.notify_connection_error("Error", backend.open_failure_text.format(bus=bus_num, error=e))

    def close_can(self, bus_num=None):
        """채널 닫기 및 초기화"""
        buses_to_close = [bus_num] if bus_num else list(BUS_IDS)
        
        for b in buses_to_close:
            already_busy = b in self._bus_busy
            self._bus_busy.add(b)
            self.bus_state_changed.emit(b)
            self._bus_generation[b] += 1
            self.stop_bus_activity(b)
            if self.rx_threads[b]:
                self.rx_threads[b].stop()
                self.rx_threads[b] = None
                
            if self.buses[b] is not None:
                try:
                    self.buses[b].shutdown()
                except Exception as exc:
                    self.log_bus_event(b, f'연결 해제 오류: {exc}', 'ERROR', diagnostic_details('CAN 종료'))
                    self.notify_connection_error('CAN 종료 오류', f'BUS {b}: {exc}')
                finally:
                    self.buses[b] = None
                    self.bus_capabilities[b]['is_fd'] = False
                
            self.combo_backend[b].setEnabled(True)
            self.combo_channels[b].setEnabled(True)
            self.combo_bitrate[b].setEnabled(True)
            
            channel_data = self.combo_channels[b].currentData()
            is_fd = isinstance(channel_data, dict) and channel_data.get('is_fd', False)
            if is_fd:
                self.combo_fd_iso[b].setEnabled(True)
                self.combo_data_bitrate[b].setEnabled(True)
                
            
            if self.bus_issues.get(b, ('', ''))[1] == 'WARN':
                self.bus_issues.pop(b, None)
            self._graph_pending_frames = {key: value for key, value in self._graph_pending_frames.items() if key[0] != b}
            self._panel_pending_frames = {key: value for key, value in self._panel_pending_frames.items() if key[0] != b}
            if not already_busy:
                self._bus_busy.discard(b)
            self.log_bus_event(b, '연결 해제')
            self.bus_state_changed.emit(b)
            self.statusBar().showMessage(f"Bus {b} Closed.", 5000)
            
        if not any(self.rx_threads.values()):
            self.ui_update_timer.stop()
            self.btn_record.setEnabled(False)
            
        if hasattr(self, 'tx_panel'):
            self.tx_panel.update_all_action_buttons()
            
        if not getattr(self, '_is_closing', False) and hasattr(self, 'tx_panel'):
            self.tx_panel.auto_save_packets()

    def handle_rx_error(self, err_msg, bus_num):
        err_lower = (err_msg or "").lower()
        if "receive queue was read too late" in err_lower or err_lower.startswith("rx warning"):
            self.log_bus_event(bus_num, '연결됨 · 경고: 수신 지연', 'WARN', err_msg)
            self.statusBar().showMessage(f"Bus {bus_num}: {err_msg}", 5000)
            return

        receiver = self.rx_threads.get(bus_num)
        details = getattr(receiver, 'last_error_details', '') or diagnostic_details('CAN 수신')
        panel = self.user_panel_window
        context = {
            'BUS': bus_num,
            '장치': self.combo_channels[bus_num].currentText(),
            'Nominal bitrate': self.combo_bitrate[bus_num].currentText(),
            'CAN FD': self.bus_capabilities[bus_num].get('is_fd', False),
            'Data bitrate': self.combo_data_bitrate[bus_num].currentText(),
            '유저패널 모드': getattr(panel, 'mode', '패널 없음'),
            '그래프 수': len(self.active_graphs),
            '프로그램 버전': self.get_app_version(),
            '오류 후 처리': '해당 BUS 송수신 중지 및 연결 해제',
        }
        details += '\n오류 처리 직전 상태:\n' + '\n'.join(f'{k}: {v}' for k, v in context.items())
        self.log_bus_event(bus_num, f'오류로 연결 해제: {err_msg}', 'ERROR', details)
        self.close_can(bus_num)
        show_error(self.user_panel_window or self, f"CAN Rx Error (Bus {bus_num})",
                   err_msg, details=details)
   
    def load_database_file(self, bus_num):
        """DBC 또는 SYM 파일 로드 후 파싱하여 트리 구조 생성"""
        file_paths, _ = QFileDialog.getOpenFileNames(self, f"Select DBC/SYM files for Bus {bus_num}", "", "CAN DB Files (*.dbc *.sym)")
        
        if not file_paths:
            return
            
        for path in file_paths:
            self.load_db_from_path(path, bus_num, auto_save=True)

    def load_db_from_path(self, path, bus_num, auto_save=False):
        try:
            with open(path, 'rb') as stream:
                raw = stream.read()
            db = self.parse_database_bytes(os.path.basename(path), raw)
            self.install_database(db, bus_num, os.path.basename(path), raw, path)
            if auto_save and not self.viewer_only:
                self.tx_panel.auto_save_packets()
        except Exception as exc:
            self.panel_system_event(f'BUS {bus_num} DBC 불러오기 실패: {path}: {exc}', 'ERROR')
            show_error(self.user_panel_window or self, 'Load Error', f'Failed to parse {path}:\n{exc}')

    def parse_database_bytes(self, name, raw):
        import tempfile
        # Reuse the existing SYM 5/6 compatibility parser, never the original path.
        with tempfile.TemporaryDirectory(prefix='pjj-db-') as folder:
            path = os.path.join(folder, 'database' + os.path.splitext(name)[1].lower())
            with open(path, 'wb') as stream:
                stream.write(raw)
            if name.lower().endswith('.sym'):
                header = raw[:4096].decode('latin1')
                db = self._load_sym_v5(path) if re.search(r'FormatVersion\s*=\s*5\.0', header) else self._load_sym_v6(path)
            else:
                encoding = None
                if name.lower().endswith('.dbc'):
                    # cantools defaults to cp1252, which turns UTF-8 units
                    # such as °/s into Â°/s. Preserve legacy DBC encoding
                    # unless the complete file is valid UTF-8 (with/without BOM).
                    try:
                        raw.decode('utf-8-sig')
                    except UnicodeDecodeError:
                        pass
                    else:
                        encoding = 'utf-8-sig'
                db = cantools.database.load_file(path, encoding=encoding)
        return db

    def install_database(self, db, bus_num, name, raw, path='', ident=None):
        import uuid
        item = QListWidgetItem(name)
        item.setData(Qt.UserRole, db)
        item.setData(Qt.UserRole + 1, path)
        item.setData(Qt.UserRole + 2, bytes(raw))
        item.setData(Qt.UserRole + 3, ident or uuid.uuid4().hex)
        self.list_db_files[bus_num].addItem(item)
        for msg in db.messages:
            try:
                msg.signals.sort(key=lambda s: s.start_bit)
            except AttributeError:
                pass
        self.register_db_messages(db, bus_num)
        self.btn_open_log.setEnabled(any(self.db_messages.values()))
        if not self.viewer_only:
            self.tx_panel.refresh_db_symbols(bus_num)
        self.statusBar().showMessage(f'Loaded Database: {name} (Bus {bus_num})', 5000)

    def resolve_graph_bindings(self, bindings):
        databases = {b: [] for b in BUS_IDS}
        for b, listing in self.list_db_files.items():
            for i in range(listing.count()):
                item = listing.item(i)
                databases[b].append(({'id': item.data(Qt.UserRole + 3)}, None, item.data(Qt.UserRole)))
        return {key: self.session.resolve_binding(binding, databases) for key, binding in bindings.items()}

    def update_bound_graphs(self):
        pending, self._graph_pending_frames = self._graph_pending_frames, {}
        for graph in self.active_graphs:
            decoded = {}
            for key, binding in graph.signal_bindings.items():
                frame = (binding['bus'], binding['id'], binding['extended'])
                if frame not in pending:
                    continue
                ts, raw = pending[frame]
                msg, signal = graph._bound_signals[key]
                try:
                    if id(msg) not in decoded:
                        decoded[id(msg)] = msg.decode(raw, decode_choices=False)
                    if signal.name in decoded[id(msg)]:
                        graph.update_data(key, ts, decoded[id(msg)][signal.name], signal.unit or '')
                except (ValueError, KeyError, cantools.database.errors.DecodeError):
                    continue

    def _load_sym_v6(self, file_path):
        """FormatVersion=6.0 심볼 파일을 읽어들이는 함수"""
        content = ""
        for enc in ['utf-8-sig', 'cp949', 'cp1252', 'latin1']:
            try:
                with open(file_path, 'r', encoding=enc) as f:
                    content = f.read()
                break
            except UnicodeDecodeError:
                pass
        else:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
            
        lines = content.splitlines()
        for i, line in enumerate(lines):
            stripped = line.strip()
            lower_stripped = stripped.lower()
            
            # 1. 헤더 버전 라인 청소 (주석 제거 및 6.0 강제 적용)
            if 'FormatVersion=' in line:
                lines[i] = 'FormatVersion=6.0'
                continue
            
            # Title은 지원되는 헤더이므로 유지합니다. 미지원 항목은 행 번호를
            # 유지하며 비웁니다. 주석으로 바꾸면 헤더/심볼 문법을 끊을 수 있습니다.
            if lower_stripped.startswith(('version=', 'author=', 'date=', 'description=', 'brs=')):
                lines[i] = ''
                
        cleaned_content = '\n'.join(lines)
        try:
            return load_sym_with_fd(cleaned_content)
        except Exception as e:
            error_msg = str(e)
            line_match = re.search(r'line:\s*(\d+)', error_msg, re.IGNORECASE)
            if line_match:
                line_num = int(line_match.group(1))
                if 1 <= line_num <= len(lines):
                    modified_line = lines[line_num - 1]
                    error_msg += f"\n\n[파싱 오류 발생 위치 (Line: {line_num})]\n- 변환된 내용: {modified_line}"
            raise Exception(error_msg)

    def _load_sym_v5(self, file_path):
        """FormatVersion=5.0 심볼 파일을 읽어들이는 함수"""
        content = ""
        for enc in ['utf-8-sig', 'cp949', 'cp1252', 'latin1']:
            try:
                with open(file_path, 'r', encoding=enc) as f:
                    content = f.read()
                break
            except UnicodeDecodeError:
                pass
        else:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()

        # cantools가 SYM v5.0의 멀티라인 Enum 또는 대소문자 오류 등을 정상적으로 파싱하지 못하는 문제를 해결하기 위해
        # 파싱 전 파일을 전처리하여 해당 구문을 cantools 친화적인 형태로 변환합니다.
        original_lines = content.splitlines()
        processed_lines = []
        in_multiline_enum = False
        current_enum_line = ""

        for line in original_lines:
            stripped = line.strip()

            # 1. 멀티라인 Enum 정의 결합
            if not in_multiline_enum:
                if stripped.lower().startswith('enum '):
                    current_enum_line = " ".join(stripped.split())
                    if ')' in current_enum_line:
                        processed_lines.append(current_enum_line)
                        current_enum_line = ""
                    else:
                        in_multiline_enum = True
                else:
                    processed_lines.append(line)
            else:
                current_enum_line += " " + " ".join(stripped.split())
                if ')' in current_enum_line:
                    processed_lines.append(current_enum_line)
                    current_enum_line = ""
                    in_multiline_enum = False

        if in_multiline_enum:
            processed_lines.append(current_enum_line)

        lines = processed_lines
        for i, line in enumerate(lines):
            stripped = line.strip()
            lower_stripped = stripped.lower()

            # 1. cantools는 SYM 6.0만 지원하므로 5.0 파일이더라도 6.0으로 속여서 파싱하도록 강제 변경
            if 'FormatVersion=' in line:
                lines[i] = 'FormatVersion=6.0'
                continue

            # 2. 사용자 정의 5.0 규칙: 'DLC=<심볼 바이트 수>' -> V6.0의 'Len='으로 변환
            # (CycleTime은 생략 가능, Var 구조 동일하므로 해당 키워드만 맞춰주면 내부적으로 동일 처리됨)
            if lower_stripped.startswith('dlc'):
                lines[i] = re.sub(r'(?i)^DLC\s*=', 'Len=', stripped)
                continue

            # Title은 유지하고 미지원 항목은 빈 줄로 대체합니다.
            if lower_stripped.startswith(('version=', 'author=', 'date=', 'description=', 'brs=')):
                lines[i] = ''
                continue

            # 4. cantools는 'Enum=NAME(...)' 포맷을 기대하지만, 일부 파일은 'enum NAME(...)'을 사용합니다.
            #    호환성을 위해 'enum '을 'Enum='으로 치환합니다. (대소문자 무시)
            if lower_stripped.startswith('enum '):
                lines[i] = re.sub(r'(?i)^enum\s+', 'Enum=', stripped)
                continue

            # 5. 단위(/u:) 등에 공백이 포함된 큰따옴표 문자열이 있을 경우 cantools 파서 오류 방지를 위해 공백을 밑줄로 치환하고 따옴표 제거
            if stripped.startswith('Var=') and '"' in stripped:
                def replace_spaces(match):
                    return match.group(0).replace(' ', '_').replace('"', '')
                lines[i] = re.sub(r'"[^"]+"', replace_spaces, lines[i])
                
        cleaned_content = '\n'.join(lines)
        try:
            return load_sym_with_fd(cleaned_content)
        except Exception as e:
            error_msg = str(e)
            line_match = re.search(r'line:\s*(\d+)', error_msg, re.IGNORECASE)
            if line_match:
                line_num = int(line_match.group(1))
                if 1 <= line_num <= len(lines):
                    modified_line = lines[line_num - 1]
                    error_msg += f"\n\n[파싱 오류 발생 위치 (Line: {line_num})]\n- 변환된 내용: {modified_line}"
            raise Exception(error_msg)

    def remove_database_file(self, item, bus_num):
        """더블클릭된 DB 파일을 목록에서 삭제하고 캐시/트리/뷰어를 갱신"""
        # 해당 버스(Bus)의 실시간 그래프가 하나라도 열려있는지 확인
        bus_prefix = f"B{bus_num}:"
        is_graph_open = False
        for g in self.active_graphs:
            if g.isVisible() or getattr(g, 'is_in_combined_view', False):
                if any(sig_name.startswith(bus_prefix) for sig_name in g.signal_names):
                    is_graph_open = True
                    break
                    
        if is_graph_open:
            QMessageBox.warning(self, "Warning", f"Bus {bus_num}에 해당하는 실시간 그래프가 켜져있습니다.\n해당 그래프 창을 닫은 후 삭제를 다시 시도해주세요.")
            return
            
        row = self.list_db_files[bus_num].row(item)
        self.list_db_files[bus_num].takeItem(row)
        self.panel_system_event(f'BUS {bus_num} DBC 등록 해제: {item.text()}')
        
        self.db_messages[bus_num].clear()
        self.reset_tree_values(bus_num)
        
        for i in range(self.list_db_files[bus_num].count()):
            db = self.list_db_files[bus_num].item(i).data(Qt.UserRole)
            if db:
                self.register_db_messages(db, bus_num)
                
        if not any(self.db_messages.values()):
            self.btn_open_log.setEnabled(False)
        if self.user_panel_window is not None:
            self.user_panel_window.refresh_dbc_bindings()
            
        for viewer in getattr(self, 'log_viewers', []):
            if viewer.isVisible():
                viewer.refresh_parsing()
                
        if hasattr(self, 'tx_panel'):
            if not self.viewer_only:
                self.tx_panel.refresh_db_symbols(bus_num)
                self.tx_panel.auto_save_packets()
            
    def remove_selected_db_file(self, bus_num):
        """Delete 키를 통해 선택된 DB 파일을 목록에서 삭제"""
        selected_items = self.list_db_files[bus_num].selectedItems()
        for item in selected_items:
            self.remove_database_file(item, bus_num)
            
    def clear_all_database_files(self):
        """등록된 모든 데이터베이스 파일을 삭제합니다."""
        is_graph_open = False
        for g in self.active_graphs:
            if g.isVisible() or getattr(g, 'is_in_combined_view', False):
                is_graph_open = True
                break
                
        if is_graph_open:
            QMessageBox.warning(self, "Warning", "Real-time graphs are currently open.\nPlease close all graph windows and try again.")
            return
            
        reply = QMessageBox.question(self, 'Confirm', 'Are you sure you want to clear all loaded database files?', QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes:
            for bus_num in BUS_IDS:
                while self.list_db_files[bus_num].count() > 0:
                    item = self.list_db_files[bus_num].item(0)
                    self.remove_database_file(item, bus_num)

    def register_db_messages(self, db, bus_num):
        """데이터베이스의 메시지를 메모리에 등록 (수신 시 트리 추가용)"""
        new_ids = []
        for msg in db.messages:
            if msg.frame_id in self.db_messages[bus_num]:
                continue
            self.db_messages[bus_num][msg.frame_id] = msg
            new_ids.append(msg.frame_id)
            
        for can_id in new_ids:
            if (bus_num, can_id) in self.msg_tree_items:
                old_item = self.msg_tree_items[(bus_num, can_id)]
                index = self.tree.indexOfTopLevelItem(old_item)
                if index != -1:
                    self.tree.takeTopLevelItem(index)
                del self.msg_tree_items[(bus_num, can_id)]
                
                self.add_message_to_tree(bus_num, can_id)

        if self.user_panel_window is not None:
            self.user_panel_window.refresh_dbc_bindings()

    def add_message_to_tree(self, bus_num, can_id, direction='Rx'):
        """CAN 데이터가 수신되었을 때 해당 메시지와 시그널을 트리에 동적 생성"""
        if can_id in self.db_messages[bus_num]:
            db_msg = self.db_messages[bus_num][can_id]
            msg_name = db_msg.name
            # DB 로드 시점에 이미 영구적으로 정렬되었으므로, 여기서는 db_msg.signals를 그대로 사용합니다.
            signals = db_msg.signals
        else:
            msg_name = f"Unknown ID: {hex(can_id).upper()}"
            signals = []
        
        # 정렬이 활성화된 상태에서 자식 노드를 추가하면 __lt__ 비교 함수에 의해 자동 정렬되지만,
        # 간혹 순서가 꼬이는 문제가 있어, 수동으로 정렬된 순서대로 삽입되도록 잠시 비활성화합니다.
        # is_sorting_enabled = self.tree.isSortingEnabled()
        # self.tree.setSortingEnabled(False)

        # 1. Message Node (Root)를 메모리에서 먼저 생성 (트리에 추가하기 전)
        msg_item = ReceiveTreeWidgetItem()
        can_id_str = f"0x{can_id:03X}" if can_id <= 0x7FF else f"0x{can_id:X}"
        msg_item.setText(0, str(bus_num))
        msg_item.setText(1, "-") # Type
        msg_item.setText(2, can_id_str)
        msg_item.setText(3, msg_name)
        msg_item.setText(4, direction)
        msg_item.setText(5, "-") # Value
        msg_item.setText(7, "-") # Cycle
        msg_item.setText(8, "0") # Count
        
        # 부모 노드는 굵은 글씨체 적용
        font = QFont()
        font.setBold(True)
        msg_item.setFont(3, font)
        
        # 부모 노드(메시지)에도 체크박스 추가
        msg_item.setFlags(msg_item.flags() | Qt.ItemIsUserCheckable)
        msg_item.setCheckState(3, Qt.Unchecked)
        
        # 2. Signal Node (Child)를 생성하여 메모리 상의 부모 노드에 추가
        for sig in signals:
            sig_item = ReceiveTreeWidgetItem(msg_item)
            # .sym 파일 등 start_bit 속성이 없는 경우를 대비하여 방어 코드 추가
            if hasattr(sig, 'start_bit'):
                sig_item.setData(3, Qt.UserRole, sig.start_bit) # 정렬을 위해 start_bit 저장
            # 시그널(세부항목) 행은 이름과 값만 표시하여 가독성 향상
            sig_item.setText(0, "") # Bus
            sig_item.setText(1, "") # Type
            sig_item.setText(2, "") # CAN ID
            sig_item.setText(3, f"    {sig.name}")
            sig_item.setText(4, "") # Direction
            sig_item.setText(5, "-") # Value
            sig_item.setText(6, sig.unit if sig.unit else "")
            
            # 체크박스 추가
            sig_item.setFlags(sig_item.flags() | Qt.ItemIsUserCheckable)
            sig_item.setCheckState(3, Qt.Unchecked)
            
            key = (bus_num, can_id, sig.name)
            self.signal_tree_items[key] = sig_item
            self.signal_display_decimals[key] = _signal_display_decimals(sig)
            
            # Enum 정보 저장
            if getattr(sig, 'choices', None):
                self.signal_choices[(bus_num, sig.name)] = sig.choices
            
        # 3. 완성된 메시지 노드(자식 포함)를 트리에 추가
        self.tree.addTopLevelItem(msg_item)
        self.msg_tree_items[(bus_num, can_id)] = msg_item
        
        # 4. 추가된 노드 펼치기
        msg_item.setExpanded(True)
        
        # 원래 정렬 상태로 복원
        # self.tree.setSortingEnabled(is_sorting_enabled)

        # 현재 검색어가 있다면 새로 추가된 항목에도 필터링 상태 즉시 적용
        if hasattr(self, 'search_input') and self.search_input.text():
            self.filter_tree_items(self.search_input.text())

    def open_log_viewer(self):
        """로컬 TRC 로그 파일을 열어 새 뷰어 창을 생성"""
        file_paths, _ = QFileDialog.getOpenFileNames(self, "Open CAN Log Files", "", "CAN Log Files (*.trc *.txt *.TXT);;TRC Files (*.trc);;CL2000 Logs (*.txt *.TXT)")
        for file_path in file_paths:
            viewer = LogViewerWindow(file_path, self.db_messages, main_window=self)
            self.log_viewers.append(viewer)
            viewer.show()
            self.statusBar().showMessage(f"Opened CAN Log: {os.path.basename(file_path)}", 5000)

    def update_ui_data(self):
        """Rx 스레드에서 수집한 최신 데이터를 기반으로 UI만 업데이트"""
        # 닫힌 그래프 창 메모리 정리.
        # Combined View에 포함되어 숨겨진(is_visible=False) 그래프도 업데이트 대상에 포함시켜야 하므로,
        # is_in_combined_view 플래그를 함께 확인하여 목록에서 제외되지 않도록 합니다.
        self.active_graphs = [g for g in self.active_graphs if g.isVisible() or getattr(g, 'is_in_combined_view', False) or getattr(g, '_panel_only_hidden', False)]
        self.update_bound_graphs()
        
        # 닫힌 그래프는 동기화 목록에서도 동일한 기준으로 정리합니다.
        self.synced_graphs_ordered = [g for g in self.synced_graphs_ordered if g.isVisible() or getattr(g, 'is_in_combined_view', False)]
        
        for bus_num, rx_thread in self.rx_threads.items():
            if not rx_thread: continue
            
            try:
                for can_id, stats in list(rx_thread.latest_msg_stats.items()):
                    if (bus_num, can_id) not in self.msg_tree_items:
                        self.add_message_to_tree(bus_num, can_id)
                        
                    if (bus_num, can_id) in self.msg_tree_items:
                        item = self.msg_tree_items[(bus_num, can_id)]
                        from PyQt5.QtGui import QColor

                        direction = stats.get("direction", 'Rx')
                        if direction == 'Tx':
                            color = QColor(230, 240, 255) # Light blue for Tx
                        else:
                            color = QColor(Qt.transparent)
                        
                        for i in range(self.tree.columnCount()):
                            item.setBackground(i, color)
                        
                        hex_str = " ".join(f"{b:02X}" for b in stats["data"])
                        msg_type = "FD" if stats.get("is_fd") else "CAN"
                        item.setText(1, msg_type)
                        item.setText(4, direction)
                        item.setText(5, hex_str)
                        item.setText(7, f"{stats['cycle']:.1f}")
                        item.setText(8, str(stats['count']))

                    self.queue_panel_message(bus_num, can_id, stats.get('data', b''),
                                             stats.get('last_time') or time.time())
            except RuntimeError:
                pass

            self.flush_panel_messages()
                
            try:
                for (can_id, sig_name), (val, unit, ts) in list(rx_thread.latest_data.items()):
                    key = (bus_num, can_id, sig_name)
                    if key in self.signal_tree_items:
                        item = self.signal_tree_items[key]
                        from PyQt5.QtGui import QColor
                        for i in range(self.tree.columnCount()):
                            item.setBackground(i, QColor(Qt.transparent))
                        
                        message = self.db_messages[bus_num].get(can_id)
                        choices = message.get_signal_by_name(sig_name).choices if message is not None else None
                        if choices:
                            if isinstance(val, (int, float)) and int(val) in choices:
                                display_val = f"{choices[int(val)]} ({val})" # Enum 매칭 시 문자열과 수치값 함께 출력
                            else:
                                val_str = str(val)
                                num_val = next((k for k, v in choices.items() if str(v) == val_str), None)
                                display_val = f"{val_str} ({num_val})" if num_val is not None else val_str
                        else:
                            decimals = self.signal_display_decimals.get(key, 3)
                            display_val = val if isinstance(val, str) else (
                                f"{val:.{decimals}f}" if isinstance(val, float) else str(val))
                        item.setText(5, display_val)

                    if self.user_panel_window and self.user_panel_window.isVisible():
                        try:
                            self.user_panel_window.on_signal_update(bus_num, sig_name, val, unit, ts, can_id=can_id)
                        except RuntimeError:
                            self.user_panel_window = None
                            
                    item_unit = unit
                    for graph in self.active_graphs:
                        graph_sig_name = f"B{bus_num}:{sig_name}"
                        if graph_sig_name in graph.signal_names:
                            graph.update_data(graph_sig_name, ts, val, item_unit)
            except RuntimeError:
                pass

        self.flush_panel_messages()

    def reset_tree_values(self, bus_num):
        """채널 재연결 시 기존 트리 데이터 및 UI 초기화"""
        self._panel_pending_frames = {key: value for key, value in self._panel_pending_frames.items() if key[0] != bus_num}
        items_to_remove = []
        for (b, can_id), item in list(self.msg_tree_items.items()):
            if b == bus_num:
                items_to_remove.append(item)
                del self.msg_tree_items[(b, can_id)]
                
        for (b, can_id, sig_name) in list(self.signal_tree_items.keys()):
            if b == bus_num:
                del self.signal_tree_items[(b, can_id, sig_name)]
                self.signal_display_decimals.pop((b, can_id, sig_name), None)
                if (b, sig_name) in getattr(self, 'signal_choices', {}):
                    del self.signal_choices[(b, sig_name)]
                
        for item in items_to_remove:
            index = self.tree.indexOfTopLevelItem(item)
            if index != -1:
                self.tree.takeTopLevelItem(index)
        
        if self.rx_threads[bus_num]:
            self.rx_threads[bus_num].latest_data.clear()
            self.rx_threads[bus_num].latest_msg_stats.clear()

    def closeEvent(self, event):
        """프로그램 종료 시 포트 안전 종료"""
        # 기록 저장에 실패하면 재저장할 수 있도록 종료 작업을 취소합니다.
        try:
            if getattr(self, 'record_window', None) and self.record_window.isVisible():
                if not self.record_window.close():
                    event.ignore()
                    return
        except RuntimeError:
            self.record_window = None
        self.session.autosave()
        self.session.timer.stop()
        self.bus_status_timer.stop()
        self.panel_update_timer.stop()
        self._panel_pending_frames.clear()
        self.session.stop_all()
        self._is_closing = True
        if not self.viewer_only:
            for i in BUS_IDS:
                self.close_can(i)
        
        # 메인 윈도우가 닫힐 때 실시간 그래프 창 모두 닫기
        for graph in list(getattr(self, 'active_graphs', [])):
            graph.close()
            
        # 메인 윈도우가 닫힐 때 모든 로그 뷰어 창도 닫기
        for viewer in list(getattr(self, 'log_viewers', [])):
            viewer.close()

        # 사용자 패널 창이 열려 있으면 함께 종료
        try:
            if self.user_panel_window and self.user_panel_window.isVisible():
                self.user_panel_window.close()
        except RuntimeError:
            pass
            
        # 메인 윈도우가 닫힐 때 Combined View 창도 닫기
        try:
            if self.combined_view_window and self.combined_view_window.isVisible():
                self.combined_view_window.parent_is_closing = True
                self.combined_view_window.close()
        except RuntimeError:
            self.combined_view_window = None
            
        # 메인 윈도우가 닫힐 때 송신부 타이머 종료
        if hasattr(self, 'tx_panel') and not self.viewer_only:
            for i in range(self.tx_panel.tree.topLevelItemCount()):
                item = self.tx_panel.tree.topLevelItem(i)
                if hasattr(item, 'stop_timer'):
                    item.stop_timer()

        event.accept()
