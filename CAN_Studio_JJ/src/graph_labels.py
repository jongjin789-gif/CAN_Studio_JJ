"""Compact graph labels without changing signal identity or data routing."""
from collections import Counter
from PyQt5.QtCore import Qt


def signal_labels(signals):
    """signals maps stable keys to bus/symbol/signal/id metadata."""
    counts = Counter(item['signal'] for item in signals.values())
    labels = {
        key: (f"B{item['bus']}:{item['symbol']}.{item['signal']}"
              if counts[item['signal']] > 1 else f"B{item['bus']}:{item['signal']}")
        for key, item in signals.items()
    }
    collisions = Counter(labels.values())
    for key, item in signals.items():
        if collisions[labels[key]] > 1:
            labels[key] = f"B{item['bus']}:{item['symbol']}[0x{item['id']:X}].{item['signal']}"
    return labels


def update_realtime_labels(graph, preserve_custom=False):
    signals = {
        key: dict(bus=graph.signal_bindings[key]['bus'], symbol=message.name,
                  signal=signal.name, id=message.frame_id)
        for key, (message, signal) in graph._bound_signals.items()
    }
    labels = signal_labels(signals)
    for key, label in labels.items():
        item = signals[key]
        full = f"B{item['bus']}:{item['symbol']}.{item['signal']}"
        current = graph.display_names.get(key, key)
        if preserve_custom and current not in (key, full, label):
            continue
        graph.display_names[key] = label
        graph.curves[key].opts['name'] = label
        for index in range(graph.legend_widget.count()):
            row = graph.legend_widget.item(index)
            if row.data(Qt.UserRole) == key:
                row.setText(label)
                row.setToolTip(full)
                break
        index = graph.combo_hover_signal.findData(key)
        if index >= 0:
            graph.combo_hover_signal.setItemText(index, label)
