import re
from src.error_dialog import diagnostic_details
from src.can_log_formats import is_vector_log, read_vector_entries
from PyQt5.QtCore import QThread, pyqtSignal
import datetime
import os
import time


class CANReceiverThread(QThread):
    """
    CAN 메시지를 수신하고 cantools를 이용해 디코딩하는 백그라운드 스레드
    """
    error_signal = pyqtSignal(str)
    # bytes 타입의 마샬링(Marshaling) 오류를 막기 위해 object 사용
    raw_msg_signal = pyqtSignal(float, int, object, bool, bool, bool, bool, bool)

    def __init__(self, bus, db_messages):
        super().__init__()
        self.bus = bus
        self.db_messages = db_messages  # {can_id: cantools.Message}
        self.running = False
        self.latest_data = {}  # {(can_id, signal_name): (value, unit, timestamp)}
        self.latest_rx_data = {}  # {can_id: (raw_payload, timestamp)}; excludes local Tx echoes
        self.latest_msg_stats = {}  # {can_id: {"count": 0, "cycle": 0.0, "last_time": None, "data": b""}}
        # RX signal statistics: {(can_id, signal_name): [min, max, sum, count]}
        self.signal_stats = {}
        # Error frames are only counted (a missing ACK can produce thousands per second;
        # sending each one to the GUI thread would load the whole program).
        self.error_frames = 0
        self.last_error_frame = None   # (arbitration_id, data) of the latest error frame
        # Optional BUS-to-BUS forwarding hook (src/gateway.py), called on this thread.
        self.gateway = None
        self.bus_num = None
        
        # 타임스탬프 정규화를 위한 변수
        self._is_first_msg = True
        self._timestamp_offset = 0.0
        self._last_queue_warning_ts = 0.0
        self.last_error_details = ""

    def run(self):
        self.running = True
        while self.running:
            try:
                msg = self.bus.recv(timeout=0.1)
                if msg is None:
                    continue
                if msg.is_error_frame:
                    self.error_frames += 1
                    self.last_error_frame = (int(msg.arbitration_id), bytes(msg.data))
                    continue
                gateway = self.gateway
                if gateway is not None and msg.is_rx and not msg.is_error_frame:
                    gateway.forward(self.bus_num, msg)
                
                # --- 타임스탬프 정규화 (Timestamp Normalization) ---
                # 일부 시스템(예: Windows PCAN)에서 드라이버가 제공하는 타임스탬프는 Unix Epoch가 아닌
                # 시스템 부팅 기준일 수 있습니다. 이 로직은 최초 수신 메시지를 기준으로 CAN 타임스탬프와
                # 시스템 시간(time.time())의 오프셋을 계산하여, 모든 타임스탬프를 시스템 시간 기준으로 보정합니다.
                # 이를 통해 Linux(SocketCAN)와 Windows(PCAN) 간의 타임스탬프 기준 차이를 줄여 일관성을 높입니다.
                if self._is_first_msg and msg.timestamp > 0:
                    self._timestamp_offset = time.time() - msg.timestamp
                    self._is_first_msg = False
                
                # 계산된 오프셋을 적용합니다. (첫 메시지 수신 전까지는 오프셋이 0)
                normalized_timestamp = msg.timestamp + self._timestamp_offset
                
                # GUI 렌더링 스레드로 원본 데이터 전송 (완벽한 타입 캐스팅으로 PyQt5 충돌 차단)
                self.raw_msg_signal.emit(
                    float(normalized_timestamp), # 정규화된 타임스탬프 사용
                    int(msg.arbitration_id), 
                    bytes(msg.data), 
                    bool(msg.is_extended_id), 
                    bool(msg.is_error_frame),
                    bool(getattr(msg, 'is_fd', False)),
                    bool(msg.is_rx),
                    bool(getattr(msg, 'bitrate_switch', False))
                )
                if msg.is_error_frame:
                    continue
                if msg.is_rx:
                    self.latest_rx_data[int(msg.arbitration_id)] = (bytes(msg.data), float(normalized_timestamp))
                
                # 모든 수신 메시지 통계(Count, Cycle Time, Data) 업데이트
                can_id = msg.arbitration_id
                now = normalized_timestamp # 정규화된 타임스탬프 사용
                stats = self.latest_msg_stats.get(can_id, {"count": 0, "cycle": 0.0, "last_time": None, "data": b"", "is_fd": False, "direction": 'Rx'})
                stats["count"] += 1
                if stats["last_time"] is not None:
                    stats["cycle"] = (now - stats["last_time"]) * 1000.0
                stats["last_time"] = now
                stats["data"] = msg.data
                stats["is_fd"] = getattr(msg, 'is_fd', False)
                stats["direction"] = 'Rx' if msg.is_rx else 'Tx'
                self.latest_msg_stats[can_id] = stats

                # 수신된 CAN ID가 등록된 DBC/SYM에 존재하는 경우 시그널 디코딩
                if can_id in self.db_messages:
                    db_msg = self.db_messages[can_id]
                    try:
                        # decode_choices=False 설정하여 NamedSignalValue(Enum) 대신 순수 수치값으로 받음
                        decoded_data = db_msg.decode(msg.data, decode_choices=False)
                        
                        # 최신 데이터 딕셔너리 갱신 (Factor, Offset 연산 완료된 값)
                        for sig_name, sig_val in decoded_data.items():
                            signal_def = db_msg.get_signal_by_name(sig_name)
                            unit = signal_def.unit if signal_def.unit else ""
                            self.latest_data[(can_id, sig_name)] = (sig_val, unit, now)
                            if msg.is_rx and isinstance(sig_val, (int, float)):
                                stat = self.signal_stats.get((can_id, sig_name))
                                if stat is None:
                                    self.signal_stats[(can_id, sig_name)] = [sig_val, sig_val, sig_val, 1]
                                else:
                                    if sig_val < stat[0]:
                                        stat[0] = sig_val
                                    if sig_val > stat[1]:
                                        stat[1] = sig_val
                                    stat[2] += sig_val
                                    stat[3] += 1
                    except Exception as e:
                        pass # 데이터 길이 오류 등 예외 처리
            except Exception as e:
                err_text = str(e)
                err_lower = err_text.lower()

                # 드라이버 수신 큐 지연 경고는 복구 가능한 상태이므로 연결을 끊지 않고 수신을 계속합니다.
                if "receive queue was read too late" in err_lower:
                    now = time.monotonic()
                    if now - self._last_queue_warning_ts >= 2.0:
                        self._last_queue_warning_ts = now
                        self.error_signal.emit(f"Rx Warning: {err_text}")
                    continue

                self.last_error_details = diagnostic_details(
                    "CAN 수신 / CANReceiverThread.run",
                    {"채널": getattr(self.bus, "channel_info", "unknown")})
                self.error_signal.emit(f"Rx Error: {err_text}")
                break

    def stop(self):
        self.running = False
        self.wait()


