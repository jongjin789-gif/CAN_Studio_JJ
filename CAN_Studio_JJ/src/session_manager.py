from src.bus_config import BUS_IDS, PACKET_BUS_IDS
"""Main-window session coordinator: validate first, then replace and reconnect."""
import copy
import json
import hashlib
import os
import platform
import sys
from src.gateway import normalized_rules
from src.trigger_capture import TriggerConfig
from pathlib import Path
import uuid
from types import SimpleNamespace

from PyQt5.QtCore import QObject, Qt, QTimer
from PyQt5.QtWidgets import QFileDialog, QMessageBox

from src.session_storage import (EXTENSION, FORMAT, atomic_write, decode_db, encode_db,
                                 read_session, serialize, validate_structure, write_session,
                                 validate_graph, validate_panel)
from src.session_graph import graph_state, restore_graph, restore_geometry
from src.graph_labels import update_realtime_labels


def device_identity(data):
    if not isinstance(data, dict) or data.get('handle') is None:
        return None
    handle = data['handle']
    handle = getattr(handle, 'value', handle)
    identity = dict(bustype=data.get('bustype', 'pcan'), handle=handle)
    for key in ('device_id', 'device_type', 'controller_number', 'serial', 'dongle_channel'):
        if key in data:
            identity[key] = data[key]
    return identity


def same_device(first, second):
    """CANlib channel numbers can change on rescan; match the physical port."""
    if first is None or second is None:
        return False
    if (first.get('bustype') == second.get('bustype') == 'kvaser'
            and all(key in item for item in (first, second) for key in ('serial', 'dongle_channel'))):
        return all(first[key] == second[key] for key in ('serial', 'dongle_channel'))
    # gs_usb device numbers follow USB enumeration order; the serial number identifies the adapter.
    if first.get('bustype') == second.get('bustype') == 'gs_usb' and first.get('serial') and second.get('serial'):
        return first['serial'] == second['serial']
    return first == second


