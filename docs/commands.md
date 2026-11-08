# Unit command catalogue (R8050-era Di2)

What E-Tube Project 4.0.4 reads and writes on each component, traced from its
screens down to bytes. **Only the rows marked ✅ have been tried on a bike**;
in the settings tables ✅ means the *read* was verified. No write has been tried.
Everything else comes from reading the decompiled code; treat writes as
untested until verified.

Notation: a unit command is `grp cmd params…`, sent as `48 slot grp cmd params…`
after `06 slot` (see [protocol.md](protocol.md)). Replies are `grp cmd|2 params…`;
`cmd|3` is an error with a code (`39` firmware mismatch, `3A` unit can't handle
it now, `3B` invalid command, `42` no data). Reads carry one reserved byte,
usually `00`.

Where it comes from in E-Tube: each unit's JSON file
(`etubeproject_units.dll` → `db/unit/<model>.json`) names a symbolic command per
setting (`DCC_CMD_SW_CND_SET_C`); `db/plccomtbl.json` gives its bytes; per-unit
"Supporter" classes (`StSwImmediate`, `FdImmediate`, `RdMuImmediate`,
`MasterDi2Supporter`, `ScEwImmediate`) pack and decode the parameters. Reads use
the SET name with `SET_C` → `GET_C`.

## Identification (all units)

| what | request | reply params | |
|---|---|---|---|
| model (series, unit no) | `01 1C 00` | series, unit no | ✅ |
| shifter part no (unit no 1) | `04 0C 00` | part: R8050 L=1 R=2, R8060 L=0A R=09, R8070 L=0D R=0C | ✅ |
| derailleur part no (unit no 3/4) | `32 9C 00` | RD-R8050: 0 = SS, 1 = GS | ✅ |
| firmware | `01 2C 00` | `major<<4 \| minor`, sub, revision | ✅ |
| serial (DCAS) | `01 3C 00` | 6 bytes LE → hex | ✅ |
| production serial | `01 A4 00` then `01 AC 00` | ASCII p1..p5 + p0..p5 (all `FF` = unset) | |
| bootloader version | `01 9C 00` | 4 bytes | |
| app version the unit requires | `32 B4 00` | major, minor, sub | |
| slot map (master only) | `01 14 00` | `[2:6]` LE bitmap | ✅ |

Series/unit numbers: BT-DN110 17/0, EW-RS910 17/2, EW-WU111 16/8, ST-R80x0 20/1,
FD-R8050 20/3, RD-R8050 20/4.

## Status

| unit | what | request | reply | |
|---|---|---|---|---|
| BT-DN110 | charge % | `26 44 00` | p0 = 0..100 (E-Tube shows >100 as invalid) | ✅ |
| FD, RD | current / max gear | `0A 0C 00`, `0B 0C 00` | cur, max | ✅ |
| FD, RD | trim (adjust) value | `0A 14 00`, `0B 14 00` | int8 current, range ±p1 | |
| FD | trim range | `0A 24 00` | int8 min, max | |
| FD, RD | sensor error | `32 74 00` | 0 = OK | |
| all | usage history | `32 C4 idx` | `idx d…` (counters; per-unit index lists in E-Tube `UnitDataDefinitions`) | |
| all | failure history | `32 CC idx` | `idx d…`; index 0/1 = last error code record | |

The BT-DN110 offers no voltage, cycle count or health reading.

## Settings (reversible)

All take effect when acknowledged; there is no save/commit step and E-Tube
does not read back. "Restore default" just writes the defaults.

### Shifter buttons (ST-R80x0-L/R)

| | bytes |
|---|---|
| read ✅ | `04 14 00` → `b0 b1 …` (e.g. left `01 FA`, right `32 FB` on a stock setup) |
| write | `04 10 b0 b1 00 00` (always all four buttons) |

