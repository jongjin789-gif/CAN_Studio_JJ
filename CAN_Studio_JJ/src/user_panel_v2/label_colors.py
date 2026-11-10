"""Ordered RX label color rules. Enum rules use RAW; range rules use physical values."""
import math
from PyQt5.QtGui import QColor


CONDITIONS = (
    ('lt', '작을 때 (<)'), ('le', '작거나 같을 때 (≤)'), ('eq', '같을 때 (=)'),
    ('ne', '다를 때 (≠)'), ('gt', '클 때 (>)'), ('ge', '크거나 같을 때 (≥)'),
    ('between', '범위 안 [min, max]'), ('outside', '범위 밖 (< min / > max)'),
)


def validate_color_rules(rules):
    if not isinstance(rules, list) or len(rules) > 128:
        raise ValueError('라벨 색상 규칙은 최대 128개까지 등록할 수 있습니다.')
    for index, rule in enumerate(rules, 1):
        prefix = f'색상 규칙 {index}행: '
        if not isinstance(rule, dict):
            raise ValueError(prefix + '규칙 형식이 올바르지 않습니다.')
        if type(rule.get('enabled', True)) is not bool:
            raise ValueError(prefix + '사용 여부를 확인하세요.')
        if rule.get('source') not in ('enum', 'value') or rule.get('target') not in ('background', 'text'):
            raise ValueError(prefix + '기준과 적용 대상을 확인하세요.')
        if not isinstance(rule.get('color'), str) or not QColor(rule['color']).isValid():
            raise ValueError(prefix + '올바른 색상을 선택하세요.')
        if rule['source'] == 'enum':
            if type(rule.get('raw')) is not int:
                raise ValueError(prefix + 'Enum 값을 선택하세요.')
            continue
        if rule.get('op') not in dict(CONDITIONS):
            raise ValueError(prefix + '비교 조건을 선택하세요.')
        keys = ('a', 'b') if rule['op'] in ('between', 'outside') else ('a',)
        if any(type(rule.get(key)) not in (int, float) or not math.isfinite(rule[key]) for key in keys):
            raise ValueError(prefix + '기준값은 유한한 숫자여야 합니다.')
        if len(keys) == 2 and rule['a'] > rule['b']:
            raise ValueError(prefix + 'min은 max보다 클 수 없습니다.')


def inferred_raw(binding, value, raw_value=None):
    if raw_value is not None:
        return raw_value
    scale = float(binding.get('scale', 1))
    if not scale:
        return None
    candidate = (value - float(binding.get('offset', 0))) / scale
    if math.isfinite(candidate) and math.isclose(candidate, round(candidate), rel_tol=0, abs_tol=1e-7):
        return round(candidate)
    return None


def matches_value(value, op, a, b=0):
    # Physical values such as 3 * 0.1 should match an entered threshold of 0.3.
    equal = math.isclose(value, a, rel_tol=0, abs_tol=1e-9)
    if op == 'lt':
        return value < a and not equal
    if op == 'le':
        return value < a or equal
    if op == 'eq':
        return equal
    if op == 'ne':
        return not equal
    if op == 'gt':
        return value > a and not equal
    if op == 'ge':
        return value > a or equal
    inside = (value > a or equal) and (value < b or math.isclose(value, b, rel_tol=0, abs_tol=1e-9))
    return inside if op == 'between' else not inside if op == 'outside' else False


def label_color_style(binding, value, raw_value=None):
    background = foreground = None
    rules = binding.get('label_color_rules', [])
    if not rules or not math.isfinite(value):
        return ''
    raw = inferred_raw(binding, value, raw_value)
    for rule in rules:
        if not rule.get('enabled', True):
            continue
        match = (raw is not None and raw == rule.get('raw') if rule['source'] == 'enum'
                 else matches_value(value, rule['op'], rule['a'], rule.get('b', 0)))
        if not match:
            continue
        color = QColor(rule['color']).name()
        if rule['target'] == 'background' and background is None:
            background = color
        elif rule['target'] == 'text' and foreground is None:
            foreground = color
        if background is not None and foreground is not None:
            break
    if background is None and foreground is None:
        return ''
    if foreground is None:
        foreground = '#ffffff' if QColor(background).lightnessF() < 0.5 else '#0f172a'
    return (f'QLabel[panelValue="true"] {{ color: {foreground}; '
            f'background-color: {background or "transparent"}; border: none; border-radius: 4px; }}')
