# How E-Tube Project behaves (app layer)

Blueprint for a frontend, from reading E-Tube Project 4.0.4's decompiled UI
and model code (the protocol itself is in [protocol.md](protocol.md) and
[commands.md](commands.md)). Scope: a road Di2 bike like ours (BT-DN110
battery master, ST-R8070 shifters, FD/RD-R8050, EW-RS910, EW-WU111) on an
SM-BCR2. E-Tube decides this is genre "DN R11". Quoted strings are E-Tube's
English UI text; class names point into the decompiled source.

## Session lifecycle

| state | what the user can do |
|---|---|
| disconnected | Connect screen ("CONNECT THE BIKE AND CLICK NEXT"), Settings/Help. No Disconnect button, no battery icon. |
| connecting | modal progress dialog with Cancel: *"Unit recognition. Do not remove or insert the battery or electric wires while this procedure is in progress. If they have been removed or inserted, repeat the procedure from the main window."* |
| connected | Update / Customize / Maintenance tabs, battery icon, Disconnect button. |

On connect (`BikeConnector.ConnectUnitsRecognizing` → `BikeConnectViewModel`):

* With an SM-BCR2, once per run: *"Charging was stopped because E-TUBE PROJECT
  has been started."* The BCR2 does not charge the bike during a session, and
  E-Tube sends nothing to restart charging afterwards (a USB replug does).
* Every unit's settings are read once (`ReadUnitAllValues`); the battery %
  is read once (no live refresh).
