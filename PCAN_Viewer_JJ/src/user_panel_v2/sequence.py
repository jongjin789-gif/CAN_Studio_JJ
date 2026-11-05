"""DBC-independent sequence execution and panel control."""
import copy
import time
from datetime import datetime

import can
from PyQt5.QtCore import QTimer, pyqtSignal, QSize, Qt
from PyQt5.QtGui import QColor, QIcon, QTextCursor, QTextCharFormat
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
                             QToolButton, QSizePolicy, QTextEdit, QAction)
from src.crc_utils import calculate_crc16_ccitt_false
from src.utils import get_resource_path
from .masked_data import masked_match


def format_can_id(packet):
    can_id = int(packet['id'])
    width = 8 if packet.get('is_extended_id', can_id > 0x7FF) else 3
    return f"0x{can_id:0{width}X}"


def validate_steps(steps, actions_only=False, allow_empty=False):
    if not steps and not allow_empty:
        raise ValueError("Sequence has no steps.")
    for index, step in enumerate(steps, 1):
        kind = step.get("kind")
        if actions_only and kind == 'RCV':
            raise ValueError('Init/실패 처리에는 수신 비교(RCV)를 사용할 수 없습니다.')
        if kind in ('START', 'STOP'):
            if not isinstance(step.get('target_packet_id'), str) or not step['target_packet_id']:
                raise ValueError(f'Step {index}: 시작/정지할 등록 패킷을 선택하세요.')
            continue
        if kind == "DEL":
            if not 0 <= int(step.get("delay_ms", -1)) <= 600000:
                raise ValueError(f"Step {index}: invalid delay.")
            continue
        if kind not in ("CMD", "RCV") or not step.get("packet"):
            raise ValueError(f"Step {index}: configure a packet.")
        p = step["packet"]
        n = int(p["length"])
        if not 0 <= int(p["id"]) <= (0x1FFFFFFF if p.get("is_extended_id", p["id"] > 0x7FF) else 0x7FF):
            raise ValueError(f"Step {index}: invalid CAN ID.")
        if n not in (list(range(9)) + ([12, 16, 20, 24, 32, 48, 64] if p.get("is_fd") else [])):
            raise ValueError(f"Step {index}: invalid packet length.")
        if len(p["data"]) != n or any(not 0 <= int(b) <= 255 for b in p["data"]):
            raise ValueError(f"Step {index}: invalid data.")
        if p.get("is_brs") and not p.get("is_fd"):
            raise ValueError("BRS requires FD.")
        if kind == 'CMD' and 'data_mask' in step:
            from .packets import validate_command_data
            validate_command_data(step.get('data_override'), n)
            validate_command_data(step['data_mask'], n)
        if kind == "RCV":
            mask = step.get("mask", [])
            if len(mask) != n or not any(mask) or any(not 0 <= int(b) <= 255 for b in mask):
                raise ValueError(f"Step {index}: select at least one comparison bit.")
            if not 1 <= int(step.get("timeout_ms", 0)) <= 600000:
                raise ValueError(f"Step {index}: invalid timeout.")
        else:
            if not 1 <= int(step.get("repeat", 1)) <= 10000 or not 0 <= int(p.get("cycle", 0)) <= 600000:
                raise ValueError(f"Step {index}: invalid repeat/cycle.")
            if p.get("crc_type") not in (None, "N/A", "Hyundai_CRC"):
                raise ValueError("Unsupported CRC type.")
            if p.get("crc_type") == "Hyundai_CRC" and (n < 3 or p["id"] + 0xF800 > 65535):
                raise ValueError("Hyundai CRC requires at least 3 bytes and a compatible ID.")


