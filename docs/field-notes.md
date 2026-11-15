# Field notes

What we learned getting an SM-BCR2 to talk to a Di2 bike from Linux, including
the mistakes, so you don't repeat them. Protocol details are in
[protocol.md](protocol.md); this is the "why" and the "watch out".

## How to approach it

**Read the app before you poke the hardware.** E-Tube Project is a .NET
application and decompiles almost back to source (ILSpy / `ilspycmd`). Every
time we guessed a sequence and tried it on the bike, we lost time and sometimes
left the bike in a bad state; every time we read E-Tube's code top-down first,
it just worked. Start from the screen or button that does the thing (for
connecting, `BikeConnector.ConnectUnitsRecognizing`) and follow it down to
the bytes. The transport layer on its own tells you which commands exist, not
the order, the waits, or what to do with each reply.

**Read the UI layer too.** The protocol code doesn't tell you that E-Tube's
session model is Connect → work → Disconnect, that it warns the user not to
unplug anything while connected, or that the SM-BCR2 stops charging during a
session. We built a one-shot "read the bike" first and had to redo it.

**Change one thing per hardware test.** Several of our failed attempts changed
two or three things at once, so a failure taught us nothing. Worse, some
failures leave the hardware in a state that needs a manual reset (see below),
so every attempt has a real cost for whoever is holding the bike.

**Keep a simulated bike.** Most UI and library work can be done against a fake
that answers the same commands (`tests/`). Only protocol questions need the
real thing.

## The SM-BCR2

