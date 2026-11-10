"""candleLight / gs_usb USB-CAN adapters on Windows (WinUSB driver, libusb).

The adapter's CAN interface must use the WinUSB driver; ``Driver/candleLight_WinUSB``
switches it when a serial (COM port) driver was installed by mistake.  On Linux the
kernel ``gs_usb`` driver exposes the same adapters as ``canX``, so they are used
through SocketCAN there and this module is not offered.

python-can's gs_usb interface supports Classic CAN only (no CAN FD).
"""
import logging
import queue
import threading
import time

import can

from .device_base import CanDeviceBackend, DeviceOpenError

log = logging.getLogger(__name__)

LIBRARY_MESSAGE = 'gs_usb 라이브러리 없음 · pip install gs_usb libusb-package 후 다시 실행하세요.'
NOT_FOUND_TEXT = 'candleLight 장치 없음 · USB 연결과 WinUSB 드라이버(Driver\\candleLight_WinUSB) 확인'
_HW_TIMESTAMP = 1 << 4   # GS_CAN_MODE_HW_TIMESTAMP
_COUNTER_SPAN = 1 << 32  # the device timestamp is a 32-bit microsecond counter
# TX handling, from a PCAN-USB FD <-> candleLight bench at 500 kBit/s (tested unit S/N 0038...):
# * The adapter silently drops a frame that arrives while the previous one is still being
#   sent (bursts: 2 frames in flight lost 1/300, 16 lost 15/1000; 1 in flight lost 0/1300,
#   ~1,770 frames/s). So only one frame is in flight; the next send waits for its echo.
# * With bus traffic in both directions it still drops ~0.1-1 % of frames: no echo and the
#   frame never reaches the bus (echo count == frames received by PCAN). Such a frame is
#   sent again when its echo does not arrive within the echo timeout.
TX_TIMEOUT_S = 1.0       # no echo for this long: frames are not leaving (no ACK / bus-off)
ECHO_RETRIES = 2         # resend attempts for a frame whose echo is missing
ECHO_IDS = 10            # distinct echo IDs in rotation (the Linux gs_usb driver also uses 10)
READ_TIMEOUT_MS = 500    # long USB read timeout for the reader thread (see CandleBus)


def echo_timeout(bitrate):
    """Time to wait for an echo before resending: 30 longest Classic frames, at least 20 ms."""
    return max(0.020, 30 * 135 / max(int(bitrate or 0), 10_000))


def bit_timing(capability, bitrate, sample_point=87.5):
    """``(brp, tseg1, tseg2, sjw)`` for an exact ``bitrate`` within the adapter's limits.

    python-can's gs_usb interface searches prescalers only up to 64, so a 48 MHz adapter
    cannot reach 50 kBit/s and below. Here the adapter's own limits (BRP up to 1024) are
    used. Among exact timings the one closest to ``sample_point`` wins, then the one with
    more time quanta; for 100 kBit/s and above this gives python-can's previous values.
    """
    c = capability
    best = None
    for brp in range(max(1, c.brp_min), c.brp_max + 1, max(1, c.brp_inc)):
        quanta, remainder = divmod(c.fclk_can, bitrate * brp)
        if remainder:
            continue
        if quanta < 1 + c.tseg1_min + c.tseg2_min:
            break            # larger prescalers only give fewer quanta
        if quanta > 1 + c.tseg1_max + c.tseg2_max:
            continue
        tseg2 = min(max(round(quanta * (100 - sample_point) / 100), c.tseg2_min), c.tseg2_max)
        tseg1 = quanta - 1 - tseg2
        if not c.tseg1_min <= tseg1 <= c.tseg1_max:
            continue
        error = abs((1 + tseg1) / quanta * 100 - sample_point)
        key = (round(error, 6), -quanta)
        if best is None or key < best[0]:
            best = (key, (brp, tseg1, tseg2, min(c.sjw_max, tseg2)))
    if best is None:
        raise ValueError(f'{c.fclk_can // 1_000_000} MHz 장치 클럭으로 만들 수 없는 속도입니다.')
    return best[1]


