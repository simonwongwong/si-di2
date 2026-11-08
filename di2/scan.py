"""Read everything the library can read in one session and return a JSON-friendly snapshot."""
import time
from datetime import datetime, timezone
from typing import Callable, Dict

from . import commands, settings
from .session import Session

KINDS = [('BT-', 'battery'), ('ST-', 'shifter'), ('FD-', 'front_derailleur'),
         ('RD-', 'rear_derailleur'), ('EW-WU', 'wireless'), ('EW-RS', 'junction')]


def kind_of(model: str) -> str:
    return next((k for prefix, k in KINDS if model.startswith(prefix)), 'unknown')


def scan(s: Session, progress: Callable[[str], None] = lambda msg: None) -> Dict:
    """Assumes `s` is connected. Reads only."""
    started = time.time()
    v = s.command(0x05)
    snap = {
        'read_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'interface': {'model': 'SM-BCR2', 'bus_slot': s.pc_slot,
                      'firmware': f'{v[0] >> 4}.{v[0] & 0xF}.{v[1]}' if v and len(v) >= 2 else None},
        'units': [],
    }
    progress('reading slot map')
    slots = commands.occupied_slots(s) or []
    for slot in [0] + [x for x in slots if x not in (0, s.pc_slot)]:
        progress(f'slot {slot}: identifying')
        unit = {'slot': slot, 'responding': False}
        stock = commands.stock_no(s, slot)
        if stock:
            unit['responding'] = True
            unit['model'] = commands.model_name(stock[0], stock[1], commands.part_no(s, slot, stock[1]))
            unit['firmware'] = commands.firmware_version(s, slot)
            unit['serial'] = commands.serial_number(s, slot)
        else:
            reg = commands.registered_stock(s, slot) if slot else None
            unit['model'] = commands.model_name(*reg) if reg else None
        unit['kind'] = kind_of(unit['model'] or '')
        reader = settings.reader_for(unit['model']) if unit['responding'] else None
        if reader:
            progress(f'slot {slot}: reading {unit["model"]} settings')
            unit['settings'] = reader(s, slot)
        snap['units'].append(unit)
    battery = next((u for u in snap['units'] if u['kind'] == 'battery'), None)
    snap['battery_percent'] = (battery or {}).get('settings', {}).get('charge_percent')
    snap['duration_s'] = round(time.time() - started, 1)
    return snap


def read_bike(dev: str = '/dev/di2', progress: Callable[[str], None] = lambda msg: None) -> Dict:
    """Connect, scan, disconnect."""
    progress('connecting (takes ~15 s)')
    s = Session(dev, log=lambda *a: None)
    try:
        s.connect()
        snap = scan(s, progress)
        progress('disconnecting')
    finally:
        s.close()
    # Which units acknowledged PC link off. There is no command to ask a unit whether
    # it's still in PC link mode, so this is the best check that the bike was released.
    report = s.close_report or {}
    names = {u['slot']: u.get('model') for u in snap['units']}
    snap['disconnect'] = {
        'master_released': report.get(0, False),
        'units': [{'slot': k, 'model': names.get(k), 'acked': v} for k, v in sorted(report.items()) if k != 0],
    }
    return snap
