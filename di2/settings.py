"""Read a unit's current settings (no writes), decoded the way E-Tube 4.0.4 does.

Byte layouts and value tables: docs/commands.md. Each reader returns a dict of
decoded values plus the raw reply params under 'raw' so decoding can be checked.
"""
from typing import Callable, Dict, Optional

from .session import Session

SWITCH_FUNCTIONS = {
    0: 'front shift up', 1: 'front shift down', 2: 'rear shift up', 3: 'rear shift down',
    4: 'cycle computer (left)', 5: 'cycle computer (right)',
    10: 'D-FLY ch.1', 11: 'D-FLY ch.2', 12: 'D-FLY ch.3', 13: 'D-FLY ch.4', 15: 'unassigned',
}
CHAINRINGS = {9: '53-39', 5: '52-36', 6: '50-34', 11: '46-36'}                      # FD-R8050
CASSETTES_SS = {2: '11-25', 3: '11-28', 4: '11-30', 5: '12-25', 6: '12-28', 9: '14-28'}
CASSETTES_GS = {3: '11-28', 4: '11-30', 8: '11-32', 11: '11-34'}
# Sprockets largest first (RD gear 1 = largest cog), from E-Tube's cstoothsetpattern.json.
SPROCKETS = {
    '11-25': [25, 23, 21, 19, 17, 16, 15, 14, 13, 12, 11],
    '11-28': [28, 25, 23, 21, 19, 17, 15, 14, 13, 12, 11],
    '11-30': [30, 27, 24, 21, 19, 17, 15, 14, 13, 12, 11],
    '11-32': [32, 28, 25, 22, 20, 18, 16, 14, 13, 12, 11],
    '11-34': [34, 30, 27, 25, 23, 21, 19, 17, 15, 13, 11],
    '12-25': [25, 23, 21, 19, 18, 17, 16, 15, 14, 13, 12],
    '12-28': [28, 25, 23, 21, 19, 17, 16, 15, 14, 13, 12],
    '14-28': [28, 25, 23, 21, 20, 19, 18, 17, 16, 15, 14],
}
SYNCHRO_SPEED = {0: 'normal', 1: 'slow', 2: 'very slow'}
MULTI_SHIFT_SPEED = {0: 'off', 1: 'very slow', 2: 'slow', 3: 'normal', 4: 'fast', 5: 'very fast'}
WIRELESS_MODE = {0: 'off', 1: 'ANT', 2: 'Bluetooth LE', 3: 'ANT + Bluetooth LE'}


def _read(s: Session, slot: int, group: int, cmd: int, params: bytes = b'\x00') -> Optional[bytes]:
    st, p = s.unit(slot, group, cmd, params)
    return p if st == 'ok' else None


def shifter(s: Session, slot: int) -> Dict:
    """SW_CND_GET (04 14): b0 = X<<4 | Y, b1 = sprinter<<4 | A."""
    p = _read(s, slot, 0x04, 0x14)
    if not p or len(p) < 2:
        return {'raw': p.hex(' ') if p else None}
    nib = {'X': p[0] >> 4, 'Y': p[0] & 0xF, 'sprinter': p[1] >> 4, 'A': p[1] & 0xF}
    return {**{k: SWITCH_FUNCTIONS.get(v, v) for k, v in nib.items()}, 'codes': nib, 'raw': p.hex(' ')}


def _synchro_map(p: Optional[bytes]) -> Dict:
    """PCA_SYNC_GET reply: mode<<4 | direction, FD position, RD shift point, RD target."""
    if not p or len(p) < 4:
        return {'raw': p.hex(' ') if p else None}
    if p[2] == 0xFE:
        return {'type': 'semi-synchronized', 'rd_steps': p[3], 'raw': p.hex(' ')}
    return {'type': 'synchronized', 'rd_shift_point': p[2], 'rd_target_gear': p[3], 'raw': p.hex(' ')}


