"""Apply/verify the VCU CAN1 RX color preset, retaining original settings backups."""
import argparse
import base64
import copy
import io
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import zipfile

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from PyQt5.QtGui import QFont, QFontDatabase
from PyQt5.QtWidgets import QApplication
from src.main_window import UniversalCANMonitor
from src.session_storage import atomic_write, serialize, validate_panel
from src.user_panel_v2.binding import matching_signal, pack_value, validate_config
from src.user_panel_v2.window import UserPanelWindow


PALETTES = {
    'neutral': ('#F1F5F9', '#475569'), 'success': ('#DCFCE7', '#166534'),
    'danger': ('#FEE2E2', '#991B1B'), 'warning': ('#FEF9C3', '#854D0E'),
    'blue': ('#E8F1FF', '#1E3A8A'), 'sky': ('#E0F2FE', '#075985'),
    'teal': ('#CCFBF1', '#115E59'), 'purple': ('#EDE9FE', '#5B21B6'),
    'orange': ('#FFEDD5', '#9A3412'),
}
GROUP_COLORS = {
    0x130: ('#E8F1FF', '#60A5FA'), 0x131: ('#F1F5F9', '#94A3B8'),
    **{key: ('#E0F2FE', '#38BDF8') for key in (0x132, 0x133, 0x134, 0x135)},
    **{key: ('#CCFBF1', '#2DD4BF') for key in (0x136, 0x137)},
    0x138: ('#FFEDD5', '#FB923C'), 0x13A: ('#DCFCE7', '#4ADE80'),
    **{key: ('#EDE9FE', '#A78BFA') for key in (0x13B, 0x13C, 0x13D)},
}
STYLE_FIELDS = {'stroke_color', 'fill_color', 'fill', 'shape_kind', 'label_color_rules'}


def palette_for_enum(can_id, name, label):
    if can_id == 0x131:
        return {'OFF': 'neutral', 'ON': 'success', 'Error_Func': 'danger'}[label]
    if can_id == 0x137:
        return {'Unlock': 'sky', 'Lock': 'warning'}[label]
    if can_id == 0x138:
        return {'Disabled': 'neutral', 'Enabled': 'blue', 'Disabling': 'warning', 'Enabling': 'warning'}[label]
    assert can_id == 0x130
    if 'Emergency' in name:
        return 'success' if label == 'None' else 'danger'
    return {
        'None': 'neutral', 'Remote': 'blue', 'CmdCtrl': 'teal', 'Sync': 'success',
        'Bulk': 'blue', 'Individaul': 'purple', 'Throttle': 'blue', 'Speed': 'teal',
        'P': 'blue', 'R': 'orange', 'N': 'neutral', 'D': 'success',
        'Front': 'blue', 'Rear': 'sky', 'Crab': 'purple', '4Wheel': 'teal',
        'ZeroTurn': 'orange', 'Parking': 'neutral',
    }[label]


def physical_domain(binding):
    bits = binding['bit_length']
    raw = (-(1 << (bits - 1)), (1 << (bits - 1)) - 1) if binding['signed'] else (0, (1 << bits) - 1)
    values = [value * binding['scale'] + binding['offset'] for value in raw]
    return min(values), max(values)


def pair_rules(source, background, text, **condition):
    return [dict(enabled=True, source=source, target=target, color=color, **condition)
            for target, color in (('background', background), ('text', text))]


def load_messages(session):
    parser = SimpleNamespace(_load_sym_v6=lambda path: UniversalCANMonitor._load_sym_v6(None, path),
                             _load_sym_v5=lambda path: UniversalCANMonitor._load_sym_v5(None, path))
    messages = {}
    for bus, entries in session['databases'].items():
        messages[int(bus)] = {}
        for entry in entries:
            db = UniversalCANMonitor.parse_database_bytes(parser, entry['name'], base64.b64decode(entry['content']))
            for message in db.messages:
                messages[int(bus)].setdefault(message.frame_id, message)
    return messages


