"""Windows: find connected USB-CAN adapters whose driver is missing or wrong, and guide the setup.

Detection reads the device registry (HKLM\\SYSTEM\\CurrentControlSet\\Enum\\USB) and asks the
configuration manager whether each instance is connected; no administrator rights are needed
and a search takes a few milliseconds.

Products
* PCAN (PEAK, VID 0C72): driver service ``PCAN_USB``; installs ``Driver/pcan_drivers/pcan_usb.inf``.
* Kvaser (VID 0BFD): any Kvaser driver service plus the CANlib library; runs the Kvaser
  driver setup in ``Driver/kvaser_drivers`` (the program must be restarted afterwards).
* candleLight / gs_usb: service ``WinUSB``; removes a wrong (e.g. COM port) driver package so
  Windows binds WinUSB, which the adapter requests itself.

Composite parents (``usbccgp``) and interfaces other than the CAN one (e.g. the LIN interface
``MI_01`` of a PCAN-USB Pro FD, which has no driver by design) are ignored.
"""
from dataclasses import dataclass
import glob
import logging
import os
import platform
import re
import sys

log = logging.getLogger(__name__)

PEAK_DOWNLOAD_URL = 'https://www.peak-system.com/Drivers.523.0.html'
KVASER_DOWNLOAD_URL = 'https://kvaser.com/download/'


@dataclass(frozen=True)
class Product:
    key: str            # backend key that lists this product's channels
    label: str
    ids: tuple          # (VID, PID or None for any, interface 'MI_xx' or None)


PRODUCTS = (
    Product('pcan', 'PCAN-USB', (('0C72', '000C', None), ('0C72', '0011', '00'), ('0C72', '0012', None),
                                 ('0C72', '0013', None), ('0C72', '0014', None), ('0C72', '0030', None))),
    Product('kvaser', 'Kvaser', (('0BFD', None, None), ('0BFD', None, '00'))),
    Product('gs_usb', 'candleLight (gs_usb)', (('1D50', '606F', '00'), ('1209', '2323', '00'), ('1CD2', '606F', '00'),
                                               ('16D0', '10B8', '00'), ('1209', 'CA01', '00'))),
)
PRODUCT_BY_KEY = {p.key: p for p in PRODUCTS}
_HW_ID = re.compile(r'^VID_([0-9A-F]{4})&PID_([0-9A-F]{4})(?:&MI_([0-9A-F]{2}))?$', re.I)


@dataclass(frozen=True)
class DriverIssue:
    product: str        # Product.key
    instance_id: str    # e.g. USB\VID_1D50&PID_606F&MI_00\7&16c48c12&0&0000
    name: str
    service: str        # current driver service ('' = none)

    @property
    def label(self):
        return PRODUCT_BY_KEY[self.product].label

    @property
    def hint(self):
        return {'pcan': '드라이버 설치 필요', 'kvaser': '드라이버 설치 필요',
                'gs_usb': 'WinUSB 드라이버 필요'}[self.product]


def supported():
    return platform.system() == 'Windows'


def driver_dir():
    """``Driver`` folder beside the executable (or the project folder when run from source)."""
    base = (os.path.dirname(sys.executable) if getattr(sys, 'frozen', False)
            else os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(base, 'Driver')


# ---- detection ---------------------------------------------------------------------------
def _present(instance_id):
    import ctypes
    cfg = ctypes.WinDLL('cfgmgr32')
    cfg.CM_Locate_DevNodeW.argtypes = [ctypes.POINTER(ctypes.c_uint32), ctypes.c_wchar_p, ctypes.c_uint32]
    node = ctypes.c_uint32()
    return cfg.CM_Locate_DevNodeW(ctypes.byref(node), instance_id, 0) == 0   # CR_SUCCESS


def usb_instances():
    """Yield ``(vid, pid, mi, instance_id, values)`` for every USB device the registry knows."""
    import winreg
    root = r'SYSTEM\CurrentControlSet\Enum\USB'
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, root) as usb:
        index = 0
        while True:
            try:
                hardware = winreg.EnumKey(usb, index)
            except OSError:
                return
            index += 1
            match = _HW_ID.match(hardware)
            if not match:
                continue
            vid, pid, mi = (part.upper() if part else None for part in match.groups())
            with winreg.OpenKey(usb, hardware) as hardware_key:
                sub = 0
                while True:
                    try:
                        instance = winreg.EnumKey(hardware_key, sub)
                    except OSError:
                        break
                    sub += 1
                    values = {}
                    try:
                        with winreg.OpenKey(hardware_key, instance) as key:
                            for name in ('Service', 'Driver', 'DeviceDesc', 'FriendlyName'):
                                try:
                                    values[name] = str(winreg.QueryValueEx(key, name)[0])
                                except OSError:
                                    values[name] = ''
                    except OSError:
                        continue
                    yield vid, pid, mi, f'USB\\{hardware}\\{instance}', values


