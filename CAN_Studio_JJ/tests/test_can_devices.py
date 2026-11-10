"""CAN device modules (src/can_devices): registry, per-device open arguments and UI wiring."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PyQt5.QtWidgets import QApplication

from src.can_devices import (DeviceRegistry, DeviceOpenCancelled, DeviceOpenError, OpenSettings,
                             KvaserBackend, PcanBackend, PythonCanBackend, SocketCanBackend)
from src.can_devices.device_kvaser import CAP_CAN_FD, CAP_CAN_FD_NONISO, CAP_VIRTUAL, DRIVER_MESSAGE
from src.PCANBasic import PCAN_ERROR_BUSHEAVY, PCAN_ERROR_BUSOFF, PCAN_ERROR_OK

LEGACY_NOMINAL_TEXT = ['1 MBit/s', '800 kBit/s', '500 kBit/s', '250 kBit/s', '125 kBit/s',
                       '100 kBit/s', '50 kBit/s', '20 kBit/s', '10 kBit/s']
LEGACY_DATA_TEXT = ['Off', '1 MBit/s', '2 MBit/s', '4 MBit/s', '8 MBit/s']


def settings(backend, channel, nominal='500 kBit/s', data='Off', iso='ISO'):
    nominal_rates = dict(backend.nominal_bitrates(channel))
    data_rates = dict(backend.data_bitrates(channel) or [])
    return OpenSettings(bitrate_text=nominal, bitrate_data=nominal_rates[nominal],
                        data_enabled=bool(data_rates), data_text=data if data_rates else 'N/A',
                        data_data=data_rates.get(data), fd_iso=iso)


class RegistryTest(unittest.TestCase):
    def test_methods_offered_per_os(self):
        registry = DeviceRegistry(None)
        self.assertEqual([b.key for b in registry.selectable('Windows')], ['pcan', 'kvaser', 'gs_usb'])
        self.assertEqual([b.key for b in registry.selectable('Linux')], ['socketcan'])
        self.assertEqual(registry.default_key('Windows'), 'pcan')
        self.assertEqual(registry.default_key('Linux'), 'socketcan')

    def test_channel_bustype_selects_module(self):
        registry = DeviceRegistry(None)
        self.assertIsInstance(registry.for_bustype('pcan'), PcanBackend)
        self.assertIsInstance(registry.for_bustype('socketcan'), SocketCanBackend)
        self.assertIsInstance(registry.for_bustype('kvaser'), KvaserBackend)
        self.assertIsInstance(registry.for_bustype('virtual'), PythonCanBackend)
        self.assertEqual(registry.key_for_device({'bustype': 'pcan', 'handle': 0x51}), 'pcan')
        self.assertIsNone(registry.key_for_device(None))

    def test_bitrate_texts_are_identical_for_every_module(self):
        fd = {'bustype': 'x', 'handle': 1, 'is_fd': True}
        for cls in (PcanBackend, KvaserBackend, SocketCanBackend, PythonCanBackend):
            backend = cls(None)
            with self.subTest(backend=cls.key):
                self.assertEqual([t for t, _ in backend.nominal_bitrates(fd)], LEGACY_NOMINAL_TEXT)
                self.assertEqual([t for t, _ in backend.data_bitrates(fd)], LEGACY_DATA_TEXT)
                self.assertIsNone(backend.data_bitrates({'is_fd': False}))


class PcanBackendTest(unittest.TestCase):
    def test_classic_open_arguments_match_previous_code(self):
        backend = PcanBackend(None)
        channel = {'bustype': 'pcan', 'handle': 0x51, 'is_fd': False}
        with patch('can.interface.Bus') as bus:
            _, is_fd = backend.open(channel, settings(backend, channel, '250 kBit/s'))
        bus.assert_called_once_with(bustype='pcan', channel=0x51, receive_own_messages=False, bitrate=250000)
        self.assertFalse(is_fd)

    def test_fd_open_uses_80mhz_timing(self):
        backend = PcanBackend(None)
        channel = {'bustype': 'pcan', 'handle': 0x51, 'is_fd': True}
        with patch('can.interface.Bus') as bus:
            _, is_fd = backend.open(channel, settings(backend, channel, '500 kBit/s', '2 MBit/s', 'Non-ISO'))
        self.assertTrue(is_fd)
        self.assertEqual(bus.call_args.kwargs, dict(
            bustype='pcan', channel=0x51, receive_own_messages=False, fd=True, f_clock_mhz=80,
            iso_fd=False, bitrate=500000, nom_brp=2, nom_tseg1=63, nom_tseg2=16, nom_sjw=16,
            data_brp=2, data_tseg1=15, data_tseg2=4, data_sjw=4))

    def test_status_mapping(self):
        backend = PcanBackend(None)
        for code, state in ((PCAN_ERROR_BUSOFF, 'busoff'), (PCAN_ERROR_BUSHEAVY, 'warn'), (PCAN_ERROR_OK, 'ok')):
            with self.subTest(code=code):
                self.assertEqual(backend.poll_status(SimpleNamespace(status=lambda c=code: c))[0], state)
        self.assertIsNone(backend.poll_status(SimpleNamespace()))

    def test_channel_search_failure_returns_empty_list(self):
        with patch('src.can_devices.device_pcan.PCANBasic', side_effect=OSError('no driver')):
            self.assertEqual(PcanBackend(None).list_channels(), [])


class KvaserBackendTest(unittest.TestCase):
    def setUp(self):
        loader = patch('src.can_devices.device_kvaser.ctypes.WinDLL', create=True)
        self.driver = loader.start().return_value
        self.addCleanup(loader.stop)

        def scan(count):
            count._obj.value = 3
            return 0

        self.driver.canEnumHardwareEx.side_effect = scan

    def library(self):
        library = Mock()
        library.canGetNumberOfChannels.side_effect = lambda count: setattr(count._obj, 'value', 3)
        library.get_channel_info.side_effect = lambda ch: dict(
            device_name='Kvaser Test', serial=100 + ch, dongle_channel=ch + 1)

        def capabilities(channel, item, buffer, size):
            self.assertEqual(size, 4)
            buffer._obj.value = (0, CAP_CAN_FD | CAP_CAN_FD_NONISO, CAP_VIRTUAL)[channel]

        library.canGetChannelData.side_effect = capabilities
        return library

    def test_discovery_preserves_channel_zero_identity_and_capabilities(self):
        backend = KvaserBackend(None)
        with patch.object(backend, '_load_canlib', return_value=self.library()):
            channels = backend.list_channels()
        self.assertEqual([data['handle'] for _, data in channels], [0, 1, 2])
        self.assertEqual([data['is_fd'] for _, data in channels], [False, True, False])
        self.assertEqual([data['is_virtual'] for _, data in channels], [False, False, True])
        self.assertIn('S/N 100', channels[0][0])
        self.assertIn('[FD]', channels[1][0])
        self.assertIn('[Virtual]', channels[2][0])
        from src.session_manager import device_identity
        self.assertEqual(device_identity(channels[0][1]), dict(
            bustype='kvaser', handle=0, serial=100, dongle_channel=1))
        replacement = dict(channels[0][1], serial=999)
        self.assertNotEqual(device_identity(channels[0][1]), device_identity(replacement))

    def test_refresh_finds_four_physical_channels_beyond_cached_virtual_pair(self):
        backend, library = KvaserBackend(None), self.library()

        def scan(count):
            count._obj.value = 6
            return 0

        self.driver.canEnumHardwareEx.side_effect = scan
        library.canGetNumberOfChannels.side_effect = lambda count: setattr(count._obj, 'value', 2)
        library.get_channel_info.side_effect = lambda ch: dict(
            device_name='USBcan Light 4xHS' if ch < 4 else 'Virtual CAN',
            serial=15037 if ch < 4 else 0, dongle_channel=ch + 1 if ch < 4 else ch - 3)
        library.canGetChannelData.side_effect = lambda ch, item, buffer, size: setattr(
            buffer._obj, 'value', 0 if ch < 4 else CAP_VIRTUAL | CAP_CAN_FD)
        with patch.object(backend, '_load_canlib', return_value=library):
            channels = backend.list_channels()
        library.canGetNumberOfChannels.assert_not_called()
        physical = [data for _, data in channels if not data['is_virtual']]
        self.assertEqual([data['dongle_channel'] for data in physical], [1, 2, 3, 4])
        self.assertTrue(all(not data['is_fd'] for data in physical))
        self.assertEqual(len(channels), 6)

    def test_rescan_failure_is_reported_and_old_drivers_use_cached_enumeration(self):
        backend, library = KvaserBackend(None), self.library()
        self.driver.canEnumHardwareEx.side_effect = None
        self.driver.canEnumHardwareEx.return_value = -1
        with patch.object(backend, '_load_canlib', return_value=library), self.assertLogs(level='WARNING'):
            self.assertEqual(backend.list_channels(), [])
        self.assertIn('검색 실패', backend.empty_text)
        with patch('src.can_devices.device_kvaser.ctypes.WinDLL', return_value=SimpleNamespace()):
            self.assertEqual(backend._channel_count(library), 3)

    def test_stale_channel_number_cannot_open_a_different_physical_port(self):
        backend = KvaserBackend(None)
        channel = dict(handle=0, serial=100, dongle_channel=1, is_fd=False)
        library = Mock()
        library.get_channel_info.return_value = dict(serial=999, dongle_channel=1)
        with patch.object(backend, '_load_canlib', return_value=library), patch('can.interface.Bus') as bus, \
                self.assertRaises(DeviceOpenError) as error:
            backend.open(channel, settings(backend, channel))
        self.assertIn('Refresh', error.exception.message)
        bus.assert_not_called()

    def test_missing_driver_shows_installation_hint_and_does_not_break_search(self):
        backend = KvaserBackend(None)
        with patch('src.can_devices.device_kvaser.importlib.import_module', return_value=SimpleNamespace()):
            self.assertEqual(backend.list_channels(), [])
            self.assertEqual(backend.empty_text, DRIVER_MESSAGE)
            with self.assertRaises(DeviceOpenError):
                backend.open({'handle': 0}, None)
        with patch.object(backend, '_load_canlib', return_value=self.library()):
            self.assertEqual(len(backend.list_channels()), 3)
        self.assertEqual(backend.empty_text, type(backend).empty_text)

    def test_inaccessible_channel_does_not_hide_other_channels(self):
        backend, library = KvaserBackend(None), self.library()
        library.get_channel_info.side_effect = [OSError('unplugged'),
            dict(device_name='Kvaser FD', serial=10, dongle_channel=1),
            dict(device_name='Virtual', serial=0, dongle_channel=1)]
        with patch.object(backend, '_load_canlib', return_value=library), self.assertLogs(level='WARNING'):
            channels = backend.list_channels()
        self.assertEqual([data['handle'] for _, data in channels], [1, 2])

    def test_classic_uses_separate_handles_without_tx_echo(self):
        backend = KvaserBackend(None)
        channel = dict(bustype='kvaser', handle=0, is_fd=False)
        with patch.object(backend, '_load_canlib'), patch('can.interface.Bus') as bus:
            _, is_fd = backend.open(channel, settings(backend, channel, '250 kBit/s'))
        self.assertFalse(is_fd)
        bus.assert_called_once_with(interface='kvaser', channel=0, bitrate=250000,
                                    single_handle=False, receive_own_messages=False, accept_virtual=False)

    def test_all_advertised_fd_rates_produce_correct_timing_and_iso_mode(self):
        backend = KvaserBackend(None)
        channel = dict(handle=0, is_fd=True, fd_iso_supported=True, fd_non_iso_supported=True)
        for nominal, rate in backend.nominal_bitrates(channel):
            for data, data_rate in backend.data_bitrates(channel)[1:]:
                for iso in ('ISO', 'Non-ISO'):
                    with self.subTest(nominal=nominal, data=data, iso=iso), \
                            patch.object(backend, '_load_canlib'), patch('can.interface.Bus') as bus:
                        _, is_fd = backend.open(channel, settings(backend, channel, nominal, data, iso))
                        kwargs = bus.call_args.kwargs
                        self.assertTrue(is_fd)
                        self.assertEqual(kwargs['fd_non_iso'], iso == 'Non-ISO')
                        self.assertEqual(kwargs['timing'].nom_bitrate, rate['bitrate'])
                        self.assertEqual(kwargs['timing'].data_bitrate, data_rate['data_bitrate'])
                        self.assertEqual(kwargs['timing'].f_clock, 80_000_000)

    def test_classic_nonpreset_rates_and_virtual_channel(self):
        backend = KvaserBackend(None)
        channel = dict(handle=0, is_fd=False, is_virtual=True)
        for nominal, bitrate in [('800 kBit/s', 800000), ('20 kBit/s', 20000)]:
            with patch.object(backend, '_load_canlib'), patch('can.interface.Bus') as bus:
                backend.open(channel, settings(backend, channel, nominal))
            self.assertTrue(bus.call_args.kwargs['accept_virtual'])
            self.assertEqual(bus.call_args.kwargs['bitrate'], bitrate)
            self.assertEqual(bus.call_args.kwargs['tseg1'], 15)
            self.assertEqual(bus.call_args.kwargs['tseg2'], 4)

    def test_unsupported_fd_mode_is_rejected_before_opening(self):
        backend = KvaserBackend(None)
        fd_channel = dict(handle=0, is_fd=True, fd_iso_supported=True, fd_non_iso_supported=False)
        request = settings(backend, fd_channel, data='2 MBit/s', iso='Non-ISO')
        for channel in (fd_channel, dict(handle=0, is_fd=False)):
            with patch.object(backend, '_load_canlib'), patch('can.interface.Bus') as bus, \
                    self.assertRaises(DeviceOpenError):
                backend.open(channel, request)
            bus.assert_not_called()


class SocketCanBackendTest(unittest.TestCase):
    def backend(self, results):
        backend = SocketCanBackend(None)
        backend.commands = []

        def run(command):
            backend.commands.append(command)
            return results.pop(0)

        backend.run_ip_command = run
        return backend

    def test_hardware_fd_open_runs_down_then_up_with_fd_options(self):
        ok = subprocess.CompletedProcess([], 0, '', '')
        backend = self.backend([ok, ok])
        channel = {'bustype': 'socketcan', 'handle': 'can0', 'is_fd': True}
        with patch('can.interface.Bus') as bus:
            _, is_fd = backend.open(channel, settings(backend, channel, '500 kBit/s', '2 MBit/s', 'ISO'))
        self.assertTrue(is_fd)
        self.assertEqual(backend.commands, [
            ['ip', 'link', 'set', 'can0', 'down'],
            ['ip', 'link', 'set', 'can0', 'up', 'type', 'can', 'bitrate', '500000',
             'dbitrate', '2000000', 'fd', 'on', 'fd-non-iso', 'off']])
        bus.assert_called_once_with(bustype='socketcan', channel='can0', fd=True)

    def test_vcan_only_sets_link_up(self):
        backend = self.backend([subprocess.CompletedProcess([], 0, '', '')])
        channel = {'bustype': 'socketcan', 'handle': 'vcan0', 'is_fd': True}
        with patch('can.interface.Bus') as bus:
            backend.open(channel, settings(backend, channel))
        self.assertEqual(backend.commands, [['ip', 'link', 'set', 'vcan0', 'up']])
        bus.assert_called_once_with(bustype='socketcan', channel='vcan0')

    def test_cancel_and_failure_are_reported_without_opening(self):
        channel = {'bustype': 'socketcan', 'handle': 'can0', 'is_fd': True}
        backend = self.backend([None])
        with patch('can.interface.Bus') as bus, self.assertRaises(DeviceOpenCancelled):
            backend.open(channel, settings(backend, channel))
        backend = self.backend([subprocess.CompletedProcess([], 2, '', 'No such device')])
        with patch('can.interface.Bus') as bus, self.assertRaises(DeviceOpenError) as error:
            backend.open(channel, settings(backend, channel))
        bus.assert_not_called()
        self.assertIn('No such device', error.exception.message)

    def test_channel_list_marks_virtual_interfaces(self):
        with patch('os.listdir', return_value=['eth0', 'can1', 'vcan0', 'can0']):
            channels = SocketCanBackend(None).list_channels()
        self.assertEqual([text for text, _ in channels], ['can0 [SocketCAN]', 'can1 [SocketCAN]', 'Test-Only (vcan0)'])


class GsUsbBackendTest(unittest.TestCase):
    """candleLight / gs_usb (Windows): discovery, serial-based reopen, Classic-only, echo/timestamps."""

    @staticmethod
    def device(serial, product='candleLight USB to CAN adapter'):
        return SimpleNamespace(serial_number=serial, gs_usb=SimpleNamespace(product=product))

    def scanner(self, *devices):
        return SimpleNamespace(scan=Mock(return_value=list(devices)))

    def test_discovery_lists_classic_channels_with_serial(self):
        from src.can_devices.device_gsusb import GsUsbBackend
        scanner = self.scanner(self.device('AAA'), self.device('BBB'))
        with patch('src.can_devices.device_gsusb.load_gs_usb', return_value=scanner), \
                patch('src.can_devices.device_gsusb._release') as release:
            channels = GsUsbBackend(None).list_channels()
        self.assertEqual([text for text, _ in channels],
                         ['candleLight USB to CAN adapter (#0, S/N AAA)', 'candleLight USB to CAN adapter (#1, S/N BBB)'])
        self.assertEqual(channels[1][1], dict(bustype='gs_usb', handle=1, is_fd=False, serial='BBB'))
        release.assert_called_once()   # scan handles are closed so the device can be opened
        self.assertIsNone(GsUsbBackend(None).data_bitrates(channels[0][1]))

    def test_missing_library_or_device_is_explained_in_channel_combo(self):
        from src.can_devices.device_gsusb import GsUsbBackend, LIBRARY_MESSAGE, NOT_FOUND_TEXT
        backend = GsUsbBackend(None)
        with patch('src.can_devices.device_gsusb.load_gs_usb',
                   side_effect=DeviceOpenError('gs_usb 라이브러리 확인', LIBRARY_MESSAGE)):
            self.assertEqual(backend.list_channels(), [])
        self.assertEqual(backend.empty_text, LIBRARY_MESSAGE)
        with patch('src.can_devices.device_gsusb.load_gs_usb', return_value=self.scanner()):
            self.assertEqual(backend.list_channels(), [])
        self.assertEqual(backend.empty_text, NOT_FOUND_TEXT)

    def test_open_follows_serial_after_usb_reordering_and_rejects_fd(self):
        from src.can_devices.device_gsusb import GsUsbBackend
        backend = GsUsbBackend(None)
        channel = dict(bustype='gs_usb', handle=0, is_fd=False, serial='BBB')
        scanner = self.scanner(self.device('AAA'), self.device('BBB'))
        bus_class = Mock(return_value='bus')
        with patch('src.can_devices.device_gsusb.load_gs_usb', return_value=scanner), \
                patch('src.can_devices.device_gsusb._release'), \
                patch('src.can_devices.device_gsusb._make_bus_class', return_value=bus_class):
            self.assertEqual(backend.open(channel, settings(backend, channel, '250 kBit/s')), ('bus', False))
            bus_class.assert_called_once_with(1, 250000)
            with self.assertRaises(DeviceOpenError):
                backend.open(dict(channel, serial='GONE'), settings(backend, channel))
        fd = OpenSettings('500 kBit/s', {'bitrate': 500000}, True, '2 MBit/s', {'data_bitrate': 2000000}, 'ISO')
        with self.assertRaises(DeviceOpenError) as raised:
            backend.open(channel, fd)
        self.assertIn('Classic', raised.exception.message)

    @staticmethod
    def bus():
        """CandleBus without hardware: only the state used by frame handling and send()."""
        import threading
        from src.can_devices.device_gsusb import _make_bus_class
        bus_class = _make_bus_class()
        bus = bus_class.__new__(bus_class)
        bus._base, bus._last_raw, bus._wraps = None, None, 0
        bus._tx_ready, bus._pending, bus._next_echo = threading.Condition(), None, 0
        bus._echo_timeout, bus.resent, bus.channel_info = 0.02, 0, 0
        bus.gs_usb = SimpleNamespace(device_flags=1 << 4, send=Mock())
        return bus

    @staticmethod
    def frame(us, can_id=0x123, echo_id=0xFFFFFFFF, data=(1, 2)):
        from gs_usb.gs_usb_frame import GsUsbFrame
        frame = GsUsbFrame(can_id=can_id, data=list(data))
        frame.echo_id, frame.timestamp_us = echo_id, us
        return frame

    def test_rx_frames_echo_release_and_timestamp_unwrap(self):
        bus = self.bus()
        bus._pending = ('frame', 0.0, 0)
        with patch('time.time', return_value=1000.0):
            first = bus._on_frame(self.frame(4_294_967_000))
            echo = bus._on_frame(self.frame(4_294_967_100, echo_id=3))       # our TX confirmed
            late = bus._on_frame(self.frame(4_294_966_900))                  # slightly out of order
            wrapped = bus._on_frame(self.frame(1_000))                       # 32-bit counter wrapped
        self.assertIsNone(echo)
        self.assertIsNone(bus._pending)                                      # send slot released
        self.assertEqual((first.arbitration_id, bytes(first.data), first.is_rx), (0x123, b'\x01\x02', True))
        self.assertAlmostEqual(first.timestamp, 1000.0)
        self.assertAlmostEqual(late.timestamp - first.timestamp, -0.0001, places=6)   # no false wrap
        self.assertAlmostEqual(wrapped.timestamp - first.timestamp, (2**32 + 1_000 - 4_294_967_000) / 1e6, places=6)

    def test_send_tags_distinct_echo_ids_waits_for_echo_and_rejects_fd(self):
        import can
        from src.can_devices import device_gsusb
        bus = self.bus()
        bus.send(can.Message(arbitration_id=0x18DAF110, data=b'\x11\x22', is_extended_id=True))
        frame = bus.gs_usb.send.call_args.args[0]
        self.assertEqual((frame.can_id, frame.can_dlc, frame.echo_id), (0x18DAF110 | 0x80000000, 2, 0))
        bus._echo_done()
        bus.send(can.Message(arbitration_id=0x100, data=b'\x01'))
        self.assertEqual(bus.gs_usb.send.call_args.args[0].echo_id, 1)
        # No echo for the previous frame: the next send waits, then reports a clear error.
        with patch.object(device_gsusb, 'TX_TIMEOUT_S', 0.05), self.assertRaises(can.CanOperationError) as raised:
            bus.send(can.Message(arbitration_id=0x100, data=b'\x02'))
        self.assertIn('ACK', str(raised.exception))
        self.assertIsNone(bus._pending)   # not blocked forever
        with self.assertRaises(can.CanOperationError):
            bus.send(can.Message(arbitration_id=1, data=bytes(12), is_fd=True))

    def test_frame_without_echo_is_resent_by_watchdog(self):
        import threading, time
        import can
        bus = self.bus()
        bus._reading = threading.Event()
        bus._reading.set()
        watchdog = threading.Thread(target=bus._watch_loop, daemon=True)
        watchdog.start()
        bus.send(can.Message(arbitration_id=0x202, data=b'\x12\x34'))
        time.sleep(0.1)                      # > echo timeout (20 ms): dropped by the adapter
        bus._echo_done()
        bus._reading.clear()
        with bus._tx_ready:
            bus._tx_ready.notify_all()
        watchdog.join(1)
        self.assertGreaterEqual(bus.gs_usb.send.call_count, 2)
        self.assertLessEqual(bus.resent, 2)  # ECHO_RETRIES
        self.assertIs(bus.gs_usb.send.call_args_list[0].args[0], bus.gs_usb.send.call_args_list[1].args[0])

    def test_bit_timing_covers_every_offered_rate_and_keeps_previous_values(self):
        from src.can_devices.device_base import NOMINAL_BITRATES
        from src.can_devices.device_gsusb import bit_timing
        # Limits reported by the tested candleLight (48 MHz, BRP up to 1024).
        cap = SimpleNamespace(fclk_can=48_000_000, brp_min=1, brp_max=1024, brp_inc=1, tseg1_min=1,
                              tseg1_max=16, tseg2_min=1, tseg2_max=8, sjw_max=4)
        for _, rate in NOMINAL_BITRATES:
            with self.subTest(rate=rate):
                brp, tseg1, tseg2, sjw = bit_timing(cap, rate)
                self.assertEqual(cap.fclk_can, rate * brp * (1 + tseg1 + tseg2))   # exact bitrate
                self.assertTrue(1 <= tseg1 <= 16 and 1 <= tseg2 <= 8 and 1 <= sjw <= min(4, tseg2))
        # Same values python-can produced before (>= 100 kBit/s); 50 kBit/s and below now work.
        self.assertEqual(bit_timing(cap, 500_000), (6, 13, 2, 2))
        self.assertEqual(bit_timing(cap, 800_000), (4, 12, 2, 2))
        self.assertEqual(bit_timing(cap, 10_000), (300, 13, 2, 2))
        with self.assertRaises(ValueError):
            bit_timing(SimpleNamespace(**dict(vars(cap), brp_max=64)), 10_000)

    def test_echo_timeout_scales_with_bitrate(self):
        from src.can_devices.device_gsusb import echo_timeout
        self.assertEqual(echo_timeout(500_000), 0.02)
        self.assertAlmostEqual(echo_timeout(10_000), 30 * 135 / 10_000)

    def test_same_adapter_is_matched_by_serial_after_renumbering(self):
        from src.session_manager import same_device
        self.assertTrue(same_device(dict(bustype='gs_usb', handle=0, serial='AAA'),
                                    dict(bustype='gs_usb', handle=1, serial='AAA')))
        self.assertFalse(same_device(dict(bustype='gs_usb', handle=0, serial='AAA'),
                                     dict(bustype='gs_usb', handle=0, serial='BBB')))


class PythonCanBackendTest(unittest.TestCase):
    def test_virtual_and_generic_arguments_match_previous_code(self):
        backend = PythonCanBackend(None)
        virtual = {'bustype': 'virtual', 'handle': 'v1', 'is_fd': True}
        with patch('can.interface.Bus') as bus:
            backend.open(virtual, settings(backend, virtual, data='2 MBit/s'))
        bus.assert_called_once_with(bustype='virtual', channel='v1', receive_own_messages=True, fd=True)
        generic = {'bustype': 'kvaser', 'handle': 0, 'is_fd': True}
        with patch('can.interface.Bus') as bus:
            backend.open(generic, settings(backend, generic, '250 kBit/s', '2 MBit/s'))
        bus.assert_called_once_with(bustype='kvaser', channel=0, receive_own_messages=True,
                                    bitrate=250000, fd=True, data_bitrate=2000000)


class MainWindowMethodComboTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from src.main_window import UniversalCANMonitor
        from src.session_manager import SessionManager
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        with patch.object(UniversalCANMonitor, 'search_can_channels'), patch.object(SessionManager, 'start'):
            self.main = UniversalCANMonitor()
        self.main.session.ready = False
        self.main.session.directory = Path(self.temp.name)
        self.main.session.autosave_path = Path(self.temp.name) / 'auto.pjjsettings'
        self.addCleanup(self.main.deleteLater)
        self.addCleanup(self.main.close)

    def test_each_bus_has_method_combo_and_search_uses_it(self):
        expected = [b.key for b in self.main.devices.selectable()]
        if len(expected) > 1:
            expected.append('all')   # ALL: every method of this OS at once (not on Linux)
        for bus in (1, 2, 3, 4):
            combo = self.main.combo_backend[bus]
            self.assertEqual([combo.itemData(i) for i in range(combo.count())], expected)
        backend = self.main.selected_backend(2)
        found = [('Dev A', {'bustype': backend.key, 'handle': 'a', 'is_fd': False})]
        with patch.object(type(backend), 'list_channels', return_value=found) as listing:
            self.main._populate_channels()
        listing.assert_called_once()  # one search per method, shared by all BUS rows
        self.assertEqual(self.main.combo_channels[2].currentText(), 'Dev A')

    def test_method_combo_locks_while_connected_and_session_stores_it(self):
        combo = self.main.combo_channels[1]
        combo.clear()
        combo.addItem('v', dict(bustype='virtual', handle='v', is_fd=False))
        receiver = Mock()
        with patch('src.main_window.can.interface.Bus', return_value=Mock()), \
                patch('src.main_window.CANReceiverThread', return_value=receiver):
            self.main.open_can(1)
        self.assertIsNotNone(self.main.buses[1])
        self.assertFalse(self.main.combo_backend[1].isEnabled())
        self.assertEqual(self.main.session.capture()['can']['1']['backend'], self.main.selected_backend(1).key)
        self.main.close_can(1)
        self.assertTrue(self.main.combo_backend[1].isEnabled())

    def test_old_session_without_method_key_still_validates(self):
        from src.session_storage import validate_structure
        state = self.main.session.capture()
        for cfg in state['can'].values():
            cfg.pop('backend', None)
        validate_structure(state)
        state['can']['1']['backend'] = 3
        with self.assertRaises(ValueError):
            validate_structure(state)

    def test_kvaser_selection_panel_mirror_open_and_session_restore(self):
        from src.connection_controls import ConnectionGrid
        main = self.main
        backend = main.devices.get('kvaser')
        channel = dict(bustype='kvaser', handle=0, serial=1234, dongle_channel=1,
                       is_fd=True, fd_iso_supported=True, fd_non_iso_supported=False)
        combo = main.combo_backend[4]
        if combo.findData('kvaser') < 0:  # Exercise the Windows UI also on Linux CI.
            combo.addItem('Kvaser', 'kvaser')
        with patch.object(backend, 'list_channels', return_value=[('Kvaser Test [FD]', channel)]):
            combo.setCurrentIndex(combo.findData('kvaser'))
        main.combo_data_bitrate[4].setCurrentIndex(main.combo_data_bitrate[4].findText('2 MBit/s'))
        grid = ConnectionGrid(main, mirror=True)
        self.addCleanup(grid.deleteLater)
        self.addCleanup(grid.close)
        grid.refresh()
        self.assertEqual(grid.fields[4, 'backend'].currentData(), 'kvaser')
        self.assertEqual(grid.fields[4, 'channels'].currentData()['handle'], 0)
        library = Mock()
        library.get_channel_info.return_value = channel
        with patch.object(backend, '_load_canlib', return_value=library), patch('can.interface.Bus', return_value=Mock()) as bus, \
                patch('src.main_window.CANReceiverThread', return_value=Mock()):
            main.open_can(4)
        self.assertEqual(bus.call_args.kwargs['interface'], 'kvaser')
        self.assertTrue(main.bus_capabilities[4]['is_fd'])
        grid.refresh()
        self.assertFalse(grid.fields[4, 'backend'].isEnabled())
        saved = main.session.capture()['can']['4']
        self.assertEqual(saved['backend'], 'kvaser')
        self.assertEqual(saved['device'], dict(bustype='kvaser', handle=0, serial=1234, dongle_channel=1))
        main.close_can(4)
        main.session.errors = []
        saved['is_open'] = False
        # Rescanning can assign a new global number to the same physical port.
        main.combo_channels[4].setItemData(0, dict(channel, handle=5))
        main.combo_data_bitrate[4].setCurrentIndex(0)
        main.session.apply_can(4, saved)
        self.assertEqual(main.session.errors, [])
        self.assertEqual(main.combo_data_bitrate[4].currentText(), '2 MBit/s')

    def test_same_kvaser_port_cannot_be_opened_twice_after_channel_renumbering(self):
        from src.session_manager import same_device
        first = dict(bustype='kvaser', handle=0, serial=15037, dongle_channel=1)
        second = dict(first, handle=5)
        self.assertTrue(same_device(first, second))
        self.assertFalse(same_device(first, dict(second, dongle_channel=2)))
        main = self.main
        main.combo_channels[1].addItem('old', first)
        main.buses[1] = Mock()
        main.combo_channels[4].addItem('new', second)
        with patch.object(main, '_open_can') as opening:
            main.open_can(4)
        opening.assert_not_called()


if __name__ == '__main__':
    unittest.main()
