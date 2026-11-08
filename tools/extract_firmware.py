#!/usr/bin/env python3
"""Extract the SM-BCR2's TUSB3410 firmware from your own E-Tube Project installer.

The SM-BCR2 is a TI TUSB3410 USB-serial chip that boots into a ROM bootloader;
the host has to upload firmware (Shimano's ``umpf3410.i51``) before it appears
as a serial port. Linux's ``ti_usb_3410_5052`` driver does the upload if the
image is at ``/lib/firmware/ti_usb-v1e44-p7220.fw``. We don't redistribute
Shimano's firmware; this script pulls it out of the installer you downloaded.

Tested with E-Tube Project 4.0.4 (``E-tube_Proj_V_4_0_4.exe``, the last
version that supports the SM-BCR2).

The installer is an InstallShield "ISSetupStream" launcher wrapping an MSI;
the firmware sits in the MSI's ``ISSetupFile`` streams, unnamed. We pick the
image whose SHA-256 matches the one verified on hardware.

usage: extract_firmware.py E-tube_Proj_V_4_0_4.exe [-o ti_usb-v1e44-p7220.fw]
needs: pip install olefile
"""
import argparse
import hashlib
import struct
import sys
import zlib

import olefile

# umpf3410.i51 from E-Tube 4.0.4, verified with an SM-BCR2 (bcdDevice 1.01).
KNOWN_GOOD_SHA256 = '2a392186cf3d93b6a56514cdcd483a26097bf20b2bfc744bad7bb2fa1fd8bcd6'


# --- InstallShield ISSetupStream ----------------------------------------------------

_IS_KEY_MAGIC = bytes([0x13, 0x35, 0x86, 0x07])


def _pe_overlay_offset(d: bytes) -> int:
    pe = struct.unpack_from('<I', d, 0x3C)[0]
    nsect = struct.unpack_from('<H', d, pe + 6)[0]
    sect = pe + 24 + struct.unpack_from('<H', d, pe + 20)[0]
    return max(sum(struct.unpack_from('<II', d, sect + i * 40 + 16)) for i in range(nsect))


def _is_decode(data: bytes, name: str) -> bytes:
    """Undo ISSetupStream obfuscation: per-1024-byte blocks, key derived from the file name."""
    key = bytes(b ^ _IS_KEY_MAGIC[i % 4] for i, b in enumerate(name.encode('utf-8')))
    swap = [((b << 4) | (b >> 4)) & 0xFF for b in range(256)]
    tables = {k: bytes((~(k ^ swap[b])) & 0xFF for b in range(256)) for k in set(key)}
    out = bytearray(len(data))
    for j in range(min(1024, len(data))):
        out[j::1024] = data[j::1024].translate(tables[key[j % len(key)]])
    return bytes(out)


def issetupstream_files(exe: bytes):
    """Yield (name, contents) for each file in an ISSetupStream launcher."""
    o = _pe_overlay_offset(exe)
    if exe[o:o + 13] != b'ISSetupStream':
        raise ValueError('not an InstallShield ISSetupStream launcher')
    count = struct.unpack_from('<H', exe, o + 14)[0]
    p = o + 46
    for _ in range(count):
        name_len, flags = struct.unpack_from('<II', exe, p)
        size = struct.unpack_from('<I', exe, p + 10)[0]
        unicode_launcher = struct.unpack_from('<H', exe, p + 22)[0]
        p += 24 + (24 if unicode_launcher else 0)   # three FILETIMEs in the unicode variant
        name = exe[p:p + name_len].decode('utf-16-le').rstrip('\0')
        p += name_len
        data = exe[p:p + size]
        p += size
        if flags & 6:
            data = _is_decode(data, name)
        if data[:1] == b'\x78':
            data = zlib.decompress(data)
        yield name, data


# --- main ----------------------------------------------------------------------------

def find_firmware(installer: bytes) -> bytes:
    for name, data in issetupstream_files(installer):
        if not name.lower().endswith('.msi'):
            continue
        ole = olefile.OleFileIO(data)
        for entry in ole.listdir():
            stream = ole.openstream(entry).read()
            if len(stream) < 20000 and stream[:4] == b'\x02\x00\x1e\x02':   # 8051 image: LJMP 0x001E
                if hashlib.sha256(stream).hexdigest() == KNOWN_GOOD_SHA256:
                    return stream
    raise SystemExit('firmware not found (is this E-Tube Project 4.0.4?)')


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('installer')
    ap.add_argument('-o', '--output', default='ti_usb-v1e44-p7220.fw')
    args = ap.parse_args()
    image = find_firmware(open(args.installer, 'rb').read())
    # Linux's ti_3410.fw layout: LE16 length + 8-bit checksum, then the image (the kernel recomputes both).
    with open(args.output, 'wb') as f:
        f.write(struct.pack('<HB', len(image), sum(image) & 0xFF) + image)
    print(f'wrote {args.output} ({len(image)} byte image); install with:\n'
          f'  sudo install -m 0644 {args.output} /lib/firmware/ti_usb-v1e44-p7220.fw')


if __name__ == '__main__':
    sys.exit(main())