def _kvaser_library_available():
    try:
        from src.can_devices.device_kvaser import KvaserBackend
        KvaserBackend._load_canlib()
        return True
    except Exception:
        return False


def _driver_ok(product, service, kvaser_library):
    service = (service or '').lower()
    if product == 'pcan':
        return service == 'pcan_usb'
    if product == 'gs_usb':
        return service == 'winusb'
    return bool(service) and kvaser_library   # Kvaser: a driver service and CANlib


def connected_adapters(product_keys=None, instances=None, present=None, kvaser_library=None):
    """``[(DriverIssue-like record, driver_ok)]`` for connected adapters of ``product_keys``."""
    if not supported() and instances is None:
        return []
    wanted = [p for p in PRODUCTS if product_keys is None or p.key in product_keys]
    if not wanted:
        return []
    present = present or _present
    try:
        candidates = list(instances if instances is not None else usb_instances())
    except OSError as exc:
        log.warning('USB device registry is not readable: %s', exc)
        return []
    found = []
    for vid, pid, mi, instance_id, values in candidates:
        service = values.get('Service', '')
        if service.lower() == 'usbccgp':
            continue                    # composite parent, handled by Windows
        product = next((p for p in wanted for v, pp, m in p.ids
                        if v == vid and (pp is None or pp == pid) and m == mi), None)
        if product is None or not present(instance_id):
            continue
        if product.key == 'kvaser' and kvaser_library is None:
            kvaser_library = _kvaser_library_available()
        name = (values.get('FriendlyName') or values.get('DeviceDesc') or product.label).split(';')[-1]
        found.append((DriverIssue(product.key, instance_id, name, service),
                      _driver_ok(product.key, service, bool(kvaser_library))))
    return found


def find_issues(product_keys=None, **kwargs):
    """Connected adapters of ``product_keys`` (all when None) whose driver is missing/wrong."""
    return [record for record, ok in connected_adapters(product_keys, **kwargs) if not ok]


# ---- candleLight: wrong driver package ---------------------------------------------------
def _class_inf(driver_value):
    """INF of an installed driver, from the device's ``Driver`` registry value."""
    import winreg
    if not driver_value:
        return ''
    try:
        path = r'SYSTEM\CurrentControlSet\Control\Class' + '\\' + driver_value
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path) as key:
            return str(winreg.QueryValueEx(key, 'InfPath')[0])
    except OSError:
        return ''


