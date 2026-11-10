"""Export decoded signal values as a time-axis CSV table (Excel friendly, UTF-8 BOM)."""
import csv
import datetime
import heapq


def export_signals_csv(path, series, fill_forward=False):
    """Write one row per timestamp and one column per signal.

    ``series``: list of ``(column_name, times, values)`` with ascending ``times``.
    Without ``fill_forward`` a cell is empty when that signal has no sample at the
    row's time; with it, the last known value is repeated.
    Returns the number of data rows.
    """
    if not series:
        return 0
    def tagged(column, times):
        return ((t, column) for t in times)

    merged = heapq.merge(*[tagged(column, times) for column, (_, times, _) in enumerate(series)])
    cursors = [0] * len(series)
    current = [''] * len(series)
    start = None
    rows = 0
    with open(path, 'w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(['Time', 'Offset (s)'] + [name for name, _, _ in series])
        pending_time, touched = None, set()

        def emit(ts):
            nonlocal rows
            stamp = datetime.datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
            cells = [current[i] if fill_forward or i in touched else '' for i in range(len(series))]
            writer.writerow([stamp, f'{ts - start:.6f}'] + cells)
            rows += 1

        for ts, column in merged:
            if start is None:
                start = ts
            if pending_time is not None and ts != pending_time:
                emit(pending_time)
                touched = set()
            values = series[column][2]
            value = values[cursors[column]]
            cursors[column] += 1
            current[column] = f'{value:.10g}' if isinstance(value, float) else str(value)
            touched.add(column)
            pending_time = ts
        if pending_time is not None:
            emit(pending_time)
    return rows
