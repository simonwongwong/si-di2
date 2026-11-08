"""Change settings the way E-Tube's Customize screen does, then read each one back.

A change is ``{'slot': int, 'key': str, 'value': int}``. Keys (v1):

  button.X / button.Y / button.A      shifter function (SWITCH_FUNCTIONS codes)
  multi_shift_speed                   rear derailleur; 0 = multi-shift off
  multi_shift_gear_limit              battery (slot 0): 255 unlimited, 2 or 3
  synchro_shift_speed                 front derailleur: 0 normal, 1 slow, 2 very slow
  semi_steps.1.up / .1.down / .2.up / .2.down
                                      front derailleur, semi-synchronized mode only:
                                      rear steps that go with a front shift

Rules come from E-Tube 4.0.4 (docs/etube-app.md): D-FLY availability by firmware,
semi-synchro step limits per chainring/cassette, multi-shift off forcing the gear
limit to unlimited, and the warning when a shift direction is left unassigned.
Like E-Tube, each change is written once with no rollback; unlike E-Tube, each is
read back afterwards and reported as verified or not.
"""
from typing import Callable, Dict, List, Tuple

from . import settings
from .session import Session

SHIFT_FUNCTIONS = {0: 'front shift up', 1: 'front shift down', 2: 'rear shift up', 3: 'rear shift down'}
DFLY = {10: 'D-FLY ch.1', 11: 'D-FLY ch.2', 12: 'D-FLY ch.3', 13: 'D-FLY ch.4'}
CYCLE_COMPUTER = {4: 'cycle computer (left)', 5: 'cycle computer (right)'}
GEAR_LIMITS = {255: 'no limit', 2: '2 gears', 3: '3 gears'}

# Semi-synchronized step limit, FD-R8050 with RD-R8050-GS (E-Tube fdtoothset.json, GetMaxStep).
SEMI_STEP_MAX = {
    ('52-36', '11-28'): 3, ('52-36', '11-30'): 3, ('52-36', '11-32'): 2, ('52-36', '11-34'): 2,
    ('53-39', '11-28'): 2, ('53-39', '11-30'): 2, ('53-39', '11-32'): 2, ('53-39', '11-34'): 1,
    ('50-34', '11-28'): 3, ('50-34', '11-30'): 3, ('50-34', '11-32'): 3, ('50-34', '11-34'): 2,
    ('46-36', '11-28'): 2, ('46-36', '11-30'): 1, ('46-36', '11-32'): 1, ('46-36', '11-34'): 1,
}
SEMI_KEYS = {'semi_steps.1.up': (0x00, 0, 1), 'semi_steps.1.down': (0x00, 1, 2),
             'semi_steps.2.up': (0x10, 0, 1), 'semi_steps.2.down': (0x10, 1, 2)}


def _ver(v) -> Tuple[int, ...]:
    try:
        return tuple(int(x) for x in str(v).split('.')[:3])
    except ValueError:
        return (0,)


def _units(snap: Dict, kind: str) -> List[Dict]:
    return [u for u in snap.get('units', []) if u.get('kind') == kind and u.get('responding')]


def shifter_codes(unit: Dict) -> Dict:
    """Button codes {X, Y, sprinter, A}; falls back to the raw reply in older snapshots."""
    st = unit.get('settings') or {}
    if st.get('codes'):
        return dict(st['codes'])
    raw = bytes.fromhex(st.get('raw') or '')
    return {'X': raw[0] >> 4, 'Y': raw[0] & 0xF, 'sprinter': raw[1] >> 4, 'A': raw[1] & 0xF} if len(raw) >= 2 else {}


def dfly_available(snap: Dict) -> bool:
    """StSwImpl.CheckKome3_in: shifter >= 3.1.0, BT-DN110 >= 4.4.0, wireless unit >= 4.3.0."""
    st = _units(snap, 'shifter')
    bt = _units(snap, 'battery')
    wu = _units(snap, 'wireless')
    return (bool(st) and all(_ver(u.get('firmware')) >= (3, 1, 0) for u in st)
            and any(_ver(u.get('firmware')) >= (4, 4, 0) for u in bt)
            and any(_ver(u.get('firmware')) >= (4, 3, 0) for u in wu))


