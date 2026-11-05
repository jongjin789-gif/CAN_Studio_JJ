"""CAN device modules, one file per communication method.

    device_base.py       common contract (CanDeviceBackend)
    device_pcan.py       PCAN-Basic (Windows)
    device_socketcan.py  SocketCAN can0~ (Linux, any adapter vendor)
    device_pythoncan.py  generic python-can interfaces (fallback, not listed yet)

Adding a method (e.g. Kvaser):
    1. create ``device_kvaser.py`` with a ``CanDeviceBackend`` subclass
       (key='kvaser', bustypes=('kvaser',), platforms=('Windows',));
    2. add the class to ``BACKEND_CLASSES`` below.
Existing modules and the rest of the program do not need to change.
"""
import platform

from .device_base import (CanDeviceBackend, DeviceOpenCancelled, DeviceOpenError,  # noqa: F401
                          OpenSettings, DEFAULT_NOMINAL_INDEX)
from .device_pcan import PcanBackend
from .device_pythoncan import PythonCanBackend
from .device_socketcan import SocketCanBackend

# Order defines the order in the "CAN 방식" combo box.
BACKEND_CLASSES = (PcanBackend, SocketCanBackend, PythonCanBackend)
FALLBACK_KEY = PythonCanBackend.key


class DeviceRegistry:
    """Backend instances for one main window."""

    def __init__(self, host, classes=BACKEND_CLASSES):
        self.backends = {cls.key: cls(host) for cls in classes}

    def get(self, key):
        return self.backends.get(key)

    def selectable(self, system=None):
        system = system or platform.system()
        return [b for b in self.backends.values() if b.selectable and system in b.platforms]

    def default_key(self, system=None):
        choices = self.selectable(system)
        return choices[0].key if choices else FALLBACK_KEY

    def for_bustype(self, bustype):
        for backend in self.backends.values():
            if bustype in backend.bustypes:
                return backend
        return self.backends[FALLBACK_KEY]

    def key_for_device(self, device):
        """Backend key for a saved device identity (``{'bustype': ..., 'handle': ...}``)."""
        if isinstance(device, dict) and device.get('bustype'):
            return self.for_bustype(device['bustype']).key
        return None
