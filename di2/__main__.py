"""Command line: ``python -m di2 status`` connects, prints what it can read, and disconnects."""
import argparse
import signal
import sys

from . import commands, settings
from .session import Di2Error, Session


def status(args) -> int:
    log = print if args.verbose else (lambda *a: None)
    with Session(args.device, log=log) as s:
        print(f'connected; PC interface is bus slot {s.pc_slot}')
        # E-Tube's order: master first, then the slot map again, then each slave in turn.
        print(f'  slot  0: {commands.identify(s, 0)}  fw {commands.firmware_version(s, 0)}')
        pct = commands.battery_percent(s)
        print(f'battery: {pct} %' if pct is not None else 'battery: no reply')
        slots = commands.occupied_slots(s) or []
        print('units on the bus:', ', '.join(map(str, slots)))
        for slot in slots:
            if slot in (0, s.pc_slot):
                continue
            print(f'  slot {slot:2}: {commands.identify(s, slot)}  fw {commands.firmware_version(s, slot)}',
                  flush=True)
    return 0


def show_settings(args) -> int:
    """Identify every unit and print its current settings (reads only)."""
    log = print if args.verbose else (lambda *a: None)
    with Session(args.device, log=log) as s:
        slots = commands.occupied_slots(s) or []
        for slot in [0] + [x for x in slots if x not in (0, s.pc_slot)]:
            model = commands.identify(s, slot)
            print(f'slot {slot:2}: {model}', flush=True)
            reader = settings.reader_for(model) if model else None
            if reader:
                for k, v in reader(s, slot).items():
                    print(f'    {k}: {v}', flush=True)
    return 0


def serve(args) -> int:
    from .server import serve as run
    run(args.host, args.port, args.device)
    return 0


def errors(args) -> int:
    """Release stuck units, then read every unit's failure history (reads only)."""
    log = print if args.verbose else (lambda *a: None)
    with Session(args.device, log=log) as s:
        print('released PC link mode on slots:', s.release_units_pc_link())
        if not any(s.pc_link(0, True) for _ in range(3)):
            raise Di2Error('master did not re-enter PC link mode after release')
        slots = commands.occupied_slots(s) or []
        for slot in [0] + [x for x in slots if x not in (0, s.pc_slot)]:
            model = commands.identify(s, slot) or 'not answering'
            print(f'slot {slot:2}: {model}', flush=True)
            if model == 'not answering':
                continue
            print(f'    last error code: {commands.last_error_code(s, slot) or "none recorded"}', flush=True)
            names = next((v for k, v in commands.FAILURE_INDICES.items() if model.startswith(k)), {})
            for idx, name in names.items():
                st, data = commands.failure_record(s, slot, idx)
                if st != 'none':
                    print(f'    [{idx:2}] {name}: {st} {data.hex(" ") if data else ""}', flush=True)
    return 0


def release(args) -> int:
    """Free units left in PC link mode by an interrupted session (they then don't shift)."""
    log = print if args.verbose else (lambda *a: None)
    s = Session(args.device, log=log)
    try:
        s.connect()
        print('released PC link mode on slots:', s.release_units_pc_link())
        # E-Tube follows ReleaseUnitsPCLink with its normal Disconnect (close()): mode OFF to
        # every unit and then the master. The release leaves the master in WAIT, which
        # still blocks shifting.
    finally:
        s.close()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog='di2')
    ap.add_argument('--device', default='/dev/di2')
    ap.add_argument('-v', '--verbose', action='store_true')
    sub = ap.add_subparsers(dest='cmd', required=True)
    sub.add_parser('status', help='connect, read battery and unit list, disconnect').set_defaults(func=status)
    sub.add_parser('settings', help='identify every unit and print its current settings').set_defaults(
        func=show_settings)
    sp = sub.add_parser('serve', help='local web UI (reads the bike on demand)')
    sp.add_argument('--host', default='127.0.0.1')
    sp.add_argument('--port', type=int, default=8095)
    sp.set_defaults(func=serve)
    sub.add_parser('errors', help='release stuck units, then read every unit\'s failure history').set_defaults(
        func=errors)
    sub.add_parser('release', help='free units stuck in PC link mode (E-Tube ReleaseUnitsPCLink)').set_defaults(
        func=release)
    args = ap.parse_args()

    def interrupted(signum, frame):
        # Turn SIGTERM/SIGHUP into an exception so Session.close() still runs and the
        # bike isn't left in PC link mode (it then doesn't shift).
        raise KeyboardInterrupt(f'signal {signum}')

    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGHUP, interrupted)
    try:
        return args.func(args)
    except Di2Error as e:
        print(f'error: {e}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
