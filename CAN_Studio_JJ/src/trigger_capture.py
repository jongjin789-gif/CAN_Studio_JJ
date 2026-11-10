"""Trigger capture: save the traffic around a DBC signal condition to a TRC file.

Only one trigger can be registered. Frames of all BUSes are kept in a short ring
buffer (pre-trigger time). When the condition becomes true (false -> true edge) the
buffer plus the following post-trigger time is saved. A new trigger while saving
restarts the post-trigger countdown. One file is limited to MAX_CAPTURE_S seconds.
"""
import datetime
import os
import re
import sys
from collections import deque
from dataclasses import dataclass, asdict

from PyQt5.QtCore import QObject, pyqtSignal, Qt
from PyQt5.QtWidgets import (QDialog, QFormLayout, QComboBox, QDoubleSpinBox, QLineEdit,
                             QDialogButtonBox, QLabel, QVBoxLayout)
from src.can_log_formats import write_trc_frames

OPERATORS = ('>', '>=', '<', '<=', '==', '!=')
MIN_SECONDS, MAX_SECONDS = 1.0, 2.0
MAX_CAPTURE_S = 60.0


@dataclass
class TriggerConfig:
    bus: int
    can_id: int
    message: str
    signal: str
    op: str
    value: float
    pre_s: float = 1.0
    post_s: float = 1.0

    def describe(self):
        return f'{self.signal} {self.op} {self.value:g}'

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        """Session value -> config, or None when malformed."""
        if not isinstance(data, dict):
            return None
        try:
            config = cls(bus=int(data['bus']), can_id=int(data['can_id']),
                         message=str(data.get('message', '')), signal=str(data['signal']),
                         op=str(data['op']), value=float(data['value']),
                         pre_s=float(data.get('pre_s', 1.0)), post_s=float(data.get('post_s', 1.0)))
        except (KeyError, TypeError, ValueError):
            return None
        if config.op not in OPERATORS:
            return None
        config.pre_s = min(MAX_SECONDS, max(MIN_SECONDS, config.pre_s))
        config.post_s = min(MAX_SECONDS, max(MIN_SECONDS, config.post_s))
        return config


def compare(value, op, target):
    return {'>': value > target, '>=': value >= target, '<': value < target,
            '<=': value <= target, '==': value == target, '!=': value != target}[op]