def options(snap: Dict) -> Dict:
    """Editable fields per slot with allowed values: {slot: {key: {value: label}}}."""
    out: Dict = {}
    dfly = dfly_available(snap)
    for u in _units(snap, 'shifter'):
        codes = shifter_codes(u)
        for btn in ('X', 'Y', 'A'):
            allowed = dict(SHIFT_FUNCTIONS)
            allowed.update(DFLY if dfly else (CYCLE_COMPUTER if btn == 'A' else {}))
            cur = codes.get(btn)
            if cur is not None and cur not in allowed:      # keep what's on the bike selectable
                allowed[cur] = settings.SWITCH_FUNCTIONS.get(cur, str(cur))
            out.setdefault(u['slot'], {})[f'button.{btn}'] = allowed
    for u in _units(snap, 'rear_derailleur'):
        out.setdefault(u['slot'], {})['multi_shift_speed'] = dict(settings.MULTI_SHIFT_SPEED)
    for u in _units(snap, 'battery'):
        out.setdefault(u['slot'], {})['multi_shift_gear_limit'] = dict(GEAR_LIMITS)
    fds, rds = _units(snap, 'front_derailleur'), _units(snap, 'rear_derailleur')
    for u in fds:
        st = u.get('settings') or {}
        out.setdefault(u['slot'], {})['synchro_shift_speed'] = dict(settings.SYNCHRO_SPEED)
        cassette = ((rds[0].get('settings') or {}).get('cassette') if rds else None)
        gs = bool(rds) and rds[0].get('model', '').endswith('-GS')
        step_max = SEMI_STEP_MAX.get((st.get('chainrings'), cassette)) if gs else None
        for key, (m, d, fd) in SEMI_KEYS.items():
            mode, direction = key.split('.')[1:]
            entry = st.get(f'synchro mode {mode} {direction}') or {}
            if step_max is not None and entry.get('type') == 'semi-synchronized':
                out[u['slot']][key] = {n: f'{n} step{"" if n == 1 else "s"}' for n in range(step_max + 1)}
    return out


def current_value(snap: Dict, slot: int, key: str):
    u = next((x for x in snap.get('units', []) if x['slot'] == slot), None)
    st = (u or {}).get('settings') or {}
    if key.startswith('button.'):
        return shifter_codes(u or {}).get(key.split('.')[1])
    if key == 'multi_shift_gear_limit' and st.get(key + '_code') is None and st.get('raw_limit'):
        return bytes.fromhex(st['raw_limit'])[0]
    if key in ('multi_shift_speed', 'multi_shift_gear_limit', 'synchro_shift_speed'):
        return st.get(key + '_code')
    if key in SEMI_KEYS:
        mode, direction = key.split('.')[1:]
        return (st.get(f'synchro mode {mode} {direction}') or {}).get('rd_steps')
    return None


def check(snap: Dict, changes: List[Dict]) -> Tuple[List[str], List[str], List[Dict]]:
    """Validate staged changes. Returns (errors, warnings, changes to write incl. implied ones)."""
    opts = options(snap)
    errors, warnings, out = [], [], []
    for c in changes:
        allowed = opts.get(c['slot'], {}).get(c['key'])
        if allowed is None:
            errors.append(f'slot {c["slot"]}: {c["key"]} is not editable')
        elif c['value'] not in allowed:
            errors.append(f'slot {c["slot"]}: {c["value"]} is not an allowed value for {c["key"]}')
        elif c['value'] != current_value(snap, c['slot'], c['key']):
            out.append(dict(c))
    # Multi-shift off forces the gear limit to unlimited (CustomizeSettingMultiShift).
    if any(c['key'] == 'multi_shift_speed' and c['value'] == 0 for c in out):
        bt = _units(snap, 'battery')
        if bt and current_value(snap, bt[0]['slot'], 'multi_shift_gear_limit') != 255:
            out = [c for c in out if c['key'] != 'multi_shift_gear_limit']
            out.append({'slot': bt[0]['slot'], 'key': 'multi_shift_gear_limit', 'value': 255, 'implied': True})
    # With multi-shift off the gear limit is greyed out in E-Tube (it stays unlimited).
    rd = _units(snap, 'rear_derailleur')
    if rd:
        speed = next((c['value'] for c in out if c['key'] == 'multi_shift_speed'),
                     current_value(snap, rd[0]['slot'], 'multi_shift_speed'))
        if speed == 0 and any(c['key'] == 'multi_shift_gear_limit' and c['value'] != 255 for c in out):
            errors.append('the multi-shift gear limit can only be set while multi-shift is on')
    # BikeInfoBase.SettingStatus: every shift direction should be on some button.
    assigned = set()
    for u in _units(snap, 'shifter'):
        for btn in ('X', 'Y', 'A'):
            key = f'button.{btn}'
            new = next((c['value'] for c in out if c['slot'] == u['slot'] and c['key'] == key), None)
            assigned.add(new if new is not None else current_value(snap, u['slot'], key))
    missing = [name for code, name in SHIFT_FUNCTIONS.items() if code not in assigned]
    if missing:
        warnings.append('The following functions are not included: ' + ', '.join(missing))
    return errors, warnings, out


