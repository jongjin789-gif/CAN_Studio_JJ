"""Portable session files. No external DB paths or executable Python objects."""
import ast
import base64
import json
import math
import os
from pathlib import Path
import tempfile
import copy
from src.bus_config import BUS_IDS, default_can_config

EXTENSION = '.pjjsettings'
FORMAT = 'CAN_Studio_JJ_SESSION'
# Files saved before the CAN Studio JJ rename (R012_1 and earlier) are read and
# converted to FORMAT; the next save writes the new name.
LEGACY_FORMATS = ('PCAN_VIEWER_SESSION',)
MAX_BYTES = 64 * 1024 * 1024


def encode_db(name, raw, ident):
    return dict(id=ident, name=name, content=base64.b64encode(raw).decode('ascii'))


def decode_db(entry):
    name = entry['name']
    if (not isinstance(name, str) or '/' in name or '\\' in name
            or Path(name).suffix.lower() not in ('.dbc', '.sym')):
        raise ValueError('DB 파일명 또는 형식이 올바르지 않습니다.')
    return base64.b64decode(entry['content'], validate=True)


def compile_formula(expr, signal_count):
    if not expr:
        return None
    if not isinstance(expr, str) or len(expr) > 4096:
        raise ValueError('수식이 너무 길거나 올바르지 않습니다.')
    node = ast.parse(expr, mode='eval')
    allowed = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant, ast.Name,
               ast.Load, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv,
               ast.Mod, ast.Pow, ast.UAdd, ast.USub, ast.Compare, ast.IfExp,
               ast.BoolOp, ast.And, ast.Or, ast.Not, ast.Eq, ast.NotEq,
               ast.Lt, ast.LtE, ast.Gt, ast.GtE)
    names = {f'X{i}' for i in range(1, signal_count + 1)} | {'Y1', 'Y2', 'Y3'}
    for part in ast.walk(node):
        if not isinstance(part, allowed):
            raise ValueError('수식에는 숫자, X/Y 변수, 산술·비교 연산만 사용할 수 있습니다.')
        if isinstance(part, ast.Name) and part.id not in names:
            raise ValueError(f'수식의 신호 변수를 찾을 수 없습니다: {part.id}')
        if isinstance(part, ast.Constant) and (type(part.value) not in (int, float, bool)
                                              or not math.isfinite(part.value)):
            raise ValueError('수식 상수는 유한한 숫자여야 합니다.')
    return compile(node, '<graph formula>', 'eval')


def validate_structure(data):
    if (not isinstance(data, dict) or data.get('format') not in (FORMAT, *LEGACY_FORMATS)
            or type(data.get('version')) is not int or data['version'] not in (1, 2)):
        raise ValueError('지원하지 않는 통합 설정 파일 형식/버전입니다.')
    if data['format'] != FORMAT:
        data = dict(data, format=FORMAT)
    expected = {'1', '2', '3'} if data['version'] == 1 else {str(b) for b in BUS_IDS}
    for key in ('databases', 'can'):
        if not isinstance(data.get(key), dict) or set(data[key]) != expected:
            raise ValueError(f'{key}: BUS {", ".join(sorted(expected))} 설정이 필요합니다.')
    if data['version'] == 1:
        data = copy.deepcopy(data)
        data['version'] = 2
        data['databases']['4'] = []
        data['can']['4'] = default_can_config()
    ui = data.get('ui', {})
    if not isinstance(ui, dict) or type(ui.get('dbc_collapsed', False)) is not bool:
        raise ValueError('DBC 접기 설정이 올바르지 않습니다.')
    if type(ui.get('can_collapsed', False)) is not bool:
        raise ValueError('CAN 접기 설정이 올바르지 않습니다.')
    for key in ('graphs', 'tx_packets'):
        if not isinstance(data.get(key), list):
            raise ValueError(f'{key}: 목록이 필요합니다.')
    if len(data['graphs']) > 128 or len(data['tx_packets']) > 10000:
        raise ValueError('그래프 또는 TX 패킷 개수가 너무 많습니다.')
    if data.get('panel') is not None and not isinstance(data['panel'], dict):
        raise ValueError('UserPanel 설정이 올바르지 않습니다.')
    ids = set()
    for bus, entries in data['databases'].items():
        if not isinstance(entries, list):
            raise ValueError(f'BUS {bus}: DB 목록이 올바르지 않습니다.')
        for entry in entries:
            ident = entry['id']
            if not isinstance(ident, str) or not ident or ident in ids:
                raise ValueError('DB 식별자가 없거나 중복됩니다.')
            ids.add(ident)
            decode_db(entry)
    for bus, cfg in data['can'].items():
        if not isinstance(cfg, dict) or type(cfg.get('is_open')) is not bool:
            raise ValueError(f'BUS {bus}: CAN 설정이 올바르지 않습니다.')
        if cfg.get('device') is not None and not isinstance(cfg['device'], dict):
            raise ValueError(f'BUS {bus}: 장치 정보가 올바르지 않습니다.')
        # 'backend' (CAN 방식) is optional: older files infer it from device.bustype.
        if cfg.get('backend') is not None and not isinstance(cfg['backend'], str):
            raise ValueError(f'BUS {bus}: CAN 방식 설정이 올바르지 않습니다.')
        for key in ('bitrate', 'fd_iso', 'data_bitrate'):
            if not isinstance(cfg.get(key), str):
                raise ValueError(f'BUS {bus}: {key} 설정이 없습니다.')
    return data