def log_dir():
    base = (os.path.dirname(sys.executable) if getattr(sys, 'frozen', False)
            else os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(base, 'Log')


class TriggerCapture(QObject):
    state_changed = pyqtSignal()
    saved = pyqtSignal(str, int)      # path, frame count
    save_failed = pyqtSignal(str)

    def __init__(self, parent=None, directory=None, resolve_message=None):
        super().__init__(parent)
        self.directory = directory
        self.config = None
        # (bus, can_id) -> cantools message; looked up per frame so DBC changes apply at once.
        self.resolve_message = resolve_message or (lambda bus, can_id: None)
        self.bitrates = {}
        self.fired = 0
        self.saved_files = 0
        self._ring = deque()
        self._capture = None             # list of frames while saving
        self._capture_start = 0.0
        self._end = 0.0
        self._last_state = False

    @property
    def capturing(self):
        return self._capture is not None

    def set_config(self, config):
        self.flush()
        self.config = config
        self.fired = self.saved_files = 0
        self._ring.clear()
        self._last_state = False
        self.state_changed.emit()

    def clear(self):
        self.set_config(None)

    def _evaluate(self, message, data):
        try:
            value = message.decode(data, decode_choices=False)[self.config.signal]
        except Exception:
            return None
        if not isinstance(value, (int, float)):
            return None
        return compare(value, self.config.op, self.config.value)

    def feed(self, ts, bus, can_id, data, is_ext, is_fd, is_rx):
        if self.config is None:
            return
        frame = (ts, bus, can_id, bytes(data), bool(is_ext), bool(is_fd), bool(is_rx))
        if self._capture is not None and (ts > self._end or ts - self._capture_start > MAX_CAPTURE_S):
            self.flush()
        if self._capture is not None:
            self._capture.append(frame)
        else:
            ring = self._ring
            ring.append(frame)
            limit = ts - self.config.pre_s
            while ring and ring[0][0] < limit:
                ring.popleft()
        if bus == self.config.bus and can_id == self.config.can_id:
            message = self.resolve_message(bus, can_id)
            if message is None:
                return
            state = self._evaluate(message, data)
            if state is None:
                return
            if state and not self._last_state:
                self._fire(ts)
            self._last_state = state

    def _fire(self, ts):
        self.fired += 1
        if self._capture is None:
            self._capture = list(self._ring)
            self._ring.clear()
            self._capture_start = self._capture[0][0] if self._capture else ts
        self._end = ts + self.config.post_s
        self.state_changed.emit()

    def poll(self, now):
        """Finish a capture when the post-trigger time passed without new frames."""
        if self._capture is not None and now > self._end + 0.2:
            self.flush()

    def flush(self):
        frames, self._capture = self._capture, None
        if not frames:
            if frames is not None:
                self.state_changed.emit()
            return
        config = self.config
        stamp = datetime.datetime.fromtimestamp(frames[0][0]).strftime('%Y%m%d_%H%M%S')
        name = re.sub(r'[^\w.-]+', '_', config.signal)[:40] if config else 'signal'
        path = os.path.join(self.directory or log_dir(), f'Trigger_{stamp}_{name}.trc')
        base, number = path[:-4], 1
        while os.path.exists(path):
            number += 1
            path = f'{base}_{number}.trc'
        try:
            count = write_trc_frames(path, frames, self.bitrates)
        except OSError as exc:
            self.save_failed.emit(str(exc))
        else:
            self.saved_files += 1
            self.saved.emit(path, count)
        self.state_changed.emit()

    def status_text(self):
        if self.config is None:
            return ''
        text = f'{self.config.op} {self.config.value:g}'
        if self.capturing:
            return text + ' · 저장 중'
        if self.saved_files:
            return text + f' · {self.saved_files}건'
        return text


class TriggerDialog(QDialog):
    """Edit the single trigger for one DBC signal."""

    def __init__(self, bus, can_id, message, signal, current=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle('트리거 설정')
        self.bus, self.can_id, self.message_name, self.signal = bus, can_id, message, signal
        root = QVBoxLayout(self)
        form = QFormLayout()
        id_text = f'0x{can_id:03X}' if can_id <= 0x7FF else f'0x{can_id:X}'
        form.addRow('신호', QLabel(f'BUS {bus} · {id_text} {message} · {signal}'))
        self.op = QComboBox()
        self.op.addItems(OPERATORS)
        self.op.setToolTip('신호 값과 기준 값을 비교합니다. 조건이 거짓→참으로 바뀌는 순간 트리거됩니다.')
        form.addRow('조건', self.op)
        self.value = QLineEdit('0')
        self.value.setToolTip('물리값(Factor/Offset 적용 값)으로 입력합니다. Enum은 숫자 값으로 입력합니다.')
        form.addRow('기준 값', self.value)
        self.pre = QDoubleSpinBox()
        self.post = QDoubleSpinBox()
        for spin, tip in ((self.pre, '트리거 이전에 저장할 시간'), (self.post, '트리거 이후에 저장할 시간')):
            spin.setRange(MIN_SECONDS, MAX_SECONDS)
            spin.setSingleStep(0.5)
            spin.setDecimals(1)
            spin.setSuffix(' 초')
            spin.setToolTip(tip + ' (1~2초)')
        form.addRow('트리거 앞', self.pre)
        form.addRow('트리거 뒤', self.post)
        root.addLayout(form)
        note = QLabel('· 트리거는 한 개만 등록됩니다. 새로 설정하면 기존 트리거를 대체합니다.\n'
                      '· 저장 중 다시 트리거되면 그 시점부터 뒤 시간을 다시 셉니다(한 파일 최대 60초).\n'
                      '· 모든 BUS의 프레임을 Log 폴더에 Trigger_날짜_시간_신호.trc로 저장합니다.')
        note.setStyleSheet('color: #555;')
        root.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        if current is not None:
            self.op.setCurrentText(current.op)
            self.value.setText(f'{current.value:g}')
            self.pre.setValue(current.pre_s)
            self.post.setValue(current.post_s)
        else:
            self.pre.setValue(1.0)
            self.post.setValue(1.0)
        self.result_config = None

    def _accept(self):
        try:
            value = float(self.value.text().strip())
        except ValueError:
            self.value.setFocus()
            self.value.selectAll()
            self.value.setStyleSheet('border: 1px solid #b3261e;')
            return
        self.result_config = TriggerConfig(self.bus, self.can_id, self.message_name, self.signal,
                                           self.op.currentText(), value,
                                           self.pre.value(), self.post.value())
        self.accept()
