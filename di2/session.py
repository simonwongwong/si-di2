"""A connection to a Di2 system through an SM-BCR2, following E-Tube Project 4.0.4.

Connect (``BikeConnector.ConnectUnitsRecognizing`` and the calls below it):

1. ``10``        soft reset, 3 s, reset again (without the second reset the BCR2
                 never gets a bus slot). The bike must be awake: an idle Di2 system
                 makes the BCR2 report no battery in step 3, and it then stays that
                 way until it's replugged at the USB end.
2. ``1A 01``     release the BCR2's current limit. Without this the bike side is
                 starved and junction A flashes red.
3. ``1A 02``     power state: ``01`` the bike's battery powers the bus; ``02`` no external
                 power, so ``1A 03`` makes the BCR2 power the bus (as E-Tube does).
4. wait >= 5 s after the unlock (``UnitManager.WaitPcDevPnPDone``).
5. ``03 00``     join the bus as a slave of the bike's master unit.
6. ``04``        check: ``0x80`` connected, ``0x20`` master, low 5 bits = our bus slot.
7. PC link mode on the master (slot 0), see :meth:`Session.pc_link`.

Close (``PcInterfaceDevice.Disconnect``): PC link mode off on every unit, then the
master; ``10`` reset, ``1A 01`` power-limit unlock, ``10`` reset.

Unit commands go to a component on the bus: select it with ``06 <slot>``, then
send ``48 <slot> <group> <command> <params...>``. The reply is a ``48`` frame
carrying ``<x> <group> <command|2> <params...>`` (``command|3`` on error).
"""
import os
import time
from typing import Callable, Optional, Tuple

import serial

from . import framing

BAUD = 38400  # SM-BCR2 and SM-PCE1; the SM-PCE02 runs at 2 Mbaud with RTS on.

# PCA_SECURE_CODE_SET pairs sent after PCA_COM_MODE_SET (EtubeDataLinksDccCommand.SendSetPCLinkModeStart).
_SECURE_CODES = [(43, 162), (14, 48), (77, 122), (43, 98), (180, 133)]


class Di2Error(IOError):
    pass


def forbidden(ctrl: int, data: bytes = b'') -> Optional[str]:
    """Refuse frames that put the BCR2 or a unit into firmware-update/bootloader modes.

    From E-Tube's firmware-update paths (EtubeDataLinksDccCommand, DataLinksFirmware,
    DataLinksPce, NrfWrite). Nothing in this library needs them; this guards typos.
    """
    if ctrl == 0x03 and data and data[0] & 0x40:
        return 'BCR2 update protocol (03 with 0x40)'
    if ctrl == 0x0E:
        return 'BCR2 into its bootloader (0E)'
    if ctrl == 0x88:
        return 'bootloader FirmUp keys (88)'
    if ctrl == 0x48 and len(data) >= 3:
        group, cmd = data[1], data[2]
        if (group, cmd) == (0x32, 0x20):
            return 'PCA_UPDATE_SET (32 20): unit into update mode'
        if group in (0xF0, 0xF8):
            return 'Nordic DFU packet'
        if (group, cmd) in ((0x01, 0x28), (0x26, 0x08)):
            return 'power off / bus power (01 28, 26 08)'
        if group in (0x0A, 0x0B) and cmd in (0x18, 0x20, 0x48):
            return 'derailleur gear-count / trim-range / calibration write'
        if (group, cmd) in ((0x26, 0x40), (0x32, 0x60), (0x32, 0x68)):
            return 'battery gauge or position calibration write'
    return None


def open_port(dev: str) -> serial.Serial:
    """Open the BCR2, then drop DTR/RTS (E-Tube runs with both low).

    Opening with the lines low from the start (no brief raise) coincided with the BCR2
    losing the bike's battery during connect (2026-11-07); this is the open that worked.
    """
    port = serial.Serial(dev, BAUD, timeout=0.1)
    port.dtr = port.rts = False
    return port


