"""Generic python-can interfaces (virtual, vector, ixxat, gs_usb, slcan, ...).

Not offered in the "CAN 방식" combo yet (``selectable = False``).  It already opens
channels whose ``bustype`` is not owned by another module, e.g. the ``virtual``
test channels.  To offer it later, implement :meth:`list_channels` with
``can.detect_available_configs()`` and set ``selectable = True``.
"""
import can

from src.bus_config import bitrate_value
from .device_base import CanDeviceBackend


class PythonCanBackend(CanDeviceBackend):
    key = 'pythoncan'
    label = '범용 (python-can)'
    bustypes = ()          # fallback for every bustype without a dedicated module
    platforms = ('Windows', 'Linux')
    selectable = False

    def open(self, channel_data, settings):
        bustype = channel_data.get('bustype')
        # Generic drivers echo own frames only when asked; keep the previous behavior.
        kwargs = {'bustype': bustype, 'channel': channel_data['handle'], 'receive_own_messages': True}
        if bustype == 'virtual':
            if settings.data_enabled and settings.data_data:
                kwargs['fd'] = True
        else:
            kwargs['bitrate'] = settings.bitrate_data['bitrate']
            if settings.data_enabled and settings.data_data:
                kwargs['fd'] = True
                kwargs['data_bitrate'] = bitrate_value(settings.data_text)
        is_fd = kwargs.get('fd', False)
        return can.interface.Bus(**kwargs), is_fd