def load_gs_usb():
    """Import gs_usb and make pyusb use the libusb DLL shipped with ``libusb-package``."""
    try:
        import libusb_package
        from usb.backend import libusb1
        from gs_usb.gs_usb import GsUsb
    except ImportError as exc:
        raise DeviceOpenError('gs_usb 라이브러리 확인', LIBRARY_MESSAGE) from exc
    # pyusb keeps the first successfully loaded library; later get_backend() calls
    # inside gs_usb/python-can reuse it without searching PATH again.
    if libusb1.get_backend(find_library=libusb_package.find_library) is None:
        raise DeviceOpenError('libusb 확인', 'libusb-1.0 라이브러리를 불러오지 못했습니다.')
    return GsUsb


def _serial(device):
    try:
        return str(device.serial_number or '')
    except Exception:   # some clones have no readable string descriptor
        return ''


def _release(devices):
    """Close pyusb handles. WinUSB allows one open handle per device, so a handle kept by
    a scan (reading the serial number opens the device) blocks the next open."""
    import usb.util
    for device in devices:
        try:
            usb.util.dispose_resources(device.gs_usb)
        except Exception:
            pass


def _product(device):
    try:
        return str(device.gs_usb.product or 'gs_usb')
    except Exception:
        return 'gs_usb'