def tune(session, messages):
    result = copy.deepcopy(session)
    panel = result['panel']['data']
    assert panel['title'].startswith('VCU CAN1 RX'), panel['title']
    stats = dict(groups=0, enum_labels=0, numeric_labels=0, rules=0, enum_values=0)
    for cfg in panel['widgets']:
        binding = cfg['binding']
        if cfg['widget_type'] in ('shape_rect', 'group_box', 'tab_container'):
            can_id = int(cfg['title'].split('|')[0].strip(), 16)
            background, border = GROUP_COLORS[can_id]
            binding.update(fill=True, fill_color=background, stroke_color=border)
            if cfg['widget_type'] == 'shape_rect':
                binding['shape_kind'] = 'rect'
            stats['groups'] += 1
            continue
        assert cfg['widget_type'] == 'label' and cfg['behavior'] == 'rx' and binding['bus'] == 1
        signal = matching_signal(messages, binding, require_name=True)
        assert signal is not None, cfg['id']
        low, high = physical_domain(binding)
        rules = []
        if signal.choices:
            stats['enum_labels'] += 1
            for raw, choice in sorted(signal.choices.items()):
                palette = palette_for_enum(binding['can_id'], signal.name, str(choice))
                rules.extend(pair_rules('enum', *PALETTES[palette], raw=int(raw)))
                stats['enum_values'] += 1
            rules.extend(pair_rules('value', *PALETTES['warning'], op='between', a=low, b=high))
        else:
            stats['numeric_labels'] += 1
            # Presentation only: use the encoded domain, not incomplete SYM operating limits.
            rules.extend(pair_rules('value', '#FFFFFF', '#0F172A', op='between', a=low, b=high))
        binding['label_color_rules'] = rules
        stats['rules'] += len(rules)
    validate_panel(panel)
    sanitized = copy.deepcopy(result)
    for old, new in zip(session['panel']['data']['widgets'], sanitized['panel']['data']['widgets']):
        assert old['id'] == new['id']
        validate_config(new)
        for key in STYLE_FIELDS:
            if key in old['binding']:
                new['binding'][key] = copy.deepcopy(old['binding'][key])
            else:
                new['binding'].pop(key, None)
    assert sanitized == session, 'Non-appearance settings changed'
    return result, stats


def package_bytes(original_bytes, panel):
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(original_bytes)) as source, zipfile.ZipFile(output, 'w') as target:
        for entry in source.infolist():
            raw = (json.dumps(panel, ensure_ascii=False, indent=2).encode('utf-8')
                   if entry.filename == 'panel/panel.json' else source.read(entry.filename))
            target.writestr(entry, raw)
    with zipfile.ZipFile(io.BytesIO(original_bytes)) as source, zipfile.ZipFile(io.BytesIO(output.getvalue())) as target:
        assert source.namelist() == target.namelist()
        for name in source.namelist():
            if name != 'panel/panel.json':
                assert source.read(name) == target.read(name), name
    return output.getvalue()