class SequenceControl(QWidget):
    finished = pyqtSignal(bool)
    COLORS = {"ready": "#245A81", "run": "#00695C", "receive": "#F2B544",
              "delay": "#6A4594", "stop": "#59636E", "done": "#237541", "error": "#B3261E"}
    def __init__(self, owner, cfg):
        super().__init__()
        self.owner, self.cfg = owner, cfg
        self.running = False
        self.in_failure = False
        self.failure_steps = []
        self.index = -1
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._tick)
        self.watchdog = QTimer(self)
        self.watchdog.setInterval(100)
        self.watchdog.timeout.connect(self._check_connection)
        force_button_width = max(1, int(getattr(owner, 'grid_cell_size', 32)))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(4)
        self.button = QPushButton()
        button_font = self.button.font()
        button_font.setPixelSize(max(8, min(10, force_button_width * 5 // 8)))
        self.button.setFont(button_font)
        self.button.setIconSize(QSize(24, 24))
        self.button.clicked.connect(lambda: self.toggle())
        self.force_button = QToolButton()
        self.force_button.setIcon(QIcon(get_resource_path('icon/panel/force.svg')))
        force_icon_size = max(8, min(24, force_button_width - 8))
        self.force_button.setIconSize(QSize(force_icon_size, force_icon_size))
        self.force_button.setToolTip('강제 실행 (RCV 무시)')
        self.force_button.setAccessibleName('강제 실행 (RCV 무시)')
        self.force_button.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self.force_button.setFixedWidth(force_button_width)
        self.force_button.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        self.force_button.setStyleSheet(
            'QToolButton { background:#fff3db; border:1px solid #d79b34; border-radius:3px; padding:0; } '
            'QToolButton:hover { background:#ffe3ad; }')
        self.force_button.clicked.connect(lambda: self.force_toggle())
        self.packet_button = QToolButton()
        self.packet_button.setIcon(QIcon(get_resource_path('icon/panel/packet_control.svg')))
        self.packet_button.setIconSize(QSize(force_icon_size, force_icon_size))
        self.packet_button.setToolTip('패킷 제어 (START / STOP / DELAY만 실행, CMD·RCV 제외)')
        self.packet_button.setAccessibleName('패킷 제어')
        self.packet_button.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self.packet_button.setFixedWidth(force_button_width)
        self.packet_button.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        self.packet_button.setStyleSheet(
            'QToolButton { background:#e3f5f7; border:1px solid #2194a4; border-radius:3px; padding:0; } '
            'QToolButton:hover { background:#bfe9ee; }')
        self.packet_button.clicked.connect(lambda: self.toggle(packet_only=True))
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setStyleSheet("background:white; color:black;")
        log_font = self.log.font()
        log_font.setPointSize(max(7, log_font.pointSize() - 2))
        self.log.setFont(log_font)
        self.log.document().setMaximumBlockCount(2000)
        clear = QAction("로그 지우기", self.log)
        clear.triggered.connect(self.log.clear)
        self.log.addAction(clear)
        self.log.setContextMenuPolicy(Qt.ActionsContextMenu)
        actions.addWidget(self.button, 1)
        actions.addWidget(self.force_button)
        actions.addWidget(self.packet_button)
        layout.addLayout(actions)
        layout.addWidget(self.log, 1)
        self._state("ready", "실행")

    def _state(self, state, label):
        button_text = (
            f"{self.cfg.get('title', 'Sequence')} · {label}"
            if self.cfg.get('is_init')
            else label
        )
        self.button.setText(button_text)
        active = state in ('run', 'receive', 'delay')
        icon_path = 'icon/graph_controls/stop.svg' if active else 'icon/panel/run.svg'
        self.button.setIcon(QIcon(get_resource_path(icon_path)))
        self.button.setToolTip('시퀀스 정지' if active else '시퀀스 실행 (RCV 응답 확인)')
        self.button.setAccessibleName(self.button.toolTip())
        color = "black" if state == "receive" else "white"
        self.button.setStyleSheet(f"background:{self.COLORS[state]}; color:{color}; padding:1px;")

    def write(self, message, color="black"):
        if hasattr(self.owner, 'log_system'):
            title = 'INIT' if self.cfg.get('is_init') else self.cfg.get('title', '시퀀스')
            self.owner.log_system(f'{title}: {message}', 'ERROR' if color == '#B3261E' else 'INFO')
        stamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        cursor = self.log.textCursor()
        cursor.movePosition(QTextCursor.End)
        if not self.log.document().isEmpty():
            cursor.insertBlock()
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(color))
        cursor.insertText(f"[{stamp}] {message}", fmt)
        self.log.setTextCursor(cursor)
        self.log.ensureCursorVisible()

    def step_log(self, status):
        step = self.steps[self.index]
        p = step.get('packet', {})
        bus = f"BUS_{p['bus']}" if p else "-"
        can_id = format_can_id(p) if p else "-"
        name = step.get('name', '').strip() or "이름 없음"
        if step['kind'] in ('START', 'STOP'):
            name += ' · ' + step.get('summary', step.get('target_packet_id', ''))
        return f"{self.index + 1}/{len(self.steps)} {step['kind']} {bus} {can_id} {name} · {status}"

    def stop(self, reason="사용자 정지"):
        was_running = self.running
        self.running = False
        self.timer.stop()
        self.watchdog.stop()
        if was_running:
            self.write(reason)
            self._state("stop", "정지됨 · 재실행")
            self.finished.emit(False)

    def force_toggle(self):
        if self.running:
            self.stop()
            return
        if self.owner.mode != 'run':
            return
        self.toggle(force_rcv_skip=True)

    def fail(self, message):
        self.timer.stop()
        self.watchdog.stop()
        self.write(message, "#B3261E")
        if (not self.in_failure and self.failure_steps and self.owner.mode == 'run'
                and self.owner.isVisible()):
            self.in_failure = True
            self.steps = copy.deepcopy(self.failure_steps)
            self.index = -1
            self.running = True
            self.required_buses = set()
            self.write('NG · 실패 처리 명령 시작', '#B3261E')
            self.watchdog.start()
            self._next()
            return
        self.running = False
        self._state("error", "NG · 재실행")
        self.finished.emit(False)

    def _check_connection(self):
        if not self.running:
            return
        if self.owner.mode != "run" or not self.owner.isVisible():
            self.stop("패널 상태 변경")
        elif any(self.owner.main_window.buses.get(bus) is None for bus in self.required_buses):
            self.fail("CAN 연결 해제")
        elif self.cfg.get('is_init') and hasattr(self.owner, '_check_init_connections'):
            try:
                self.owner._check_init_connections()
            except ValueError as exc:
                self.fail(str(exc))

    def toggle(self, force_rcv_skip=False, packet_only=False):
        if self.running:
            self.stop()
            return
        if self.owner.mode != "run":
            return
        if getattr(self.owner, '_init_running', False) and not self.cfg.get('is_init'):
            self.write('Init 실행 중에는 일반 시퀀스를 시작할 수 없습니다.')
            return
        self.in_failure = False
        self.failure_steps = []
        try:
            self.steps = copy.deepcopy(self.cfg.get("binding", {}).get("sequence_steps", []))
            skipped_receives = sum(step.get('kind') == 'RCV' for step in self.steps) if force_rcv_skip else 0
            if force_rcv_skip:
                self.steps = [step for step in self.steps if step.get('kind') != 'RCV']
            failures = copy.deepcopy(self.cfg.get('binding', {}).get('sequence_failure_steps', []))
            if packet_only:
                allowed = ('START', 'STOP', 'DEL')
                self.steps = [step for step in self.steps if step.get('kind') in allowed]
                failures = [step for step in failures if step.get('kind') in allowed]
                if not any(step.get('kind') in ('START', 'STOP') for step in self.steps):
                    self.write('패킷 제어: START·STOP 단계가 없어 실행하지 않습니다.')
                    return
            validate_steps(self.steps, allow_empty=force_rcv_skip)
            validate_steps(failures, actions_only=True, allow_empty=True)
            if hasattr(self.owner, 'tx_packets'):
                from .packets import validate_tool
                validate_tool(self.owner.tx_packets, dict(behavior='tx', widget_type='sequence',
                              binding=dict(sequence_steps=self.steps, sequence_failure_steps=failures)))
            self.failure_steps = failures
            main = self.owner.main_window
            self.required_buses = {s["packet"]["bus"] for s in self.steps if s["kind"] in ('CMD', 'RCV')}
            for s in self.steps:
                if s["kind"] not in ('CMD', 'RCV'):
                    continue
                p = s["packet"]
                from .packets import connected_packet_bus
                connected_packet_bus(main, p)
        except Exception as exc:
            self.fail(str(exc))
            return
        self.running = True
        self.index = -1
        self.started = time.time()
        self.last_receive = "수신 없음"
        if force_rcv_skip:
            self.write(f'강제 실행: RCV {skipped_receives}단계 건너뜀')
        if packet_only:
            self.write('--- 패킷 제어 실행: CMD·RCV 제외 (실패 처리 포함) ---')
        else:
            self.write("--- 강제 시퀀스 시작 ---" if force_rcv_skip else "--- 시퀀스 시작 ---")
        self._state('run', '패킷 제어 중 · 정지' if packet_only else '실행 중 · 정지')
        self.watchdog.start()
        self._next()

    def _next(self):
        if not self.running:
            return
        previous = self.steps[self.index]["kind"] if self.index >= 0 else None
        self.index += 1
        if self.index == len(self.steps):
            self.running = False
            self.watchdog.stop()
            success = not self.in_failure
            self.write("전체 과정 완료 · OK" if success else '실패 처리 완료 · 원래 시퀀스 NG', '#237541' if success else '#B3261E')
            self._state("done" if success else 'error', "OK · 재실행" if success else 'NG · 재실행')
            self.finished.emit(success)
            return
        step = self.steps[self.index]
        kind = step["kind"]
        if kind in ('START', 'STOP'):
            try:
                self.owner.set_packet_transmission(step['target_packet_id'], kind == 'START')
                self.write(self.step_log('주기 전송 시작' if kind == 'START' else '주기 전송 정지'))
                self.timer.start(0)
            except Exception as exc:
                self.fail(self.step_log(str(exc)))
        elif kind == "CMD":
            self._state("run", "실행 중 · 정지")
            self.remaining = int(step.get("repeat", 1))
            self.response_floor = time.time()
            self.pending_match = False
            self.last_receive = "수신 없음"
            self._send()
        elif kind == "DEL":
            self.write(self.step_log(f"{step['delay_ms']} ms 대기"))
            self._state("delay", "지연 중 · 정지")
            self.timer.start(int(step["delay_ms"]))
        else:
            self._state("receive", "응답 대기 · 정지")
            if previous != "CMD":
                self.response_floor = time.time()
                self.pending_match = False
                self.last_receive = "수신 없음"
            self.write(self.step_log(f"응답 대기 / 제한 {step['timeout_ms']} ms"))
            self.deadline = time.time() + step['timeout_ms'] / 1000.0
            if self.pending_match:
                self.write(self.step_log("응답 조건 일치"))
                self.timer.start(0)
                self.matched = True
            else:
                self.matched = False
                self.timer.start(int(step["timeout_ms"]))

    def _send(self):
        try:
            p = self.steps[self.index]["packet"]
            if hasattr(self.owner, 'tx_packets'):
                runtime = self.owner._packet_runtimes[p['packet_id']]
                runtime.send(data_override=self.steps[self.index].get('data_override'),
                             data_mask=self.steps[self.index].get('data_mask'))
                self.write(self.step_log("송신 완료"))
                self.remaining -= 1
                self.timer.start(int(p.get('cycle', 0)) if self.remaining else 0)
                return
            payload = bytes(p["data"])
            if p.get("crc_type") == "Hyundai_CRC":
                alive = p.get("alive_counter", 0)
                p["alive_counter"] = (alive + 1) % 256
                body = bytes([alive]) + payload[3:]
                crc = calculate_crc16_ccitt_false(body + (0xF800 + p["id"]).to_bytes(2, "little"))
                payload = crc.to_bytes(2, "little") + body
            msg = can.Message(arbitration_id=p["id"], data=payload,
                              is_extended_id=p.get("is_extended_id", p["id"] > 0x7FF),
                              is_fd=p.get("is_fd", False), bitrate_switch=p.get("is_brs", False), check=True)
            self.owner.main_window.buses[p["bus"]].send(msg)
            self.owner.main_window.record_tx_activity(p["bus"], p["id"], payload, msg.is_fd,
                                                      is_ext=msg.is_extended_id)
            self.write(self.step_log("송신 완료"))
            self.remaining -= 1
            self.timer.start(int(p.get("cycle", 0)) if self.remaining else 0)
        except Exception as exc:
            self.fail(self.step_log(f"송신 실패: {exc}"))

    def _tick(self):
        if not self.running:
            return
        kind = self.steps[self.index]["kind"]
        if kind == "CMD" and self.remaining:
            self._send()
        elif kind == "RCV" and not self.matched:
            self.fail(self.step_log("RCV 타임아웃"))
        else:
            self._next()

    def receive(self, ts, bus, can_id, data, extended, fd, brs=False):
        if self.in_failure or not self.running or ts < max(self.started, getattr(self, "response_floor", self.started)):
            return
        idx = self.index
        if self.steps[idx]["kind"] == "CMD":
            idx += 1
        if idx >= len(self.steps) or self.steps[idx]["kind"] != "RCV":
            return
        if idx == self.index and ts > self.deadline:
            return
        s = self.steps[idx]
        p = s["packet"]
        if (bus, can_id, extended, fd, brs) != (p["bus"], p["id"], p.get("is_extended_id", p["id"] > 0x7FF), p.get("is_fd", False), p.get("is_brs", False)):
            return
        self.last_receive = bytes(data).hex(" ").upper()
        if not masked_match(data, p['data'], s['mask'], s.get('allow_length_mismatch', False)):
            return
        self.pending_match = True
        if idx == self.index and not self.matched:
            self.matched = True
            self.write(self.step_log("응답 조건 일치"))
            self.timer.start(0)
