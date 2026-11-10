"""Serialize writes to each CAN bus and let log replay yield to periodic TX."""
import threading
import time
import weakref


class BusTxSchedule:
    def __init__(self):
        self.lock = threading.RLock()
        self._tasks_lock = threading.Lock()
        self._tasks = weakref.WeakSet()

    def add(self, task):
        with self._tasks_lock:
            self._tasks.add(task)

    def discard(self, task):
        with self._tasks_lock:
            self._tasks.discard(task)

    def send_log(self, bus, frame):
        # A log packet can wait; a periodic worker must never wait for log UI work.
        if not self.lock.acquire(blocking=False):
            return False
        try:
            now = time.perf_counter()
            with self._tasks_lock:
                if any(task.active and task.deadline <= now + min(.002, task.period / 4)
                       for task in self._tasks):
                    return False
            bus.send(frame)
            return True
        finally:
            self.lock.release()


def bus_schedule(main, bus):
    # All owners are created on the GUI thread before their worker starts.
    if not hasattr(main, '_tx_bus_schedules'):
        main._tx_bus_schedules = {}
    if bus not in main._tx_bus_schedules:
        main._tx_bus_schedules[bus] = BusTxSchedule()
    return main._tx_bus_schedules[bus]
