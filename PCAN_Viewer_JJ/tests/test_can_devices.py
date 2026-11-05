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
                             PcanBackend, PythonCanBackend, SocketCanBackend)
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
        self.assertEqual([b.key for b in registry.selectable('Windows')], ['pcan'])
        self.assertEqual([b.key for b in registry.selectable('Linux')], ['socketcan'])
        self.assertEqual(registry.default_key('Windows'), 'pcan')
        self.assertEqual(registry.default_key('Linux'), 'socketcan')

    def test_channel_bustype_selects_module(self):
        registry = DeviceRegistry(None)
        self.assertIsInstance(registry.for_bustype('pcan'), PcanBackend)
        self.assertIsInstance(registry.for_bustype('socketcan'), SocketCanBackend)
        self.assertIsInstance(registry.for_bustype('virtual'), PythonCanBackend)
        self.assertEqual(registry.key_for_device({'bustype': 'pcan', 'handle': 0x51}), 'pcan')
        self.assertIsNone(registry.key_for_device(None))

    def test_bitrate_texts_are_identical_for_every_module(self):
        fd = {'bustype': 'x', 'handle': 1, 'is_fd': True}
        for cls in (PcanBackend, SocketCanBackend, PythonCanBackend):
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


if __name__ == '__main__':
    unittest.main()
