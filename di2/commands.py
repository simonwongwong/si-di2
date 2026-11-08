"""Read-only unit commands, with E-Tube's names and decoding.

Each is (group, command); requests carry a single reserved ``00`` parameter.
Sources: EtubeDataLinksDccCommand / Unit.cs and the plccomtbl.json command table
in E-Tube Project 4.0.4.
"""
from typing import Optional, Tuple

from .session import Session

UNIT_SLOT_NO_GET = (0x01, 0x14)    # master only: bitmap of occupied bus slots in params[2:6]
UNIT_STOCK_NO_GET = (0x01, 0x1C)   # (series no, unit no, ...) -> model
UNIT_FW_VER_GET = (0x01, 0x2C)     # major<<4|minor, sub-minor, revision
UNIT_SERIAL_GET = (0x01, 0x3C)     # 6-byte little-endian DCAS serial number
MBT_SOC2_GET = (0x26, 0x44)        # battery state of charge, percent (BT-DN110)
ST_PART_GET = (0x04, 0x0C)         # shifters (unit no 1): part no, e.g. ST-R8050 1 = L, 2 = R
PCA_PART_GET = (0x32, 0x9C)        # derailleurs (unit no 3/4): part no, e.g. RD-R8050 0 = SS, 1 = GS

# (series, unit no, part no or None) -> model, from E-Tube's unitlist.json (R8050-era parts).
MODELS = {
    (0x11, 0x00, None): 'BT-DN110',
    (0x11, 0x02, None): 'EW-RS910',
    (0x10, 0x08, None): 'EW-WU111',
    (0x14, 0x01, 0x01): 'ST-R8050-L',
    (0x14, 0x01, 0x02): 'ST-R8050-R',
    (0x14, 0x01, 0x0A): 'ST-R8060-L',
    (0x14, 0x01, 0x09): 'ST-R8060-R',
    (0x14, 0x01, 0x0D): 'ST-R8070-L',
    (0x14, 0x01, 0x0C): 'ST-R8070-R',
    (0x14, 0x03, None): 'FD-R8050',
    (0x14, 0x04, 0x00): 'RD-R8050-SS',
    (0x14, 0x04, 0x01): 'RD-R8050-GS',
}


def occupied_slots(s: Session) -> Optional[list]:
    st, p = s.unit(0, *UNIT_SLOT_NO_GET)
    if st != 'ok' or len(p) < 6:
        return None
    bits = int.from_bytes(p[2:6], 'little')
    s.slots = [i for i in range(32) if bits >> i & 1]
    return s.slots


def stock_no(s: Session, slot: int) -> Optional[tuple]:
    st, p = s.unit(slot, *UNIT_STOCK_NO_GET)
    return (p[0], p[1]) if st == 'ok' and len(p) >= 2 else None


def part_no(s: Session, slot: int, unit_no: int) -> Optional[int]:
    """Only shifters (unit no 1) and derailleurs (3, 4) have a part number."""
    cmd = ST_PART_GET if unit_no == 1 else PCA_PART_GET if unit_no in (3, 4) else None
    if cmd is None:
        return None
    st, p = s.unit(slot, *cmd)
    return p[0] if st == 'ok' and p else None


def model_name(series: int, unit_no: int, part: Optional[int] = None) -> str:
    return (MODELS.get((series, unit_no, part)) or MODELS.get((series, unit_no, None))
            or f'unknown series {series} unit {unit_no} part {part}')


def identify(s: Session, slot: int) -> Optional[str]:
    """Model name the way PcInterfaceDevice.GetUnitIdentifer does it: stock no, then part no."""
    stock = stock_no(s, slot)
    if stock is None:
        return None
    return model_name(stock[0], stock[1], part_no(s, slot, stock[1]))


