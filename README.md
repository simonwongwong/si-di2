# di2

Configure Shimano Di2 (11-speed, E-Tube) from Linux through an **SM-BCR2**
charger / PC link, without Windows or E-Tube Project.

> **Not affiliated with or endorsed by Shimano.** This is reverse-engineered,
> early software that talks to the electronics that shift your bike. It can
> leave components in a state where they don't shift until released or power
> cycled (see [docs/field-notes.md](docs/field-notes.md)). Use it on a work
> stand, not before a ride, and at your own risk.

## Status

Tested on one bike: Ultegra R8050/R8070 Di2 (BT-DN110 internal battery,
ST-R8070 levers, FD-R8050, RD-R8050-GS, EW-RS910 junction A, EW-WU111
wireless unit) with an SM-BCR2.

| feature | state |
|---|---|
| connect / disconnect (E-Tube's sequence) | works; every unit confirms release |
| identify every component (model, L/R, SS/GS, firmware, serial) | works |
| battery level | works |
| read all customize settings (buttons, synchro maps, multi-shift, chainrings/cassette, wireless) | works |
| write settings, with backup and read-back verification | works for synchro shift speed; other settings (buttons, multi-shift, semi-synchro steps) implemented, not yet tried on a bike |
| rear derailleur adjustment | implemented, tested against a simulated bike only |
| full-synchro shift points, chainring/cassette, wireless mode, restore from backup, front derailleur adjustment | not yet ([WHATS-NEXT.md](WHATS-NEXT.md)) |
| firmware updates, error check | not planned; the library refuses update/bootloader commands |

## What's here

* `di2/`: a Python library (`Session`, `commands`, `settings`, `edit`, `adjust`)
  and a command line (`python -m di2 status | settings | errors | release | serve`).
* `di2/web/`: a local web UI (`python -m di2 serve`): Connect / Disconnect with
  E-Tube's warnings, settings editing with a backup before every write and a
  read-back after it, rear derailleur adjustment.
* `docs/`:
  * [protocol.md](docs/protocol.md): USB, framing, the SM-BCR2's commands, the
    connect/disconnect sequence, PC link mode.
  * [commands.md](docs/commands.md): unit commands for identification, status,
    settings and adjustment, plus what never to send.
  * [etube-app.md](docs/etube-app.md): how E-Tube Project behaves around a session,
    its warnings and its validation rules.
  * [field-notes.md](docs/field-notes.md): what went wrong while working this out,
    and how to avoid it.
* `tools/`: extract the SM-BCR2 firmware from your own E-Tube installer; unpack an
  E-Tube installer for study.
* `tests/`: a simulated bike, so the library and UI can be worked on without hardware:
  `python -m unittest discover -s tests`, and `python3 tools/sim_server.py` runs the
  web UI against it.

## Setup

You need Linux, Python 3.9+, an SM-BCR2, and the **E-Tube Project 4.0.4**
installer for Windows from Shimano (the last version that supports the SM-BCR2).
Shimano's firmware is not included here; you extract it from your copy.

1. **Firmware.** The SM-BCR2 is a TI TUSB3410 USB-serial chip that needs firmware
   uploaded every time it's plugged in. Linux's `ti_usb_3410_5052` driver does that
   once the file is in place:
   ```sh
   pip install olefile pyserial
   python3 tools/extract_firmware.py E-tube_Proj_V_4_0_4.exe
   sudo install -m 0644 ti_usb-v1e44-p7220.fw /lib/firmware/
   ```
2. **udev rule.** Binds the driver to Shimano's USB ID, gives the port to group
   `plugdev` and names it `/dev/di2`:
   ```sh
   sudo install -m 0644 udev/70-shimano-bcr2.rules /etc/udev/rules.d/
   sudo udevadm control --reload
   ```
   Make sure your user is in `plugdev`, then plug in the SM-BCR2.
3. **Run it.**
   ```sh
   ./serve.sh            # creates .venv on first run; then open http://127.0.0.1:8095
   ```
   or from the command line: `python -m di2 status`.

## Using it

Plug the SM-BCR2's round plug into the bike's charging port (junction A), then
press **Connect**. Like E-Tube:

* While connected the components are in "PC link mode": **shifting is paused**,
  and the SM-BCR2 **stops charging** the bike.
* **Always press Disconnect before unplugging anything.** Disconnect releases
  every unit and reports which ones confirmed.
* After disconnecting, unplug and replug the SM-BCR2's USB cable to resume
  charging.

If a session was interrupted and the bike won't shift, `python -m di2 release`
frees the units (E-Tube's own recovery routine). If a unit still misbehaves,
unplug it, or the battery, for 30 to 60 seconds.

The UI listens on `127.0.0.1` only: it can change your bike's settings and has
no login.

## How it works (short)

| layer | what |
|---|---|
| USB | SM-BCR2 = TI TUSB3410 (`1e44:7220`), boots into a ROM bootloader; firmware uploaded on plug-in |
| serial | 38400 8N1, frames `BB … BB` with an additive checksum and escaping |
| SM-BCR2 | reset, power control, bus mode, target selection, and a relay for unit commands |
| bus | E-Tube bus; the SM-BCR2 joins as a slave of the bike's battery; units are addressed by slot |

Details in [docs/protocol.md](docs/protocol.md).

## License

MIT, see [LICENSE](LICENSE).
