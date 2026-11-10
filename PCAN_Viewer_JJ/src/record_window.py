from src.error_dialog import show_error
import sys
import os
import datetime
import shutil
import tempfile
from PyQt5.QtWidgets import *
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QIcon, QFont
from src.utils import get_resource_path
from src.can_log_formats import trc_header, trc_line, is_vector_log, convert_app_trc
from src.gateway import parse_id_list

FILTER_MODES = (("all", "전체 기록"), ("exclude", "범위 제외"), ("only", "범위만 기록"))


class RecordWindow(QWidget):
    """TRC 포맷(저장 시 Vector ASC/BLF 선택 가능)으로 CAN 데이터를 기록하고 보여주며 저장하는 창.

    수신 로그는 메모리에 쌓지 않고 임시 파일에 바로 기록합니다.
    - Save: 선택한 경로에 헤더 + 임시 파일 내용을 저장
    - Close 버튼: 저장하지 않고 임시 파일 삭제
    - 창 X / 프로그램 종료: Log 폴더에 자동 저장 후 임시 파일 삭제
    """
    def __init__(self, parent=None, bitrates=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_DeleteOnClose) # 창이 닫힐 때(Close) 메모리에서 안전하게 완전 해제
        self.setWindowTitle("Record CAN Data (Log Viewer)")
        self.setWindowIcon(QIcon(get_resource_path(os.path.join("icon", "recorder.png"))))
        self.resize(750, 500)

        self.layout = QVBoxLayout(self)

        # ID filter: applied while recording, so filtered frames never reach the file.
        filter_layout = QHBoxLayout()
        filter_layout.addWidget(QLabel("ID 필터"))
        self.combo_filter = QComboBox()
        for key, label in FILTER_MODES:
            self.combo_filter.addItem(label, key)
        self.combo_filter.setToolTip("전체 기록 / 범위 안의 ID를 빼고 기록 / 범위 안의 ID만 기록\n"
                                     "적용한 시점 이후의 프레임부터 반영됩니다.")
        filter_layout.addWidget(self.combo_filter)
        self.edit_filter = QLineEdit()
        self.edit_filter.setPlaceholderText("예: 100-1FF, 321, 18DAF110 (16진수)")
        self.edit_filter.setToolTip("CAN ID 범위(16진수, 0x 생략 가능). 쉼표로 여러 개를 입력합니다.\n"
                                    "모든 BUS에 같은 필터를 적용합니다. Enter 또는 적용 버튼으로 반영합니다.")
        filter_layout.addWidget(self.edit_filter, 1)
        self.btn_filter = QPushButton("적용")
        self.btn_filter.setToolTip("ID 필터를 지금부터 적용합니다. 변경 이력은 저장 파일 헤더에 남습니다.")
        filter_layout.addWidget(self.btn_filter)
        self.label_filtered = QLabel("")
        self.label_filtered.setToolTip("필터로 기록하지 않은 프레임 수")
        filter_layout.addWidget(self.label_filtered)
        self.layout.addLayout(filter_layout)
        self.btn_filter.clicked.connect(self.apply_filter)
        self.edit_filter.returnPressed.connect(self.apply_filter)
        self.combo_filter.currentIndexChanged.connect(self._on_filter_mode_changed)

        self.text_edit = QPlainTextEdit()
        self.text_edit.setReadOnly(True)
        self.text_edit.setFont(QFont("Consolas", 10))
        self.text_edit.setMaximumBlockCount(50000) # 너무 많은 로그가 쌓여 화면 렌더링이 멈추는 것 방지
        self.layout.addWidget(self.text_edit)

        btn_layout = QHBoxLayout()
        self.btn_save = QPushButton("Save")
        self.btn_close = QPushButton("Close")
        btn_layout.addStretch()
        btn_layout.addWidget(self.btn_save)
        btn_layout.addWidget(self.btn_close)
        self.layout.addLayout(btn_layout)

        self.btn_save.clicked.connect(self.save_log)
        self.btn_close.clicked.connect(self.on_btn_close_clicked)

        self.last_line = ""
        self.msg_count = 0
        self.is_closed_by_btn = False
        self.is_saved = False
        self.start_timestamp = None
        self.last_offset_ms = 0.0
        self.start_datetime = datetime.datetime.now()
        self.bitrates = bitrates or {1: "500 kBit/s"}
        self.filter_mode = "all"
        self.filter_ranges = []
        self.filtered_count = 0
        self.filter_notes = []

        fd, self.temp_path = tempfile.mkstemp(prefix="CAN_Record_", suffix=".trc.tmp")
        self._temp_file = os.fdopen(fd, 'w', encoding='utf-8', newline='\n')

        # 성능 최적화를 위한 텍스트 버퍼 타이머
        self.gui_queue = []
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.flush_gui)
        self.timer.start(100) # 100ms 마다 텍스트 한 번에 렌더링 + 임시 파일 flush

        self.text_edit.appendPlainText("\n".join(self.build_header()))

    def build_header(self):
        # $STARTTIME은 Time Offset 0(첫 프레임) 시각이어야 다시 불러올 때 절대시간이 맞습니다.
        if self.start_timestamp is not None:
            start_dt = datetime.datetime.fromtimestamp(self.start_timestamp)
        else:
            start_dt = self.start_datetime
        return trc_header(start_dt, self.bitrates, self.filter_notes)

    # ---- ID filter ---------------------------------------------------------
    def _on_filter_mode_changed(self):
        # Switching back to "전체 기록" takes effect at once; other modes need a range.
        if self.combo_filter.currentData() == "all":
            self.apply_filter()

    def apply_filter(self):
        mode = self.combo_filter.currentData()
        text = self.edit_filter.text().strip()
        try:
            ranges = parse_id_list(text) if mode != "all" else []
            if mode != "all" and not ranges:
                raise ValueError("ID 범위를 입력하세요.")
        except ValueError as exc:
            self.edit_filter.setStyleSheet("border: 1px solid #b3261e;")
            self.edit_filter.setToolTip(str(exc))
            self.label_filtered.setText("입력 오류")
            return False
        self.edit_filter.setStyleSheet("")
        if (mode, ranges) == (self.filter_mode, self.filter_ranges):
            return True
        self.filter_mode, self.filter_ranges = mode, ranges
        label = dict(FILTER_MODES)[mode]
        stamp = datetime.datetime.now().strftime("%H:%M:%S")
        note = f"ID filter from {stamp}: {mode}" + (f" {text}" if mode != "all" else "")
        self.filter_notes.append(note)
        self.gui_queue.append(f";   {note}")
        self.setWindowTitle("Record CAN Data (Log Viewer)" + ("" if mode == "all" else f" · {label} {text}"))
        self._update_filter_label()
        return True

    def frame_allowed(self, can_id):
        if self.filter_mode == "all":
            return True
        hit = any(low <= can_id <= high for low, high in self.filter_ranges)
        return hit if self.filter_mode == "only" else not hit

    def _update_filter_label(self):
        self.label_filtered.setText("" if self.filter_mode == "all" and not self.filtered_count
                                    else f"제외 {self.filtered_count:,}")

    def add_log_entry(self, ts, can_id, data, is_ext, is_err, is_fd, is_rx, bus_num=1, preserve_timestamp=False):
        if is_err: return # 에러 프레임은 생략
        if not self.frame_allowed(can_id):
            self.filtered_count += 1
            return
        if self.start_timestamp is None:
            self.start_timestamp = ts

        self.msg_count += 1
        # 멀티 버스/드라이버 타임스탬프 편차로 인한 음수/역전 시간을 방지합니다.
        offset_ms = max(0.0, (ts - self.start_timestamp) * 1000.0)
        if not preserve_timestamp and offset_ms < self.last_offset_ms:
            offset_ms = self.last_offset_ms
        self.last_offset_ms = max(self.last_offset_ms, offset_ms)
        line = trc_line(self.msg_count, offset_ms, can_id, data, is_ext, is_fd, is_rx, bus_num)
        self.last_line = line
        if self._temp_file is not None:
            self._temp_file.write(line + "\n")
        self.gui_queue.append(line)

    def flush_gui(self):
        if self.filtered_count:
            self._update_filter_label()
        if self.gui_queue:
            self.text_edit.appendPlainText("\n".join(self.gui_queue))
            self.gui_queue.clear()
        if self._temp_file is not None:
            self._temp_file.flush()

    def _write_trc(self, file_path):
        """헤더 + 임시 파일에 쌓인 로그를 file_path에 기록합니다."""
        self._temp_file.flush()
        file_path = os.path.abspath(file_path)
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        fd, pending = tempfile.mkstemp(prefix='.record-', dir=os.path.dirname(file_path))
        try:
            with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as out:
                out.write("\n".join(self.build_header()) + "\n")
                with open(self.temp_path, 'r', encoding='utf-8') as src:
                    shutil.copyfileobj(src, out)
                out.flush()
                os.fsync(out.fileno())
            os.replace(pending, file_path)
        finally:
            if os.path.exists(pending):
                os.remove(pending)

    @staticmethod
    def _with_extension(file_path, selected_filter):
        """Add the extension of the chosen filter when the user typed none."""
        if os.path.splitext(file_path)[1].lower() in ('.trc', '.asc', '.blf'):
            return file_path
        for ext in ('.asc', '.blf'):
            if ext in (selected_filter or ''):
                return file_path + ext
        return file_path + '.trc'

    def _write_vector(self, file_path):
        """Vector ASC/BLF: convert the TRC temp file via python-can."""
        self._temp_file.flush()
        start = self.start_timestamp if self.start_timestamp is not None else self.start_datetime.timestamp()
        convert_app_trc(self.temp_path, start, file_path)

    def _discard_temp(self):
        if self._temp_file is not None:
            try:
                self._temp_file.close()
            except OSError:
                pass
            self._temp_file = None
        try:
            os.remove(self.temp_path)
        except OSError:
            pass

    def on_btn_close_clicked(self):
        self.is_closed_by_btn = True
        self.close()

    @staticmethod
    def _log_dir():
        # 실행 파일(.exe) 또는 스크립트(.py)의 기본 위치 확인
        if getattr(sys, 'frozen', False):
            base_dir = os.path.dirname(sys.executable)
        else:
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        log_dir = os.path.join(base_dir, "Log")
        os.makedirs(log_dir, exist_ok=True) # Log 폴더가 없으면 자동 생성
        return log_dir

    def save_log(self):
        try:
            default_filename = f"CAN_Log_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.trc"
            default_filepath = os.path.join(self._log_dir(), default_filename)

            file_path, selected = QFileDialog.getSaveFileName(
                self, "Save CAN Log", default_filepath,
                "TRC Files (*.trc);;Vector ASC (*.asc);;Vector BLF (*.blf)")
            if file_path:
                file_path = self._with_extension(file_path, selected)
                if is_vector_log(file_path):
                    self._write_vector(file_path)
                else:
                    self._write_trc(file_path)
                self.is_saved = True
                self.close() # 저장 완료 후 창 자동 닫기
        except Exception as e:
            show_error(self, "Error", f"Failed to save file:\n{str(e)}")

    def auto_save_log(self):
        try:
            default_filename = f"CAN_Log_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.trc"
            self._write_trc(os.path.join(self._log_dir(), default_filename))
            self.is_saved = True
            return True
        except Exception as exc:
            show_error(self, "기록 저장 실패",
                       f"기록을 저장하지 못해 창을 유지합니다. Save로 다시 저장해주세요.\n"
                       f"임시 기록: {self.temp_path}\n\n{exc}")
            return False

    def closeEvent(self, event):
        """창이 닫힐 때 타이머가 계속 동작하여 발생하는 RuntimeError 완벽 차단"""
        if not getattr(self, 'is_closed_by_btn', False) and not getattr(self, 'is_saved', False):
            if self.msg_count > 0:
                if not self.auto_save_log():
                    event.ignore()
                    return

        self.timer.stop()
        self._discard_temp()
        event.accept()
