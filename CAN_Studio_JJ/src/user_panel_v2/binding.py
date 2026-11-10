"""Saved CAN bit fields, independent of the presence of a DBC file."""


def bit_positions(binding, payload_length):
    start = int(binding.get("start_bit", 0))
    length = int(binding.get("bit_length", 8))
    if start < 0 or not 1 <= length <= 64:
        raise ValueError("Start Bit must be non-negative and Length must be 1..64.")
    if binding.get("byte_order", "little_endian") == "big_endian":
        positions = []
        bit = start
        for _ in range(length):
            positions.append(bit)
            bit = bit + 15 if bit % 8 == 0 else bit - 1
        positions.reverse()  # least significant value bit first
    else:
        positions = list(range(start, start + length))
    if max(positions) >= payload_length * 8:
        raise ValueError("Bit range exceeds DLC.")
    return positions


def matching_signal(db_messages, binding, require_name=False):
    if binding.get('dbc_manual'):
        return None
    message = db_messages.get(int(binding.get("bus", 1)), {}).get(int(binding.get("can_id", 0)))
    if message is None:
        return None
    matches = [s for s in message.signals
               if int(s.start) == int(binding.get("start_bit", 0))
               and int(s.length) == int(binding.get("bit_length", 8))
               and s.byte_order == binding.get("byte_order", "little_endian")]
    named = [s for s in matches if s.name == binding.get("signal_name")]
    if require_name and binding.get('signal_name'):
        return named[0] if named else None
    return named[0] if named else (matches[0] if len(matches) == 1 else None)


def reconcile_binding(db_messages, binding):
    signal = matching_signal(db_messages, binding, require_name=True)
    binding["signal_name"] = signal.name if signal else None
    return signal


def unpack_raw(payload, binding):
    positions = bit_positions(binding, len(payload))
    raw = sum(((payload[p // 8] >> (p % 8)) & 1) << i for i, p in enumerate(positions))
    if binding.get("signed", False) and raw & (1 << (len(positions) - 1)):
        raw -= 1 << len(positions)
    return raw


def _quantized_raw(binding, value, length):
    scale = float(binding.get("scale", 1))
    if scale == 0:
        raise ValueError("Scale must not be zero.")
    offset = float(binding.get("offset", 0))
    raw = round((float(value) - offset) / scale)
    low, high = (-(1 << (length - 1)), (1 << (length - 1)) - 1) if binding.get("signed") else (0, (1 << length) - 1)
    return max(low, min(high, raw))


def quantize_value(binding, value):
    length = int(binding.get("bit_length", 8))
    raw = _quantized_raw(binding, value, length)
    return raw * float(binding.get("scale", 1)) + float(binding.get("offset", 0))


def pack_value(payload, binding, value):
    positions = bit_positions(binding, len(payload))
    length = len(positions)
    raw = _quantized_raw(binding, value, length) & ((1 << length) - 1)
    result = bytearray(payload)
    for i, p in enumerate(positions):
        mask = 1 << (p % 8)
        result[p // 8] = (result[p // 8] & ~mask) | (((raw >> i) & 1) << (p % 8))
    return result


def validate_config(cfg):
    from .label_colors import validate_color_rules
    validate_color_rules(cfg.get('binding', {}).get('label_color_rules', []))
    if cfg.get('widget_type') == 'grid_view':
        from .rx_grid import validate_grid_signals, GRID_COLUMNS, DEFAULT_GRID_COLUMNS
        if cfg.get('behavior') != 'rx':
            raise ValueError('그리드 뷰는 수신 도구입니다.')
        validate_grid_signals(cfg.get('binding', {}).get('grid_signals', []))
        columns = cfg.get('binding', {}).get('grid_columns', DEFAULT_GRID_COLUMNS)
        if (not isinstance(columns, list) or any(not isinstance(key, str) or key not in dict(GRID_COLUMNS) for key in columns)
                or len(set(columns)) != len(columns)):
            raise ValueError('그리드 표시 열을 확인하세요. 값은 항상 표시됩니다.')
        return
    if cfg.get("behavior") not in ("tx", "rx") or cfg.get("widget_type") == "sequence":
        return
    from .commands import command_bindings
    for binding in command_bindings(cfg):
        validate_binding(binding)


def validate_binding(binding):
    if not 0 <= int(binding.get("can_id", 0)) <= 0x1fffffff:
        raise ValueError("CAN ID must be within 0x000..0x1FFFFFFF.")
    dlc = int(binding.get("dlc", 8))
    is_fd = binding.get("is_fd", dlc > 8)
    allowed = list(range(1, 9)) + ([12, 16, 20, 24, 32, 48, 64] if is_fd else [])
    if dlc not in allowed or (binding.get("brs") and not is_fd):
        raise ValueError("Invalid CAN frame length or BRS setting.")
    bit_positions(binding, dlc)
    if float(binding.get("scale", 1)) == 0:
        raise ValueError("Scale must not be zero.")
