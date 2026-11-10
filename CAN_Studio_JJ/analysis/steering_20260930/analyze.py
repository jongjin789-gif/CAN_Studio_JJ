"""Read-only comparison of the two supplied steering traces. Run from repo root."""
from pathlib import Path
from collections import defaultdict, Counter
import binascii
import json
import statistics
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]
fig, axes = plt.subplots(3, 2, figsize=(15, 11), constrained_layout=True)
report = {}
for col, (label, stamp) in enumerate((('MCU', '183540'), ('User panel', '184210'))):
    path = next((ROOT / 'Log').glob(f'*{stamp}*.trc'))
    groups = defaultdict(list)
    diagnostics = []
    with path.open(encoding='utf-8-sig') as stream:
        for line_number, line in enumerate(stream, 1):
            x = line.split()
            if len(x) < 9 or not x[0].isdigit():
                continue
            ident, bus, ms = int(x[4], 16), int(x[3]), float(x[1])
            data = bytes.fromhex(' '.join(x[8:]))
            if ident in (0x163, 0x147):
                groups[bus, ident].append((ms, data, line_number))
            if ident in (0x715, 0x71D):
                diagnostics.append(dict(line=line_number, ms=ms, bus=bus,
                                        id=hex(ident), direction=x[5], data=data.hex(' ')))
    entry = dict(file=path.name, diagnostics=diagnostics, buses={})
    for bus in (1, 2):
        tx = groups[bus, 0x163]
        rx = groups[bus, 0x147]
        intervals = [b[0] - a[0] for a, b in zip(tx, tx[1:])]
        valid_crc = sum(binascii.crc_hqx(d[2:], 0xffff).to_bytes(2, 'big') == d[:2]
                        for _, d, _ in tx)
        active = [(t, d[5] & 1, ln) for t, d, ln in rx]
        transitions = [r for i, r in enumerate(active) if i == 0 or r[1] != active[i-1][1]]
        entry['buses'][bus] = dict(
            count=len(tx), crc_matches=valid_crc, first_ms=tx[0][0], last_ms=tx[-1][0],
            mean_interval_ms=statistics.mean(intervals), median_interval_ms=statistics.median(intervals),
            p99_interval_ms=sorted(intervals)[int(.99 * len(intervals))],
            max_interval_ms=max(intervals),
            counter_deltas=dict(Counter((b[1][2]-a[1][2]) % 256 for a,b in zip(tx,tx[1:]))),
            active_transitions=transitions,
            largest_gaps=sorted([(b[0]-a[0], a[0], b[0], b[2]) for a,b in zip(tx,tx[1:])], reverse=True)[:6])
        axes[0, col].plot([r[0]/1000 for r in tx[1:]], intervals,
                          linewidth=.45, alpha=.65, label=f'BUS {bus}')
        ax = axes[bus, col]
        ax.plot([t/1000 for t,_,_ in tx],
                [int.from_bytes(d[8:10], 'big')*.0625-780 for _,d,_ in tx],
                linewidth=.9, label='Command 0x163')
        ax.plot([t/1000 for t,_,_ in rx],
                [int.from_bytes(d[23:25], 'big')*.005-100 for _,d,_ in rx],
                linewidth=.8, label='Feedback 0x147')
        ax.set_title(f'{label}: BUS {bus} steering angle')
        ax.set_ylim(-75, 75)
        ax.set_ylabel('Angle (SYM physical value)')
        ax.set_xlabel('Seconds from start of this trace')
        ax.legend(loc='upper right')
    axes[0, col].set_title(f'{label}: 0x163 recorded intervals (full range)')
    axes[0, col].axhline(10, color='black', linestyle='--', linewidth=.7, label='10 ms target')
    axes[0, col].set_ylabel('Interval (ms)')
    axes[0, col].set_xlabel('Seconds from start of this trace')
    axes[0, col].legend()
    report[label] = entry
for ax in axes.flat:
    ax.grid(alpha=.25)
fig.suptitle('MCU vs user panel: recorded timing and steering response\n'
             'Tx = host-side timestamp; traces are separate runs; startup invalid feedback is outside angle plot limits.')
fig.savefig(OUT / 'comparison.png', dpi=150)
(OUT / 'metrics.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
print('Wrote comparison.png and metrics.json')