def gs_usb_fix_plan(issue, instances=None, class_inf=None):
    """``(inf or '', error or '')``: the driver package to remove so WinUSB can bind.

    The package is removed only when every device that uses it is a gs_usb adapter; a
    Windows built-in driver (not ``oemNN.inf``) is never removed.
    """
    class_inf = class_inf or _class_inf
    instances = list(instances if instances is not None else usb_instances())
    own = next((values for *_, instance_id, values in instances if instance_id == issue.instance_id), {})
    if not issue.service:
        return '', ''                   # no driver at all: a device rescan binds WinUSB
    inf = class_inf(own.get('Driver', ''))
    if not re.fullmatch(r'oem\d+\.inf', inf or '', re.I):
        return '', (f'현재 드라이버({issue.service}, {inf or "알 수 없음"})는 Windows 기본 드라이버라 '
                    '자동으로 바꿀 수 없습니다. Zadig에서 Interface 0을 WinUSB로 바꿔 주세요.')
    gs_ids = {(v, pp) for v, pp, _ in PRODUCT_BY_KEY['gs_usb'].ids}
    others = [instance_id for vid, pid, mi, instance_id, values in instances
              if (vid, pid) not in gs_ids and values.get('Driver') and class_inf(values['Driver']).lower() == inf.lower()]
    if others:
        return '', f'{inf} 드라이버를 다른 장치도 사용 중이라 자동으로 제거하지 않습니다: {others[0]}'
    return inf, ''


# ---- elevated commands ---------------------------------------------------------------------
def run_elevated(executable, parameters, show_window=False):
    """Run with administrator rights (UAC) and wait. Returns the exit code, or None if cancelled."""
    import ctypes
    from ctypes import wintypes

    class ShellExecuteInfo(ctypes.Structure):
        _fields_ = [('cbSize', wintypes.DWORD), ('fMask', ctypes.c_ulong), ('hwnd', wintypes.HWND),
                    ('lpVerb', wintypes.LPCWSTR), ('lpFile', wintypes.LPCWSTR),
                    ('lpParameters', wintypes.LPCWSTR), ('lpDirectory', wintypes.LPCWSTR),
                    ('nShow', ctypes.c_int), ('hInstApp', wintypes.HINSTANCE), ('lpIDList', ctypes.c_void_p),
                    ('lpClass', wintypes.LPCWSTR), ('hkeyClass', wintypes.HKEY), ('dwHotKey', wintypes.DWORD),
                    ('hIcon', wintypes.HANDLE), ('hProcess', wintypes.HANDLE)]

    info = ShellExecuteInfo()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = 0x00000040 | 0x00000400   # SEE_MASK_NOCLOSEPROCESS | SEE_MASK_FLAG_NO_UI
    info.lpVerb, info.lpFile, info.lpParameters = 'runas', executable, parameters
    info.nShow = 1 if show_window else 0
    shell32, kernel32 = ctypes.windll.shell32, ctypes.windll.kernel32
    shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(ShellExecuteInfo)]
    if not shell32.ShellExecuteExW(ctypes.byref(info)):
        if ctypes.GetLastError() == 1223:   # ERROR_CANCELLED: UAC declined
            return None
        raise ctypes.WinError()
    try:
        kernel32.WaitForSingleObject(info.hProcess, 0xFFFFFFFF)
        code = wintypes.DWORD()
        kernel32.GetExitCodeProcess(info.hProcess, ctypes.byref(code))
        return code.value
    finally:
        kernel32.CloseHandle(info.hProcess)


def pnputil_command(*commands):
    """``(cmd.exe, arguments)`` running pnputil commands in order, stopping at the first failure.

    The whole command is wrapped in one more pair of quotes: with several quoted paths,
    ``cmd /c`` removes the first and the last quote of the line, which would break a path.
    """
    system = os.environ.get('SystemRoot', r'C:\Windows')
    pnputil = os.path.join(system, 'System32', 'pnputil.exe')
    line = ' && '.join(f'"{pnputil}" {arguments}' for arguments in commands)
    return os.path.join(system, 'System32', 'cmd.exe'), f'/c "{line}"'


def pcan_inf():
    path = os.path.join(driver_dir(), 'pcan_drivers', 'pcan_usb.inf')
    return path if os.path.isfile(path) else ''


def kvaser_setup():
    found = sorted(glob.glob(os.path.join(driver_dir(), 'kvaser_drivers', 'kvaser_drivers_setup*.exe')))
    return found[-1] if found else ''


# pnputil: 0 = OK, 3010 = OK (restart needed), 259 = nothing to do (already installed).
PNPUTIL_OK = (0, 3010, 259)