def _make_bus_class():
    from can.interfaces.gs_usb import GsUsbBus

    class CandleBus(GsUsbBus):
        """GsUsbBus with this program's conventions.

        * TX echo frames are dropped (like PCAN/Kvaser with receive_own_messages=False);
          the program records its own transmissions itself.
        * The device's 32-bit microsecond timestamp is unwrapped and anchored to wall time,
          so it does not jump back after ~71 minutes.
        * CAN FD frames are rejected instead of being truncated to 8 bytes.
        * TX: one frame in flight; a frame without echo is resent (see TX notes above).
        * A dedicated USB reader thread reads with a long timeout. Short read timeouts
          (python-can polls with a few ms) cancel WinUSB transfers and can lose a frame
          that arrives at that moment.
        """

        def __init__(self, index, bitrate):
            self._base = None
            self._last_raw = None
            self._wraps = 0
            self._tx_ready = threading.Condition()
            self._pending = None          # (frame, sent_at, retries) waiting for its echo
            self._next_echo = 0
            self._echo_timeout = echo_timeout(bitrate)
            self.resent = 0               # frames sent again after a missing echo
            self._rx_queue = queue.Queue()
            self._reading = threading.Event()
            self._open_device(index, bitrate)
            self._reading.set()
            self._reader = threading.Thread(target=self._read_loop, name=f'gs_usb RX #{index}', daemon=True)
            self._watchdog = threading.Thread(target=self._watch_loop, name=f'gs_usb TX #{index}', daemon=True)
            self._reader.start()
            self._watchdog.start()

        def _open_device(self, index, bitrate):
            """GsUsbBus.__init__ with bit timing from the adapter's limits (see bit_timing)."""
            from gs_usb.gs_usb import GsUsb
            self._is_shutdown = False
            devices = GsUsb.scan()
            if not 0 <= index < len(devices):
                _release(devices)
                raise can.CanInitializationError(f'Cannot find device {index}. Devices found: {len(devices)}')
            self.gs_usb = devices[index]
            _release(devices[:index] + devices[index + 1:])
            self._index, self._bitrate, self.channel_info = index, bitrate, index
            self._can_protocol = can.CanProtocol.CAN_20
            brp, tseg1, tseg2, sjw = bit_timing(self.gs_usb.device_capability, bitrate)
            prop_seg = 1   # same split as python-can: tseg1 = prop_seg + phase_seg1
            self.gs_usb.set_timing(prop_seg=prop_seg, phase_seg1=tseg1 - prop_seg,
                                   phase_seg2=tseg2, sjw=sjw, brp=brp)
            self.gs_usb.start()
            can.BusABC.__init__(self, channel=index)

        def _watch_loop(self):
            """Resend the in-flight frame when its echo is missing (dropped by the adapter)."""
            import usb.core
            with self._tx_ready:
                while self._reading.is_set():
                    pending = self._pending
                    if pending is None:
                        self._tx_ready.wait(0.5)
                        continue
                    frame, sent_at, retries = pending
                    remaining = sent_at + self._echo_timeout - time.perf_counter()
                    if remaining > 0:
                        self._tx_ready.wait(remaining)
                        continue
                    if retries >= ECHO_RETRIES:
                        # Still no echo: probably no ACK; send() reports it after TX_TIMEOUT_S.
                        self._tx_ready.wait(0.5)
                        continue
                    self._pending = (frame, time.perf_counter(), retries + 1)
                    self.resent += 1
                    try:
                        self.gs_usb.send(frame)
                    except usb.core.USBError:
                        pass

        def _read_loop(self):
            from gs_usb.gs_usb_frame import GsUsbFrame
            frame = GsUsbFrame()
            while self._reading.is_set():
                started = time.monotonic()
                if not self.gs_usb.read(frame=frame, timeout_ms=READ_TIMEOUT_MS):
                    if time.monotonic() - started < 0.01:
                        time.sleep(0.01)   # immediate failure (e.g. unplugged): avoid a busy loop
                    continue
                msg = self._on_frame(frame)
                if msg is not None:
                    self._rx_queue.put(msg)

        def _on_frame(self, frame):
            """Convert one device frame; TX echoes free a send slot and return None."""
            from gs_usb.gs_usb_frame import GS_USB_NONE_ECHO_ID
            timestamp = self._timestamp(frame.timestamp)
            if frame.echo_id != GS_USB_NONE_ECHO_ID:
                self._echo_done()
                return None
            return can.Message(timestamp=timestamp, arbitration_id=frame.arbitration_id,
                               is_extended_id=frame.is_extended_id, is_remote_frame=frame.is_remote_frame,
                               is_error_frame=frame.is_error_frame, channel=self.channel_info,
                               dlc=frame.can_dlc, data=bytearray(frame.data)[:frame.can_dlc], is_rx=True)

        def send(self, msg, timeout=None):
            # python-can tags every frame with echo_id 0; like the Linux gs_usb driver we use
            # distinct echo IDs, which the adapter needs to keep frames apart.
            import usb.core
            from gs_usb.constants import CAN_EFF_FLAG, CAN_RTR_FLAG
            from gs_usb.gs_usb_frame import GsUsbFrame
            if msg.is_fd or len(msg.data) > 8:
                raise can.CanOperationError('candleLight(gs_usb) 장치는 Classic CAN(최대 8바이트)만 송신할 수 있습니다.')
            can_id = msg.arbitration_id
            if msg.is_extended_id:
                can_id |= CAN_EFF_FLAG
            if msg.is_remote_frame:
                can_id |= CAN_RTR_FLAG
            frame = GsUsbFrame(can_id=can_id, data=list(bytes(msg.data)))
            frame.can_dlc = msg.dlc
            with self._tx_ready:
                if not self._tx_ready.wait_for(lambda: self._pending is None,
                                               timeout=TX_TIMEOUT_S if timeout is None else timeout):
                    # No echo for a while even after resending: frames are not leaving
                    # (no ACK / bus-off). Clear it so the next attempt is not blocked forever.
                    self._pending = None
                    raise can.CanOperationError('송신 버퍼 가득 참: 프레임이 버스로 나가지 않습니다 '
                                                '(다른 노드의 ACK, Baudrate, 배선을 확인하세요).')
                frame.echo_id = self._next_echo
                self._next_echo = (self._next_echo + 1) % ECHO_IDS
                self._pending = (frame, time.perf_counter(), 0)
                self._tx_ready.notify_all()   # wake the watchdog
            try:
                self.gs_usb.send(frame)
            except usb.core.USBError as exc:
                self._echo_done()
                raise can.CanOperationError(f'candleLight 송신 실패: {exc}') from exc

        def _echo_done(self):
            with self._tx_ready:
                self._pending = None
                self._tx_ready.notify_all()

        def _timestamp(self, device_seconds):
            if not (self.gs_usb.device_flags or 0) & _HW_TIMESTAMP:
                return time.time()
            raw = int(round(device_seconds * 1_000_000))
            # Echo and RX frames may arrive slightly out of order; only a large backward
            # step is the 32-bit counter wrapping around.
            wrapped = self._last_raw is not None and raw < self._last_raw - _COUNTER_SPAN // 2
            if wrapped:
                self._wraps += 1
            if self._last_raw is None or wrapped or raw > self._last_raw:
                self._last_raw = raw
            seconds = (raw + self._wraps * _COUNTER_SPAN) / 1_000_000
            if self._base is None:
                self._base = time.time() - seconds
            return self._base + seconds

        def shutdown(self):
            # python-can re-scans and restarts the device here, which leaves a second
            # handle open on Windows; stop and release our own handle instead.
            if self._is_shutdown:
                return
            self._is_shutdown = True
            self._reading.clear()
            with self._tx_ready:
                self._tx_ready.notify_all()
            for worker in (getattr(self, '_reader', None), getattr(self, '_watchdog', None)):
                if worker is not None and worker is not threading.current_thread():
                    worker.join(timeout=READ_TIMEOUT_MS / 1000 + 0.5)
            try:
                self.gs_usb.stop()
            finally:
                _release([self.gs_usb])
                can.BusABC.shutdown(self)

        def _recv_internal(self, timeout):
            try:
                return self._rx_queue.get(timeout=timeout), False
            except queue.Empty:
                return None, False

    return CandleBus


