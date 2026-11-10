"""Kvaser CANlib on Windows, using the installed python-can backend.

CANlib is imported only when searching/opening Kvaser channels so that PCs
without the Kvaser driver can still use the other communication methods.
"""
import ctypes
import importlib
import logging

import can

from .device_base import CanDeviceBackend, DeviceOpenError

log = logging.getLogger(__name__)

# CANlib canlib.h flags, absent from python-can 4.6.1's constants module.
# https://kvaser.com/canlib-webhelp/canlib_8h.htm
CAP_VIRTUAL = 0x00010000
CAP_CAN_FD = 0x00080000
CAP_CAN_FD_NONISO = 0x00100000
# canReadStatus flags (canstat.h).
CAN_STAT_ERROR_PASSIVE = 0x00000001
CAN_STAT_BUS_OFF = 0x00000002
CAN_STAT_ERROR_WARNING = 0x00000004
DRIVER_MESSAGE = 'Kvaser Drivers for Windows 설치 후 프로그램을 다시 실행하세요 (canlib32.dll).'


class KvaserBackend(CanDeviceBackend):
    key = 'kvaser'
    label = 'Kvaser'
    bustypes = ('kvaser',)
    platforms = ('Windows',)
    empty_text = 'Kvaser 채널 없음 · 드라이버 및 장치 연결 확인'

    @staticmethod
    def _load_canlib():
        # Use the CANlib bindings shipped with our pinned python-can version;
        # the separate Kvaser "canlib" Python package is not required.
        try:
            library = importlib.import_module('can.interfaces.kvaser.canlib')
            if not callable(getattr(library, 'canGetNumberOfChannels', None)):
                raise OSError('CANlib is unavailable')
            return library
        except (ImportError, OSError) as exc:
            raise DeviceOpenError('Kvaser 드라이버 확인', DRIVER_MESSAGE) from exc

    def list_channels(self):
        self.empty_text = type(self).empty_text
        try:
            library = self._load_canlib()
            count = self._channel_count(library)
        except DeviceOpenError:
            self.empty_text = DRIVER_MESSAGE
            return []
        except Exception as exc:
            self.empty_text = 'Kvaser 검색 실패 · 드라이버 및 장치 연결 확인'
            log.warning('Kvaser channel search failed: %s', exc)
            return []

        channels = []
        for channel in range(count):
            try:
                info = library.get_channel_info(channel)
                capabilities = ctypes.c_uint32()
                library.canGetChannelData(
                    channel, library.canstat.canCHANNELDATA_CHANNEL_CAP,
                    ctypes.byref(capabilities), ctypes.sizeof(capabilities))
                caps = capabilities.value
                is_fd = bool(caps & (CAP_CAN_FD | CAP_CAN_FD_NONISO))
                is_virtual = bool(caps & CAP_VIRTUAL)
                name = (f"{info['device_name']} (ch {channel}, S/N {info['serial']}, "
                        f"#{info['dongle_channel']})")
                if is_virtual:
                    name += ' [Virtual]'
                if is_fd:
                    name += ' [FD]'
                channels.append((name, dict(
                    bustype=self.key, handle=channel, is_fd=is_fd,
                    serial=info['serial'], dongle_channel=info['dongle_channel'],
                    fd_iso_supported=bool(caps & CAP_CAN_FD),
                    fd_non_iso_supported=bool(caps & CAP_CAN_FD_NONISO),
                    is_virtual=is_virtual)))
            except Exception as exc:
                # One inaccessible channel must not hide the remaining devices.
                log.warning('Cannot query Kvaser channel %s: %s', channel, exc)
        return channels

    @staticmethod
    def _channel_count(library):
        # canGetNumberOfChannels alone returns the cached startup enumeration.
        # Re-enumeration keeps open bus handles valid, but may change channel IDs.
        # https://kvaser.com/canlib-webhelp/group__can__general.htm
        driver = ctypes.WinDLL('canlib32.dll')
        scan = getattr(driver, 'canEnumHardwareEx', None)
        count = ctypes.c_int()
        if scan is None:  # Older CANlib versions require restarting after hotplug.
            library.canGetNumberOfChannels(ctypes.byref(count))
        else:
            scan.argtypes = [ctypes.POINTER(ctypes.c_int)]
            scan.restype = ctypes.c_int
            status = scan(ctypes.byref(count))
            if status < 0:
                raise OSError(f'canEnumHardwareEx failed: {status}')
        return count.value

    def open(self, channel_data, settings):
        library = self._load_canlib()
        if all(key in channel_data for key in ('serial', 'dongle_channel')):
            # Another BUS refresh can renumber channels after USB reconnection.
            # Reject stale selections before opening a different physical port.
            try:
                actual = library.get_channel_info(channel_data['handle'])
                matches = all(actual[key] == channel_data[key] for key in ('serial', 'dongle_channel'))
            except Exception:
                matches = False
            if not matches:
                raise DeviceOpenError('Kvaser 채널 재검색',
                                      'Kvaser 장치 구성이 변경되었습니다. Refresh 후 채널을 다시 선택하세요.')
        is_fd = settings.fd_requested
        if is_fd:
            mode = 'fd_iso_supported' if settings.fd_iso == 'ISO' else 'fd_non_iso_supported'
            if not channel_data.get('is_fd') or not channel_data.get(mode, True):
                raise DeviceOpenError('Kvaser CAN FD 설정',
                                      f"선택한 Kvaser 채널은 {settings.fd_iso} CAN FD를 지원하지 않습니다.")

        bitrate = settings.bitrate_data['bitrate']
        # RX runs in a separate thread. Disable echo because the app records TX.
        kwargs = dict(interface=self.key, channel=channel_data['handle'],
                      bitrate=bitrate, single_handle=False, receive_own_messages=False,
                      accept_virtual=channel_data.get('is_virtual', False))
        if is_fd:
            data_bitrate = settings.data_data['data_bitrate']
            kwargs.update(fd=True, fd_non_iso=settings.fd_iso != 'ISO',
                          data_bitrate=data_bitrate)
            # Explicit timing also covers nominal rates without CANlib presets.
            kwargs['timing'] = can.BitTimingFd.from_sample_point(
                f_clock=80_000_000, nom_bitrate=bitrate, nom_sample_point=80,
                data_bitrate=data_bitrate, data_sample_point=60 if data_bitrate == 8_000_000 else 80)
        elif bitrate in (800_000, 20_000):
            # python-can/CANlib have no predefined constants for these rates.
            kwargs.update(tseg1=15, tseg2=4, sjw=4, no_samp=1)
        return can.interface.Bus(**kwargs), is_fd

    # ---- monitoring --------------------------------------------------------
    @staticmethod
    def _driver():
        driver = ctypes.WinDLL('canlib32.dll')
        driver.canReadStatus.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_ulong)]
        driver.canReadStatus.restype = ctypes.c_int
        driver.canRequestChipStatus.argtypes = [ctypes.c_int]
        driver.canRequestChipStatus.restype = ctypes.c_int
        return driver

    @staticmethod
    def _handle(bus):
        handle = getattr(bus, '_read_handle', None)
        return getattr(handle, 'value', handle)

    def poll_status(self, bus):
        """Read the controller state; python-can does not report Kvaser bus-off by itself."""
        handle = self._handle(bus)
        if handle is None:
            return None
        driver = self._driver()
        # USB devices report the controller state asynchronously; ask for a fresh one.
        # The answer arrives shortly, so each 1 s poll reads the previous request's state.
        driver.canRequestChipStatus(handle)
        flags = ctypes.c_ulong()
        if driver.canReadStatus(handle, ctypes.byref(flags)) < 0:
            return None
        flags = flags.value
        detail = f'Kvaser status: 0x{flags:X}'
        if flags & CAN_STAT_BUS_OFF:
            return ('busoff', detail)
        if flags & (CAN_STAT_ERROR_PASSIVE | CAN_STAT_ERROR_WARNING):
            return ('warn', detail)
        return ('ok', detail)
