"""Configurable signal CRCs using normal (unreflected) polynomial notation."""
import re


def signal_bits(signal):
    bit = signal.start
    for _ in range(signal.length):
        yield bit
        bit = bit + 1 if signal.byte_order == 'little_endian' else (bit + 15 if bit % 8 == 0 else bit - 1)


def parse_extra(text):
    text = re.sub(r'0x', '', text.strip(), flags=re.IGNORECASE)
    text = re.sub(r'[\s,]+', '', text)
    if len(text) % 2 or re.search(r'[^0-9a-fA-F]', text):
        raise ValueError('추가 데이터는 HEX 바이트로 입력하세요. 예: 00 00 또는 0x0000')
    return list(bytes.fromhex(text))


def calculate_crc(data, width, poly, init, xorout, refin=False, refout=False):
    def reflect(value, bits):
        return int(f'{value:0{bits}b}'[::-1], 2)
    mask = (1 << width) - 1
    crc = init
    for byte in data:
        if refin:
            byte = reflect(byte, 8)
        crc ^= byte << (width - 8)
        for _ in range(8):
            crc = ((crc << 1) ^ (poly if crc & (1 << (width - 1)) else 0)) & mask
    if refout:
        crc = reflect(crc, width)
    return (crc ^ xorout) & mask


def validate_crc(signal, cfg, length):
    width = {'crc8': 8, 'crc16': 16}.get(cfg.get('mode'))
    if width is None or signal.length != width or getattr(signal, 'is_float', False) or signal.is_multiplexer:
        raise ValueError(f'{signal.name}: CRC는 해당 폭(8/16비트)의 정수 신호에 설정하세요. Multiplexer 선택 신호는 사용할 수 없습니다.')
    if width == 16 and cfg.get('result_byte_order', signal.byte_order) not in ('little_endian', 'big_endian'):
        raise ValueError(f'{signal.name}: CRC-16 결과 순서를 LSB 또는 MSB로 선택하세요.')
    start, size = cfg.get('start'), cfg.get('size')
    if type(start) is not int or type(size) is not int or start < 0 or size <= 0 or start + size > length:
        raise ValueError(f'{signal.name}: 연산 시작/사이즈가 패킷 길이를 벗어납니다. 사이즈는 1 이상이어야 합니다.')
    occupied = {bit // 8 for bit in signal_bits(signal)}
    if occupied & set(range(start, start + size)):
        raise ValueError(f'{signal.name}: 연산 범위에 CRC 신호 자신이 포함된 바이트가 있습니다. (신호 바이트: {sorted(occupied)})')
    if max(occupied) >= length:
        raise ValueError(f'{signal.name}: CRC 신호가 패킷 길이를 벗어납니다.')
    for key in ('poly', 'init', 'xorout'):
        val = cfg.get(key)
        if type(val) is not int or not 0 <= val < (1 << width) or (key == 'poly' and val == 0):
            raise ValueError(f'{signal.name}: {key} 값이 CRC-{width} 범위를 벗어납니다.')
    for key in ('refin', 'refout'):
        if type(cfg.get(key)) is not bool:
            raise ValueError(f'{signal.name}: {key} 설정이 잘못되었습니다.')
    if not isinstance(cfg.get('extra'), list) or any(type(v) is not int or not 0 <= v <= 255 for v in cfg['extra']):
        raise ValueError(f'{signal.name}: 추가 데이터가 올바른 바이트 배열이 아닙니다.')


def crc_order(message, configs, length):
    crcs = {name: cfg for name, cfg in configs.items() if cfg.get('mode') in ('crc8', 'crc16')}
    occupied = {}
    for name, cfg in crcs.items():
        sig = message.get_signal_by_name(name)
        validate_crc(sig, cfg, length)
        occupied[name] = {bit // 8 for bit in signal_bits(sig)}
    pending = {name: {other for other in crcs if other != name and
                     occupied[other] & set(range(cfg['start'], cfg['start'] + cfg['size']))}
               for name, cfg in crcs.items()}
    order = []
    while pending:
        ready = [name for name, deps in pending.items() if not deps]
        if not ready:
            raise ValueError('CRC 연산 범위가 서로를 참조합니다. 순환 참조를 제거하세요.')
        for name in ready:
            order.append(name)
            del pending[name]
        for deps in pending.values():
            deps.difference_update(ready)
    return order


def apply_crcs(message, payload, configs):
    active = message.decode(payload, decode_choices=False, scaling=False)
    configs = {name: cfg for name, cfg in configs.items() if name in active}
    result = bytearray(payload)
    for name in crc_order(message, configs, len(payload)):
        cfg = configs[name]
        sig = message.get_signal_by_name(name)
        data = result[cfg['start']:cfg['start'] + cfg['size']] + bytes(cfg['extra'])
        value = calculate_crc(data, sig.length, cfg['poly'], cfg['init'], cfg['xorout'], cfg['refin'], cfg['refout'])
        # Preserve the DBC bit locations; only swap the two result bytes.
        # Configurations saved before this option retain their original ordering.
        if sig.length == 16 and cfg.get('result_byte_order', sig.byte_order) != sig.byte_order:
            value = ((value & 0xFF) << 8) | (value >> 8)
        for index, bit in enumerate(signal_bits(sig)):
            shift = index if sig.byte_order == 'little_endian' else sig.length - 1 - index
            byte, offset = divmod(bit, 8)
            result[byte] = (result[byte] & ~(1 << offset)) | (((value >> shift) & 1) << offset)
    return bytes(result)