class SessionManager(QObject):
    def __init__(self, main, storage_dir=None):
        super().__init__(main)
        self.main = main
        # 개인용 프로그램이므로 세션/설정 파일을 AppData가 아닌 실행 파일(또는 소스) 위치에 둔다.
        base = storage_dir or (
            os.path.dirname(sys.executable) if getattr(sys, 'frozen', False)
            else os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        self.directory = Path(base)
        self.autosave_path = self.directory / ('session' + EXTENSION)
        self.busy = False
        self.ready = False
        self.enabled = True
        self.errors = None
        self.last_digest = None
        self.pending_can = {}
        self.timer = QTimer(self)
        self.timer.setInterval(5000)
        self.timer.timeout.connect(self.autosave)

    def start(self):
        if self.main.viewer_only:
            return
        if self.autosave_path.exists():
            if not self.load(self.autosave_path, startup=True):
                # Keep the damaged file available for recovery; don't overwrite it.
                self.enabled = False
        else:
            self.main.tx_panel.auto_load_packets()
        self.ready = True
        self.timer.start()

    def report(self, title, summary, issues):
        if self.main.user_panel_window is not None:
            self.main.user_panel_window.log_system(summary + ': ' + ' | '.join(issues), 'WARN')
        box = QMessageBox(self.main.user_panel_window or self.main)
        box.setWindowTitle(title)
        box.setIcon(QMessageBox.Warning)
        box.setText(summary)
        box.setInformativeText('\n'.join(issues[:8]))
        if len(issues) > 8:
            box.setDetailedText('\n'.join(issues))
        box.exec_()

    def _backend_key(self, bus):
        """CAN 방식(장치 모듈) 키. 이전 버전 파일에는 없으며 device.bustype으로 대신 판단합니다."""
        method = getattr(self.main, 'selected_method', None)
        if callable(method):
            return method(bus)          # a module key, or 'all' for the ALL method
        selected = getattr(self.main, 'selected_backend', None)
        return selected(bus).key if callable(selected) else None

    def _select_backend(self, bus, cfg):
        """저장된 CAN 방식이 이 OS에서 선택 가능하면 콤보를 맞추고 해당 방식으로 채널을 다시 찾습니다."""
        m = self.main
        devices, combo = getattr(m, 'devices', None), getattr(m, 'combo_backend', {}).get(bus)
        if devices is None or combo is None:
            return
        key = cfg.get('backend') or devices.key_for_device(cfg.get('device'))
        index = combo.findData(key) if key else -1
        if index >= 0 and index != combo.currentIndex():
            combo.setCurrentIndex(index)  # on_backend_changed -> 채널 재검색

    def capture_can(self, bus):
        m = self.main
        cfg = dict(backend=self._backend_key(bus),
                   device=device_identity(m.combo_channels[bus].currentData()),
                   channel=m.combo_channels[bus].currentText(),
                   bitrate=m.combo_bitrate[bus].currentText(),
                   fd_iso=m.combo_fd_iso[bus].currentText(),
                   data_bitrate=m.combo_data_bitrate[bus].currentText(),
                   is_open=m.buses[bus] is not None)
        pending = self.pending_can.get(bus)
        if pending and cfg == pending[1]:
            # Retain unavailable FD settings and device identity for another PC.
            desired = copy.deepcopy(pending[0])
            desired['is_open'] = cfg['is_open']
            return desired
        self.pending_can.pop(bus, None)
        return cfg

    def capture(self):
        m = self.main
        databases = {}
        for bus, listing in m.list_db_files.items():
            entries = []
            for i in range(listing.count()):
                item = listing.item(i)
                raw = item.data(Qt.UserRole + 2)
                if raw is None:
                    raise ValueError(f'BUS {bus}: {item.text()} 원문이 없습니다. DB를 다시 등록하세요.')
                ident = item.data(Qt.UserRole + 3)
                entries.append(encode_db(item.text(), bytes(raw), ident))
            databases[str(bus)] = entries
        packets = []
        for i in range(m.tx_panel.tree.topLevelItemCount()):
            packet = copy.deepcopy(m.tx_panel.tree.topLevelItem(i).packet_data)
            packet['count'] = 0
            packets.append(packet)
        graphs = [graph_state(g) for g in m.active_graphs
                  if g.isVisible() or getattr(g, 'is_in_combined_view', False) or getattr(g, '_panel_only_hidden', False)]
        panel = m.user_panel_window
        panel_state = None
        if panel is not None:
            panel_state = dict(data=copy.deepcopy(panel._panel_data()), visible=panel.isVisible(),
                               geometry=panel.geometry().getRect())
            panel_state['data']['mode'] = 'standby'
        return dict(format=FORMAT, version=2, databases=databases,
                    can={str(b): self.capture_can(b) for b in BUS_IDS},
                    tx_packets=packets, graphs=graphs, panel=panel_state,
                    ui=dict(dbc_collapsed=m.db_group.is_collapsed(), can_collapsed=m.conn_group.is_collapsed()),
                    tools=self.capture_tools())

    def capture_tools(self):
        """Gateway rules, trigger and timeout settings (optional key; older files lack it)."""
        m = self.main
        trigger = m.trigger.config
        return dict(gateway_rules=copy.deepcopy(m.gateway.rules),
                    trigger=trigger.to_dict() if trigger is not None else None,
                    timeout=dict(m.timeout_monitor.settings))

    def apply_tools(self, tools):
        """Restore tool settings. Invalid values are ignored; the gateway always starts off."""
        m = self.main
        tools = tools if isinstance(tools, dict) else {}
        try:
            m.set_gateway(normalized_rules(tools.get('gateway_rules')), False)
        except ValueError:
            m.set_gateway([], False)
        m.trigger.set_config(TriggerConfig.from_dict(tools.get('trigger')))
        m.timeout_monitor.configure(tools.get('timeout'))

    def autosave(self):
        if (not self.ready or not self.enabled or self.busy or self.main.viewer_only
                or getattr(self.main, '_is_closing', False)):
            return
        try:
            raw = serialize(self.capture())
            digest = hashlib.sha256(raw).digest()
            if digest != self.last_digest:
                atomic_write(self.autosave_path, raw)
                self.last_digest = digest
        except Exception as exc:
            self.main.statusBar().showMessage(f'설정 자동 저장 실패: {exc}', 10000)

    def save_dialog(self):
        path, _ = QFileDialog.getSaveFileName(self.main, '통합 설정 저장',
                    str(self.directory / ('settings' + EXTENSION)), f'통합 설정 (*{EXTENSION})')
        if not path:
            return
        if not path.lower().endswith(EXTENSION):
            path += EXTENSION
        try:
            write_session(path, self.capture())
            self.enabled = True
            self.autosave()
            self.main.statusBar().showMessage(f'통합 설정 저장 완료: {path}', 10000)
        except Exception as exc:
            self.report('저장 실패', '통합 설정을 저장하지 못했습니다.', [str(exc)])

    def load_dialog(self):
        path, _ = QFileDialog.getOpenFileName(self.main, '통합 설정 불러오기',
                    str(self.directory), f'통합 설정 (*{EXTENSION})')
        if path:
            self.load(path)

    def prepare(self, data, strict=True):
        """Build hidden replacement widgets with disconnected CAN, without touching live state."""
        from src.graph_realtime import SignalGraphWindow
        from src.user_panel import UserPanelWindow
        from src.user_panel_v2.packets import validate_packet, UnregisteredPacketError
        from src.user_panel_v2.sequence import validate_steps
        data = validate_structure(data)
        prepared = dict(databases={}, messages={b: {} for b in BUS_IDS}, graphs=[], panel=None)
        issues = []
        for bus in BUS_IDS:
            prepared['databases'][bus] = []
            for entry in data['databases'][str(bus)]:
                try:
                    raw = decode_db(entry)
                    db = self.main.parse_database_bytes(entry['name'], raw)
                    prepared['databases'][bus].append((entry, raw, db))
                    for msg in db.messages:
                        prepared['messages'][bus].setdefault(msg.frame_id, msg)
                except Exception as exc:
                    issues.append(f'BUS {bus} / {entry["name"]}: {exc}')
        for i, packet in enumerate(data['tx_packets']):
            try:
                if packet['bus'] not in PACKET_BUS_IDS:
                    raise ValueError('BUS는 미선택 또는 1~4이어야 합니다.')
                if not isinstance(packet.get('symbol'), str) or not isinstance(packet.get('note'), str):
                    raise ValueError('심볼과 메모는 문자열이어야 합니다.')
                validate_packet(packet, prepared['messages'])
            except Exception as exc:
                issues.append(f'TX {i + 1}: {exc}')
        for i, state in enumerate(data['graphs']):
            graph = None
            try:
                validate_graph(state)
                for key, binding in state['bindings'].items():
                    self.resolve_binding(binding, prepared['databases'])
                graph = SignalGraphWindow(state['signals'], main_window=None)
                restore_graph(graph, state)
                prepared['graphs'].append(graph)
            except Exception as exc:
                if graph:
                    graph.close()
                    graph.deleteLater()
                issues.append(f'그래프 {i + 1}: {exc}')
        if data['panel'] is not None:
            try:
                validate_panel(data['panel']['data'])
                # No connected hardware or callbacks during validation/construction.
                from .user_panel_v2.dbc_identity import database_token
                db_signature = {str(bus): [database_token(entry['name'], raw)
                    for entry, raw, db in prepared['databases'][bus]] for bus in BUS_IDS}
                proxy = SimpleNamespace(buses={b: None for b in BUS_IDS},
                                        db_messages=prepared['messages'], database_content_signature=db_signature)
                panel = UserPanelWindow(proxy, prepared['messages'],
                                        security_config=self.main.user_panel_security)
                prepared['panel'] = panel
                panel._load_panel_data(copy.deepcopy(data['panel']['data']))
                if panel.dbc_signature is None:
                    panel.dbc_signature = db_signature
                if strict:
                    # Restore editable, unfinished tools without allowing transmission.
                    # Packet corruption still rejects the file before readiness checks.
                    for packet in panel.tx_packets:
                        validate_packet(packet, prepared['messages'])
                    try:
                        panel._prepare_registered_packets()
                    except UnregisteredPacketError as exc:
                        panel.log_system(f'패널 복원 완료 / 송신 준비 미완료: {exc} RUN 전에 TX 패킷 등록/연결이 필요합니다.', 'WARN')
                for cfg in panel.widgets_config:
                    if cfg.get('widget_type') == 'sequence':
                        binding = cfg.get('binding', {})
                        validate_steps(binding.get('sequence_steps', []), allow_empty=True)
                        validate_steps(binding.get('sequence_failure_steps', []), actions_only=True, allow_empty=True)
                panel.stop_panel_commands('통합 복원')
                panel._packet_runtimes.clear()
                panel.mode = 'standby'
                panel.refresh_mode_ui()
                restore_geometry(panel, data['panel'].get('geometry'))
            except Exception as exc:
                issues.append(f'UserPanel: {exc}')
        if issues:
            self.dispose(prepared)
            raise ValueError('\n'.join(issues))
        return prepared

    @staticmethod
    def dispose(prepared):
        from PyQt5 import sip
        for window in prepared.get('graphs', []) + ([prepared['panel']] if prepared.get('panel') else []):
            if not sip.isdeleted(window):
                window.close()
                window.deleteLater()

    @staticmethod
    def resolve_binding(binding, databases):
        for entry, raw, db in databases[int(binding['bus'])]:
            if entry['id'] != binding['database']:
                continue
            for msg in db.messages:
                if msg.frame_id == binding['id'] and msg.is_extended_frame == binding['extended']:
                    return msg, msg.get_signal_by_name(binding['signal'])
        raise ValueError(f'그래프 신호를 찾을 수 없습니다: {binding}')

    def bind_signal(self, bus, can_id, signal):
        listing = self.main.list_db_files[bus]
        active = self.main.db_messages[bus].get(can_id)
        for i in range(listing.count()):
            item = listing.item(i)
            for msg in item.data(Qt.UserRole).messages:
                if msg is active and any(s.name == signal for s in msg.signals):
                    ident = item.data(Qt.UserRole + 3)
                    key = f'B{bus}:{can_id:X}:{int(msg.is_extended_frame)}:{signal}'
                    return key, dict(bus=bus, database=ident, id=can_id,
                                     extended=msg.is_extended_frame, signal=signal)
        raise ValueError(f'BUS {bus} / {can_id:X} / {signal}: DB 신호를 찾을 수 없습니다.')

    def stop_all(self):
        m = self.main
        m.tx_panel.stop_all_timers()
        panel = m.user_panel_window
        if panel is not None:
            panel.stop_panel_commands('전체 송신 정지')
            panel._sim_timer.stop()
            panel.mode = 'standby'
            panel.refresh_mode_ui()
        for viewer in list(m.log_viewers):
            viewer.stop_sending()

    def preserve_record(self):
        record = self.main.record_window
        from PyQt5 import sip
        if record is not None and sip.isdeleted(record):
            self.main.record_window = None
            return
        if record and record.isVisible() and not record.is_saved and record.msg_count:
            # Write before stopping/closing anything; a disk failure leaves the recording open.
            folder = self.directory / 'Log'
            target = folder / f'CAN_Log_{uuid.uuid4().hex}.trc'
            record._write_trc(target)
            record.is_saved = True

    def new_screen(self):
        """Stop communication and replace the workspace with startup defaults."""
        if self.busy:
            return False
        try:
            self.preserve_record()
        except Exception as exc:
            self.report('새 화면 실패', '기록을 보존하지 못해 현재 화면을 유지했습니다.', [str(exc)])
            return False
        self.busy = True
        self.errors = []
        m = self.main
        try:
            self.clear_live()
            m.log_viewers.clear()
            self.pending_can.clear()
            m.signal_display_decimals.clear()
            m.bus_issues.clear()
            m._pending_panel_events.clear()
            for dialog in list(m._bus_log_windows.values()):
                from PyQt5 import sip
                if not sip.isdeleted(dialog):
                    dialog.close()
            m._bus_log_windows.clear()
            for bus in BUS_IDS:
                m.bus_events[bus].clear()
                m.combo_channels[bus].setCurrentIndex(-1)
                m.on_channel_changed(bus)
                m.combo_fd_iso[bus].setCurrentIndex(0)
                m.bus_state_changed.emit(bus)
            m.search_input.clear()
            m.db_group.set_collapsed(False)
            m.conn_group.set_collapsed(False)
            m.btn_open_log.setEnabled(False)
            m.btn_record.setEnabled(False)
            if hasattr(m.tx_panel, '_packet_clipboard'):
                m.tx_panel._packet_clipboard = None
            self.enabled = True
            self.last_digest = None
            if getattr(m, 'user_panel_only', False):
                m.open_user_panel()
        finally:
            self.busy = False
            issues, self.errors = self.errors, None
        self.autosave()
        m.statusBar().showMessage('새 화면 · 통신 및 전체 설정 초기화 완료', 5000)
        if issues:
            self.report('새 화면', '화면을 초기화했습니다. 종료 중 발생한 항목을 확인하세요.', issues)
        return True

    def clear_live(self):
        m = self.main
        self.stop_all()
        m._session_generation += 1
        for viewer in list(m.log_viewers):
            viewer.close()
            viewer.deleteLater()
        if m.record_window:
            m.record_window.close()
            m.record_window = None
        if m.combined_view_window:
            combined = m.combined_view_window
            combined.close()  # Close combined graphs before the remaining windows.
            combined.deleteLater()
        for graph in list(m.active_graphs):
            graph.close()
            graph.deleteLater()
        m.active_graphs = []
        m.synced_graphs_ordered = []
        m.combined_view_window = None
        if m.user_panel_window:
            m.user_panel_window.close()
            m.user_panel_window.deleteLater()
            m.user_panel_window = None
        m.close_can()
        m.trigger.clear()
        m._trigger_item = None
        m.tree.clear()
        m.signal_tree_items.clear()
        m.signal_choices.clear()
        m.msg_tree_items.clear()
        m.user_tx_cache.clear()
        m.user_frame_properties.clear()
        m._graph_pending_frames.clear()
        m.tx_panel.tree.clear()
        for bus in BUS_IDS:
            m.list_db_files[bus].clear()
            m.db_messages[bus].clear()

    def apply_can(self, bus, cfg):
        m = self.main
        self._select_backend(bus, cfg)
        combo = m.combo_channels[bus]
        desired = cfg['device']
        index = next((i for i in range(combo.count())
                      if same_device(device_identity(combo.itemData(i)), desired)), -1)
        if index < 0:
            combo.setCurrentIndex(-1)
        else:
            combo.setCurrentIndex(index)
        m.on_channel_changed(bus)
        errors = []
        if desired is not None and index < 0:
            errors.append('저장된 장치를 찾을 수 없습니다')
        if desired is None and cfg['is_open']:
            errors.append('장치 정보가 없습니다')
        fd = cfg['data_bitrate'] not in ('Off', 'N/A', '')
        if fd and index >= 0 and not (combo.currentData() or {}).get('is_fd', False):
            errors.append('장치가 CAN FD를 지원하지 않습니다')
        for widget, value in ((m.combo_bitrate[bus], cfg['bitrate']),
                              (m.combo_fd_iso[bus], cfg['fd_iso']),
                              (m.combo_data_bitrate[bus], cfg['data_bitrate'])):
            i = widget.findText(value)
            if i < 0 and value in ('Off', 'N/A'):
                i = widget.findText('Off' if value == 'N/A' else 'N/A')
            if i < 0:
                if index >= 0:
                    errors.append(f'지원하지 않는 설정: {value}')
                widget.addItem(value, None)
                widget.setCurrentIndex(widget.count() - 1)
            else:
                widget.setCurrentIndex(i)
        reconnect = cfg['is_open'] and platform.system() != 'Linux'
        if not errors and reconnect:
            try:
                m.open_can(bus)
            except Exception as exc:
                errors.append(str(exc))
            if m.buses[bus] is None:
                errors.append('재연결에 실패했습니다')
        if errors:
            m.close_can(bus)
            m.log_bus_event(bus, '설정 복원: ' + '; '.join(errors), 'ERROR')
            self.errors.append(f'BUS {bus}: Close — ' + '; '.join(errors))
        actual = dict(backend=self._backend_key(bus),
                      device=device_identity(combo.currentData()), channel=combo.currentText(),
                      bitrate=m.combo_bitrate[bus].currentText(), fd_iso=m.combo_fd_iso[bus].currentText(),
                      data_bitrate=m.combo_data_bitrate[bus].currentText(), is_open=m.buses[bus] is not None)
        if errors:
            self.pending_can[bus] = (copy.deepcopy(cfg), actual)

    def apply(self, data, prepared):
        m = self.main
        self.clear_live()
        m.bus_issues.clear()
        m.db_group.set_collapsed(data.get('ui', {}).get('dbc_collapsed', False))
        m.conn_group.set_collapsed(data.get('ui', {}).get('can_collapsed', False))
        m.search_can_channels()
        self.pending_can.clear()
        for bus in BUS_IDS:
            for entry, raw, db in prepared['databases'][bus]:
                m.install_database(db, bus, entry['name'], raw, '', entry['id'])
        for packet in data['tx_packets']:
            packet = copy.deepcopy(packet)
            packet['count'] = 0
            m.tx_panel.add_packet_to_tree(packet)
        panel = prepared['panel']
        if panel:
            panel.main_window = m
            panel.db_messages = m.db_messages
            m.user_panel_window = panel
            if data['panel'].get('visible', False) or getattr(m, 'user_panel_only', False):
                m.open_user_panel()
        for graph in prepared['graphs']:
            graph.main_window = m
            graph._bound_signals = m.resolve_graph_bindings(graph.signal_bindings)
            update_realtime_labels(graph, preserve_custom=True)
            m.active_graphs.append(graph)
            graph._panel_only_hidden = bool(getattr(m, 'user_panel_only', False))
            if not getattr(m, 'user_panel_only', False):
                graph.show()
        for bus in BUS_IDS:
            self.apply_can(bus, data['can'][str(bus)])
            m.bus_state_changed.emit(bus)
        m.btn_open_log.setEnabled(any(m.db_messages.values()))
        m.tx_panel.update_all_action_buttons()
        self.apply_tools(data.get('tools'))

    def load(self, path, startup=False):
        if self.main.viewer_only or self.busy:
            return False
        prepared = backup_prepared = None
        try:
            data = read_session(path)
            prepared = self.prepare(data)
            backup = self.capture()
            backup_prepared = self.prepare(backup, strict=False)
            self.preserve_record()
            original = Path(path).read_bytes()
            if json.loads(original).get('version') == 1:
                legacy_backup = Path(str(path) + '.v1.bak')
                if not legacy_backup.exists():
                    atomic_write(legacy_backup, original)
        except Exception as exc:
            if prepared:
                self.dispose(prepared)
            if backup_prepared:
                self.dispose(backup_prepared)
            self.report('불러오기 실패', '기존 설정과 창을 유지했습니다.', str(exc).splitlines())
            return False
        self.busy = True
        self.errors = []
        success = False
        try:
            self.apply(data, prepared)
            success = True
        except Exception as exc:
            self.errors.append(f'설정 적용 실패: {exc}')
            try:
                self.apply(backup, backup_prepared)
                backup_prepared = None
                self.errors.append('이전 설정을 복원했습니다. 송신은 정지 상태이며 로그뷰는 다시 열어야 합니다.')
            except Exception as rollback_exc:
                self.enabled = False
                self.errors.append(f'이전 설정 복구 실패: {rollback_exc}. 자동 저장을 중지했습니다.')
            # Staged windows not yet attached when apply failed also need disposal.
            self.dispose(prepared)
        finally:
            if backup_prepared:
                self.dispose(backup_prepared)
            self.busy = False
        if success:
            self.enabled = True
            try:
                raw = serialize(self.capture())
                atomic_write(self.autosave_path, raw)
                self.last_digest = hashlib.sha256(raw).digest()
            except Exception as exc:
                self.errors.append(f'화면 적용은 완료했지만 자동 복원 파일 저장 실패: {exc}')
            self.main.statusBar().showMessage('통합 설정 적용 완료 · 전체 송신 정지', 10000)
        issues, self.errors = self.errors, None
        if startup:
            issues = [issue for issue in issues if '저장된 장치를 찾을 수 없습니다' not in issue]
        if issues:
            status = [f'BUS {b}: {"Open" if self.main.buses[b] else "Close"}' for b in BUS_IDS]
            self.report('통합 불러오기 결과',
                        '설정을 적용했습니다. 아래 항목을 확인하세요.' if success else '설정 적용에 실패했습니다.',
                        issues + status)
        return success

    def export_databases(self, bus=None, selected=False):
        m = self.main
        entries = []
        for b in ((bus,) if bus else BUS_IDS):
            listing = m.list_db_files[b]
            items = listing.selectedItems() if selected else [listing.item(i) for i in range(listing.count())]
            entries.extend((b, item) for item in items)
        if not entries:
            m.statusBar().showMessage('내보낼 DB를 선택/등록하세요.', 5000)
            return
        folder = QFileDialog.getExistingDirectory(m, 'DBC/SYM 내보내기', str(self.directory))
        if not folder:
            return
        issues = []
        for b, item in entries:
            try:
                target_dir = Path(folder) / f'BUS{b}'
                target_dir.mkdir(parents=True, exist_ok=True)
                name = Path(item.text()).name
                raw = item.data(Qt.UserRole + 2)
                if raw is None:
                    raise ValueError('DB 원문이 없습니다.')
                stem, suffix = os.path.splitext(name)
                for n in range(100000):
                    target = target_dir / (name if n == 0 else f'{stem}_{n}{suffix}')
                    try:
                        with target.open('xb') as stream:
                            stream.write(bytes(raw))
                        break
                    except FileExistsError:
                        continue
                else:
                    raise ValueError('중복되지 않는 파일명을 만들 수 없습니다.')
            except Exception as exc:
                issues.append(f'BUS {b} / {item.text()}: {exc}')
        if issues:
            self.report('DB 내보내기 결과', '일부 파일을 내보내지 못했습니다.', issues)
        else:
            m.statusBar().showMessage(f'DB {len(entries)}개 내보내기 완료: {folder}', 10000)
