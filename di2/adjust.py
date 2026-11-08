"""Rear derailleur adjustment, following E-Tube 4.0.4's RearDerailleurAdjust.

The derailleur moves, so the rider must have the rear wheel free and turn the cranks.

  prepare()  read gear count, current gear and the trim (value and +/- range)
  start()    shift to gear 5 (E-Tube's start position), enter adjust mode (0B 00 01)
  shift(d)   one gear at a time; gear 1 is the largest cog
  trim(d)    one step at a time; each value is stored on the derailleur immediately
  finish()   leave adjust mode (0B 00 00); the new trim stays
  cancel()   put the original trim back and leave adjust mode (E-Tube's cancel does
             the first but not the second)

Every shift is mirrored to the front derailleur (E-Tube's SendRearShiftToFD), and
power is checked first: battery % (> 4) if the bike's battery feeds the bus, else
the SM-BCR2's capacitor (>= 2659, PCE 1C).
"""
from typing import Dict, Optional

from .session import Di2Error, Session

START_GEAR = 5
CAPACITOR_MIN = 2659


class RearAdjust:
    def __init__(self, s: Session, rd_slot: int, fd_slot: Optional[int], master_slot: int = 0):
        self.s, self.rd, self.fd, self.master = s, rd_slot, fd_slot, master_slot
        self.gear = self.max_gear = None
        self.trim_value = self.trim_range = self.original_trim = None
        self.in_adjust_mode = False

    # --- reads ------------------------------------------------------------------------

    def _read_gear(self) -> None:
        st, p = self.s.unit(self.rd, 0x0B, 0x0C)
        if st != 'ok' or not p or len(p) < 2:
            raise Di2Error(f'could not read the rear gear ({st})')
        self.gear, self.max_gear = p[0], p[1]

    def _read_trim(self) -> None:
        st, p = self.s.unit(self.rd, 0x0B, 0x14)
        if st != 'ok' or not p or len(p) < 2:
            raise Di2Error(f'could not read the rear trim ({st})')
        self.trim_value = p[0] - 256 if p[0] > 127 else p[0]     # int8
        self.trim_range = p[1]

    def power(self) -> Dict:
        """Battery % when the bike's battery feeds the bus, else the BCR2 capacitor level."""
        if self.s.battery_present:
            st, p = self.s.unit(self.master, 0x26, 0x44)
            return {'source': 'battery', 'level': p[0] if st == 'ok' and p else None}
        r = self.s.command(0x1C)
        return {'source': 'capacitor', 'level': (r[0] | r[1] << 8) if r and len(r) >= 2 else None}

    def _check_power(self, battery_min: int) -> None:
        pw = self.power()
        if pw['level'] is None:
            raise Di2Error('could not read the power level')
        if pw['source'] == 'battery' and pw['level'] < battery_min:
            raise Di2Error(f'battery at {pw["level"]} %: charge it before adjusting (needs {battery_min} %)')
        if pw['source'] == 'capacitor' and pw['level'] < CAPACITOR_MIN:
            raise Di2Error('the SM-BCR2 capacitor is low; leave it connected a minute and try again')

    def state(self) -> Dict:
        return {'gear': self.gear, 'max_gear': self.max_gear, 'trim': self.trim_value,
                'trim_range': self.trim_range, 'original_trim': self.original_trim,
                'in_adjust_mode': self.in_adjust_mode}

    # --- steps ------------------------------------------------------------------------

    def prepare(self) -> Dict:
        self._read_gear()
        self._read_trim()
        self.original_trim = self.trim_value
        self._check_power(5)                     # E-Tube: charge < 5 -> BatteryLack
        return self.state()

    def _send_gear(self, slot: int, gear: int) -> None:
        """EXTM_RA_SFT_LV_SET: reply must echo the target gear (2 retries, +1 after an error reply)."""
        for _ in range(4):
            st, p = self.s.unit(slot, 0x0B, 0x08, bytes([gear, self.max_gear, 0, 0]))
            if st == 'ok' and p and p[0] == gear:
                return
        raise Di2Error(f'slot {slot} did not confirm the shift to gear {gear}')

    def _go_to(self, gear: int) -> None:
        self._check_power(5)                     # per shift E-Tube needs > 4 %
        if gear == self.gear:
            return
        self._send_gear(self.rd, gear)
        if self.fd is not None:
            self._send_gear(self.fd, gear)       # tell the FD the rear position
        for _ in range(5):                       # E-Tube re-reads until the RD reports it
            self._read_gear()
            if self.gear == gear:
                return
        raise Di2Error(f'the rear derailleur reports gear {self.gear}, expected {gear}')

    def _set_mode(self, mode: int) -> None:
        st, _ = self.s.unit(self.rd, 0x0B, 0x00, bytes([mode]))
        if st != 'ok':
            raise Di2Error(f'could not set the rear shift mode to {mode} ({st})')
        self.in_adjust_mode = mode == 1

    def start(self) -> Dict:
        self._go_to(min(START_GEAR, self.max_gear))
        self._set_mode(1)                        # SendManualAdjustShiftCommand
        return self.state()

    def shift(self, delta: int) -> Dict:
        target = self.gear + (1 if delta > 0 else -1)
        if 1 <= target <= self.max_gear:
            self._go_to(target)
        return self.state()

    def _send_trim(self, value: int) -> None:
        """EXTM_RA_ADJ_LV_SET. E-Tube's rear wizard passes the gear count as the second byte."""
        for _ in range(3):
            st, p = self.s.unit(self.rd, 0x0B, 0x10, bytes([value & 0xFF, self.max_gear]))
            if st == 'ok' and p and p[0] == value & 0xFF:
                self.trim_value = value
                return
        raise Di2Error(f'the rear derailleur did not confirm trim {value}')

    def trim(self, delta: int) -> Dict:
        target = self.trim_value + (1 if delta > 0 else -1)
        if -self.trim_range <= target <= self.trim_range:
            self._send_trim(target)
        return self.state()

    def finish(self) -> Dict:
        self._set_mode(0)                        # SendManualShiftCommand
        return self.state()

    def cancel(self) -> Dict:
        """Best effort: restore the original trim, then always try to leave adjust mode."""
        err = None
        if self.original_trim is not None and self.trim_value != self.original_trim:
            try:
                self._send_trim(self.original_trim)
            except Di2Error as e:
                err = e
        if self.in_adjust_mode:
            self._set_mode(0)
        if err:
            raise err
        return self.state()