def interface_status(dev: str = '/dev/di2', query: bool = False) -> dict:
    """What the BCR2 itself reports, without joining the bus or talking to any component.

    ``04`` is E-Tube's own link check (CheckPcInterfaceDeviceLink); ``1A 02`` is the
    power-state query E-Tube polls while charging. Neither reaches the bike's units.
    Off by default (query=False: USB presence only): sending ``1A 02`` to an idle BCR2,
    which E-Tube never does, is a suspect for the BCR2 later losing the bike's battery.
    """
    out = {'usb': os.path.exists(dev), 'responding': None, 'bike_battery': None,
           'bus': None, 'bus_slot': None}
    if not out['usb'] or not query:
        return out
    port, dec = open_port(dev), framing.Decoder()
    try:
        def ask(ctrl: int, data: bytes = b'') -> Optional[bytes]:
            port.write(framing.encode(ctrl, data))
            end = time.time() + 1.5
            while time.time() < end:
                for f in dec.feed(port.read(64)):
                    if f.fcs_ok and f.ctrl == ctrl | 0x20:
                        return f.data
            return None

        mode = ask(0x04)
        out['responding'] = mode is not None
        if mode:
            m = mode[0]
            out['bus'] = 'idle' if not m & 0x80 else ('joined as master' if m & 0x20 else 'joined as slave')
            out['bus_slot'] = m & 0x1F if m & 0x80 else None
        power = ask(0x1A, b'\x02')
        if power and len(power) >= 2:
            out['bike_battery'] = {1: 'detected', 2: 'not detected', 3: 'needs charge', 4: 'needs charge',
                                   5: 'busy'}.get(power[1], f'unknown ({power[1]})')
    finally:
        port.close()
    return out


