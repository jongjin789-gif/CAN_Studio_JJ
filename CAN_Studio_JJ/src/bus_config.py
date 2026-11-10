"""Logical CAN channels shared by the monitor, panels and portable files."""

BUS_IDS = (1, 2, 3, 4)
PACKET_BUS_IDS = (0, *BUS_IDS)


def default_can_config():
    return dict(device=None, channel='', bitrate='500 kBit/s', fd_iso='ISO',
                data_bitrate='Off', is_open=False)


def normalized_signature(signature):
    """An absent, empty fourth BUS is equivalent to an old three-BUS panel."""
    if not isinstance(signature, dict):
        return signature
    return {str(bus): list(signature.get(str(bus), [])) for bus in BUS_IDS}


def bitrate_value(text):
    """Read both legacy unit strings and compact labels without changing storage."""
    text = str(text).strip().lower().replace(' ', '')
    if text in ('off', 'n/a', ''):
        return 0
    if text.endswith('mbit/s'):
        return int(float(text[:-6]) * 1000000)
    if text.endswith('kbit/s'):
        return int(float(text[:-6]) * 1000)
    if text.endswith('m'):
        return int(float(text[:-1]) * 1000000)
    if text.endswith('k'):
        return int(float(text[:-1]) * 1000)
    return int(text)
