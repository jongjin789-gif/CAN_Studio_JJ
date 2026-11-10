"""Per-BUS load estimate, frame rate and error-frame count.

The load is an estimate computed from the frames this program sees (RX from the
driver and TX sent by this program): frame length in bits x bit time, divided by the
measurement window. Bit stuffing is approximated (about 1 stuff bit per 10 bits),
so the value may differ slightly from hardware bus-load counters.
"""
import time

_STUFF = 1.1


def frame_seconds(length, is_ext, is_fd, brs, nominal, data_rate):
    """Approximate transmission time of one frame in seconds."""
    nominal = nominal or 500_000
    if not is_fd:
        # SOF..EOF + 3 bit intermission; 11-bit: 47 + 8n, 29-bit: 67 + 8n.
        bits = (67 if is_ext else 47) + 8 * length
        return bits * _STUFF / nominal
    # Arbitration phase (nominal): SOF, ID, RRS/SRR/IDE, FDF, res, BRS.
    arbitration = 36 if is_ext else 17
    crc = 17 if length <= 16 else 21
    # Data phase: ESI, DLC, data, stuff count, CRC with fixed stuff bits.
    data_bits = 1 + 4 + 8 * length + 4 + crc + (crc + 3) // 4
    # CRC delimiter, ACK, ACK delimiter, EOF, intermission (nominal).
    tail = 1 + 1 + 1 + 7 + 3
    data_bitrate = data_rate if (brs and data_rate) else nominal
    return ((arbitration + tail) * _STUFF / nominal) + (data_bits * _STUFF / data_bitrate)


class BusLoadMeter:
    """Accumulates frames for one BUS; :meth:`sample` closes a measurement window."""

    def __init__(self, nominal=500_000, data_rate=None, clock=time.monotonic):
        self.nominal = nominal
        self.data_rate = data_rate
        self.clock = clock
        self.reset()

    def reset(self):
        self._busy = 0.0
        self._frames = 0
        self._started = self.clock()
        self.load = 0.0
        self.fps = 0.0
        self.error_frames = 0
        self.total_frames = 0

    def feed(self, length, is_ext, is_fd, brs, is_err):
        if is_err:
            self.error_frames += 1
            return
        self._frames += 1
        self.total_frames += 1
        self._busy += frame_seconds(length, is_ext, is_fd, brs, self.nominal, self.data_rate)

    def sample(self, min_window=0.5):
        now = self.clock()
        elapsed = now - self._started
        if elapsed < min_window:
            return  # an extra call right after a sample would give a meaningless rate
        self.load = min(100.0, self._busy / elapsed * 100.0)
        self.fps = self._frames / elapsed
        self._busy = 0.0
        self._frames = 0
        self._started = now

    def text(self, counters=None):
        parts = [f'부하 {self.load:.1f}%', f'{self.fps:,.0f} fps', f'Err {self.error_frames:,}']
        if counters is not None:
            parts.append(f'TEC {counters[0]} / REC {counters[1]}')
        return ' · '.join(parts)
