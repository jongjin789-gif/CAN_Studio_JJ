"""Common contract for CAN device modules (PCAN, SocketCAN, Kvaser, python-can ...).

Each device module lives in its own ``device_<name>.py`` file and implements
:class:`CanDeviceBackend`.  The rest of the program never talks to a driver directly:

* channel search / bitrate lists / open / status polling go through the backend;
* transmit and receive use the python-can ``Bus`` object that ``open()`` returns
  (``bus.send()`` / ``bus.recv()``).  A device that python-can does not support can
  implement its own ``can.BusABC`` subclass inside its module and return it from
  ``open()``; nothing else in the program has to change.

DBC/SYM handling, graphs, logs and the User Panel are bound to logical BUS 1-4
and are independent of the device module.
"""
from dataclasses import dataclass


class DeviceOpenError(Exception):
    """Expected setup failure that should be shown as-is (no traceback)."""

    def __init__(self, title, message):
        super().__init__(message)
        self.title = title
        self.message = message


class DeviceOpenCancelled(Exception):
    """The user cancelled a prompt (e.g. a sudo password) while opening."""


@dataclass
class OpenSettings:
    """Values selected in the connection grid for one BUS."""
    bitrate_text: str
    bitrate_data: object
    data_enabled: bool
    data_text: str
    data_data: object
    fd_iso: str

    @property
    def fd_requested(self):
        return bool(self.data_enabled and self.data_data)


# Display strings are what sessions and User Panel files store; keep them stable.
NOMINAL_BITRATES = (
    ("1 MBit/s", 1000000), ("800 kBit/s", 800000), ("500 kBit/s", 500000),
    ("250 kBit/s", 250000), ("125 kBit/s", 125000), ("100 kBit/s", 100000),
    ("50 kBit/s", 50000), ("20 kBit/s", 20000), ("10 kBit/s", 10000),
)
DATA_BITRATES = (("1 MBit/s", 1000000), ("2 MBit/s", 2000000),
                 ("4 MBit/s", 4000000), ("8 MBit/s", 8000000))
DEFAULT_NOMINAL_INDEX = 2  # 500 kBit/s


class CanDeviceBackend:
    #: Stored in sessions (``can.<bus>.backend``); never rename an existing key.
    key = ''
    #: Shown in the "CAN 방식" combo box.
    label = ''
    #: python-can ``bustype`` values owned by this module (from channel data).
    bustypes = ()
    #: ``platform.system()`` values where the module is offered in the combo box.
    platforms = ()
    #: False keeps a module usable for saved/test channels without listing it.
    selectable = True
    #: Shown when no channel is found.
    empty_text = 'No available CAN channels found'
    #: Diagnostic context and message used when ``open()`` raises unexpectedly.
    diag_context = 'CAN 연결'
    open_failure_text = 'Failed to open CAN channel for Bus {bus}:\n{error}'

    def __init__(self, host):
        # ``host`` is the main window; used only as a dialog parent and for logging.
        self.host = host

    # ---- channel discovery -------------------------------------------------
    def list_channels(self):
        """Return ``[(display_text, channel_data), ...]``.

        ``channel_data`` must contain at least ``bustype``, ``handle`` and ``is_fd``.
        """
        return []

    # ---- bitrates ----------------------------------------------------------
    def nominal_bitrates(self, channel_data):
        return [(text, {'bitrate': value}) for text, value in NOMINAL_BITRATES]

    def data_bitrates(self, channel_data):
        """FD data-phase options (first entry "Off"), or ``None`` without FD."""
        if not (isinstance(channel_data, dict) and channel_data.get('is_fd', False)):
            return None
        return [("Off", None)] + [(text, {'data_bitrate': value}) for text, value in DATA_BITRATES]

    # ---- connection --------------------------------------------------------
    def open(self, channel_data, settings):
        """Open the channel and return ``(bus, is_fd)``.

        Raise :class:`DeviceOpenError` for expected failures, :class:`DeviceOpenCancelled`
        when the user cancelled; any other exception is reported as a connection failure.
        """
        raise NotImplementedError

    def status_label(self, channel_data):
        return channel_data.get('bustype', self.key)

    # ---- monitoring --------------------------------------------------------
    def poll_status(self, bus):
        """Return ``None`` (no information), ``('ok', detail)``, ``('warn', detail)``,
        ``('busoff', detail)`` or ``('lost', detail)`` when the device was removed."""
        return None

    def error_counters(self, bus):
        """Return the controller ``(TEC, REC)`` error counters, or ``None`` if unsupported."""
        return None