def validate_graph(state):
    if not isinstance(state, dict) or not isinstance(state.get('title'), str):
        raise ValueError('그래프 제목/설정이 올바르지 않습니다.')
    signals = state['signals']
    if (not isinstance(signals, list) or not 1 <= len(signals) <= 2048
            or any(not isinstance(key, str) for key in signals)
            or len(set(signals)) != len(signals) or set(state['bindings']) != set(signals)):
        raise ValueError('그래프 신호 목록 또는 참조가 올바르지 않습니다.')
    formulas = state.get('formulas', {})
    expected = set(signals)
    for key, formula in formulas.items():
        if key not in ('Y1', 'Y2', 'Y3') or type(formula.get('enabled')) is not bool:
            raise ValueError('사용자 수식 설정이 올바르지 않습니다.')
        for field in ('name', 'unit', 'expr'):
            if not isinstance(formula.get(field), str):
                raise ValueError('사용자 수식의 이름/단위/표현식이 올바르지 않습니다.')
        compile_formula(formula['expr'], len(signals))
        if formula['enabled']:
            expected.add(key)
    legend = state['legend']
    if len(legend) != len(expected) or {item['key'] for item in legend} != expected:
        raise ValueError('그래프 범례가 누락되었거나 중복됩니다.')
    for item in legend:
        if (not isinstance(item['name'], str) or type(item['visible']) is not bool
                or type(item['style']) is not int or item['style'] not in range(1, 7)
                or len(item['color']) not in (3, 4)
                or any(type(c) is not int or not 0 <= c <= 255 for c in item['color'])):
            raise ValueError('그래프 범례/색상/선 설정이 올바르지 않습니다.')
    low, high = state['y_range']
    if not all(type(n) in (int, float) and math.isfinite(n) for n in (low, high)) or low >= high:
        raise ValueError('Y축 범위가 올바르지 않습니다.')
    span = state.get('x_span', 30)
    if type(span) not in (float, int) or not math.isfinite(span) or span <= 0:
        raise ValueError('시간축 범위가 올바르지 않습니다.')


def validate_panel(data):
    if not isinstance(data, dict):
        raise ValueError('패널 데이터가 올바르지 않습니다.')
    grid = data.get('grid', {})
    for key in ('rows', 'cols'):
        value = grid.get(key, 12)
        if type(value) is not int or not 1 <= value <= 512:
            raise ValueError('패널 격자는 1~512 범위여야 합니다.')
    widgets = data.get('widgets', [])
    if not isinstance(widgets, list) or len(widgets) > 10000:
        raise ValueError('패널 도구 목록이 올바르지 않습니다.')
    by_id = {cfg['id']: cfg for cfg in widgets}
    if len(by_id) != len(widgets):
        raise ValueError('패널 도구 ID가 중복됩니다.')
    # Prevent cyclic group ancestry before creating Qt parent widgets.
    for cfg in widgets:
        seen = {cfg['id']}
        parent = cfg.get('parent_id')
        while parent:
            if parent in seen or parent not in by_id:
                raise ValueError('패널 그룹 참조가 없거나 순환합니다.')
            seen.add(parent)
            parent = by_id[parent].get('parent_id')


def serialize(data):
    data = validate_structure(data)
    raw = json.dumps(data, ensure_ascii=False, allow_nan=False, indent=2).encode('utf-8')
    if len(raw) > MAX_BYTES:
        raise ValueError('설정 파일은 64 MiB를 넘을 수 없습니다.')
    return raw


def read_session(path):
    with open(path, 'rb') as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError('설정 파일은 64 MiB를 넘을 수 없습니다.')
    def invalid_constant(value):
        raise ValueError(f'유효하지 않은 숫자: {value}')
    return validate_structure(json.loads(raw, parse_constant=invalid_constant))


def atomic_write(path, raw):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.session-', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def write_session(path, data):
    atomic_write(path, serialize(data))