def registered_stock(s: Session, slot: int) -> Optional[tuple]:
    """What the master has registered in `slot` (SYS_SLOT_STOCK_NO_GET, group 2 cmd 0x14).

    Works even when the unit itself doesn't answer. Reply params: slot, series, unit no.
    """
    st, p = s.unit(0, 0x02, 0x14, bytes([slot]))
    return (p[1], p[2]) if st == 'ok' and len(p) >= 3 and p[0] == slot else None


def firmware_version(s: Session, slot: int) -> Optional[str]:
    st, p = s.unit(slot, *UNIT_FW_VER_GET)
    if st != 'ok' or len(p) < 3:
        return None
    return f'{p[0] >> 4}.{p[0] & 0xF}.{p[1]}.{p[2]}'


def serial_number(s: Session, slot: int) -> Optional[str]:
    st, p = s.unit(slot, *UNIT_SERIAL_GET)
    return format(int.from_bytes(p[:6], 'little'), 'X') if st == 'ok' and len(p) >= 6 else None


# Failure-history indices E-Tube reads per unit (UnitDataDefinitions). Index 0 and 1 together
# hold the last error-code record (DTC): 0 = date/time, 1 = reset count + DTC value.
_FAILURE_COMMON = {40: 'serial registration fail', 41: 'unit disconnect', 42: 'compatibility (nc)'}
FAILURE_INDICES = {
    'BT-': {4: 'RTC communication', 5: 'SOC communication fail', 26: 'ADC failure',
            33: 'power output fail', 34: 'Li-ion low temperature', 35: 'Li-ion high temperature',
            36: 'CPC low temperature', 37: 'CPC high temperature', 39: 'other master detected'},
    'FD-': {21: 'MR sensor', 26: 'ADC', 27: 'operation stop', 28: 'operation time over',
            29: 'position gap / calibration', 30: 'motor', 31: 'inching timeout', **_FAILURE_COMMON},
    'RD-': {21: 'MR sensor', 26: 'ADC', 27: 'operation stop', 28: 'operation time over',
            29: 'position gap / calibration', 30: 'motor', 31: 'inching timeout', 32: 'EEPROM fail',
            **_FAILURE_COMMON},
    'ST-': {14: 'switch X stuck', 15: 'switch Y stuck', 17: 'sprinter switch stuck', 18: 'switch A stuck',
            **_FAILURE_COMMON},
    'EW-RS': {19: 'adjust switch stuck', **_FAILURE_COMMON},
    'EW-WU': {6: 'ANT communication fail', **_FAILURE_COMMON},
}


def failure_record(s: Session, slot: int, index: int) -> Tuple[str, Optional[bytes]]:
    """PCA_HIST_ERR_GET (32 CC idx). Returns ('none'|'set'|status, data)."""
    st, p = s.unit(slot, 0x32, 0xCC, bytes([index]))
    if st == 'error':
        return ('none' if p and p[0] == 0x42 else f'error {p.hex(" ") if p else ""}'), None
    if st != 'ok' or not p or p[0] != index:
        return st, None
    data = p[1:]
    return ('none' if not data or all(b == 0 for b in data) or all(b == 0xFF for b in data) else 'set'), data


def last_error_code(s: Session, slot: int) -> Optional[dict]:
    """Last error-code record (failure history 0 + 1): when, reset count, DTC value."""
    st0, when = failure_record(s, slot, 0)
    st1, code = failure_record(s, slot, 1)
    if st0 != 'set' and st1 != 'set':
        return None
    out = {}
    if st0 == 'set' and len(when) >= 5:
        out['when'] = f'{2000 + when[0]:04}-{when[1]:02}-{when[2]:02} {when[3]:02}:{when[4]:02}'
    if st1 == 'set' and len(code) >= 3:
        out['reset_count'] = code[0] | code[1] << 8
        out['dtc'] = f'0x{code[2]:02X}'
    return out


def battery_percent(s: Session, slot: int = 0) -> Optional[int]:
    """BT-DN110 charge. E-Tube treats values over 100 as invalid."""
    st, p = s.unit(slot, *MBT_SOC2_GET)
    return p[0] if st == 'ok' and p and p[0] <= 100 else None