def _verify(s: Session, slot: int, group: int, cmd: int, params: bytes, ok: Callable[[bytes], bool]):
    st, p = s.unit(slot, group, cmd, params)
    return (st == 'ok' and p is not None and ok(p)), (p.hex(' ') if p else st)


def apply(s: Session, snap: Dict, changes: List[Dict],
          progress: Callable[[str], None] = lambda m: None) -> List[Dict]:
    """Write validated changes (from check()), read each back. Returns one report per change."""
    report = []

    def record(cs, written, verified, read_back):
        for c in cs:
            report.append({**c, 'written': written, 'verified': verified, 'read_back': read_back})

    # Shifter buttons: one SW_CND_SET per shifter carrying all four nibbles (StSwImmediate).
    by_slot: Dict[int, List[Dict]] = {}
    for c in changes:
        if c['key'].startswith('button.'):
            by_slot.setdefault(c['slot'], []).append(c)
    for slot, cs in by_slot.items():
        codes = shifter_codes(next(u for u in snap['units'] if u['slot'] == slot))
        for c in cs:
            codes[c['key'].split('.')[1]] = c['value']
        progress(f'slot {slot}: writing button assignments')
        b0, b1 = codes['X'] << 4 | codes['Y'], codes['sprinter'] << 4 | codes['A']
        st, _ = s.unit(slot, 0x04, 0x10, bytes([b0, b1, 0, 0]))
        ok, rb = _verify(s, slot, 0x04, 0x14, b'\x00', lambda p: len(p) >= 2 and p[0] == b0 and p[1] == b1)
        record(cs, st == 'ok', ok, rb)

    simple = {  # key: (group, set cmd, set params, get cmd, get params, check)
        'synchro_shift_speed': lambda v: (0x0A, 0x98, bytes([v]), 0x9C, b'\x00', lambda p: p[0] == v),
        'multi_shift_speed': lambda v: (0x0B, 0x50, bytes([v, 0, 0, 0]), 0x54, b'\x00', lambda p: p[0] == v),
        'multi_shift_gear_limit': lambda v: (0x0B, 0x60, bytes([v, v, 0, 0]), 0x64, b'\x00', lambda p: p[0] == v),
    }
    order = ['synchro_shift_speed', *SEMI_KEYS, 'multi_shift_speed', 'multi_shift_gear_limit']
    for key in order:
        for c in [c for c in changes if c['key'] == key]:
            progress(f'slot {c["slot"]}: writing {key}')
            if key in SEMI_KEYS:
                m, d, fd = SEMI_KEYS[key]
                v = c['value']
                st, _ = s.unit(c['slot'], 0x32, 0xA8, bytes([m | d, fd, 0xFE, v]))
                ok, rb = _verify(s, c['slot'], 0x32, 0xAC, bytes([m | d, fd, 0, 0]),
                                 lambda p: len(p) >= 4 and p[2] == 0xFE and p[3] == v)
            else:
                g, sc, sp, gc, gp, chk = simple[key](c['value'])
                st, _ = s.unit(c['slot'], g, sc, sp)
                ok, rb = _verify(s, c['slot'], g, gc, gp, lambda p, chk=chk: bool(p) and chk(p))
            record([c], st == 'ok', ok, rb)
    return report
