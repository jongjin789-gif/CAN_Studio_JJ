"""Raw DBC counters; state is committed only after a successful send."""
from src.tx_crc import apply_crcs


RX_COUNTER_MODES = {'rx_up': 'up', 'rx_down': 'down', 'rx_alternate': 'alternate'}


def latest_rx_payload(main, bus, can_id):
    rx_threads = getattr(main, 'rx_threads', {})
    thread = rx_threads.get(bus) if isinstance(rx_threads, dict) else None
    latest = getattr(thread, 'latest_rx_data', {})
    entry = latest.get(can_id) if isinstance(latest, dict) else None
    return entry[0] if entry else None


def _following_counter(value, direction, mode, low, high, step):
    if mode == 'up':
        return (low if value + step > high else value + step), direction
    if mode == 'down':
        return (high if value - step < low else value - step), direction
    if value >= high:
        direction = -1
    elif value <= low:
        direction = 1
    return max(low, min(high, value + direction * step)), direction


def counter_payload(message, payload, counters, states, received_payload=None):
    raw = message.decode(payload, decode_choices=False, scaling=False)
    received_raw = None
    if received_payload is not None and any(cfg.get('mode') in RX_COUNTER_MODES
                                            for cfg in counters.values()):
        try:
            received_raw = message.decode(received_payload, decode_choices=False, scaling=False)
        except Exception:
            received_raw = None
    next_states = dict(states)
    changed = []
    for name, cfg in counters.items():
        mode = cfg['mode']
        if mode in ('none', 'crc8', 'crc16') or name not in raw:
            continue
        low, high, step = cfg['min'], cfg['max'], cfg['step']
        if mode in RX_COUNTER_MODES:
            counter_mode = RX_COUNTER_MODES[mode]
            previous = states.get(name)
            if not isinstance(previous, dict) or previous.get('mode') != mode:
                previous = dict(next_value=raw[name], direction=-1 if counter_mode == 'down' else 1,
                                last_rx=None)
            direction = previous['direction']
            latest = received_raw.get(name) if received_raw is not None else None
            if latest is not None and latest != previous['last_rx']:
                value = max(low, min(high, latest))
                last_rx = latest
            else:
                value = max(low, min(high, previous['next_value']))
                last_rx = previous['last_rx']
            following, direction = _following_counter(value, direction, counter_mode,
                                                       low, high, step)
            next_states[name] = dict(mode=mode, next_value=following,
                                     direction=direction, last_rx=last_rx)
        else:
            value, direction = states.get(name, (raw[name], -1 if mode == 'down' else 1))
            value = max(low, min(high, value))
            following, direction = _following_counter(value, direction, mode, low, high, step)
            next_states[name] = (following, direction)
        raw[name] = value
        changed.append(message.get_signal_by_name(name))
    encoded = message.encode(raw, scaling=False, strict=False)
    result = bytearray(payload)
    # Copy only changed signal bits, retaining padding and other signal values.
    for sig in changed:
        bit = sig.start
        for _ in range(sig.length):
            byte, shift = divmod(bit, 8)
            mask = 1 << shift
            result[byte] = (result[byte] & ~mask) | (encoded[byte] & mask)
            bit = bit + 1 if sig.byte_order == 'little_endian' else (bit + 15 if shift == 0 else bit - 1)
    if any(cfg.get('mode') in ('crc8', 'crc16') for cfg in counters.values()):
        result = apply_crcs(message, result, counters)
    return bytes(result), next_states
