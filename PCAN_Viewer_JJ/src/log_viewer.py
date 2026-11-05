from src.error_dialog import show_error
import os
import re
import can
import time
from PyQt5.QtWidgets import *
import json
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QIcon, QFont, QColor, QKeySequence
from src.utils import get_resource_path, SortableTreeWidgetItem
from src.can_threads import LogParserThread
from src.graph_log import LogGraphWindow
from src.graph_lifecycle import close_graph_windows
from src.button_icons import decorate_button
from src.bus_config import BUS_IDS
from src.tx_schedule import bus_schedule


class LogViewerWindow(QMainWindow):
    """분석된 로그의 DBC 데이터를 표시하고 개별 시그널 그래프 창을 띄울 수 있는 창"""
    def __init__(self, file_path, db_messages, main_window=None):
        super().__init__()
        self.file_path = file_path
        self.db_messages = db_messages
        self.main_window = main_window
        self.active_graphs = []
        self.synced_graphs_ordered = []
        self.combined_view_window = None
        self.signal_data = {}
        self.signal_choices = {}
        self.raw_log_data = []
        self.highlighted_row = None
        self._populate_index = 0
        self._populate_chunk_size = 1000
        self._populate_progress = None
        self._is_closing = False
        self._fallback_notice_buses = set()
        self._populating = False
        self._filtering = False
        self._filter_index = 0
        self._filter_range = None
        self._filter_bus = None
        self._filter_matches = []
        self._visible_log_rows = None
        self._hidden_log_rows = set()
        self._filter_restore_count = 0
        self._filter_chunk_size = 1024
        self._filter_timer = QTimer(self)
        self._filter_timer.setSingleShot(True)
        self._filter_timer.timeout.connect(self._apply_filter_chunk)

        # Transmission state
        self.is_sending = False
        self.current_send_index = 0
        self.send_timer = QTimer(self)
        self.send_timer.setTimerType(Qt.PreciseTimer)
        self.send_timer.setSingleShot(True)
        self.send_timer.timeout.connect(self.send_next_packet)
        self._pending_single = None
        self._single_send_timer = QTimer(self)
        self._single_send_timer.setTimerType(Qt.PreciseTimer)
        self._single_send_timer.setSingleShot(True)
        self._single_send_timer.timeout.connect(self._retry_single_send)
        self.send_start_time = 0
        self.log_start_offset_ms = 0
        
        self.setWindowTitle(f"Log Viewer - {os.path.basename(file_path)}")
        self.setWindowIcon(QIcon(get_resource_path(os.path.join("icon", "viewer.png"))))
        self.resize(800, 700)
        
        self.init_ui()
        self.start_parsing()

    def init_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)

        # --- Tab Widget ---
        self.tabs = QTabWidget()
        main_layout.addWidget(self.tabs)

        # --- DBC Tab ---
        self.dbc_tab = QWidget()
        self.tabs.addTab(self.dbc_tab, "DBC")
        dbc_layout = QVBoxLayout(self.dbc_tab)
        
        dbc_btn_layout = QHBoxLayout()
        self.btn_view_graph = QPushButton("Graph View")
        decorate_button(self.btn_view_graph, 'graph', '선택한 신호의 로그 그래프를 엽니다.')
        self.btn_view_graph.clicked.connect(self.open_graph)
        dbc_btn_layout.addWidget(self.btn_view_graph)
        
        self.btn_uncheck_all = QPushButton("Uncheck All")
        decorate_button(self.btn_uncheck_all, 'uncheck', '로그 신호의 체크를 모두 해제합니다.')
        self.btn_uncheck_all.clicked.connect(self.uncheck_all_signals)
        dbc_btn_layout.addWidget(self.btn_uncheck_all)
        
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search (Message/Signal Name or ID)")
        self.search_input.textChanged.connect(self.filter_tree_items)
        dbc_btn_layout.addWidget(self.search_input)
        
        dbc_btn_layout.addStretch()
        self.btn_close_graphs = QPushButton("Close Graphs")
        decorate_button(self.btn_close_graphs, 'graph_close', '이 로그뷰에서 연 모든 그래프를 닫습니다.')
        self.btn_close_graphs.clicked.connect(self.close_graphs)
        dbc_btn_layout.addWidget(self.btn_close_graphs)
        dbc_layout.addLayout(dbc_btn_layout)
        
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(['Message / Signal', 'Bus', 'CAN ID (HEX)'])
        self.tree.setColumnWidth(0, 300)
        self.tree.setColumnWidth(1, 50)
        self.tree.setColumnWidth(2, 100)
        self.tree.setAlternatingRowColors(True)
        self.tree.setSortingEnabled(True)
        self.tree.sortByColumn(2, Qt.AscendingOrder) # 2. CAN ID
        self.tree.sortByColumn(1, Qt.AscendingOrder) # 1. Bus (Primary Sort Key)
        self.tree.itemChanged.connect(self.on_tree_item_changed)
        self.tree.setStyleSheet(
            "QTreeView::item { border-bottom: 1px solid #E0E0E0; }"
            "QTreeView::item:selected { background-color: #0078D7; color: white; }"
        )
        dbc_layout.addWidget(self.tree)

        # --- LOG Tab ---
        self.log_tab = QWidget()
        self.tabs.addTab(self.log_tab, "LOG")
        log_layout = QVBoxLayout(self.log_tab)

        # Control layout
        log_ctrl_layout1 = QHBoxLayout()
        self.btn_send = QPushButton("전송")
        self.btn_send.setCheckable(True)
        self.btn_send_single = QPushButton("선택 전송")
        self.btn_save = QPushButton("SAVE")
        self.btn_save.clicked.connect(self.save_filtered_log)
        self.chk_repeat = QCheckBox("반복")
        log_ctrl_layout1.addWidget(self.btn_send)
        log_ctrl_layout1.addWidget(self.btn_send_single)
        log_ctrl_layout1.addWidget(self.chk_repeat)
        log_ctrl_layout1.addStretch()
        log_ctrl_layout1.addWidget(self.btn_save)
        log_layout.addLayout(log_ctrl_layout1)

        log_filter_layout = QHBoxLayout()
        log_filter_layout.addWidget(QLabel("CAN 버스"))
        self.combo_filter_bus = QComboBox()
        self.combo_filter_bus.addItem('ALL', None)
        for bus in BUS_IDS:
            self.combo_filter_bus.addItem(f'BUS{bus}', bus)
        log_filter_layout.addWidget(self.combo_filter_bus)
        log_filter_layout.addWidget(QLabel("CAN ID 필터"))
        self.edit_filter_low = QLineEdit("0x000")
        self.edit_filter_low.setMinimumWidth(105)
        self.edit_filter_high = QLineEdit("0x7FF")
        self.edit_filter_high.setMinimumWidth(105)
        self.btn_filter = QPushButton("필터")
        self.btn_filter.setCheckable(True)
        self.btn_cancel_filter = QPushButton("필터 취소")
        self.btn_cancel_filter.setToolTip('진행 중인 필터를 중단하고 전체 버스·ID의 로그를 다시 표시합니다.')
        self.btn_cancel_filter.clicked.connect(self.cancel_filter)
        log_filter_layout.addWidget(self.edit_filter_low)
        log_filter_layout.addWidget(QLabel("~"))
        log_filter_layout.addWidget(self.edit_filter_high)
        log_filter_layout.addWidget(self.btn_filter)
        log_filter_layout.addWidget(self.btn_cancel_filter)
        log_filter_layout.addStretch()
        log_layout.addLayout(log_filter_layout)

        # Log grid view
        self.log_table = QTableWidget()
        self.log_table.setColumnCount(7)
        self.log_table.setHorizontalHeaderLabels(["번호", "시간(ms)", "타입", "버스", "CAN ID", "DLC", "데이터"])
        self.log_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.log_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.log_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.log_table.horizontalHeader().setStretchLastSection(True)
        self.log_table.setFont(QFont("Consolas", 9))
        self.log_table.itemSelectionChanged.connect(self.update_control_states)
        log_layout.addWidget(self.log_table)

        # Connect signals
        self.btn_send.clicked.connect(self.on_send_toggled)
        self.btn_send_single.clicked.connect(self.on_send_single_clicked)
        self.btn_filter.toggled.connect(self.apply_filter)
        self.combo_filter_bus.currentIndexChanged.connect(lambda: self.apply_filter(True) if self.btn_filter.isChecked() else None)
        self.edit_filter_low.editingFinished.connect(lambda: self.apply_filter(True) if self.btn_filter.isChecked() else None)
        self.edit_filter_high.editingFinished.connect(lambda: self.apply_filter(True) if self.btn_filter.isChecked() else None)

        # Shortcuts
        self.shortcut_send_single = QShortcut(QKeySequence(Qt.Key_Space), self.log_table)
        self.shortcut_send_single.setContext(Qt.WidgetWithChildrenShortcut)
        self.shortcut_send_single.activated.connect(self.on_send_single_clicked)
        self.shortcut_copy = QShortcut(QKeySequence.Copy, self.log_table)
        self.shortcut_copy.setContext(Qt.WidgetWithChildrenShortcut)
        self.shortcut_copy.activated.connect(self.copy_selected_logs_to_clipboard)

        self.update_control_states()

    def update_control_states(self):
        any_bus_connected = False
        if self.main_window:
            any_bus_connected = any(bus is not None for bus in self.main_window.buses.values())

        busy = self._filtering or self._populating
        self.btn_send.setEnabled(any_bus_connected and not busy)
        self.btn_send.setText("정지" if self.is_sending else "전송")

        is_row_selected = self.log_table.currentRow() >= 0
        self.btn_send_single.setEnabled(any_bus_connected and not self.is_sending and is_row_selected and not busy)
        self.btn_save.setEnabled(not busy)
        self.shortcut_copy.setEnabled(not busy)

        self.chk_repeat.setEnabled(not self.is_sending)
        self.combo_filter_bus.setEnabled(not self.is_sending and not self._populating)
        self.edit_filter_low.setEnabled(not self.is_sending)
        self.edit_filter_high.setEnabled(not self.is_sending)
        self.btn_filter.setEnabled(not self.is_sending and not self._populating)
        self.btn_cancel_filter.setEnabled(
            not self.is_sending and not self._populating
            and (self.btn_filter.isChecked() or (self._filtering and self._filter_range is not None)))
        
        if not any_bus_connected and self.is_sending:
            self.stop_sending()

    def on_tree_item_changed(self, item, column):
        """트리 아이템의 체크 상태가 변경될 때 호출됩니다."""
        # 'Message / Signal' 컬럼(0)의 체크박스 변경에만 반응합니다.
        if column != 0:
            return

        # --- 재귀 호출 방지를 위해 시그널 처리 중단 ---
        self.tree.blockSignals(True)

        try:
            check_state = item.checkState(0)
            
            # 1. 부모 아이템(메시지)이 변경된 경우 -> 모든 자식(시그널)의 상태를 동기화
            if item.childCount() > 0:
                if check_state != Qt.PartiallyChecked:
                    for i in range(item.childCount()):
                        child = item.child(i)
                        child.setCheckState(0, check_state)
            
            # 2. 자식 아이템(시그널)이 변경된 경우 -> 부모의 상태를 갱신
            else:
                parent = item.parent()
                if parent:
                    checked_count = sum(1 for i in range(parent.childCount()) if parent.child(i).checkState(0) == Qt.Checked)
                    
                    if checked_count == 0:
                        parent.setCheckState(0, Qt.Unchecked)
                    elif checked_count == parent.childCount():
                        parent.setCheckState(0, Qt.Checked)
                    else:
                        parent.setCheckState(0, Qt.PartiallyChecked)
        finally:
            self.tree.blockSignals(False)
        
    def start_parsing(self):
        self.progress = QProgressDialog("CAN 로그 파일을 분석하고 있습니다...", "취소", 0, 100, self)
        self.progress.setWindowTitle("로그 분석 중")
        self.progress.setWindowModality(Qt.WindowModal)
        
        self.parser_thread = LogParserThread(self.file_path, self.db_messages)
        self.parser_thread.progress_signal.connect(self.progress.setValue)
        self.parser_thread.finished_signal.connect(self.on_parse_finished)
        self.parser_thread.error_signal.connect(self.on_parse_error)
        self.progress.canceled.connect(self.parser_thread.cancel)
        
        self.parser_thread.start()
        self.progress.show()

    def on_parse_finished(self, signal_data, found_msgs, raw_logs):
        if self._is_closing:
            return
        self.progress.close()
        self.signal_data = signal_data
        self.raw_log_data = raw_logs
        self.populate_tree(found_msgs)
        self.populate_log_table()

    def on_parse_error(self, err_msg):
        if self._is_closing:
            return
        self.progress.close()
        show_error(self, "Parse Error", f"Failed to parse log file:\n{err_msg}",
                   details=getattr(self.parser_thread, "last_error_details", None))

    def populate_log_table(self):
        self._cancel_filter()
        self._populating = False
        self._visible_log_rows = None
        self._hidden_log_rows.clear()
        self.log_table.setRowCount(0)
        total = len(self.raw_log_data)
        if total == 0:
            self.update_control_states()
            return

        self.log_table.setSortingEnabled(False)
        self.log_table.setUpdatesEnabled(False)
        self.log_table.setRowCount(total)

        self._populate_index = 0
        self._populating = True
        self.update_control_states()
        self._populate_progress = QProgressDialog("로그 데이터를 표시하고 있습니다...", None, 0, total, self)
        self._populate_progress.setWindowTitle("로그 불러오는 중")
        self._populate_progress.setWindowModality(Qt.WindowModal)
        self._populate_progress.setCancelButton(None)
        self._populate_progress.show()
        QTimer.singleShot(0, self._populate_log_table_chunk)

    def _populate_log_table_chunk(self):
        if self._is_closing or not self._populating:
            return

        total = len(self.raw_log_data)
        end = min(self._populate_index + self._populate_chunk_size, total)

        for i in range(self._populate_index, end):
            log_entry = self.raw_log_data[i]
            self.log_table.setItem(i, 0, QTableWidgetItem(str(i + 1)))
            self.log_table.setItem(i, 1, QTableWidgetItem(f"{log_entry['offset_ms']:.3f}"))
            self.log_table.setItem(i, 2, QTableWidgetItem(log_entry['type']))
            self.log_table.setItem(i, 3, QTableWidgetItem(str(log_entry['bus'])))
            self.log_table.setItem(i, 4, QTableWidgetItem(f"{log_entry['can_id']:X}"))
            self.log_table.setItem(i, 5, QTableWidgetItem(str(log_entry['dlc'])))
            self.log_table.setItem(i, 6, QTableWidgetItem(" ".join(f"{b:02X}" for b in log_entry['data'])))

        self._populate_index = end

        if self._populate_progress:
            self._populate_progress.setValue(end)

        if end < total:
            QTimer.singleShot(0, self._populate_log_table_chunk)
            return

        if self._populate_progress:
            self._populate_progress.close()
            self._populate_progress = None

        self._populating = False
        self.log_table.setUpdatesEnabled(True)
        if self.btn_filter.isChecked():
            self.apply_filter(True)
        if total <= 5000:
            self.log_table.resizeColumnsToContents()
        else:
            self.log_table.setColumnWidth(0, 70)
            self.log_table.setColumnWidth(1, 110)
            self.log_table.setColumnWidth(2, 70)
            self.log_table.setColumnWidth(3, 55)
            self.log_table.setColumnWidth(4, 90)
            self.log_table.setColumnWidth(5, 55)
        self.update_control_states()

    def populate_tree(self, found_msgs):
        self.tree.setSortingEnabled(False)

        for bus_num, can_id in sorted(found_msgs):
            if can_id in self.db_messages[bus_num]:
                msg_def = self.db_messages[bus_num][can_id]
                # 1. 부모 아이템을 메모리에서 먼저 생성합니다. (트리에 추가하기 전)
                msg_item = SortableTreeWidgetItem()
                msg_item.setText(0, msg_def.name)
                msg_item.setText(1, str(bus_num))
                msg_item.setText(2, hex(can_id).upper())

                font = QFont()
                font.setBold(True)
                # TypeError를 수정하고, 체크박스 렌더링 충돌을 피하기 위해
                # 메시지 아이템의 모든 컬럼에 폰트를 적용합니다.
                for i in range(self.tree.columnCount()):
                    msg_item.setFont(i, font)

                msg_item.setFlags(msg_item.flags() | Qt.ItemIsUserCheckable)
                msg_item.setCheckState(0, Qt.Unchecked)

                # 2. 자식 아이템들을 생성하여 메모리상의 부모 아이템에 추가합니다.
                for sig in msg_def.signals:
                    key = (bus_num, can_id)
                    if key in self.signal_data and sig.name in self.signal_data[key]:
                        sig_item = SortableTreeWidgetItem(msg_item)
                        if hasattr(sig, 'start_bit'):
                            sig_item.setData(0, Qt.UserRole, sig.start_bit)
                        sig_item.setText(0, sig.name)
                        sig_item.setText(1, "")
                        sig_item.setText(2, "")
                        sig_item.setFlags(sig_item.flags() | Qt.ItemIsUserCheckable)
                        sig_item.setCheckState(0, Qt.Unchecked)
                        sig_item.setData(0, Qt.UserRole + 1, (bus_num, can_id))
                    if getattr(sig, 'choices', None):
                        self.signal_choices[(bus_num, sig.name)] = sig.choices

                # 3. 자식까지 모두 포함된 부모 아이템을 트리에 추가합니다.
                self.tree.addTopLevelItem(msg_item)

        self.tree.setSortingEnabled(True)
        self.tree.expandAll()
        
        # 파싱 완료 후 검색어가 유지되고 있다면 즉시 필터링 적용
        if hasattr(self, 'search_input') and self.search_input.text():
            self.filter_tree_items(self.search_input.text())

    def apply_filter(self, checked):
        if self._is_closing or self.is_sending or self._populating:
            return
        bounds = None
        if checked:
            try:
                low_id = int(self.edit_filter_low.text(), 16)
                high_id = int(self.edit_filter_high.text(), 16)
                if not 0 <= low_id <= high_id <= 0x1FFFFFFF:
                    raise ValueError()
                bounds = (low_id, high_id)
            except ValueError:
                QMessageBox.warning(self, "입력 오류", "CAN ID는 0x000~0x1FFFFFFF 범위의 16진수이며 시작값은 끝값 이하여야 합니다.")
                self.btn_filter.setChecked(False)
                return

        bus = self.combo_filter_bus.currentData() if checked else None
        if checked and bounds == self._filter_range and bus == self._filter_bus:
            if self._filtering or self._visible_log_rows is not None:
                return
        if not checked and self._filtering and self._filter_range is None:
            return

        # Stop the old request before starting new work. Clearing a filter only
        # restores rows already hidden; it does not scan the original log again.
        self._cancel_filter()
        self._filter_range = bounds
        self._filter_bus = bus
        self._filter_index = 0
        self._filter_restore_count = len(self._hidden_log_rows) if not checked else 0
        self._filter_matches = []
        self._filtering = True
        self.update_control_states()
        self._apply_filter_chunk()

    def cancel_filter(self):
        if self._is_closing or self.is_sending or self._populating:
            return
        blocked = self.btn_filter.blockSignals(True)
        self.btn_filter.setChecked(False)
        self.btn_filter.blockSignals(blocked)
        self.apply_filter(False)

    def _cancel_filter(self):
        self._filter_timer.stop()
        self._filtering = False
        self._filter_matches = []

    def _apply_filter_chunk(self):
        if self._is_closing or not self._filtering:
            return
        total = self.log_table.rowCount()
        restoring = self._filter_range is None
        work_total = self._filter_restore_count if restoring else total
        end = min(self._filter_index + self._filter_chunk_size, work_total)
        deadline = time.perf_counter() + 0.008
        bounds = self._filter_range
        bus = self._filter_bus
        table = self.log_table
        updates_enabled = table.updatesEnabled()
        signals_blocked = table.blockSignals(True)
        table.setUpdatesEnabled(False)
        try:
            while self._filter_index < end:
                if restoring:
                    row = self._hidden_log_rows.pop()
                    table.setRowHidden(row, False)
                else:
                    row = self._filter_index
                    entry = self.raw_log_data[row] if row < len(self.raw_log_data) else None
                    visible = (entry is not None and bounds[0] <= entry['can_id'] <= bounds[1]
                               and (bus is None or entry.get('bus', 1) == bus))
                    if table.isRowHidden(row) == visible:
                        table.setRowHidden(row, not visible)
                    if visible:
                        self._hidden_log_rows.discard(row)
                        self._filter_matches.append(row)
                    else:
                        self._hidden_log_rows.add(row)
                self._filter_index += 1
                if self._filter_index % 64 == 0 and time.perf_counter() >= deadline:
                    break
        finally:
            table.blockSignals(signals_blocked)
            table.setUpdatesEnabled(updates_enabled)

        if self._filter_index < work_total:
            label = '전체 로그 복원 중' if restoring else '로그 필터 처리 중'
            self.statusBar().showMessage(f'{label}: {self._filter_index:,} / {work_total:,}')
            self._filter_timer.start(0)
            return

        self._visible_log_rows = self._filter_matches if bounds is not None else None
        count = len(self._filter_matches) if bounds is not None else total
        self._filter_matches = []
        self._filtering = False
        message = f'필터 취소 완료: 전체 {total:,}개 표시' if restoring else f'로그 필터: {count:,} / {total:,}개 표시'
        self.statusBar().showMessage(message)
        self.update_control_states()

    def get_visible_log_rows(self):
        """필터를 존중하여 현재 표시되는 로그의 원본 행 번호 목록을 반환합니다."""
        if self._filtering or self._populating:
            return []
        if self._visible_log_rows is None:
            return list(range(min(self.log_table.rowCount(), len(self.raw_log_data))))
        return list(self._visible_log_rows)

    def get_visible_log_data(self):
        """필터를 존중하여 현재 표시되는 로그 데이터 목록을 반환합니다."""
        if self._filtering or self._populating:
            return []
        if self._visible_log_rows is None:
            return self.raw_log_data[:self.log_table.rowCount()]
        return [self.raw_log_data[row] for row in self._visible_log_rows]

    def save_filtered_log(self):
        if self._filtering or self._populating:
            return
        logs = self.get_visible_log_data()
        if not logs:
            QMessageBox.information(self, "Save", "저장할 필터 결과가 없습니다.")
            return
        stem, ext = os.path.splitext(self.file_path)
        path, _ = QFileDialog.getSaveFileName(self, "Save filtered log", stem + "_filtered" + ext, "CAN log (*.trc *.txt);;All files (*)")
        if not path:
            return
        if os.path.normcase(os.path.abspath(path)) == os.path.normcase(os.path.abspath(self.file_path)):
            QMessageBox.warning(self, "Save", "원본과 다른 파일 이름으로 저장해주세요.")
            return
        try:
            count = self.export_filtered_log(path, logs)
            QMessageBox.information(self, "Save", f"{count}개 메시지를 저장했습니다.")
        except Exception as exc:
            show_error(self, "Save", str(exc))

    def export_filtered_log(self, path, logs=None):
        logs = self.get_visible_log_data() if logs is None else logs
        selected = {entry['source_line'] for entry in logs}
        is_logger = bool(logs and logs[0].get('format') == 'cl2000')
        # Copy original bytes, including header, timestamps, flags and line endings.
        with open(self.file_path, 'rb') as source:
            lines = source.readlines()
        output = []
        number = 0
        for line_number, line in enumerate(lines, 1):
            if line_number in selected:
                number += 1
                if is_logger:
                    output.append(line)
                    continue
                line, replaced = re.subn(rb'^(\s*)\d+(\)?)(?=\s)',
                                        lambda m: m[1] + str(number).encode('ascii') + m[2], line, count=1)
                if replaced != 1:
                    raise ValueError(f"원본 {line_number}행의 메시지 번호를 확인할 수 없습니다.")
                output.append(line)
            elif not line.strip() or line.lstrip().removeprefix(b'\xef\xbb\xbf').startswith((b';', b'#', b'Timestamp;Type;ID;Data')):
                output.append(line)
        if number != len(logs):
            raise ValueError("원본 로그가 변경되었습니다. 다시 불러온 뒤 저장해주세요.")
        with open(path, 'wb') as dest:
            dest.writelines(output)
        return number

    def _resolve_target_bus(self, original_bus_num):
        """원본 버스가 미연결이면 현재 연결된 첫 번째 버스로 폴백합니다."""
        bus_obj = self.main_window.buses.get(original_bus_num)
        if bus_obj:
            return original_bus_num, bus_obj

        connected_buses = [b for b, obj in self.main_window.buses.items() if obj is not None]
        if not connected_buses:
            return original_bus_num, None

        fallback_bus = connected_buses[0]
        if original_bus_num not in self._fallback_notice_buses:
            self._fallback_notice_buses.add(original_bus_num)
            if self.main_window:
                self.main_window.statusBar().showMessage(
                    f"Bus {original_bus_num} 로그는 미연결 상태라 Bus {fallback_bus}로 전송합니다.",
                    4000
                )
        return fallback_bus, self.main_window.buses.get(fallback_bus)

    def copy_selected_logs_to_clipboard(self):
        if self._filtering or self._populating:
            return
        selected_rows = sorted(list(set(index.row() for index in self.log_table.selectedIndexes())))
        if not selected_rows:
            return

        packets_to_copy = []
        for row in selected_rows:
            if row < len(self.raw_log_data):
                log_entry = self.raw_log_data[row]
                
                msg_type = log_entry['type']
                is_fd = 'F' in msg_type or 'B' in msg_type or 'E' in msg_type
                
                packet_data = {
                    "bus": log_entry.get('bus', 1), "id": log_entry['can_id'], "is_fd": is_fd,
                    "length": log_entry['dlc'], "data": list(log_entry['data']), "cycle": 0,
                    "note": f"From log at {log_entry['offset_ms']:.3f}ms", "symbol": "N/A",
                    "count": 0, "crc_type": "N/A"
                }
                packets_to_copy.append(packet_data)
        
        if packets_to_copy:
            try:
                text_data = json.dumps(packets_to_copy)
                QApplication.clipboard().setText(text_data)
                self.main_window.statusBar().showMessage(f"{len(packets_to_copy)} log entries copied to clipboard.", 3000)
            except Exception as e:
                self.main_window.statusBar().showMessage(f"Failed to copy logs: {e}", 3000)

    def on_send_toggled(self, checked):
        if checked:
            self.start_sending()
        else:
            self.stop_sending()

    def start_sending(self):
        if not self.main_window or self._filtering or self._populating: return
        if self._pending_single is not None and self.highlighted_row is not None:
            self.clear_row_highlight(self.highlighted_row)
        self._single_send_timer.stop()
        self._pending_single = None
        # 원본 행 번호를 함께 보관해 송신 중 list.index() 탐색(O(n²))을 피합니다.
        self._send_rows = self.get_visible_log_rows()
        self._send_logs = [self.raw_log_data[row] for row in self._send_rows]
        if not self._send_logs:
            self.btn_send.setChecked(False)
            return

        if not any(bus is not None for bus in self.main_window.buses.values()):
            QMessageBox.warning(self, "전송 불가", "연결된 CAN 채널이 없습니다.")
            self.btn_send.setChecked(False)
            return

        self.is_sending = True

        # 전송 시작 위치 결정 (선택된 행 또는 처음부터)
        selected_row = self.log_table.currentRow()
        if selected_row >= 0:
            try:
                # 원본 행 번호를 송신 목록에서의 위치로 변환
                self.current_send_index = self._send_rows.index(selected_row)
            except ValueError:
                # Selected row is not visible due to filter, start from beginning of visible logs
                self.current_send_index = 0
        else:
            self.current_send_index = 0
        
        # 드리프트 보정을 위한 타이밍 기준 초기화/리셋
        visible_logs = self._send_logs
        if self.current_send_index < len(visible_logs):
            self.send_start_time = time.monotonic()
            self.log_start_offset_ms = visible_logs[self.current_send_index]['offset_ms']
        
        self.update_control_states()
        self.send_next_packet()

    def stop_sending(self):
        had_pending_single = self._pending_single is not None
        self._single_send_timer.stop()
        self._pending_single = None
        was_sending = self.is_sending or self.send_timer.isActive() or had_pending_single
        self.is_sending = False
        if self.send_timer.isActive():
            self.send_timer.stop()

        if was_sending and self.highlighted_row is not None:
            self.clear_row_highlight(self.highlighted_row)
            self.highlighted_row = None

        self.update_control_states()
        if self.btn_send.isChecked():
            self.btn_send.setChecked(False)

    def on_send_single_clicked(self):
        if not self.main_window or self.is_sending or self._filtering or self._populating: return
        if self._pending_single is not None:
            return

        selected_row = self.log_table.currentRow()
        if selected_row < 0 or self.log_table.isRowHidden(selected_row):
            QMessageBox.warning(self, "전송 불가", "전송할 로그를 선택해주세요.")
            return

        try:
            log_entry = self.raw_log_data[selected_row]
            original_bus_num = log_entry.get('bus', 1)
            target_bus_num, bus_obj = self._resolve_target_bus(original_bus_num)

            if not bus_obj:
                QMessageBox.warning(self, "전송 불가", "연결된 CAN 채널이 없습니다.")
                return

            for j in range(self.log_table.columnCount()):
                item = self.log_table.item(selected_row, j)
                if item: item.setBackground(QColor("#d4edda"))
            self.highlighted_row = selected_row

            # Compatibility check
            msg_type = log_entry['type']
            # BRS(Bit Rate Switch) 플래그가 있거나 DLC가 8을 초과하는 패킷은 FD 전용 기능으로 간주합니다.
            is_incompatible = log_entry['dlc'] > 8 or 'B' in msg_type
            bus_is_fd = self.main_window.bus_capabilities[target_bus_num].get('is_fd', False)
            if is_incompatible and not bus_is_fd:
                QMessageBox.warning(self, "전송 무시됨", f"Bus {target_bus_num}은(는) CAN FD를 지원하지 않아\n"
                                                     f"선택된 FD 패킷(ID: {log_entry['can_id']:X})을 전송할 수 없습니다.")
                self.clear_row_highlight(selected_row)
                return

            is_fd = 'F' in msg_type or 'B' in msg_type or 'E' in msg_type or log_entry['dlc'] > 8
            is_brs = 'B' in msg_type
            msg = can.Message(arbitration_id=log_entry['can_id'], data=log_entry['data'], is_extended_id=log_entry.get('is_extended_id', log_entry['can_id'] > 0x7FF), is_fd=is_fd, bitrate_switch=is_brs)
            if not bus_schedule(self.main_window, target_bus_num).send_log(bus_obj, msg):
                self._pending_single = (selected_row, target_bus_num, bus_obj, msg)
                self._single_send_timer.start(1)
                return
            self._record_single_send(selected_row, target_bus_num, msg)
        except Exception as e:
            show_error(self, "전송 오류", f"선택된 로그를 전송하는 중 오류가 발생했습니다:\n{e}")
            self.clear_row_highlight(selected_row)

    def _record_single_send(self, row, bus, msg):
        if hasattr(self.main_window, 'record_tx_activity'):
            self.main_window.record_tx_activity(bus, msg.arbitration_id, bytes(msg.data), msg.is_fd,
                                                is_ext=msg.is_extended_id)
        QTimer.singleShot(200, lambda: self.clear_row_highlight(row))

    def _retry_single_send(self):
        if self._is_closing or self._pending_single is None:
            return
        row, bus, device, msg = self._pending_single
        if self.main_window.buses.get(bus) is not device:
            self._pending_single = None
            self.clear_row_highlight(row)
            return
        try:
            if not bus_schedule(self.main_window, bus).send_log(device, msg):
                self._single_send_timer.start(1)
                return
            self._pending_single = None
            self._record_single_send(row, bus, msg)
        except Exception as exc:
            self._pending_single = None
            self.clear_row_highlight(row)
            show_error(self, "전송 오류", f"선택된 로그를 전송하는 중 오류가 발생했습니다:\n{exc}")

    def clear_row_highlight(self, row):
        if self._is_closing:
            return
        for j in range(self.log_table.columnCount()):
            item = self.log_table.item(row, j)
            if item:
                item.setBackground(QColor(Qt.transparent))
        if self.highlighted_row == row:
            self.highlighted_row = None

    def send_next_packet(self):
        if not self.is_sending:
            self.stop_sending()
            return
        
        if not any(bus is not None for bus in self.main_window.buses.values()):
            self.stop_sending()
            QMessageBox.warning(self, "전송 중지", "CAN 연결이 끊어져 전송을 중지합니다.")
            return

        log_data = self._send_logs
        send_rows = getattr(self, '_send_rows', None)
        if send_rows is None or len(send_rows) != len(log_data):
            send_rows = self._send_rows = [self.raw_log_data.index(entry) for entry in log_data]

        if self.current_send_index > 0:
            prev_log_index = self.current_send_index - 1
            if prev_log_index < len(log_data):
                try:
                    self.clear_row_highlight(send_rows[prev_log_index])
                except (ValueError, IndexError):
                    pass
        
        if not log_data or self.current_send_index >= len(log_data):
            if self.chk_repeat.isChecked():
                self.current_send_index = 0
                # 반복 시 타이밍 기준 리셋
                if log_data:
                    self.send_start_time = time.monotonic()
                    self.log_start_offset_ms = log_data[0]['offset_ms']
                self.send_timer.start(1)
            else:
                self.stop_sending()
            return

        current_log = log_data[self.current_send_index]
        try:
            original_index = send_rows[self.current_send_index]
            self.log_table.selectRow(original_index)
            for j in range(self.log_table.columnCount()):
                item = self.log_table.item(original_index, j)
                if item: item.setBackground(QColor("#d4edda"))
            self.highlighted_row = original_index
        except (ValueError, IndexError):
            pass

        original_bus_num = current_log.get('bus', 1)
        target_bus_num, bus_obj = self._resolve_target_bus(original_bus_num)
        
        should_send = True
        if bus_obj:
            msg_type = current_log['type']
            # BRS(Bit Rate Switch) 플래그가 있거나 DLC가 8을 초과하는 패킷은 FD 전용 기능으로 간주합니다.
            is_incompatible = current_log['dlc'] > 8 or 'B' in msg_type
            
            bus_is_fd = self.main_window.bus_capabilities[target_bus_num].get('is_fd', False)
            if is_incompatible and not bus_is_fd:
                should_send = False
        else:
            should_send = False

        if should_send:
            msg_type = current_log['type']
            is_fd = 'F' in msg_type or 'B' in msg_type or 'E' in msg_type or current_log['dlc'] > 8
            is_brs = 'B' in msg_type
            msg = can.Message(arbitration_id=current_log['can_id'], data=current_log['data'], is_extended_id=current_log.get('is_extended_id', current_log['can_id'] > 0x7FF), is_fd=is_fd, bitrate_switch=is_brs)
            try:
                if not bus_schedule(self.main_window, target_bus_num).send_log(bus_obj, msg):
                    self.send_timer.start(1)
                    return  # Keep this row pending while periodic TX owns the bus.
                if hasattr(self.main_window, 'record_tx_activity'):
                    self.main_window.record_tx_activity(target_bus_num, current_log['can_id'], current_log['data'], is_fd,
                                                        is_ext=msg.is_extended_id)
            except Exception as e:
                if self.main_window:
                    self.main_window.statusBar().showMessage(f"Tx Error: {e}", 3000)

        self.current_send_index += 1
        if self.current_send_index < len(log_data):
            next_log = log_data[self.current_send_index]
            
            # 드리프트 보정 로직: 다음 패킷의 목표 시간과 현재 시간의 차이를 계산하여 지연시간 결정
            # 이렇게 하면 QTimer의 불확실성이나 처리 오버헤드로 인한 누적 오차를 보정할 수 있습니다.
            elapsed_time_ms = (time.monotonic() - self.send_start_time) * 1000
            target_elapsed_time_ms = next_log['offset_ms'] - self.log_start_offset_ms
            delay_ms = target_elapsed_time_ms - elapsed_time_ms
            
            self.send_timer.start(max(1, int(delay_ms)))
        else:
            if self.chk_repeat.isChecked():
                self.current_send_index = 0
                # 반복 시 타이밍 기준 리셋
                if log_data:
                    self.send_start_time = time.monotonic()
                    self.log_start_offset_ms = log_data[0]['offset_ms']
                self.send_timer.start(1)
            else:
                self.stop_sending()

    def uncheck_all_signals(self):
        """트리에서 체크된 모든 시그널의 체크를 해제합니다."""
        blocked = self.tree.blockSignals(True)
        try:
            for index in range(self.tree.topLevelItemCount()):
                message = self.tree.topLevelItem(index)
                message.setCheckState(0, Qt.Unchecked)
                for row in range(message.childCount()):
                    message.child(row).setCheckState(0, Qt.Unchecked)
        finally:
            self.tree.blockSignals(blocked)
                    
    def filter_tree_items(self, text):
        """검색어에 따라 트리 뷰의 메시지/시그널을 필터링합니다."""
        search_text = text.lower()
        for i in range(self.tree.topLevelItemCount()):
            msg_item = self.tree.topLevelItem(i)
            # 메시지 이름 또는 CAN ID (HEX) 매칭 확인
            msg_match = search_text in msg_item.text(0).lower() or search_text in msg_item.text(2).lower()
            
            any_child_match = False
            for j in range(msg_item.childCount()):
                sig_item = msg_item.child(j)
                sig_match = search_text in sig_item.text(0).lower()
                
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

    def open_graph(self):
        plot_data = {}
        for i in range(self.tree.topLevelItemCount()):
            msg_item = self.tree.topLevelItem(i)
            for j in range(msg_item.childCount()):
                sig_item = msg_item.child(j)
                if sig_item.checkState(0) == Qt.Checked:
                    bus_num, can_id = sig_item.data(0, Qt.UserRole + 1)
                    sig_name = sig_item.text(0)
                    key = (bus_num, can_id)
                    if key in self.signal_data and sig_name in self.signal_data[key]:
                        graph_sig_name = f"B{bus_num}:{can_id:X}:{sig_name}"
                        db_msg = self.db_messages[bus_num][can_id]
                        sig_def = db_msg.get_signal_by_name(sig_name)
                        unit = sig_def.unit if sig_def.unit else ""
                        plot_data[graph_sig_name] = {
                            "times": self.signal_data[key][sig_name][0],
                            "values": self.signal_data[key][sig_name][1],
                            "unit": unit,
                            "bus": bus_num, "id": can_id,
                            "symbol": db_msg.name, "signal": sig_name,
                            "choices": sig_def.choices
                        }
                        
        if not plot_data:
            QMessageBox.warning(self, "Warning", "그래프를 확인할 시그널을 트리에서 체크해주세요.")
            return
            
        graph = LogGraphWindow(plot_data, viewer_window=self)
        self.active_graphs.append(graph)
        graph.show()
        self.uncheck_all_signals()

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

    def closeEvent(self, event):
        self._is_closing = True
        self._cancel_filter()
        self._populating = False
        self.stop_sending()
        if hasattr(self, 'progress'):
            self.progress.close()
        if self._populate_progress:
            self._populate_progress.close()
            self._populate_progress = None
        if hasattr(self, 'parser_thread') and self.parser_thread.isRunning():
            self.parser_thread.cancel()
            self.parser_thread.wait()
        for graph in list(self.active_graphs):
            graph.close()
        if self.main_window and self in getattr(self.main_window, 'log_viewers', []):
            self.main_window.log_viewers.remove(self)
            
        # 로그 뷰어 창이 닫힐 때 Combined View 창도 닫기
        try:
            if self.combined_view_window and self.combined_view_window.isVisible():
                self.combined_view_window.parent_is_closing = True
                self.combined_view_window.close()
        except RuntimeError:
            self.combined_view_window = None
        event.accept()

    def refresh_parsing(self):
        """메인 윈도우에서 DB가 변경되었을 때 로그 뷰어 상태를 갱신합니다"""
        self.stop_sending()
        self._cancel_filter()
        self._visible_log_rows = None
        self._hidden_log_rows.clear()
        self._populating = False
        if hasattr(self, 'parser_thread') and self.parser_thread.isRunning():
            self.parser_thread.cancel()
            self.parser_thread.wait()
        self.tree.clear()
        self.log_table.setRowCount(0)
        self.signal_data.clear()
        for graph in list(self.active_graphs):
            graph.close()
        self.active_graphs.clear()
        self.start_parsing()
