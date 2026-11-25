"""조향모터(AQMD6008NS-TBE) CANopen 유저 패널 생성 스크립트.

근거: Doc 조향모터_CAN_명령_시퀀스.md (1장 1회 설정, 2장 부팅 명령, 3장 원점/위치 제어)
출력: USER_PANEL/Steering_AQMD_CANopen.upp.json, USER_PANEL/Steering_AQMD_CANopen.pjjupkg

시퀀스 규칙: SDO는 CMD -> RCV(응답) -> DEL 10 ms. 응답이 없는 명령 사이에도 DEL 10 ms를 넣는다.
CMD와 그 응답 RCV 사이에는 DEL을 넣지 않는다(지연 중 도착한 응답을 놓치지 않도록).

노드 ID나 BUS를 바꾸려면 아래 NODES / BUS를 수정하고 다시 실행한다.
    python USER_PANEL/build_steering_panel.py
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

BUS = 1
NODES = [("FL", 1), ("FR", 2), ("RL", 3), ("RR", 4)]   # 펌웨어 기준 노드 ID
FACTORY_ID = 1                                        # 출하 상태 노드 ID
TITLE = "조향모터 AQMD6008NS CANopen"
OUT_JSON = ROOT / "USER_PANEL" / "Steering_AQMD_CANopen.upp.json"
OUT_PKG = ROOT / "USER_PANEL" / "Steering_AQMD_CANopen.pjjupkg"

GAP_MS = 10            # 요청: 명령/수신 다음 10 ms
SDO_TIMEOUT_MS = 200   # 문서 100 ms + 재시도 없음 보완
NMT_WAIT_MS = 50       # 문서 2.1: NMT 송신 후 50 ms
SAVE_WAIT_MS = 500     # 문서 1.2: 저장 응답 후 500 ms

# 위치/운전 파라미터 기본값 (문서 2.3, 3.3)
DEF_SPEED = 500
DEF_TARGET = 80050
DEF_ACC = 8000
DEF_DEC = 8000
DEF_MAX = 1500
POS_MIN, POS_RES, POS_STEPS = 500, 32, 4971          # 슬라이더 최대 5000단계 제한
POS_MAX = POS_MIN + POS_RES * POS_STEPS              # 159572


def le(value, n):
    return list(int(value & ((1 << (8 * n)) - 1)).to_bytes(n, "little"))


# ---------------------------------------------------------------- 패킷
packets = {}


def packet(can_id, length, data, note):
    key = f"pkt-b{BUS}-{can_id:03x}"
    if key not in packets:
        packets[key] = {
            "packet_id": key, "bus": BUS, "id": can_id,
            "is_extended_id": False, "is_fd": False, "is_brs": False,
            "length": length, "data": list(data), "cycle": 0,
            "symbol": "N/A", "dbc_manual": True, "note": note,
            "count": 0, "crc_type": "N/A", "signal_counters": {},
        }
    return packets[key]


def nmt_packet():
    return packet(0x000, 2, [0, 0], "NMT [명령, 노드ID]")


def sdo_packet(node):
    return packet(0x600 + node, 8, [0] * 8, f"SDO 요청 ID{node}")


def rpdo1_packet(node):
    data = [0x10] + le(DEF_SPEED, 2) + [0] + le(DEF_TARGET, 4)
    return packet(0x200 + node, 8, data, f"RPDO1 ID{node}: 모드/속도/기준/목표위치")


def rpdo2_packet(node):
    data = le(DEF_ACC, 2) + le(DEF_DEC, 2) + le(DEF_MAX, 2)
    return packet(0x300 + node, 6, data, f"RPDO2 ID{node}: 가속/감속/최대속도")


# ---------------------------------------------------------------- 단계
def cmd(pkt, data, name, mask=None):
    full = [255] * pkt["length"]
    return {
        "kind": "CMD", "name": name, "packet": dict(pkt, data=list(data)),
        "repeat": 1, "data_override": list(data),
        "data_mask": list(mask if mask is not None else full),
        "input_format": "BIN" if mask is not None else "HEX",
    }


def rcv(can_id, length, data, mask, name, timeout=SDO_TIMEOUT_MS):
    return {
        "kind": "RCV", "name": name,
        "packet": {
            "bus": BUS, "id": can_id, "is_extended_id": False, "is_fd": False,
            "is_brs": False, "length": length, "data": list(data), "cycle": 0,
            "symbol": "N/A", "dbc_manual": True, "note": "", "count": 0,
            "crc_type": "N/A", "signal_counters": {},
        },
        "mask": list(mask), "timeout_ms": timeout, "direct_input": True,
        "input_format": "BIN", "allow_length_mismatch": False,
    }


def dl(ms, name=None):
    return {"kind": "DEL", "name": name or f"대기 {ms} ms", "delay_ms": ms}


def sdo(node, cs, index, sub, value, name, wait=GAP_MS, tx_node=None):
    """SDO 쓰기/읽기: CMD -> RCV -> DEL. cs: 0x2F/0x2B/0x23 쓰기, 0x40 읽기."""
    tx = node if tx_node is None else tx_node
    nbytes = {0x2F: 1, 0x2B: 2, 0x27: 3, 0x23: 4, 0x40: 0}[cs]
    payload = le(value, nbytes) + [0] * (4 - nbytes)
    head = [cs] + le(index, 2) + [sub]
    expect = 0x4B if cs == 0x40 else 0x60
    return [
        cmd(sdo_packet(tx), head + payload, name),
        rcv(0x580 + tx, 8, [expect] + head[1:] + [0] * 4, [255] * 4 + [0] * 4,
            f"{name} 응답 {expect:02X}"),
        dl(wait),
    ]


def nmt(node, command, name):
    return [cmd(nmt_packet(), [command, node], name), dl(NMT_WAIT_MS)]


def tpdo2_mode(node, mode, name, timeout=500):
    data = [0] * 8
    data[6] = mode
    mask = [0] * 8
    mask[6] = 255
    return rcv(0x280 + node, 8, data, mask, name, timeout)


def rpdo1_mode(node, mode, name):
    return cmd(rpdo1_packet(node), [mode] + [0] * 7, name, mask=[255] + [0] * 7)


# ---------------------------------------------------------------- 시퀀스 정의
def one_time_setup(node):
    s = []
    s += sdo(node, 0x2B, 0x4120, 0, 1, "통신모드=CANopen (0x0120)", tx_node=FACTORY_ID)
    s += sdo(node, 0x2B, 0x4128, 0, 0, "자동기동 끔 (0x0128)", tx_node=FACTORY_ID)
    s += sdo(node, 0x2B, 0x4129, 0, 100, "하트비트 100ms (0x0129)", tx_node=FACTORY_ID)
    s += sdo(node, 0x2B, 0x4121, 0, node, f"노드ID={node} (0x0121)", tx_node=FACTORY_ID)
    s += sdo(node, 0x2B, 0x41E0, 0, 1, "CAN 설정 저장 (0x01E0)", SAVE_WAIT_MS, tx_node=FACTORY_ID)
    s += sdo(node, 0x2B, 0x41E8, 0, 1, "CANopen 설정 저장 (0x01E8)", SAVE_WAIT_MS, tx_node=FACTORY_ID)
    return s


def save_at_new_id(node):
    s = []
    s += sdo(node, 0x2B, 0x41E0, 0, 1, "CAN 설정 저장 (0x01E0)", SAVE_WAIT_MS)
    s += sdo(node, 0x2B, 0x41E8, 0, 1, "CANopen 설정 저장 (0x01E8)", SAVE_WAIT_MS)
    return s


def stroke_measure(node):
    s = []
    s += sdo(node, 0x2F, 0x2000, 0, 0x10, "제어모드=정상정지 (0x2000)")
    s += sdo(node, 0x2B, 0x40CF, 0, 4, "스트로크 측정 시작 (0x00CF=4)")
    return s


def cob(base, node, stop=False):
    return (base + node) | (0x80000000 if stop else 0)


def boot_init(node):
    s = []
    s += nmt(node, 0x80, "NMT Pre-Operational")
    s += sdo(node, 0x23, 0x1016, 1, 0, "마스터 하트비트 감시 OFF (0x1016)")
    s += sdo(node, 0x23, 0x1006, 0, 0, "SYNC 감시 OFF (0x1006)")
    s += sdo(node, 0x2B, 0x408E, 0, 0, "통신단절 정지 OFF (0x008E)")
    s += sdo(node, 0x2F, 0x2000, 0, 0x10, "제어모드=정상정지 (0x2000)")

    # TPDO1: 위치 / 회전속도, 20 ms
    s += sdo(node, 0x23, 0x1800, 1, cob(0x180, node, True), "TPDO1 정지")
    s += sdo(node, 0x2F, 0x1A00, 0, 0, "TPDO1 매핑 해제")
    s += sdo(node, 0x23, 0x1A00, 1, 0x21050020, "TPDO1 맵1 위치 0x2105")
    s += sdo(node, 0x23, 0x1A00, 2, 0x210A0020, "TPDO1 맵2 속도 0x210A")
    s += sdo(node, 0x2F, 0x1A00, 0, 2, "TPDO1 매핑 2개")
    s += sdo(node, 0x2F, 0x1800, 2, 0xFF, "TPDO1 전송타입 255")
    s += sdo(node, 0x2B, 0x1800, 5, 20, "TPDO1 주기 20ms")
    s += sdo(node, 0x23, 0x1800, 1, cob(0x180, node), "TPDO1 시작")

    # TPDO2: 전류/정지/스톨/고장/리밋/모드/오류, 50 ms
    s += sdo(node, 0x23, 0x1801, 1, cob(0x280, node, True), "TPDO2 정지")
    s += sdo(node, 0x2F, 0x1A01, 0, 0, "TPDO2 매핑 해제")
    for i, (entry, label) in enumerate([
        (0x21000010, "전류 0x2100"), (0x21060008, "정지상태 0x2106"),
        (0x21110008, "스톨 0x2111"), (0x21120008, "고장 0x2112"),
        (0x21140008, "리밋 0x2114"), (0x20000008, "모드 0x2000"),
        (0x10010008, "오류레지스터 0x1001")], 1):
        s += sdo(node, 0x23, 0x1A01, i, entry, f"TPDO2 맵{i} {label}")
    s += sdo(node, 0x2F, 0x1A01, 0, 7, "TPDO2 매핑 7개")
    s += sdo(node, 0x2F, 0x1801, 2, 0xFF, "TPDO2 전송타입 255")
    s += sdo(node, 0x2B, 0x1801, 5, 50, "TPDO2 주기 50ms")
    s += sdo(node, 0x23, 0x1801, 1, cob(0x280, node), "TPDO2 시작")

    # RPDO1: 모드 / 지령값 / 위치기준 / 목표위치
    s += sdo(node, 0x23, 0x1400, 1, cob(0x200, node, True), "RPDO1 정지")
    s += sdo(node, 0x2F, 0x1600, 0, 0, "RPDO1 매핑 해제")
    for i, (entry, label) in enumerate([
        (0x20000008, "모드 0x2000"), (0x20010010, "지령값 0x2001"),
        (0x20020008, "위치기준 0x2002"), (0x20030020, "목표위치 0x2003")], 1):
        s += sdo(node, 0x23, 0x1600, i, entry, f"RPDO1 맵{i} {label}")
    s += sdo(node, 0x2F, 0x1600, 0, 4, "RPDO1 매핑 4개")
    s += sdo(node, 0x2F, 0x1400, 2, 0xFF, "RPDO1 전송타입 255")
    s += sdo(node, 0x23, 0x1400, 1, cob(0x200, node), "RPDO1 시작")

    # RPDO2: 가속 / 감속 / 최대속도
    s += sdo(node, 0x23, 0x1401, 1, cob(0x300, node, True), "RPDO2 정지")
    s += sdo(node, 0x2F, 0x1601, 0, 0, "RPDO2 매핑 해제")
    for i, (entry, label) in enumerate([
        (0x20060010, "가속 0x2006"), (0x20070010, "감속 0x2007"),
        (0x20080010, "최대속도 0x2008")], 1):
        s += sdo(node, 0x23, 0x1601, i, entry, f"RPDO2 맵{i} {label}")
    s += sdo(node, 0x2F, 0x1601, 0, 3, "RPDO2 매핑 3개")
    s += sdo(node, 0x2F, 0x1401, 2, 0xFF, "RPDO2 전송타입 255")
    s += sdo(node, 0x23, 0x1401, 1, cob(0x300, node), "RPDO2 시작")

    # 운전 파라미터 (문서 2.3)
    s += sdo(node, 0x2B, 0x2008, 0, DEF_MAX, f"최대속도 {DEF_MAX} RPM (0x2008)")
    s += sdo(node, 0x2B, 0x2006, 0, DEF_ACC, f"가속 {DEF_ACC} RPM/s (0x2006)")
    s += sdo(node, 0x2B, 0x2007, 0, DEF_DEC, f"감속 {DEF_DEC} RPM/s (0x2007)")

    # 스트로크 읽기 (값은 SDO 응답값 표시에서 확인)
    s += sdo(node, 0x40, 0x40C2, 0, 0, "스트로크 상위 읽기 (0x00C2)")
    s += sdo(node, 0x40, 0x40C3, 0, 0, "스트로크 하위 읽기 (0x00C3)")

    # Operational 진입 + 하트비트 05 확인
    s.append(cmd(nmt_packet(), [0x01, node], "NMT Operational"))
    s.append(rcv(0x700 + node, 1, [0x05], [0x7F], "하트비트 Operational(05)", 1000))
    return s


def move_seq(node):
    return [rpdo1_mode(node, 0x03, "위치모드(03)로 목표 송신"),
            tpdo2_mode(node, 0x03, "모드 03 확인")]


def stop_seq(node):
    return [rpdo1_mode(node, 0x10, "정상 정지(10)"), tpdo2_mode(node, 0x10, "모드 10 확인")]


def estop_seq(node):
    return [rpdo1_mode(node, 0x11, "비상 정지(11)"), tpdo2_mode(node, 0x11, "모드 11 확인")]


def home_seq(node):
    s = [rpdo1_mode(node, 0x10, "정상 정지(10)"), tpdo2_mode(node, 0x10, "모드 10 확인"), dl(GAP_MS)]
    s += sdo(node, 0x2B, 0x40CF, 0, 3, "SQ2 원점 시작 (0x00CF=3)")
    return s


# ---------------------------------------------------------------- 도구
widgets = []


def sequence(page, wid, title, row, col, rs, cs, steps):
    widgets.append({
        "id": wid, "page_id": page, "widget_type": "sequence", "title": title,
        "title_align": "left", "behavior": "tx", "row": row, "col": col,
        "row_span": rs, "col_span": cs, "parent_id": None, "z_index": 1,
        "binding": {"sequence_steps": steps, "sequence_failure_steps": []},
    })


def tx_binding(pkt, start, length, signed, mn, mx, unit, res, init):
    return {
        "packet_id": pkt["packet_id"], "bus": BUS, "can_id": pkt["id"],
        "dlc": pkt["length"], "is_fd": False, "brs": False,
        "signal_name": None, "dbc_manual": True, "start_bit": start,
        "bit_length": length, "byte_order": "little_endian", "signed": signed,
        "scale": 1, "offset": 0, "min": mn, "max": mx, "unit": unit,
        "tx_resolution": res, "tx_initial_value": init,
        "slider_mapping": "direct", "value_precision_decimals": 3,
    }


def slider(page, wid, title, row, col, cs, binding):
    widgets.append({
        "id": wid, "page_id": page, "widget_type": "slider", "title": title,
        "title_align": "left", "behavior": "tx", "row": row, "col": col,
        "row_span": 4, "col_span": cs, "parent_id": None, "z_index": 1,
        "primary_enabled": True, "tx_commands": [], "binding": binding,
    })


def rx(page, wid, title, parent, row, col, can_id, dlc, start, length,
       signed=False, scale=1, unit="", lamp_pressed=False):
    b = {
        "bus": BUS, "can_id": can_id, "dlc": dlc, "is_fd": False, "brs": False,
        "signal_name": None, "dbc_manual": True, "start_bit": start,
        "bit_length": length, "byte_order": "little_endian", "signed": signed,
        "scale": scale, "offset": 0,
        "min": -(1 << (length - 1)) if signed else 0,
        "max": ((1 << (length - 1)) - 1 if signed else (1 << length) - 1) * scale,
        "unit": unit, "value_precision_decimals": 3,
    }
    if lamp_pressed:   # 극성 Low 설정: 0 = 눌림 -> 램프 ON
        b.update({"rx_on_op": "eq", "rx_on_a": 0, "rx_on_b": 0,
                  "rx_off_op": "eq", "rx_off_a": 1, "rx_off_b": 1})
    widgets.append({
        "id": wid, "page_id": page,
        "widget_type": "status_lamp" if lamp_pressed else "label",
        "title": title, "title_align": "left", "behavior": "rx",
        "row": row, "col": col, "row_span": 3, "col_span": 13,
        "parent_id": parent, "z_index": 1, "binding": b,
    })


pages = [{"id": "page-setup", "name": "1회 설정"}]

# 1회 설정 페이지: 노드별 한 줄 (드라이버 1대씩 연결)
for i, (pos, node) in enumerate(NODES):
    row = i * 11
    sequence("page-setup", f"seq-setup-{node}",
             f"[{pos}] 1회 설정: 출하 ID{FACTORY_ID} → ID{node} (1대만 연결)",
             row, 0, 10, 24, one_time_setup(node))
    sequence("page-setup", f"seq-save-{node}",
             f"[{pos}] 저장 재시도 @ ID{node} (노드ID 즉시 적용 시)",
             row, 24, 10, 24, save_at_new_id(node))
    sequence("page-setup", f"seq-stroke-{node}",
             f"[{pos}] 스트로크 측정 @ ID{node} (SQ2→SQ1 이동!)",
             row, 48, 10, 24, stroke_measure(node))

# 노드별 운전 페이지
for pos, node in NODES:
    page = f"page-{pos.lower()}"
    pages.append({"id": page, "name": f"{pos} (ID{node})"})
    p1, p2 = rpdo1_packet(node), rpdo2_packet(node)
    sdo_packet(node)

    sequence(page, f"seq-boot-{node}", f"[{pos}] 부팅 초기화 + PDO 설정 → Operational",
             0, 0, 16, 30, boot_init(node))

    group = f"group-rx-{node}"
    widgets.append({
        "id": group, "page_id": page, "widget_type": "group_box",
        "title": f"[{pos}] 수신 상태 (TPDO1/TPDO2/HB/SDO)", "title_align": "left",
        "behavior": "none", "row": 0, "col": 31, "row_span": 16, "col_span": 41,
        "parent_id": None, "z_index": 0, "binding": {},
    })
    items = [
        ("위치 count", 0x180, 8, 0, 32, True, 1, "cnt", False),
        ("회전속도", 0x180, 8, 32, 32, True, 1, "RPM", False),
        ("상전류", 0x280, 8, 0, 16, False, 0.01, "A", False),
        ("정지상태 0x2106", 0x280, 8, 16, 8, False, 1, "", False),
        ("스톨 (1정/2역)", 0x280, 8, 24, 8, False, 1, "", False),
        ("고장코드", 0x280, 8, 32, 8, False, 1, "", False),
        ("SQ1 눌림", 0x280, 8, 40, 1, False, 1, "", True),
        ("SQ2 눌림", 0x280, 8, 41, 1, False, 1, "", True),
        ("제어모드 (3위치/16정지)", 0x280, 8, 48, 8, False, 1, "", False),
        ("오류레지스터", 0x280, 8, 56, 8, False, 1, "", False),
        ("하트비트 (5 Op/127 PreOp)", 0x700, 1, 0, 8, False, 1, "", False),
        ("SDO 응답값 (B4~B7)", 0x580, 8, 32, 32, False, 1, "", False),
    ]
    for k, (title, base, dlc, start, length, signed, scale, unit, lamp) in enumerate(items):
        rx(page, f"rx-{node}-{k}", title, group, (k // 3) * 3, (k % 3) * 14,
           base + node, dlc, start, length, signed, scale, unit, lamp)

    slider(page, f"sl-target-{node}", "목표 위치 count (입력 후 Set)", 17, 0, 36,
           tx_binding(p1, 32, 32, True, POS_MIN, POS_MAX, "cnt", POS_RES, DEF_TARGET))
    slider(page, f"sl-speed-{node}", "이동 속도 RPM", 17, 36, 36,
           tx_binding(p1, 8, 16, False, 50, 1800, "RPM", 10, DEF_SPEED))
    slider(page, f"sl-acc-{node}", "가속 RPM/s (RPDO2)", 22, 0, 24,
           tx_binding(p2, 0, 16, False, 100, 20000, "RPM/s", 100, DEF_ACC))
    slider(page, f"sl-dec-{node}", "감속 RPM/s (RPDO2)", 22, 24, 24,
           tx_binding(p2, 16, 16, False, 100, 20000, "RPM/s", 100, DEF_DEC))
    slider(page, f"sl-max-{node}", "최대속도 RPM (RPDO2)", 22, 48, 24,
           tx_binding(p2, 32, 16, False, 100, 1800, "RPM", 10, DEF_MAX))

    sequence(page, f"seq-move-{node}", f"[{pos}] 목표 위치로 이동 (모드 03)", 27, 0, 8, 24, move_seq(node))
    sequence(page, f"seq-stop-{node}", f"[{pos}] 정상 정지 (10)", 27, 24, 8, 24, stop_seq(node))
    sequence(page, f"seq-estop-{node}", f"[{pos}] 비상 정지 (11)", 27, 48, 8, 24, estop_seq(node))
    sequence(page, f"seq-home-{node}", f"[{pos}] 원점 SQ2 (위치 0)", 36, 0, 8, 24, home_seq(node))
    sequence(page, f"seq-homeq-{node}", f"[{pos}] 원점 상태 읽기 (SDO 응답값 0=완료)", 36, 24, 8, 24,
             sdo(node, 0x40, 0x40CF, 0, 0, "원점 상태 읽기 (0x00CF)"))
    sequence(page, f"seq-homec-{node}", f"[{pos}] 원점 취소", 36, 48, 8, 24,
             sdo(node, 0x2B, 0x40CF, 0, 1, "원점 취소 (0x00CF=1)"))
    sequence(page, f"seq-strh-{node}", f"[{pos}] 스트로크 상위 읽기 (0x00C2)", 45, 0, 8, 24,
             sdo(node, 0x40, 0x40C2, 0, 0, "스트로크 상위 읽기"))
    sequence(page, f"seq-strl-{node}", f"[{pos}] 스트로크 하위 읽기 (0x00C3)", 45, 24, 8, 24,
             sdo(node, 0x40, 0x40C3, 0, 0, "스트로크 하위 읽기"))
    sequence(page, f"seq-posq-{node}", f"[{pos}] 위치완료 읽기 (0x0032: 1/2=완료)", 45, 48, 8, 24,
             sdo(node, 0x40, 0x4032, 0, 0, "위치완료 읽기 (0x0032)"))

panel = {
    "version": 5,
    "title": TITLE,
    "dbc_signature": {"1": [], "2": [], "3": [], "4": []},
    "pages": pages,
    "active_page_id": "page-setup",
    "tx_packets": list(packets.values()),
    "init_steps": [{"kind": "STOP", "target_packet_id": "*", "name": "Stop all registered TX"}],
    "grid": {"rows": 54, "cols": 72, "cell_size": 32},
    "mode": "standby",
    "channel_settings": {str(BUS): {"bitrate": 500000, "data_bitrate": 0}},
    "widgets": widgets,
}

if __name__ == "__main__":
    from src.user_panel_v2.storage import save_bundle
    OUT_JSON.write_text(json.dumps(panel, ensure_ascii=False, indent=2), encoding="utf-8")
    save_bundle(str(OUT_PKG), panel, {})
    print(f"{OUT_JSON.name}, {OUT_PKG.name}: pages={len(pages)} widgets={len(widgets)} "
          f"packets={len(packets)}")
