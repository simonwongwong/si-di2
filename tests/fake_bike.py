"""A simulated bike for tests and UI work without hardware.

FakeBike answers the unit commands this project uses with values like those read
from an Ultegra R8050/R8070 bike, and records every frame sent. FakeSession stands
in for di2.session.Session (same unit()/command() surface).
"""
import json
import time
from pathlib import Path

FIXTURE = Path(__file__).with_name('fixtures') / 'snapshot.json'


def load_snapshot() -> dict:
    return json.loads(FIXTURE.read_text())


class FakeBike:
    def __init__(self, delay: float = 0.0):
        self.delay = delay
        self.sent = []
        self.v = {'fd_gear': [2, 2], 'fc': 5, 'synchro_speed': 0,
                  'map': {(0x00, 1): [0xFE, 2], (0x01, 2): [0xFE, 2], (0x10, 1): [7, 5], (0x11, 2): [2, 4]},
                  'rd_gear': [3, 11], 'cs': 3, 'ms_speed': 3, 'gpc': [1, 2, 9, 2], 'limit': 0xFF, 'soc': 30,
                  'trim': 0, 'trim_range': 4, 'mode': 0, 'switches': {}}

    def unit(self, slot, group, cmd, params=b'\x00', wait=3.0, retries=2):
        if self.delay:
            time.sleep(self.delay)
        v, p, g = self.v, bytes(params), (group, cmd)
        self.sent.append((slot, group, cmd, p))
        sw = v['switches'].setdefault(slot, [0x01, 0xFA])
        replies = {
            (0x04, 0x14): lambda: bytes(sw + [0xFF, 0xFF]),
            (0x0A, 0x0C): lambda: bytes(v['fd_gear'] + [0, 0]),
            (0x0A, 0xB4): lambda: bytes([v['fc'], 0, 0xFF, 0xFF]),
            (0x0A, 0x9C): lambda: bytes([v['synchro_speed']]),
            (0x32, 0xAC): lambda: bytes([p[0], p[1]] + v['map'][(p[0], p[1])]),
            (0x0B, 0x0C): lambda: bytes(v['rd_gear'] + [0, 0]),
            (0x0B, 0xB4): lambda: bytes([v['cs'], 0, 0xFF, 0xFF]),
            (0x0B, 0x54): lambda: bytes([v['ms_speed'], 0, 0, 0]),
            (0x0B, 0x7C): lambda: bytes(v['gpc']),
            (0x0B, 0x64): lambda: bytes([v['limit']] * 4),
            (0x0B, 0x14): lambda: bytes([v['trim'] & 0xFF, v['trim_range']]),
            (0x26, 0x44): lambda: bytes([v['soc']]),
        }
        if g in replies:
            return 'ok', replies[g]()
        if g == (0x04, 0x10):
            sw[:] = [p[0], p[1]]
        elif g == (0x0A, 0x98):
            v['synchro_speed'] = p[0]
        elif g == (0x32, 0xA8):
            v['map'][(p[0], p[1])] = [p[2], p[3]]
        elif g == (0x0B, 0x50):
            v['ms_speed'] = p[0]
        elif g == (0x0B, 0x60):
            v['limit'] = p[0]
        elif g == (0x0B, 0x08):
            v['rd_gear'][0] = p[0]
            return 'ok', bytes([p[0]])
        elif g == (0x0B, 0x10):
            v['trim'] = p[0] - 256 if p[0] > 127 else p[0]
            return 'ok', bytes([p[0]])
        elif g == (0x0B, 0x00):
            v['mode'] = p[0]
        else:
            return 'timeout', None
        return 'ok', b''


class FakeSession:
    """Enough of di2.session.Session for edit, adjust, settings and the server."""

    def __init__(self, dev='/dev/null', log=None, bike=None):
        self.bike = bike or FakeBike()
        self.pc_slot, self.slots, self.close_report, self.connected_at = 25, [], None, None
        self.battery_present = True

    def connect(self):
        self.connected_at = time.time()

    def unit(self, *a, **k):
        return self.bike.unit(*a, **k)

    def command(self, ctrl, data=b'', wait=2.0):
        return bytes([0x60, 0x0A]) if ctrl == 0x1C else None    # capacitor level 2656

    def close(self):
        self.close_report = {0: True}