class GsUsbBackend(CanDeviceBackend):
    key = 'gs_usb'
    label = 'candleLight (gs_usb)'
    bustypes = ('gs_usb',)
    platforms = ('Windows',)
    empty_text = NOT_FOUND_TEXT
    diag_context = 'candleLight(gs_usb) 연결'
    open_failure_text = 'Failed to open candleLight (gs_usb) channel for Bus {bus}:\n{error}'

    # ---- channel discovery -------------------------------------------------
    def list_channels(self):
        self.empty_text = type(self).empty_text
        try:
            devices = load_gs_usb().scan()
        except DeviceOpenError as exc:
            self.empty_text = exc.message
            return []
        except Exception as exc:
            self.empty_text = 'candleLight 검색 실패 · USB 연결과 WinUSB 드라이버 확인'
            log.warning('gs_usb channel search failed: %s', exc)
            return []
        channels = []
        try:
            for index, device in enumerate(devices):
                serial = _serial(device)
                name = f'{_product(device)} (#{index}' + (f', S/N {serial})' if serial else ')')
                channels.append((name, dict(bustype=self.key, handle=index, is_fd=False, serial=serial)))
        finally:
            _release(devices)
        return channels

    def status_label(self, channel_data):
        return 'candleLight'

    # ---- connection --------------------------------------------------------
    def _resolve_index(self, GsUsb, channel_data):
        """USB re-plugging can reorder devices; reopen the same adapter by serial number."""
        devices = GsUsb.scan()
        try:
            index = channel_data.get('handle')
            serial = channel_data.get('serial') or ''
            if not serial:
                if isinstance(index, int) and 0 <= index < len(devices):
                    return index
            else:
                if isinstance(index, int) and 0 <= index < len(devices) and _serial(devices[index]) == serial:
                    return index
                for position, device in enumerate(devices):
                    if _serial(device) == serial:
                        return position
        finally:
            _release(devices)
        raise DeviceOpenError('candleLight 장치 재검색',
                              '선택한 candleLight 장치를 찾을 수 없습니다. USB 연결을 확인하고 Refresh 후 다시 선택하세요.')

    def open(self, channel_data, settings):
        if settings.fd_requested:
            raise DeviceOpenError('candleLight CAN FD 설정',
                                  'candleLight(gs_usb) 장치는 Classic CAN만 지원합니다. Data Bitrate를 Off로 설정하세요.')
        GsUsb = load_gs_usb()
        index = self._resolve_index(GsUsb, channel_data)
        bitrate = settings.bitrate_data['bitrate']
        try:
            bus = _make_bus_class()(index, bitrate)
        except ValueError as exc:
            raise DeviceOpenError('candleLight 통신 속도', f'{settings.bitrate_text}를 설정할 수 없습니다: {exc}') from exc
        except Exception as exc:
            text = str(exc)
            hint = ''
            if 'access' in text.lower() or 'busy' in text.lower() or 'not supported' in text.lower():
                hint = ('\n다른 프로그램이 장치를 사용 중이거나, 드라이버가 WinUSB가 아닙니다 '
                        '(Driver\\candleLight_WinUSB 참고).')
            raise DeviceOpenError('candleLight 연결 오류', f'{text}{hint}') from exc
        return bus, False

    # ---- monitoring --------------------------------------------------------
    def poll_status(self, bus):
        """The driver hides USB read errors, so detect a removed adapter by re-enumerating."""
        device = getattr(getattr(bus, 'gs_usb', None), 'gs_usb', None)
        if device is None:
            return None
        try:
            import usb.core
            from usb.backend import libusb1
            present = usb.core.find(idVendor=device.idVendor, idProduct=device.idProduct,
                                    bus=device.bus, address=device.address,
                                    backend=libusb1.get_backend()) is not None
        except Exception:
            return None
        return None if present else ('lost', 'candleLight USB 장치가 분리되었습니다.')