`b0 = X<<4 | Y`, `b1 = Sprinter<<4 | A`. Functions: 0 front up, 1 front down,
2 rear up, 3 rear down, 4/5 cycle computer L/R (A only), 10–13 D-FLY Ch.1–4,
15 unassigned. D-FLY needs shifter fw ≥ 3.1.0, BT-DN110 ≥ 4.4.0, EW-WU111 ≥
4.3.0. Defaults: L X=0 Y=1 S=3 A=10; R X=3 Y=2 S=2 A=11.

### Synchronized shift (FD + RD; needs BT-DN110 fw ≥ 4.2.0)

| setting | read | write | values |
|---|---|---|---|
| chainrings (FD) ✅ | `0A B4 FF` | `0A B0 lo hi FF FF` | 9 = 53-39, 5 = 52-36, 6 = 50-34, 11 = 46-36 |
| cassette (RD) ✅ | `0B B4 FF` | `0B B0 lo hi FF FF` | SS: 2 = 11-25, 3 = 11-28, 4 = 11-30, 5 = 12-25, 6 = 12-28, 9 = 14-28; GS: 3 = 11-28, 4 = 11-30, 8 = 11-32, 11 = 11-34 |
| synchro shift speed (FD) ✅ | `0A 9C 00` | `0A 98 v` | 0 normal, 1 slow, 2 very slow |
| synchro maps (FD) ✅ | `32 AC m fd 00 00` | `32 A8 m fd cp tgt` | below |
| gear position control (RD) ✅ | `0B 7C 01 02 00 00` | `0B 78 01 02 09 02` (on) or `… FF 00` (off), then `0B 78 02 02 FF 00` | off iff reply is `01 02 FF 00` |

Synchro maps: `m = mode<<4 | direction` (mode 1 → `0`, mode 2 → `1`; up 0,
down 1). Setting 1 = (up, FD 1), setting 2 = (down, FD 2). `cp = FE` means
semi-synchronized (then `tgt` = RD steps 0..4); otherwise synchronized
(`cp` RD shift point, `tgt` RD target gear 1..11). Defaults: setting 1 cp 7 tgt 5;
setting 2 cp 2 tgt 4.

### Multi-shift

| setting | read | write | values |
|---|---|---|---|
| speed, also on/off (RD) ✅ | `0B 54 00` | `0B 50 v 00 00 00` | 0 off, 1 very slow … 3 normal … 5 very fast |
| gear limit (BT-DN110, slot 0) ✅ | `0B 64 00` | `0B 60 L L 00 00` | FF unlimited, 2, 3 |

### Wireless (EW-WU111)

| setting | read | write |
|---|---|---|
| mode | `40 54 FF` → m | `40 50 m` (3 ANT+BLE, 1 ANT, 2 BLE, 0 off; needs fw ≥ 4.4.0) |
| BLE name (8 chars) | `40 14 00`, `40 1C 00` | `40 10 c0..c5 00`, `40 18 c6 c7 0 0 0 00`, then `40 20` (BLE restart) |
| BLE passkey | – | `40 08 <6 digits> 00`, then `40 20` |

Setting the mode to 0 turns the radio off.

## Adjustment (moves hardware)

`grp` = `0A` front, `0B` rear. Needs battery ≥ 5 %.

| | bytes |
|---|---|
| move to gear | `grp 08 target max 00 00` (echoes target) |
| manual-adjust mode on / off | `grp 00 01` / `grp 00 00` |
| set trim (stored immediately) | `grp 10 int8 max` |

E-Tube's rear procedure: read gear count, gear and trim; move to gear 5 (also
tells the FD); mode on; each click sends a trim value; mode off. The front
procedure also sends `26 40 14` to the FD (purpose unknown).

## Don't send

Firmware/bootloader (`32 20 01` update mode, boot command sets), identity and
calibration writes (`01 08`, `01 18`, `01 20`, `01 A0`/`A8`, `04 08` shifter
L/R, `32 98` SS/GS, `32 A0`, `32 60`/`68`, `0B 18` gear count, `grp 20` trim
range, `grp 48`), `01 28` force power off, `26 08` bus power, `26 40` battery
gauge, `32 80` log clear, `32 48` PnP reset, and the error-check test commands
(`03 00`, `05 28`, `23 08`, `32 50`).