* It's a **TI TUSB3410** USB-serial bridge. Out of the box it shows only the
  chip's ROM bootloader (one bulk OUT endpoint, no IN). The host has to upload
  firmware (`umpf3410.i51`, a Shimano build of TI's 3410 firmware) on every
  plug-in; then it re-enumerates as a serial port. Linux's `ti_usb_3410_5052`
  does this if it knows the USB ID (`new_id`) and finds
  `/lib/firmware/ti_usb-v1e44-p7220.fw`. Nothing is flashed.
* E-Tube Project 5 dropped the SM-BCR2; 4.0.4 is the last version with its
  driver and firmware.
* The installer is an InstallShield "ISSetupStream" launcher (per-file XOR
  obfuscation keyed on the file name, then zlib) around an MSI whose
  application files are in an LZX CAB. `tools/unpack_etube.py` handles all
  three layers.

## Connecting

The order and the waits matter. What works (E-Tube's sequence):

1. Reset the SM-BCR2 (`10`), wait 3 s, **reset again**. With a single reset the
   SM-BCR2 never gets a slot on the bike's bus, and selecting a unit (`06`) is
   refused with `01`.
2. Release its current limit (`1A 01`). Until then it starves the bike side;
   junction A may flash red.
3. Check power (`1A 02`). `01`: the bike's battery powers the line. `02`: no
   external power but the SM-BCR2's capacitor is charged; E-Tube then sends
   `1A 03` so the SM-BCR2 powers the bus itself, and carries on. **Handle
   both.** We treated `02` as fatal and couldn't connect for an evening; the same
   bike gave `01` all afternoon and `02` from one moment on, for reasons we still
   don't know.
4. Wait until about 5 s after the release before joining the bus (`03 00`,
   slave mode). The units need time, especially when the SM-BCR2 powers the bus.
   (E-Tube's own timer for this has a bug that often skips the wait.)
5. Poll the battery's slot map until it reads the same twice: right after
   joining, some units haven't registered yet.
6. Put the battery (the bus master) into PC link mode with the secure-code
   handshake.

Don't put the SM-BCR2 in **master mode** (`03 20`) on a bike that has a
battery: the battery is the master. We did this early on; it found no units
and may have contributed to the trouble below.

Bus slot numbers change from session to session. Always read the slot map.

## Disconnecting: the most important lesson

The battery passes PC link mode on to every unit. **Turning it off on the
battery alone leaves the other units in PC link mode.** They then don't shift,
and they ignore later sessions. We did exactly this in our first sessions.

* Disconnect like E-Tube: PC link off on **every** unit, then the battery, then
  reset the SM-BCR2, release the current limit, reset again.
* If a session was cut short, E-Tube's recovery is `ReleaseUnitsPCLink`:
  "hold" (3) to the battery, off (0) to slots 1–30, "release" (2) to the
  battery, **followed by the normal disconnect**. Without that last step the
  battery stays in "release/wait" mode, which still blocks shifting.
* Make interrupted programs disconnect too (catch SIGTERM/SIGHUP). A killed
  session left units in PC link mode and the bike wouldn't shift.
* There's no command to ask a unit whether it's in PC link mode. The best check
  is whether every unit acknowledges "off" at disconnect; record that.

## A wedged unit can flood the bus

After those early half-disconnects, the EW-WU111 wireless unit stopped
answering anything, including the release routine (its slot had changed).
Later it started flooding the bus: junction A's red and green LEDs flashed
rapidly and shifts were dropped, **even with the SM-BCR2 unplugged**.
Unplugging the EW-WU111 for about 30 s cleared it; it then worked normally
again. E-Tube's general advice for misbehaving units is to disconnect the
battery for 60 s. Nothing we could send fixed it.

## Charging

The SM-BCR2 doesn't charge the bike during a PC session. E-Tube says so
("Charging was stopped because E-TUBE PROJECT has been started") and sends
nothing to restart charging afterwards. In practice: after disconnecting,
**unplug and replug the SM-BCR2's USB cable** to resume charging. A failed
connect can also leave the SM-BCR2 not seeing the battery until it's replugged
at the USB end.

## Commands

* Unit commands go to a component via the SM-BCR2: select it (`06 slot`), then
  `48 slot group cmd params…`. Replies come back as `48` frames with `cmd|2`
  (or `cmd|3` on error). The bus is slow; allow 3 s per reply and expect late
  replies.
* Identify a unit by its model number (`01 1C`), then its part number for
  left/right shifters (`04 0C`) and SS/GS derailleurs (`32 9C`).
* Settings are plain set commands that take effect immediately. E-Tube writes
  each once, never reads back and has no rollback. We read every write back.
* The battery can tell you what's registered in a slot (`02 14 slot`) even when
  that unit doesn't answer, which is how we confirmed the silent slot was the
  wireless unit.
* **Derailleurs answer before they move.** The reply to a trim set (`0B 10`) or a
  shift (`0B 08`) carries the value *before* the change; the derailleur moves
  afterwards. Matching the reply against the target made every trim step down
  and every multi-gear shift look like a failure, and quick retries raced the
  motor. Send once, then read back (`0B 14`, `0B 0C`) until it shows the target.
* The rear trim is one value for the whole derailleur (range ±16 on RD-R8050),
  not one per gear.
* E-Tube's preset files are encrypted with a key embedded in the app; we don't
  use or publish it.

## Power check before moving a derailleur

E-Tube decides between battery % and the SM-BCR2's capacitor from the power
check (`1A 02`). On our bike that check said "no battery" while the battery
answered its charge request (`26 44`, 100 %), and the SM-BCR2 then didn't
answer the capacitor query (`1C`). Trust the battery when it answers. If the
capacitor really is the source, E-Tube doesn't command anything to recharge
it: it re-reads every 3 s for up to ~90 s while the SM-BCR2 recharges from USB.

## Things E-Tube does that we deliberately don't copy

* Opening its Maintenance tab sends a command to the front derailleur without
  asking.
* Cancelling a rear derailleur adjustment restores the trim but leaves the
  derailleur in adjust mode. Cancelling a front adjustment doesn't restore the
  trim at all, though the dialog says it does.
* Pulling the USB cable mid-session makes E-Tube quit ("An unexpected error
  has occurred. Exiting.").

## Open questions

* Why the power state changes between `01` and `02` on the same bike.
* What `26 40 14` (set battery level 20 %) sent to the front derailleur during
  E-Tube's front adjustment is for.
* The secure-code echo E-Tube waits for after PC link start arrives before the
  acknowledgement on our bike, so we skip that wait. Is that true on others?
