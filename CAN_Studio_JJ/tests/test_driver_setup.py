"""ALL CAN method and Windows driver-setup guidance (detection, one question per action, combo marks)."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PyQt5.QtWidgets import QApplication

from src import driver_setup as ds

GS = 'USB\\VID_1D50&PID_606F&MI_00\\7&1&0&0000'
PCAN = 'USB\\VID_0C72&PID_0012\\6&2&0&4'
KVASER = 'USB\\VID_0BFD&PID_0106\\008311-1'


def instance(hw, inst_id, service, driver='{class}\\0001'):
    vid, pid = hw[4:8], hw[13:17]
    mi = hw.split('MI_')[1] if 'MI_' in hw else None
    return vid, pid, mi, inst_id, dict(Service=service, Driver=driver, DeviceDesc=f'desc;{hw}', FriendlyName='')


class DetectionTest(unittest.TestCase):
    def detect(self, instances, connected=None, kvaser_library=True, products=None):
        connected = set(connected if connected is not None else [i[3] for i in instances])
        return ds.find_issues(products, instances=instances, present=lambda iid: iid in connected,
                              kvaser_library=kvaser_library)

    def test_connected_adapters_with_wrong_or_missing_driver_are_found(self):
        instances = [instance('VID_1D50&PID_606F&MI_00', GS, 'usbser'),
                     instance('VID_0C72&PID_0012', PCAN, ''),
                     instance('VID_0BFD&PID_0106', KVASER, 'kcany')]
        issues = self.detect(instances, kvaser_library=False)
        self.assertEqual([(i.product, i.instance_id, i.service) for i in issues],
                         [('gs_usb', GS, 'usbser'), ('pcan', PCAN, ''), ('kvaser', KVASER, 'kcany')])
        self.assertEqual(self.detect(instances, kvaser_library=False, products={'pcan'})[0].product, 'pcan')

    def test_working_disconnected_and_by_design_driverless_interfaces_are_ignored(self):
        instances = [instance('VID_1D50&PID_606F&MI_00', GS, 'WinUSB'),                    # OK
                     instance('VID_0C72&PID_0012', PCAN, 'PCAN_USB'),                       # OK
                     instance('VID_0BFD&PID_0106', KVASER, 'kcany'),                        # OK with CANlib
                     instance('VID_1D50&PID_606F', 'USB\\VID_1D50&PID_606F\\SER', 'usbccgp'),   # composite parent
                     instance('VID_0C72&PID_0011&MI_01', 'USB\\VID_0C72&PID_0011&MI_01\\x', ''),  # Pro FD LIN: no driver by design
                     instance('VID_0C72&PID_000C', 'USB\\VID_0C72&PID_000C\\old', '')]       # not connected
        connected = [i[3] for i in instances if not i[3].endswith('old')]
        self.assertEqual(self.detect(instances, connected=connected), [])

    def test_gs_usb_fix_removes_only_an_exclusive_oem_package(self):
        issue = ds.DriverIssue('gs_usb', GS, 'candleLight', 'usbser')
        mine = instance('VID_1D50&PID_606F&MI_00', GS, 'usbser', '{ports}\\0007')
        infs = {'{ports}\\0007': 'oem7.inf', '{ports}\\0001': 'oem7.inf', '{ports}\\0002': 'usbser.inf'}
        self.assertEqual(ds.gs_usb_fix_plan(issue, [mine], infs.get), ('oem7.inf', ''))
        other = instance('VID_2341&PID_0043', 'USB\\VID_2341&PID_0043\\1', 'usbser', '{ports}\\0001')
        inf, error = ds.gs_usb_fix_plan(issue, [mine, other], infs.get)
        self.assertEqual(inf, '')
        self.assertIn('다른 장치', error)
        builtin = instance('VID_1D50&PID_606F&MI_00', GS, 'usbser', '{ports}\\0002')
        self.assertIn('Windows 기본', ds.gs_usb_fix_plan(issue, [builtin], infs.get)[1])
        no_driver = ds.DriverIssue('gs_usb', GS, 'candleLight', '')
        self.assertEqual(ds.gs_usb_fix_plan(no_driver, [], infs.get), ('', ''))   # rescan only

    def test_pnputil_line_survives_cmd_quote_stripping(self):
        exe, params = ds.pnputil_command('/add-driver "C:\\a b\\x.inf" /install', '/scan-devices')
        self.assertTrue(exe.lower().endswith('cmd.exe'))
        self.assertTrue(params.startswith('/c ""') and params.endswith('"'))
        self.assertIn('/install && "', params)


class MainWindowAllAndGuidanceTest(unittest.TestCase):
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
        if self.main.combo_backend[1].findData('all') < 0:
            self.skipTest('ALL is offered only where several CAN methods exist (Windows)')
        self.issues = []
        patcher = patch('src.driver_setup.find_issues', side_effect=lambda products=None, **k: [
            i for i in self.issues if products is None or i.product in products])
        patcher.start()
        self.addCleanup(patcher.stop)
        supported = patch('src.driver_setup.supported', return_value=True)
        supported.start()
        self.addCleanup(supported.stop)

    def select(self, bus, method):
        combo = self.main.combo_backend[bus]
        combo.blockSignals(True)
        combo.setCurrentIndex(combo.findData(method))
        combo.blockSignals(False)

    def fake_listings(self):
        devices = self.main.devices
        listings = {
            'pcan': [('PCAN-USB FD (0x51)', dict(bustype='pcan', handle=0x51, is_fd=True))],
            'kvaser': [('Kvaser Virtual (ch 0)', dict(bustype='kvaser', handle=0, is_fd=True, is_virtual=True))],
            'gs_usb': [('candleLight (#0, S/N A)', dict(bustype='gs_usb', handle=0, is_fd=False, serial='A'))],
        }
        return [patch.object(type(devices.get(key)), 'list_channels', return_value=found)
                for key, found in listings.items()]

    def test_default_is_pcan_and_all_lists_every_method_without_virtual_channels(self):
        self.assertEqual(self.main.selected_method(1), 'pcan')
        self.select(1, 'all')
        patches = self.fake_listings()
        for p in patches:
            p.start()
        try:
            self.main.search_can_channels(1)
        finally:
            for p in patches:
                p.stop()
        combo = self.main.combo_channels[1]
        self.assertEqual([combo.itemText(i) for i in range(combo.count())],
                         ['PCAN-USB FD (0x51)', 'candleLight (#0, S/N A)'])
        self.assertEqual(self.main.session.capture()['can']['1']['backend'], 'all')
        # A candleLight channel picked under ALL opens with the candleLight module and is Classic only.
        combo.setCurrentIndex(1)
        self.assertEqual(self.main.channel_backend(1).key, 'gs_usb')
        self.assertEqual(self.main.combo_data_bitrate[1].currentText(), 'N/A')

    def test_all_without_any_device_explains_each_method(self):
        self.select(1, 'all')
        with patch.object(type(self.main.devices.get('pcan')), 'list_channels', return_value=[]), \
                patch.object(type(self.main.devices.get('kvaser')), 'list_channels', return_value=[]), \
                patch.object(type(self.main.devices.get('gs_usb')), 'list_channels', return_value=[]), \
                patch.object(type(self.main.devices.get('kvaser')), 'empty_text', 'Kvaser Drivers for Windows 설치 후 …'):
            self.main.search_can_channels(1)
        text = self.main.combo_channels[1].currentText() or self.main.combo_channels[1].itemText(0)
        self.assertIn('PCAN 없음', text)
        self.assertIn('Kvaser 드라이버 없음', text)
        self.assertFalse(self.main.combo_channels[1].isEnabled())

    def test_adapter_needing_driver_is_marked_but_not_selectable(self):
        self.issues = [ds.DriverIssue('gs_usb', GS, 'candleLight USB to CAN adapter', 'usbser')]
        self.select(1, 'all')
        patches = self.fake_listings()
        for p in patches:
            p.start()
        try:
            self.main.search_can_channels(1)
        finally:
            for p in patches:
                p.stop()
        combo = self.main.combo_channels[1]
        last = combo.count() - 1
        self.assertEqual(combo.itemText(last), 'candleLight USB to CAN adapter · WinUSB 드라이버 필요')
        self.assertIsNone(combo.itemData(last))
        self.assertFalse(combo.model().item(last).isEnabled())
        self.assertEqual(combo.currentIndex(), 0)     # a working channel stays selected

    def test_one_question_per_action_and_never_twice_for_the_same_adapter(self):
        self.issues = [ds.DriverIssue('gs_usb', GS, 'candleLight', 'usbser'),
                       ds.DriverIssue('pcan', PCAN, 'PCAN-USB FD', '')]
        for bus in (1, 2):
            self.select(bus, 'all')
        controller = self.main.driver_setup
        asked = []
        controller._offer_gs_usb = lambda bus, issue, note: asked.append((bus, issue.product, note))
        controller._offer_pcan = lambda bus, issue, note: asked.append((bus, issue.product, note))
        controller.offer([1, 2])                 # e.g. Refresh of all BUS rows
        self.assertEqual(len(asked), 1)
        self.assertEqual(asked[0][:2], (1, 'gs_usb'))
        self.assertIn('다른 장치 1개', asked[0][2])
        controller.offer([1, 2])                 # next user action: the other adapter
        controller.offer([1, 2])                 # nothing left to ask in this session
        self.assertEqual([a[1] for a in asked], ['gs_usb', 'pcan'])
        controller.busy = True                   # never while a driver operation runs
        controller.asked.clear()
        controller.offer([1])
        self.assertEqual(len(asked), 2)

    def test_method_specific_search_asks_only_about_its_own_product(self):
        self.issues = [ds.DriverIssue('kvaser', KVASER, 'Kvaser USBcan', '')]
        controller = self.main.driver_setup
        controller._offer_kvaser = Mock()
        self.select(1, 'pcan')
        controller.offer([1])
        controller._offer_kvaser.assert_not_called()
        self.select(1, 'kvaser')
        controller.offer([1])
        controller._offer_kvaser.assert_called_once()

    def test_automatic_search_never_asks(self):
        self.issues = [ds.DriverIssue('gs_usb', GS, 'candleLight', 'usbser')]
        self.select(1, 'gs_usb')
        with patch.object(self.main.driver_setup, 'offer') as offer, \
                patch.object(type(self.main.devices.get('gs_usb')), 'list_channels', return_value=[]):
            self.main.search_can_channels(1)     # start-up / session restore path
            QApplication.processEvents()
        offer.assert_not_called()


if __name__ == '__main__':
    unittest.main()
