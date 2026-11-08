#!/usr/bin/env python3
"""Unpack an E-Tube Project installer for study: driver files, firmware, and the .NET application.

    installer.exe (InstallShield ISSetupStream)
      -> setup files (MSI, prerequisites, Setup.ini)
      -> MSI streams (ISSetupFile.* = drivers/firmware, Binary.*, ...)
      -> Data1.cab (LZX) = the application, incl. the .NET assemblies

Then decompile the interesting assemblies, e.g.:

    dotnet tool install -g ilspycmd --version 8.2.0.7535    # .NET 8 SDK; run with DOTNET_ROLL_FORWARD=Major
    ilspycmd -p -o src/smpce1com -r OUT/app OUT/app/smpce1com.dll

Assemblies worth reading (4.0.4): smpce1com.dll (serial link), etubedatalinks.dll
(unit commands), etubeproject_unit.dll (connect flow, UnitManager), etubeprojectv4.dll
(BikeConnector and the screens), etubeproject_units.dll (embedded JSON unit database).

Output is Shimano's copyrighted software: keep it out of this repository.

usage: unpack_etube.py INSTALLER.exe OUTDIR
needs: pip install olefile libarchive-c   (libarchive-c uses the system libarchive for LZX)
"""
import argparse
import os
import string
import sys

import libarchive
import olefile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from extract_firmware import issetupstream_files  # noqa: E402

_MSI_CHARS = string.digits + string.ascii_uppercase + string.ascii_lowercase + '._'


def msi_stream_name(name: str) -> str:
    """Decode MSI's packed stream names (two base-64 characters per code point in U+3800..U+47FF)."""
    out = []
    for c in name:
        o = ord(c)
        if 0x3800 <= o < 0x4800:
            o -= 0x3800
            out += [_MSI_CHARS[o & 0x3F], _MSI_CHARS[o >> 6]]
        elif 0x4800 <= o < 0x4840:
            out.append(_MSI_CHARS[o - 0x4800])
        elif o == 0x4840:
            out.append('!')
        else:
            out.append(c)
    return ''.join(out)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('installer')
    ap.add_argument('outdir')
    args = ap.parse_args()
    setup_dir, streams_dir, app_dir = (os.path.join(args.outdir, d) for d in ('setup', 'msi', 'app'))
    for d in (setup_dir, streams_dir, app_dir):
        os.makedirs(d, exist_ok=True)

    for name, data in issetupstream_files(open(args.installer, 'rb').read()):
        with open(os.path.join(setup_dir, os.path.basename(name)), 'wb') as f:
            f.write(data)
        if not name.lower().endswith('.msi'):
            continue
        ole = olefile.OleFileIO(data)
        for entry in ole.listdir():
            stream = ole.openstream(entry).read()
            sname = msi_stream_name(entry[-1]).replace('/', '_')
            if sname.lower().endswith('.cab'):
                with libarchive.memory_reader(stream) as cab:
                    for item in cab:
                        with open(os.path.join(app_dir, os.path.basename(item.pathname)), 'wb') as f:
                            for block in item.get_blocks():
                                f.write(block)
            else:
                with open(os.path.join(streams_dir, sname), 'wb') as f:
                    f.write(stream)
    for d in (setup_dir, streams_dir, app_dir):
        print(f'{d}: {len(os.listdir(d))} files')
    print('note: CAB members keep their MSI File-table keys as names (e.g. smpce1com.dll).')


if __name__ == '__main__':
    main()
