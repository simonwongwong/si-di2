# What's next

Picking this up later (or contributing)? Start here. Read
[docs/field-notes.md](docs/field-notes.md) first.

## Verify on a bike (implemented, not yet tried for real)

1. **Faster connect.** The 3 s secure-code echo wait was dropped and the slot map is
   polled every 0.5 s. Expected connect time is about 13 s (was 17 s). Measure it.
2. **Settings writes** other than synchro shift speed: button assignments,
   multi-shift speed (including off, which must also force the gear limit to
   unlimited), gear limit, semi-synchro steps. Do one at a time and check the
   read-back.
3. **Rear derailleur adjustment.** Bike on a stand, turn the cranks: Start, one trim
   step up, one down, Done. Then try Cancel and check the trim and normal shifting
   come back.
4. **`python -m di2 errors`** (failure history / last error code per unit).

## Features

* **Full-synchronized shift points** (`32 A8`). E-Tube clamps shift point and target
  with a gear-ratio table (`CustomizeSettingSShift.UpdateSynchroShiftMinMaxIndexes`).
  Port those rules verbatim; they depend on the current values. See
  [docs/etube-app.md](docs/etube-app.md).
* **Chainring / cassette** (`0A B0`, `0B B0`). Changing either can invalidate the
  synchro maps (E-Tube then resets them to defaults) and changes whether gear
  position control can be switched off.
* **Wireless mode / BLE name / passkey** (EW-WU111, `40 50`, `40 10`/`18`,
  `40 08`, then BLE restart `40 20`). Mode 0 turns the radio off.
* **Restore from backup.** Every apply already saves the full snapshot under
  `~/.cache/di2/backups/`; add a "restore" that diffs a backup against the bike
  and writes the differences.
* **Front derailleur adjustment.** Needs the bolt-adjustment step and E-Tube's
  low/top sequence, and an answer to why it sends `26 40 14` to the front
  derailleur (currently on the never-send list).
* **Capacitor charging.** When the SM-BCR2 powers the bus and its capacitor is
  low (power state 3/4, or below 2659 before a shift), E-Tube charges it (up to
  ~90 s) instead of giving up. Port that from `PcInterfaceDevice.GetPowerInfo` /
  `ErrorCheckUnitFDRDCharge`.
* **Live button view.** Shifters send press/release events (`04 00`, `04 38`);
  E-Tube's error check listens to them. Could show presses live in the UI.

## Robustness

* Handle the SM-BCR2 being unplugged mid-session: go back to the connect screen
  instead of failing.
* Understand why the power check sometimes returns `02` (no external power) with
  the battery fitted.
* Other bikes and groupsets: the model table (`di2/commands.py`) and the semi-synchro
  step limits (`di2/edit.py`) only cover R8050/R8070-era parts. More comes from
  E-Tube's `unitlist.json` and `fdtoothset.json`.
* SM-PCE1 / SM-PCE02 support. Same protocol family; the PCE02 is an FTDI chip at
  2 Mbaud with RTS on.

## Project

* Package it (`pyproject.toml` exists; publish to PyPI once the name is settled).
* Screenshots in the README.
