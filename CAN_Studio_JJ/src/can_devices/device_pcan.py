"""PEAK-System PCAN (Windows, PCAN-Basic driver)."""
import can

from src.PCANBasic import (PCANBasic, PCAN_NONEBUS, PCAN_ATTACHED_CHANNELS, PCAN_ERROR_OK,
                           PCAN_CHANNEL_AVAILABLE, FEATURE_FD_CAPABLE, PCAN_ERROR_BUSOFF,
                           PCAN_ERROR_BUSLIGHT, PCAN_ERROR_BUSHEAVY, PCAN_ERROR_BUSPASSIVE,
                           PCAN_ERROR_ILLHANDLE, PCAN_ERROR_NODRIVER, PCAN_ERROR_INITIALIZE,
                           PCAN_ERROR_ILLOPERATION)
from .device_base import CanDeviceBackend


# PCAN FD needs explicit bit timing at an 80 MHz clock.
_NOMINAL = (
    ("1 MBit/s", {'bitrate': 1000000, 'nom_brp': 2, 'nom_tseg1': 31, 'nom_tseg2': 8, 'nom_sjw': 8}),
    ("800 kBit/s", {'bitrate': 800000, 'nom_brp': 2, 'nom_tseg1': 39, 'nom_tseg2': 10, 'nom_sjw': 10}),
    ("500 kBit/s", {'bitrate': 500000, 'nom_brp': 2, 'nom_tseg1': 63, 'nom_tseg2': 16, 'nom_sjw': 16}),
    ("250 kBit/s", {'bitrate': 250000, 'nom_brp': 4, 'nom_tseg1': 63, 'nom_tseg2': 16, 'nom_sjw': 16}),
    ("125 kBit/s", {'bitrate': 125000, 'nom_brp': 8, 'nom_tseg1': 63, 'nom_tseg2': 16, 'nom_sjw': 16}),
    ("100 kBit/s", {'bitrate': 100000, 'nom_brp': 10, 'nom_tseg1': 63, 'nom_tseg2': 16, 'nom_sjw': 16}),
    ("50 kBit/s", {'bitrate': 50000, 'nom_brp': 20, 'nom_tseg1': 63, 'nom_tseg2': 16, 'nom_sjw': 16}),
    ("20 kBit/s", {'bitrate': 20000, 'nom_brp': 50, 'nom_tseg1': 63, 'nom_tseg2': 16, 'nom_sjw': 16}),
    ("10 kBit/s", {'bitrate': 10000, 'nom_brp': 100, 'nom_tseg1': 63, 'nom_tseg2': 16, 'nom_sjw': 16}),
)
_DATA = (
    ("Off", None),
    ("1 MBit/s", {'data_brp': 2, 'data_tseg1': 31, 'data_tseg2': 8, 'data_sjw': 8}),
    ("2 MBit/s", {'data_brp': 2, 'data_tseg1': 15, 'data_tseg2': 4, 'data_sjw': 4}),
    ("4 MBit/s", {'data_brp': 2, 'data_tseg1': 7, 'data_tseg2': 2, 'data_sjw': 2}),
    ("8 MBit/s", {'data_brp': 2, 'data_tseg1': 3, 'data_tseg2': 1, 'data_sjw': 1}),
)
PCAN_F_CLOCK_MHZ = 80
# Status bits meaning the hardware/handle is gone (e.g. USB unplugged), not a bus error.
def _int(value):
    return int(getattr(value, 'value', value))


_DEVICE_LOST = (_int(PCAN_ERROR_ILLHANDLE) | _int(PCAN_ERROR_NODRIVER)
                | _int(PCAN_ERROR_INITIALIZE) | _int(PCAN_ERROR_ILLOPERATION))


class PcanBackend(CanDeviceBackend):
    key = 'pcan'
    label = 'PCAN'
    bustypes = ('pcan',)
    platforms = ('Windows',)
    empty_text = 'No available PCAN channels found'

    def list_channels(self):
        channels = []
        try:
            result, data = PCANBasic().GetValue(PCAN_NONEBUS, PCAN_ATTACHED_CHANNELS)
        except Exception:
            return channels
        if result != PCAN_ERROR_OK:
            return channels
        for ch in data:
            # Channels already opened elsewhere are reported as occupied and skipped.
            if ch.channel_handle != 0 and ch.channel_condition == PCAN_CHANNEL_AVAILABLE:
                name = ch.device_name.decode(errors="ignore").strip('\x00')
                is_fd = bool(ch.device_features & FEATURE_FD_CAPABLE)
                channels.append((f"{name} ({hex(ch.channel_handle)}) {'[FD]' if is_fd else ''}",
                                 {'bustype': 'pcan', 'handle': ch.channel_handle, 'is_fd': is_fd,
                                  'device_id': ch.device_id, 'device_type': ch.device_type,
                                  'controller_number': ch.controller_number}))
        return channels

    def nominal_bitrates(self, channel_data):
        return [(text, dict(rate)) for text, rate in _NOMINAL]

    def data_bitrates(self, channel_data):
        if not (isinstance(channel_data, dict) and channel_data.get('is_fd', False)):
            return None
        return [(text, dict(rate) if rate else None) for text, rate in _DATA]

    def open(self, channel_data, settings):
        # The PCAN driver does not echo own frames; the app records TX itself.
        kwargs = {'bustype': channel_data.get('bustype', 'pcan'), 'channel': channel_data['handle'],
                  'receive_own_messages': False}
        if settings.data_enabled and settings.data_data:
            kwargs['fd'] = True
            kwargs['f_clock_mhz'] = PCAN_F_CLOCK_MHZ
            kwargs['iso_fd'] = settings.fd_iso == "ISO"
            kwargs.update(settings.bitrate_data)
            kwargs.update(settings.data_data)
        else:
            kwargs['bitrate'] = settings.bitrate_data['bitrate']
        is_fd = kwargs.get('fd', False)
        return can.interface.Bus(**kwargs), is_fd

    def poll_status(self, bus):
        """python-can may only log PCAN warnings; read the controller state directly."""
        status_fn = getattr(bus, 'status', None)
        if not callable(status_fn):
            return None
        status = status_fn()
        status = int(getattr(status, 'value', status))
        detail = f'PCAN status: 0x{status:X}'
        # python-can keeps reporting an empty receive queue after the USB device is
        # removed, so detect the removal here.
        if status & _DEVICE_LOST:
            return ('lost', detail)
        if self._channel_unavailable(bus):
            return ('lost', detail + ', channel condition: unavailable')
        if status & PCAN_ERROR_BUSOFF:
            return ('busoff', detail)
        if status & (PCAN_ERROR_BUSLIGHT | PCAN_ERROR_BUSHEAVY | PCAN_ERROR_BUSPASSIVE):
            return ('warn', detail)
        if status == PCAN_ERROR_OK:
            return ('ok', detail)
        return None

    @staticmethod
    def _channel_unavailable(bus):
        api, handle = getattr(bus, 'm_objPCANBasic', None), getattr(bus, 'm_PcanHandle', None)
        if api is None or handle is None:
            return False
        try:
            from can.interfaces.pcan import basic   # same API object python-can opened
            result, condition = api.GetValue(handle, basic.PCAN_CHANNEL_CONDITION)
        except Exception:
            return False
        result = int(getattr(result, 'value', result))
        condition = int(getattr(condition, 'value', condition))
        return result == 0 and condition == int(getattr(basic.PCAN_CHANNEL_UNAVAILABLE, 'value',
                                                            basic.PCAN_CHANNEL_UNAVAILABLE))
