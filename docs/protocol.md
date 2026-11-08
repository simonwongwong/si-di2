# SM-BCR2 / E-Tube protocol

Reverse-engineered from E-Tube Project 4.0.4 (Windows, .NET) and checked
against an Ultegra R8050 Di2 bike. Class and method names refer to the
decompiled E-Tube assemblies so each claim can be traced back.

## USB

The SM-BCR2 (`1e44:7220`, bcdDevice 1.01) is a TI TUSB3410. Out of the box it
enumerates as the chip's ROM bootloader: one interface, class 255, a single
bulk OUT endpoint. The host uploads Shimano's firmware (`umpf3410.i51`, a
14 KB 8051 image) to the chip's RAM; the chip then re-enumerates with a second
configuration and works as a serial port. Nothing is flashed, and the
firmware is lost at unplug.

On Windows this is TI's `umpusb` driver, rebranded by Shimano (INF:
`USB\VID_1E44&PID_7220`, `ImageFile = umpf3410.i51`). On Linux,
`ti_usb_3410_5052` does the same once it knows the ID (`new_id`) and finds the
image as `ti_usb-v1e44-p7220.fw`. Linux's file format is the image with a
3-byte header (LE16 length, 8-bit checksum), both recomputed by the kernel.

Related interfaces: `1e44:71F4` (same TI driver), and the SM-PCE02
(`1e44:7151`, an FTDI chip at 2 Mbaud with RTS on).

## Serial framing

38400 baud, 8N1, no flow control, RTS off (`WinSerialPort.SetParam`,
`DataLinksMain.GetBaudRate`).

```
BB  escape(ctrl, data..., fcs)  BB
```

* `fcs = (-(ctrl + sum(data))) & 0xFF` (`Common.CreateFCS`).
* `BB` and `BD` in the body are sent as `BD`, `byte ^ 0x20`.
* Replies to a BCR2 command have `ctrl | 0x20`.

## BCR2 commands

| ctrl | data | reply | meaning |
|---|---|---|---|
| `03` | mode | `23` result | set protocol/operation mode: `00` normal slave, `20` normal master, `40` update. Result `00` OK, `10` no master answered |
| `04` | – | `24` m | mode: bit 7 connected, bit 6 update protocol, bit 5 master, bits 0–4 the BCR2's bus slot |
| `05` | – | `25` v… | BCR2 firmware version (`30 01 00` = 3.0.1) |
| `06` | slot | `26` r | select the target slot for unit commands; `00` OK, `01` refused |
| `10` | – | `30 00` | soft reset |
| `1A` | `01` | `3A 01 00` | release current limit (needed before the bike side works) |
| `1A` | `02` | `3A 02 s` | power state: `01` bike battery present (>3 V), `02` no external power, supercap OK, `03`/`04` needs charging, `05` busy |
| `1A` | `03` | | start supplying the bike from the BCR2's supercap (only when there is no battery) |
| `1B` | – | `3B` 4 bytes | master mode only: bitmap of occupied slots |
| `1D` | `00` | `3D 00 00` | redo bus slot assignment ("PnP retry") |
| `48` | slot, group, cmd, params… | `48` … | unit command (below) |

## Connect sequence

From `BikeConnector.ConnectUnitsRecognizing` → `PcInterfaceDevice.ConnectPcInterfaceUnit`
(`InitDataLink`: reset, power unlock) → `UnitManager.CheckPowerState` →
`UnitManager.CheckMasterSlave` → `EtubeDataLinksMain.CheckMasterUnit`:

1. `10` soft reset; E-Tube waits for a USB reconnect and resets again.
2. Wait 1 s, `1A 01`.
3. Wait 3 s, `1A 01`, `1A 02`. On `01` (the bike's battery powers the line) carry on; on
   `02` (no external power, capacitor charged) send `1A 03` so the BCR2 powers the bus,
   then carry on (`GetPowerInfo`). Both happen on the same bike: `01` all afternoon of
   2026-11-07, `02` from 18:18 on; cause unknown, connect works either way.
4. Wait until at least 5 s have passed since the unlock (`WaitPcDevPnPDone`).
5. `03 00` → `23 00`: the bike's master unit (the BT-DN110 battery on R8050)
   accepted us as a slave. `04` → e.g. `99`: connected, slave, slot 25.
6. `06 00`, then PC link mode on the master (below).

Skipping 1–4 leaves the BCR2 unable to address anything (`06` → `01`), and the
junction A LED may flash red. Putting the BCR2 in master mode (`03 20`) on a
bike that has a battery master finds no units.

Disconnect (`PcInterfaceDevice.Disconnect`): PC link mode **off on every
connected unit**, then on the master, then `10`. The master passes PC link
mode on to the other units, so switching off only the master leaves them in
PC link mode: they stop shifting and ignore later sessions. E-Tube's recovery
for that is `ReleaseUnitsPCLink` (`python -m di2 release`): `DISWAIT` (3) to the
master, off (0) to slots 1–30, `WAIT` (2) to the master. Each send is `06 slot`
without retry, then up to three `PCA_COM_MODE_SET` with a 300 ms wait.
E-Tube always follows it with the normal disconnect: the master left in
`WAIT` still blocks shifting until it gets mode `0`.

Slot numbers are reassigned between sessions, and the slot map needs a few
seconds after `03 00` to list every unit; wait until it reads the same twice.

## Unit commands

Select the unit with `06 <slot>`, then send

```
48  slot group cmd params...
```

The reply is a `48` frame with data `x group cmd|2 params...`, or `cmd|3` with
a 2-byte error code. Replies take up to ~3–4 s on this bus and can arrive late.

### PC link mode

`PCA_COM_MODE_SET` (group 50, cmd `10`, params `on, pc_slot, 00, 00`). When
turning on, wait 1 s and send five `PCA_SECURE_CODE_SET` frames (group 50,
cmd `30`, params `hi lo 00 00`) with the fixed pairs in
`EtubeDataLinksDccCommand.SendSetPCLinkModeStart`; the unit acknowledges with
group 50 cmd `12`. Normal operation (shifting) is paused while it's on.

### Read-only commands

The full catalogue, including settings, is in [commands.md](commands.md).

| name | group | cmd | reply params |
|---|---|---|---|
| UNIT_SLOT_NO_GET (master) | `01` | `14` | `[2:6]` little-endian bitmap of occupied slots |
| UNIT_STOCK_NO_GET | `01` | `1C` | series no, unit no (→ model) |
| UNIT_FW_VER_GET | `01` | `2C` | `major<<4 | minor`, sub-minor, revision |
| DCAS serial number | `01` | `3C` | 6 bytes, little-endian, printed as hex |
| MBT_SOC2_GET (battery) | `26` | `44` | state of charge in percent |

All requests carry one reserved `00` parameter.

### Model numbers seen on an R8050 bike

| series/unit | model |
|---|---|
| 17/0 | BT-DN110 battery (bus master, slot 0) |
| 17/2 | EW-RS910 junction A |
| 20/1 | ST-R80x0 shifter (part no: R8050 L 1 / R 2, R8070 L 0D / R 0C) |
| 20/4 | RD-R8050 (part no 0 SS, 1 GS) |
| 20/3 | FD-R8050 |
| 16/8 | EW-WU111 wireless unit |

## Open questions

* **A unit left in PC link mode can wedge.** After early sessions that only
  turned off the master, the EW-WU111 ignored everything (even
  `ReleaseUnitsPCLink`; its slot had moved) and later flooded the bus: junction
  A flashed red/green rapidly and shifts were dropped, with or without the BCR2
  attached. Unplugging the EW-WU111 for ~30 s cleared it; it then worked
  normally (wireless reconnected). Always turn PC link mode off on every unit.
* The BCR2 sometimes stops charging after sessions until it's replugged at
  the USB end. Cause not yet known.
* After the secure codes, E-Tube (as a slave) waits for a group-50 cmd `30`/`31`
  frame that we never see; things work without it.
* Settings are mapped in [commands.md](commands.md) but untested.

## Extracting things from the installer

`E-tube_Proj_V_4_0_4.exe` is an InstallShield ISSetupStream launcher: after
the PE image comes `ISSetupStream`, a file count, and per file a header,
UTF-16 name and data. The data is obfuscated per 1024-byte block (key = file
name XOR `13 35 86 07`, then `~(key ^ swap_nibbles(b))`) and zlib-compressed.
Inside is an MSI (OLE compound file). Driver files and the firmware are its
`ISSetupFile.*` streams; the application is in `Data1.cab` (LZX). The .NET
assemblies decompile cleanly with ILSpy (`ilspycmd`). See
`tools/extract_firmware.py`.