def render_and_check(session, messages, output):
    app = QApplication.instance() or QApplication([])
    if os.name == 'nt':
        for name in ('consola.ttf', 'malgun.ttf'):
            QFontDatabase.addApplicationFont(str(Path(os.environ['WINDIR']) / 'Fonts' / name))
    app.setStyle('Fusion')
    app.setFont(QFont('Consolas', 9))
    panel = UserPanelWindow(SimpleNamespace(), messages)
    data = copy.deepcopy(session['panel']['data'])
    panel._load_panel_data(data)
    panel.set_mode('standby')
    checks = 0
    for cfg in data['widgets']:
        if cfg['widget_type'] != 'label':
            continue
        binding = cfg['binding']
        signal = matching_signal(messages, binding, require_name=True)
        if not signal.choices:
            continue
        for raw, choice in signal.choices.items():
            physical = raw * binding['scale'] + binding['offset']
            payload = pack_value(bytes(binding['dlc']), binding, physical)
            panel.on_message_update(1, binding['can_id'], payload, 0)
            expected = PALETTES[palette_for_enum(binding['can_id'], signal.name, str(choice))]
            control = panel.widget_controls[cfg['id']]
            assert all(color.lower() in control.styleSheet() for color in expected), (cfg['id'], choice)
            assert str(choice) in control.text(), (cfg['id'], control.text())
            checks += 1
        undefined = next((raw for raw in range(1 << min(binding['bit_length'], 8)) if raw not in signal.choices), None)
        if undefined is not None:
            physical = undefined * binding['scale'] + binding['offset']
            panel._update_widget_value(cfg, physical, raw_value=undefined)
            assert all(color.lower() in panel.widget_controls[cfg['id']].styleSheet() for color in PALETTES['warning'])
            checks += 1
    # Illustrative values only, never saved as measurements or transmitted.
    examples = {0x132: [12.4, 12.2, 11.8, 12.0], 0x133: [856, 854, 852, 855],
                0x134: [32.4, 32.3, 32.2, 32.4], 0x135: [14.2, 14.0, 13.8, 14.1],
                0x136: [8.25, 8.20, -8.15, -8.10], 0x137: [0, 0, 1, 1],
                0x138: [1, 0, 3, 2, 0, 0, 250, 250], 0x13A: [68.5, 286.2, -12.3, 13.4],
                0x13B: [0.02, -0.03, 9.81], 0x13C: [0.1, -0.2, 0], 0x13D: [0.4, -0.8, 23.6, 31.5]}
    by_message = {}
    for cfg in data['widgets']:
        if cfg['widget_type'] == 'label':
            by_message.setdefault(cfg['binding']['can_id'], []).append(cfg)
    for can_id, widgets in by_message.items():
        widgets.sort(key=lambda cfg: cfg['binding']['start_bit'])
        for index, cfg in enumerate(widgets):
            binding = cfg['binding']
            if can_id == 0x131:
                value = 2 if index == 19 else 0 if index == 18 else 1
            elif can_id == 0x130:
                value = [8, 2, 1, 0, 0, 1, 1, 3, 3][index]
            else:
                value = examples[can_id][index]
            panel._update_widget_value(cfg, value, force=True)
            if not matching_signal(messages, binding, require_name=True).choices:
                style = panel.widget_controls[cfg['id']].styleSheet()
                assert '#ffffff' in style and '#0f172a' in style, cfg['id']
                checks += 1
    panel.setWindowTitle('VCU CAN1 RX · 색상 미리보기 (모의 값)')
    panel.resize(1920, 1032)
    panel.log_system('색상 미리보기: 모의 표시 값입니다. 실제 CAN 수신 또는 장비 상태가 아닙니다.')
    panel.show()
    app.processEvents()
    assert panel.grab().save(str(output))
    panel.close()
    panel.deleteLater()
    return checks


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    session_path = ROOT / 'session.pjjsettings'
    package_path = ROOT / 'USER_PANEL/VCU_CAN1_Current.pjjupkg'
    original_bytes = session_path.read_bytes()
    original_package = package_path.read_bytes()
    original = json.loads(original_bytes)
    messages = load_messages(original)
    updated, stats = tune(original, messages)
    session_bytes = serialize(updated)
    package = package_bytes(original_package, updated['panel']['data'])
    preview = ROOT / 'USER_PANEL/VCU_CAN1_Readable_preview.png'
    stats['render_checks'] = render_and_check(updated, messages, preview)
    if args.apply:
        assert session_path.read_bytes() == original_bytes, 'Session changed during preparation; rerun with latest data.'
        assert package_path.read_bytes() == original_package, 'Panel package changed during preparation.'
        backup = ROOT / 'USER_PANEL/backups'
        backup.mkdir(exist_ok=True)
        for name, raw in (('VCU_CAN1_before_colors_20261023.pjjsettings', original_bytes),
                          ('VCU_CAN1_before_colors_20261023.pjjupkg', original_package)):
            path = backup / name
            if path.exists():
                assert path.read_bytes() == raw, f'Backup differs: {path}'
            else:
                atomic_write(path, raw)
        atomic_write(ROOT / 'USER_PANEL/VCU_CAN1_Readable.pjjsettings', session_bytes)
        atomic_write(ROOT / 'USER_PANEL/VCU_CAN1_Readable.pjjupkg', package)
        atomic_write(package_path, package)
        atomic_write(session_path, session_bytes)
        assert session_path.read_bytes() == session_bytes and package_path.read_bytes() == package
    print(json.dumps(dict(applied=args.apply, **stats, preview=str(preview)), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