def front_derailleur(s: Session, slot: int) -> Dict:
    out = {}
    p = _read(s, slot, 0x0A, 0x0C)
    out['gear'] = f'{p[0]} of {p[1]}' if p and len(p) >= 2 else None
    p = _read(s, slot, 0x0A, 0xB4, b'\xff')
    out['chainrings'] = CHAINRINGS.get(p[0], p[0]) if p else None
    out['chainrings_code'] = p[0] if p else None
    p = _read(s, slot, 0x0A, 0x9C)
    out['synchro_shift_speed'] = SYNCHRO_SPEED.get(p[0], p[0]) if p else None
    out['synchro_shift_speed_code'] = p[0] if p else None
    # Shift mode 1/2 x setting 1 (up, FD inner) / setting 2 (down, FD outer).
    for mode, m in (('mode 1', 0x00), ('mode 2', 0x10)):
        for name, direction, fd in (('up', 0, 1), ('down', 1, 2)):
            out[f'synchro {mode} {name}'] = _synchro_map(
                _read(s, slot, 0x32, 0xAC, bytes([m | direction, fd, 0, 0])))
    return out


def rear_derailleur(s: Session, slot: int, gs: bool) -> Dict:
    out = {}
    p = _read(s, slot, 0x0B, 0x0C)
    out['gear'] = f'{p[0]} of {p[1]}' if p and len(p) >= 2 else None
    p = _read(s, slot, 0x0B, 0xB4, b'\xff')
    out['cassette'] = (CASSETTES_GS if gs else CASSETTES_SS).get(p[0], p[0]) if p else None
    out['cassette_code'] = p[0] if p else None
    p = _read(s, slot, 0x0B, 0x54)
    out['multi_shift_speed'] = MULTI_SHIFT_SPEED.get(p[0], p[0]) if p else None
    out['multi_shift_speed_code'] = p[0] if p else None
    p = _read(s, slot, 0x0B, 0x14)              # EXTM_RA_ADJ_LV_GET: trim (int8), +/- range
    out['trim'] = (p[0] - 256 if p[0] > 127 else p[0]) if p and len(p) >= 2 else None
    out['trim_range'] = p[1] if p and len(p) >= 2 else None
    p = _read(s, slot, 0x0B, 0x7C, bytes([1, 2, 0, 0]))
    out['gear_position_control'] = None if not p else ('off' if p[:4] == bytes([1, 2, 0xFF, 0]) else 'on')
    out['gear_position_control_raw'] = p.hex(' ') if p else None
    return out


def battery(s: Session, slot: int = 0) -> Dict:
    p = _read(s, slot, 0x0B, 0x64)
    limit = None if not p else ('unlimited' if p[0] == 0xFF else p[0])
    soc = _read(s, slot, 0x26, 0x44)
    return {'charge_percent': soc[0] if soc else None, 'multi_shift_gear_limit': limit,
            'multi_shift_gear_limit_code': p[0] if p else None,
            'raw_limit': p.hex(' ') if p else None}


def wireless(s: Session, slot: int) -> Dict:
    out = {}
    p = _read(s, slot, 0x40, 0x54, b'\xff')
    out['mode'] = WIRELESS_MODE.get(p[0], p[0]) if p else None
    name = b''
    for cmd in (0x14, 0x1C):
        p = _read(s, slot, 0x40, cmd)
        if p:
            name += p
    out['ble_name'] = name.split(b'\x00')[0].decode('ascii', 'replace') if name else None
    return out


def reader_for(model: str) -> Optional[Callable[[Session, int], Dict]]:
    if model.startswith('ST-'):
        return shifter
    if model.startswith('FD-'):
        return front_derailleur
    if model.startswith('RD-'):
        return lambda s, slot: rear_derailleur(s, slot, gs=model.endswith('-GS'))
    if model.startswith('BT-'):
        return battery
    if model.startswith('EW-WU'):
        return wireless
    return None
