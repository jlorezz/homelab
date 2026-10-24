#!/usr/bin/env python3
"""Read-only step-0 sampling. Supply ALL existing runner aggregate cgroup paths."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--cgroup", action="append", required=True, type=Path)
parser.add_argument("--samples", type=int, default=60)
parser.add_argument("--interval", type=float, default=5)
args = parser.parse_args()
if args.samples < 1 or args.interval < 0:
    parser.error("Invalid sample interval/count")
# Only non-overlapping aggregate cgroups may be added back to MemAvailable.
paths = [p.resolve(strict=True) for p in args.cgroup]
if any(a == b or a in b.parents or b in a.parents for i, a in enumerate(paths) for b in paths[i + 1:]):
    parser.error("Cgroups must be distinct and non-overlapping")
minimum = None
for number in range(args.samples):
    mem = {line.split(':')[0]: int(line.split()[1]) for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith(('MemTotal:', 'MemAvailable:'))}
    usage = sum(int((p / 'memory.current').read_text()) for p in paths)
    headroom = (mem['MemAvailable'] * 1024 + usage) // (1024 * 1024)
    minimum = headroom if minimum is None else min(minimum, headroom)
    if number + 1 < args.samples:
        time.sleep(args.interval)
total = mem['MemTotal'] // 1024
print(json.dumps({
    'measuredAt': datetime.now(timezone.utc).isoformat(),
    'measuredHeadroomMiB': minimum,
    'measuredTotalMiB': total,
    'reserveMiB': max(8192, (total + 3) // 4),
    'measuredCpuPercent': max(0, len(os.sched_getaffinity(0)) - 2) * 100,
    'samples': args.samples,
    'cgroups': [str(p) for p in paths],
    'limits': 'Sampled headroom only; repeat at representative NAS load. Reserve two logical CPUs. Peak job memory UNVERIFIED.'
}, indent=2))
