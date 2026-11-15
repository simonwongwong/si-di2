#!/usr/bin/env python3
"""Run the web UI against the simulated bike (tests/fake_bike.py): no SM-BCR2 needed.

usage: python3 tools/sim_server.py [--port 8097]
Settings, backups and presets go to a temporary directory, not your real files.
"""
import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]

import di2.server as srv  # noqa: E402
from di2 import settings  # noqa: E402
from fake_bike import FakeBike, FakeSession, load_snapshot  # noqa: E402

BIKE = FakeBike(delay=0.15)       # the real bus is slower still


class SimSession(FakeSession):
    def __init__(self, dev, log=None):
        super().__init__(dev, log, bike=BIKE)

    def connect(self):
        time.sleep(2)
        super().connect()

    def close(self):
        time.sleep(1)
        self.close_report = {u['slot']: True for u in load_snapshot()['units']}


def sim_scan(s, progress):
    snap = load_snapshot()
    for u in snap['units']:
        progress(f'slot {u["slot"]}: identifying')
        reader = settings.reader_for(u.get('model') or '') if u.get('responding') else None
        if reader:
            u['settings'] = reader(s, u['slot'])
    return snap


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--port', type=int, default=8097)
    args = ap.parse_args()
    tmp = Path(tempfile.mkdtemp(prefix='di2-sim-'))
    os.environ['DI2_PRESETS'] = str(tmp / 'presets.json')     # never touch the real presets
    srv.Session, srv.scan = SimSession, sim_scan
    srv.CACHE, srv.BACKUPS = tmp / 'last.json', tmp / 'backups'
    srv.CACHE.write_text(json.dumps(load_snapshot()))
    print(f'simulated bike; state in {tmp}')
    srv.serve('127.0.0.1', args.port, '/dev/null')


if __name__ == '__main__':
    main()