class Session:
    def __init__(self, dev: str = '/dev/di2', log: Callable[..., None] = lambda *a: None):
        self.dev = dev
        self.log = log
        self.pc_slot: Optional[int] = None
        self.slots: list = []   # occupied bus slots, from the master's slot map
        self._target: Optional[int] = None
        self.close_report: Optional[dict] = None   # filled by close(): which units acked PC link off
        self.connected_at: Optional[float] = None
        self.battery_present = True     # from the power check; affects E-Tube's waits
        self._decoder = framing.Decoder()
        self._port = open_port(dev)

    # --- transport -----------------------------------------------------------------

    def _write(self, ctrl: int, data: bytes = b'') -> None:
        why = forbidden(ctrl, data)
        if why:
            raise Di2Error(f'refusing to send {why}')
        self._port.write(framing.encode(ctrl, data))

    def _wait(self, seconds: float, match: Callable[[framing.Frame], bool]) -> Optional[framing.Frame]:
        end = time.time() + seconds
        while time.time() < end:
            for f in self._decoder.feed(self._port.read(256)):
                if not f.fcs_ok:
                    self.log('bad fcs', f)
                elif match(f):
                    return f
                else:
                    self.log('ignored', f.ctrl, f.data.hex(' '))
        return None

    def command(self, ctrl: int, data: bytes = b'', wait: float = 2.0) -> Optional[bytes]:
        """Send a command to the BCR2 itself; return its reply data, or None on timeout."""
        self._write(ctrl, data)
        f = self._wait(wait, lambda f: f.ctrl == ctrl | 0x20)
        return f.data if f else None

    def select(self, slot: int) -> None:
        """SetTargetAddress: route following unit commands to `slot`."""
        if self._target == slot:
            return
        r = self.command(0x06, bytes([slot]))
        if r is None or r[:1] != b'\x00':
            raise Di2Error(f'cannot select slot {slot}: {r!r}')
        self._target = slot

    def unit(self, slot: int, group: int, cmd: int, params: bytes = b'\x00',
             wait: float = 3.0, retries: int = 2) -> Tuple[str, Optional[bytes]]:
        """Send a unit command. Returns ('ok' | 'error', params) or ('timeout', None)."""
        self.select(slot)

        def is_reply(f: framing.Frame) -> bool:
            return f.ctrl == 0x48 and len(f.data) >= 3 and f.data[1] == group and f.data[2] in (cmd | 2, cmd | 3)

        for _ in range(retries + 1):
            self._write(0x48, bytes([slot, group, cmd]) + params)
            f = self._wait(wait, is_reply)
            if f:
                return ('ok' if f.data[2] == cmd | 2 else 'error'), f.data[3:]
        return 'timeout', None

    # --- connection ----------------------------------------------------------------

    def connect(self) -> None:
        # Reset twice, as E-Tube's BCR2 reset does (SendResetCommand: reset, wait up to 3 s
        # for a USB reconnect, reset again). Verified on the bike 2026-11-07: with a single
        # reset the BCR2 never gets a bus slot and 06 (select) is refused.
        self.log('reset', self.command(0x10, wait=1.0))
        time.sleep(3.0)
        if not os.path.exists(self.dev):
            raise Di2Error('BCR2 disappeared after reset')
        self.log('reset', self.command(0x10, wait=1.0))
        time.sleep(1.0)
        self.log('current limit release', self.command(0x1A, b'\x01'))
        unlocked_at = time.time()
        self.log('BCR2 firmware', self.command(0x05))      # UpdatePcInterfaceUnit
        # CheckPowerState / GetPowerInfo: wait 3 s, release again, then the power state.
        time.sleep(3.0)
        self.command(0x1A, b'\x01')
        power = self.command(0x1A, b'\x02')
        self.log('power state', power)
        state = power[1] if power and len(power) >= 2 else None
        # PcInterfaceDevice.GetPowerInfo: 1 = the bike's battery powers the bus; 2 = no
        # external power but the BCR2's capacitor is charged, so E-Tube sends 1A 03
        # ("discharge start": the BCR2 powers the bus) and carries on.
        if state == 1:
            self.battery_present = True
        elif state == 2:
            r = self.command(0x1A, b'\x03')
            self.log('discharge start (BCR2 powers the bus)', r)
            if not r or r[1:2] != b'\x00':
                raise Di2Error(f'BCR2 refused to power the bus (1A 03 -> {r!r})')
            self.battery_present = False
        else:
            # 3/4: capacitor too low; E-Tube charges it first (up to ~90 s). Not implemented.
            raise Di2Error(f'BCR2 not ready (power state {power!r}); leave it plugged in a minute and retry')
        time.sleep(max(0.0, 5.5 - (time.time() - unlocked_at)))
        self.log('slave mode', self.command(0x03, b'\x00', wait=5.0))
        mode = self.command(0x04)
        self.log('mode', mode)
        if not mode or mode[0] & 0xE0 != 0x80:
            raise Di2Error(f'not connected as a slave (mode {mode!r})')
        self.pc_slot = mode[0] & 0x1F
        # UnitManager.CheckMasterUnitConnect: select the master and read the slot map
        # before PC link mode (UNIT_SLOT_NO_GET, group 1 cmd 0x14).
        self._target = None
        self._wait_bus_settled()
        # SendSetPCLinkModeStart makes up to 3 attempts.
        if not any(self.pc_link(0, True) for _ in range(3)):
            raise Di2Error('master did not acknowledge PC link mode')

    def _wait_bus_settled(self, timeout: float = 30.0) -> None:
        """Poll the master's slot map until it reads the same twice in a row.

        Units that haven't joined the bus yet don't follow the master into PC link
        mode and then never answer. E-Tube gets this time for free from its UI flow.
        """
        last, end = None, time.time() + timeout
        while time.time() < end:
            st, p = self.unit(0, 0x01, 0x14)
            cur = p[2:6] if st == 'ok' and p and len(p) >= 6 else None
            self.log('slot map', cur.hex(' ') if cur else st)
            if cur is not None and cur == last:
                bits = int.from_bytes(cur, 'little')
                self.slots = [i for i in range(32) if bits >> i & 1]
                return
            last = cur
            time.sleep(0.5)
        self.log('slot map did not settle; continuing')

    def pc_link(self, slot: int, on: bool) -> bool:
        """PCA_COM_MODE_SET (group 50, cmd 0x10), plus the secure-code handshake when turning on.

        While a unit is in PC link mode it answers the PC and normal operation is paused.
        """
        self.select(slot)
        self._write(0x48, bytes([slot, 50, 0x10, 1 if on else 0, self.pc_slot, 0, 0]))
        if on:
            time.sleep(1.0 if self.battery_present else 0.3)    # SendSetPCLinkModeStart
            for i, (lo, hi) in enumerate(_SECURE_CODES):
                if i:
                    time.sleep(0.1)
                self._write(0x48, bytes([slot, 50, 0x30, hi, lo, 0, 0]))
        ack = self._wait(3.0, lambda f: f.ctrl == 0x48 and f.data[1:3] == bytes([50, 0x12]))
        self.log('pc link', 'on' if on else 'off', slot, 'ack' if ack else 'no ack')
        # E-Tube (as a slave) also waits up to 3 s for a group-50 cmd 0x30/0x31 echo of the
        # secure codes. On this bus the echo arrives before the 0x12 ack, so that wait
        # always timed out; skipping it saves 3 s per connect.
        return ack is not None

    # PCA_COM_MODE_SET modes
    PC_LINK_OFF, PC_LINK_ON, PC_LINK_WAIT, PC_LINK_DISWAIT = 0, 1, 2, 3

    def _pc_link_stop(self, slot: int, mode: int) -> bool:
        """SendSetPCLinkModeStop: select without retry, then up to 3 sends with a 300 ms wait."""
        r = self.command(0x06, bytes([slot]), wait=0.5)
        if r is None or r[:1] != b'\x00':
            return False
        self._target = slot
        for _ in range(3):
            self._write(0x48, bytes([slot, 50, 0x10, mode, self.pc_slot, 0, 0]))
            if self._wait(0.3, lambda f: f.ctrl == 0x48 and f.data[1:3] == bytes([50, 0x12])):
                return True
        return False

    def release_units_pc_link(self) -> list:
        """E-Tube's recovery after a failed connect (PcInterfaceDevice.ReleaseUnitsPCLink).

        Hold the master (DISWAIT), turn PC link mode off on every possible slot, then
        release the master (WAIT). Returns the slots that acknowledged.
        """
        acked = []
        if self._pc_link_stop(0, self.PC_LINK_DISWAIT):
            acked.append(0)
        for slot in range(1, 31):
            if slot != self.pc_slot and self._pc_link_stop(slot, self.PC_LINK_OFF):
                acked.append(slot)
        self._pc_link_stop(0, self.PC_LINK_WAIT)
        self.log('released pc link on', acked)
        return acked

    def close(self) -> None:
        """PcInterfaceDevice.Disconnect: PC link off on every connected unit, then the master, then reset.

        Turning off only the master leaves the other units in PC link mode; they then
        ignore later sessions (and don't shift) until released or power-cycled.
        """
        try:
            if self.pc_slot is not None:
                report = {}
                for slot in self.slots:
                    if slot not in (0, self.pc_slot):
                        report[slot] = self._pc_link_stop(slot, self.PC_LINK_OFF)
                report[0] = self._pc_link_stop(0, self.PC_LINK_OFF)
                self.close_report = report
                self.log('pc link off acks', report)
            # SendResetAndPowerLimitUnlock, then the application-end reset. Skipping the
            # unlock can leave the BCR2 current-limited (and the bike not charging).
            self.command(0x10, wait=1.0)
            time.sleep(1.0)
            self.command(0x1A, b'\x01')
            self.command(0x10, wait=1.0)
        finally:
            self._port.close()

    def __enter__(self) -> 'Session':
        self.connect()
        return self

    def __exit__(self, *exc) -> None:
        self.close()