* If firmware updates exist, it jumps to the Update tab.
* A 0.75 s "connected" flash, then (unless turned off under Settings → "Dialog
  at the time of connection"): *"While a bicycle is connected, do not turn OFF
  the PC or remove the USB cable, electric wire, or battery. When disconnecting
  a bicycle, always use the 'Disconnect' button in the upper left."*

Disconnect (`BikeConnector.Disconnect`): refused while a firmware update,
derailleur adjustment step or error check is running. With unapplied
customize changes it asks *"Do you want to disconnect? Customized settings
exist. If disconnected, the settings will not be applied."* Then E-Tube's
disconnect sequence (protocol.md) and back to the connect screen.

Things E-Tube does that we should do better:

* Closing the app while connected disconnects, but silently drops unapplied
  changes (no prompt).
* Pulling the USB cable mid-session shows *"An unexpected error has occurred.
  Exiting."* and quits. Nothing is sent (the device is gone).
* No keep-alive, no watchdog, no live battery refresh.

## Errors

* A unit command that fails mid-session just shows a generic message ("An
  error occurred during programming.", "An error occurred while retrieving the
  setting values."); the session stays up. No automatic recovery outside
  firmware update.
* BCR2 not answering during connect: *"A communication error with {0}
  occurred. Disconnect {0} from the PC once and reconnect again. If the same
  error persists despite doing so, {0} may be faulty."*, then
  `ReleaseUnitsPCLink` and the normal disconnect.
* Cancel during connect runs the same cleanup.
* Error notifications from the BCR2 (ctrl `3F`) are effectively ignored.

## Customize (settings)

* **Edits are staged in memory**; nothing is written until **APPLY**. Apply is
  hidden when nothing changed and disabled (with *"Settings could not be
  applied."*) while any value is invalid.
* Apply first checks: if front/rear shift up/down isn't assigned to any
  button, *"The following functions are not included. Are you sure you want to
  continue?"* On this bike E-Tube does **not** reject two buttons with the same
  function.
* Writes go unit by unit, only changed items, in the unit file's item order,
  each a single send (3 s timeout, no retry). **No rollback**: items already
  written stay written if a later one fails (*"Failed to write settings to the
  bicycle."*). **No read-back** after writing.
* Cancel: *"Do you want to reset all the changed settings?"*. Leaving the tab
  with changes: *"Do you want to change the screen? Customized settings exist.
  If changed, the settings will not be applied."*. Per-page Default button:
  *"Do you want to reset the selected settings to default?"* (stages defaults).
* A value read from the bike that isn't an allowed choice is shown blank and
  blocks Apply until fixed.

Rules that matter on this bike (`CustomizeSettingSShift`, `RdMuImpl`,
`StSwImpl`, `CustomizeSettingMultiShift`):

* **Synchro panel** only if BT-DN110 fw ≥ 4.2.0, junction A supports it, and FD/RD
  have chainring/cassette items.
* **Gear position control** is forced on (greyed out) for FD-R8050 with
  52-36 or 50-34 chainrings; with it on, small ring + the two smallest cogs are
  blocked in synchro maps.
* **Synchro shift points** are clamped from a gear-ratio table (chainring /
  cog, rear position 1 = largest cog) so that, roughly, down point ≤ down target
  ≤ up point and down point ≤ up target ≤ up point, with overlap rules. Copy the
  rules from `UpdateSynchroShiftMinMaxIndexes` verbatim; they depend on the
  current values. FD-R8050 limits: setting 1 (up) point 1–11, default 7,
  target 1–11, default 5; setting 2 (down) point 1–4, default 2, target 1–11,
  default 4.
* **Semi-synchro** is encoded as shift point 254; target = rear steps 0..StepMax
  (FD-R8050 + RD-R8050-GS: 53-39 → 2/2/2/1, 52-36 → 3/3/2/2, 50-34 → 3/3/3/2,
  46-36 → 2/1/1/1 for 11-28/30/32/34). Changing a shift mode resets that
  mode's map to defaults.
* **Changing chainrings or cassette** doesn't rewrite the maps immediately;
  invalid points block Apply, and opening the synchro page resets them with
  *"The shifting point is within the unsettable range and cannot operate
  normally. Will be displayed changed to default values."*
* **Multi-shift off** = interval 0, and the gear limit is forced to unlimited.
  1st/2nd-step positions are hidden without an SW-M9050.
* **Buttons**: D-FLY Ch.1–4 need shifter fw ≥ 3.1.0, BT-DN110 ≥ 4.4.0 and
  EW-WU111 ≥ 4.3.0; otherwise the A button offers cycle computer L/R instead.
  No sprinter item on ST-R8070.
* **Wireless**: mode needs EW-WU111 ≥ 4.4.0. BLE name `^[0-9a-zA-Z]{1,8}$`,
  passkey `000000` or `^[1-9][0-9]{5}$` entered twice; the passkey is never
  read back and a BLE restart (`40 20`) follows a passkey or name write.

Presets (`.preset4`): the settings as JSON, encrypted with a key embedded in
E-Tube. Not reproduced here; a frontend could use its own plain format.

## Maintenance

* **Error check is not offered with an SM-BCR2** (its unit file lacks the
  capability; E-Tube says an SM-PCE1/PCE02 is required). The procedures exist
  for reference: RD/FD are shifted through every gear while you turn the
  cranks; shifter buttons are tested by listening for press events; junction
  LEDs are switched through their colours; the battery runs a self-test.
* **Opening the Maintenance tab already sends writes**: it reads the rear gear
  and sends it to the FD (`0B 08 …` to the FD slot). Our app shouldn't copy
  that implicitly.
* **Derailleur adjustment** (needs battery ≥ 5 %; E-Tube checks nothing about
  the wheel or cranks, it only says *"Keep rotating the crank arm. Be careful
  not to get your hand caught in the gears!"*):
  * Rear: move to gear 5 (also told to the FD), `0B 00 01` adjust mode, each
    click sends the trim (`0B 10 value max`) immediately, finish with `0B 00
    00`. Cancel re-sends the old trim but not `0B 00 00`.
  * Front: bolt adjustment first, then low and top electric steps, each
    positioning FD and RD, sending `26 40 14` and the rear gear to the FD, then
    trim clicks; finish with `0A 00 00`. Cancel does not roll back, despite the
    dialog saying *"The previous values will be restored."*
  * Trims are stored as soon as they're sent; there is no save step.
* **Status** for Di2 is only the battery %.
* Battery-consumption check: invasive and unavailable with an SM-BCR2.

## Firmware update (for reference: we don't implement it)

Checks the PC is on AC or has > 1 h battery, the bike battery ≥ 1 %, asks for a
machinery-directive consent, then shows *"Do not disconnect until overwriting
is complete."* Cancel only stops units not yet started. Recovery from a failed
update may ask to switch the system off and on. Commands that enter update or
bootloader modes are blocked in our library (`di2/session.py`, `forbidden()`).

## EW-WU111

Talked to like the other units, plus radio firmware queries (`40 34 n`) and the
BLE settings. Nothing in E-Tube describes a unit that floods the bus after
being left in PC link mode; the nearest recovery tools are `ReleaseUnitsPCLink`
and asking the user to disconnect the battery for 60 s. Unplugging the
EW-WU111 for 30 s cleared it on the test bike.