class LogParserThread(QThread):
    """TRC 로그 파일을 비동기로 파싱하고 DBC 포맷에 맞게 디코딩하는 백그라운드 스레드"""
    progress_signal = pyqtSignal(int)
    finished_signal = pyqtSignal(object, object, object) # signal_data, found_msgs, raw_log_lines
    error_signal = pyqtSignal(str)

    def __init__(self, file_path, db_messages):
        super().__init__()
        self.file_path = file_path
        self.db_messages = db_messages
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True

    @staticmethod
    def _parse_payload(length, tokens, msg_type):
        # Explorer writes encoded FD DLCs; View and our recorder write byte
        # counts, even with the same COLUMNS header. Disambiguate using the
        # complete payload so byte-count logs remain compatible.
        fd_lengths = {9: 12, 10: 16, 11: 20, 12: 24, 13: 32, 14: 48, 15: 64}
        if msg_type in ('FD', 'FB', 'FE', 'FBE'):
            expanded = fd_lengths.get(length)
            if expanded is not None and len(tokens) == expanded:
                length = expanded
        data = bytes.fromhex(''.join(tokens[:length]))
        return length, data

    def _decode_entry(self, signal_data, found_msgs, bus_num, can_id, data, ts):
        if bus_num in self.db_messages and can_id in self.db_messages[bus_num]:
            found_msgs.add((bus_num, can_id))
            db_msg = self.db_messages[bus_num][can_id]
            try:
                decoded = db_msg.decode(data, decode_choices=False)
            except Exception:
                return
            key = (bus_num, can_id)
            series = signal_data.setdefault(key, {})
            for sig_name, val in decoded.items():
                if not isinstance(val, (int, float)):
                    continue
                times, values = series.setdefault(sig_name, ([], []))
                times.append(ts)
                values.append(float(val))

    def _run_vector(self):
        """Vector ASC/BLF via python-can; entries match the TRC parser output."""
        signal_data, found_msgs = {}, set()
        self.progress_signal.emit(5)
        entries = read_vector_entries(self.file_path, cancelled=lambda: self._is_cancelled)
        if entries is None or self._is_cancelled:
            return
        total = max(1, len(entries))
        for i, entry in enumerate(entries, 1):
            if self._is_cancelled:
                return
            if i % 2000 == 0:
                self.progress_signal.emit(5 + int(i * 95 / total))
            self._decode_entry(signal_data, found_msgs, entry['bus'], entry['can_id'],
                               entry['data'], entry['timestamp'])
        self.progress_signal.emit(100)
        self.finished_signal.emit(signal_data, found_msgs, entries)

    def run(self):
        signal_data = {} # {(bus_num, can_id): {signal_name: (times[], values[])}}
        found_msgs = set()
        raw_log_lines = []
        if is_vector_log(self.file_path):
            try:
                self._run_vector()
            except Exception as e:
                self.last_error_details = diagnostic_details("로그 파일 파싱 / LogParserThread._run_vector")
                self.error_signal.emit(str(e))
            return
        try:
            base_time = 0.0
            fallback_base_time = 0.0
            last_offset_ms = 0.0
            logger_columns = None
            logger_start = None

            try:
                fallback_base_time = os.path.getmtime(self.file_path)
            except Exception:
                fallback_base_time = datetime.datetime.now().timestamp()

            total_lines = 0
            with open(self.file_path, 'r', encoding='utf-8', errors='ignore') as f:
                for _ in f:
                    total_lines += 1
                    if self._is_cancelled:
                        return

            if total_lines <= 0:
                if not self._is_cancelled:
                    self.progress_signal.emit(100)
                    self.finished_signal.emit(signal_data, found_msgs, raw_log_lines)
                return

            with open(self.file_path, 'r', encoding='utf-8', errors='ignore') as f:
                for i, line in enumerate(f, start=1):
                    if self._is_cancelled:
                        break
                    
                    if i % 200 == 0:
                        self.progress_signal.emit(int(i * 100 / total_lines))

                    if line.startswith(';$STARTTIME='):
                        try:
                            ole_date = float(line.split('=')[1])
                            base_date = datetime.datetime(1899, 12, 30)
                            start_dt = base_date + datetime.timedelta(days=ole_date)
                            base_time = start_dt.timestamp()
                        except Exception:
                            pass
                        continue

                    line = line.strip().lstrip('\ufeff')
                    if not line or line.startswith((';', '#')):
                        continue

                    if line == 'Timestamp;Type;ID;Data':
                        logger_columns = True
                        continue

                    tokens = line.split()
                    
                    try:
                        logger_extended = None
                        logger_direction = None
                        if logger_columns:
                            stamp, frame_type, identifier, payload = line.split(';')
                            # CL2000 time format 6, as used by the logger TXT file.
                            frame_dt = datetime.datetime.strptime(stamp, '%Y_%m_%dT%H_%M_%S_%f')
                            flags = int(frame_type, 16)
                            if flags not in (0, 1, 8, 9):
                                raise ValueError('Unsupported CL2000 frame type')
                            logger_extended = bool(flags & 1)
                            logger_direction = 'Tx' if flags & 8 else 'Rx'
                            identifier_value = int(identifier, 16)
                            payload_bytes = bytes.fromhex(payload)
                            if identifier_value < 0 or identifier_value > (0x1FFFFFFF if logger_extended else 0x7FF) or len(payload_bytes) > 8:
                                raise ValueError('Invalid CL2000 CAN frame')
                            if logger_start is None:
                                logger_start = frame_dt
                                base_time = frame_dt.timestamp()
                            relative_ms = round((frame_dt - logger_start).total_seconds() * 1000)
                            tokens = [str(i), str(relative_ms), 'DT', '1', identifier,
                                      logger_direction, '-', str(len(payload_bytes)),
                                      *payload_bytes.hex(' ').split()]
                        if base_time == 0.0:
                            base_time = fallback_base_time

                        offset_ms = max(0.0, float(tokens[1]))
                        if raw_log_lines and offset_ms < last_offset_ms:
                            offset_ms = last_offset_ms
                        last_offset_ms = offset_ms
                        ts = base_time + (offset_ms / 1000.0)
                        
                        # Identify format and parse
                        # Format 1: PCAN-Explorer (e.g., "1) 1.004 1 100h Rx d 8 ...")
                        if len(tokens) > 6 and tokens[2].isdigit() and tokens[4] in ('Rx', 'Tx'):
                            bus_num = int(tokens[2])
                            can_id = int(tokens[3].rstrip('h'), 16)
                            dlc = int(tokens[6])
                            data = bytes.fromhex("".join(t.rstrip('h') for t in tokens[7:7+dlc]))
                            # Infer FD from DLC or BRS flag
                            msg_type = 'FD' if dlc > 8 or (len(tokens) > 7+dlc and 'BRS' in tokens[7+dlc:]) else 'DT'
                            id_text = tokens[3]

                        # TRC 1.2 / 1.3: "1) 1059.900 1 Rx 0300 - 8 00 ..." (bus, direction, ID, reserved)
                        elif len(tokens) > 5 and tokens[0].endswith(')') and tokens[2].isdigit() and tokens[3] in ('Rx', 'Tx'):
                            bus_num = int(tokens[2])
                            id_text = tokens[4]
                            can_id = int(id_text.rstrip('h'), 16)
                            idx = 6 if tokens[5] == '-' else 5
                            dlc = int(tokens[idx])
                            if dlc > 8 or 'RTR' in tokens[idx + 1:idx + 2]:
                                continue  # Classic-only formats; remote frames carry no data.
                            data = bytes.fromhex("".join(tokens[idx + 1:idx + 1 + dlc]))
                            if len(data) != dlc:
                                continue
                            msg_type = 'DT'

                        # Format 2 & 4: PCAN-Explorer 7, PCAN-View 5, or this app's own format
                        # - PE7 / App's: "1 0.276 FB 1 0035 Rx - 13 ..."
                        # - PV5:         "1 0.276 FB   0035 Rx 32 ..."
                        elif len(tokens) > 5 and tokens[2] in ('DT', 'FD', 'ED', 'RTR', 'FB', 'FE', 'FBE', 'BR', 'EB'):
                            msg_type = tokens[2]
                            # Distinguish formats by checking for a bus number (single digit at tokens[3])
                            if len(tokens) > 7 and tokens[3].isdigit() and len(tokens[3]) == 1 and tokens[5] in ('Rx', 'Tx'): # Format with bus number
                                bus_num = int(tokens[3])
                                id_text = tokens[4]
                                can_id = int(tokens[4].rstrip('h'), 16)
                                dlc = int(tokens[7])
                                dlc, data = self._parse_payload(dlc, tokens[8:], msg_type)
                            elif tokens[4] in ('Rx', 'Tx'): # Format without bus number
                                bus_num = 1
                                id_text = tokens[3]
                                can_id = int(tokens[3].rstrip('h'), 16)
                                dlc = int(tokens[5])
                                dlc, data = self._parse_payload(dlc, tokens[6:], msg_type)
                            else:
                                continue

                        # Format 3: PCAN-View (e.g., "1) 123.4 Rx 0100 8 00 ...")
                        elif len(tokens) > 4 and tokens[2] in ('Rx', 'Tx'):
                            bus_num = 1
                            id_text = tokens[3]
                            can_id = int(tokens[3].rstrip('h'), 16)
                            dlc = int(tokens[4])
                            data = bytes.fromhex("".join(tokens[5:5+dlc]))
                            msg_type = 'FD' if dlc > 8 else 'DT'

                        # TRC 1.0: "1) 1841 0001 8 00 ..." (no bus or direction column)
                        elif (len(tokens) > 3 and tokens[0].endswith(')') and tokens[3].isdigit()
                              and int(tokens[3]) <= 8 and re.fullmatch(r'[0-9A-Fa-f]{1,8}', tokens[2])):
                            bus_num = 1
                            id_text = tokens[2]
                            can_id = int(id_text, 16)
                            dlc = int(tokens[3])
                            data = bytes.fromhex("".join(tokens[4:4 + dlc]))
                            if len(data) != dlc:
                                continue
                            msg_type = 'DT'
                        else:
                            continue
                        
                        log_line_data = {"timestamp": ts, "offset_ms": offset_ms, "type": msg_type, "bus": bus_num, "can_id": can_id, "dlc": dlc, "data": data}
                        raw_log_lines.append(log_line_data)
                        log_line_data['source_line'] = i
                        # PEAK traces pad 29-bit IDs to 8 hex digits and 11-bit IDs to 3-4 digits.
                        id_digits = id_text.rstrip('h')
                        log_line_data['is_extended_id'] = len(id_digits) > 4 or can_id > 0x7FF
                        if logger_columns:
                            log_line_data['format'] = 'cl2000'
                            log_line_data['is_extended_id'] = logger_extended
                            log_line_data['direction'] = logger_direction

                        self._decode_entry(signal_data, found_msgs, bus_num, can_id, data, ts)
                    except (ValueError, IndexError):
                        continue

            if not self._is_cancelled:
                self.progress_signal.emit(100)
                self.finished_signal.emit(signal_data, found_msgs, raw_log_lines)
        except Exception as e:
            self.last_error_details = diagnostic_details("로그 파일 파싱 / LogParserThread.run")
            self.error_signal.emit(str(e))
