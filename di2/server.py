"""Local web UI: ``python -m di2 serve`` (stdlib only).

Session model follows E-Tube Project: Connect (recognise units, read everything,
stay connected) -> work -> Disconnect. While connected, the components are in PC
link mode and don't shift, and nothing may be unplugged.

GET  /             the page (di2/web/index.html)
GET  /api/state    phase, progress log, error, last snapshot, BCR2 link status
POST /api/connect  connect and read all units (phase: connecting -> connected)
POST /api/refresh  read all units again while connected
POST /api/disconnect   E-Tube's disconnect (PC link off on every unit, BCR2 reset)
POST /api/cancel   cancel a connect in progress (it disconnects cleanly)
POST /api/check    {changes: [...]} -> errors, warnings, what would be written (no bike traffic)
POST /api/apply    {changes: [...]} back up all settings, write, read each change back
POST /api/adjust   {action: begin|start|shift|trim|finish|cancel, delta: +1|-1}
                   rear derailleur adjustment (phase 'adjusting' blocks everything else)
GET  /api/link     BCR2 status (USB, bike battery, bus state); only while disconnected

Safety nets a desktop app doesn't need: an idle connection is closed after
IDLE_DISCONNECT_S, and stopping the server disconnects first. The server binds to
localhost by default because it can control the bike and has no authentication.
"""
import json
import os
import queue
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import edit, settings
from .adjust import RearAdjust
from .session import Di2Error
from .scan import scan
from .session import Session, interface_status

WEB = Path(__file__).with_name('web')
CACHE = Path(os.environ.get('XDG_CACHE_HOME', Path.home() / '.cache')) / 'di2' / 'last.json'
BACKUPS = CACHE.parent / 'backups'   # full snapshot saved before every write: our "preset" 
IDLE_DISCONNECT_S = 15 * 60


class Cancelled(Exception):
    pass


class Bike:
    """Owns the BCR2. All serial work happens on one worker thread, in request order."""

    def __init__(self, dev: str):
        self.dev = dev
        self.lock = threading.Lock()
        self.phase = 'disconnected'     # connecting | connected | reading | disconnecting
        self.log: list = []
        self.error = None
        self.session = None
        self.last_activity = 0.0
        self._cancel = False
        self._pending: list = []
        self.apply_report = None
        self.adjuster = None           # RearAdjust while phase == 'adjusting'
        self.adjust = None             # {'stage', 'busy', ...} for the page
        self.adjust_result = None
        self._jobs: queue.Queue = queue.Queue()
        try:
            self.snapshot = json.loads(CACHE.read_text())
        except (OSError, ValueError):
            self.snapshot = None
        threading.Thread(target=self._worker, daemon=True).start()
        threading.Thread(target=self._watchdog, daemon=True).start()

    # --- requests from HTTP handlers --------------------------------------------------

    def request(self, action: str) -> bool:
        with self.lock:
            allowed = {'connect': ('disconnected',), 'refresh': ('connected',),
                       'disconnect': ('connected',), 'cancel': ('connecting',)}[action]
            if self.phase not in allowed:
                return False
            if action == 'cancel':
                self._cancel = True
                return True
            self.phase = {'connect': 'connecting', 'refresh': 'reading', 'disconnect': 'disconnecting'}[action]
            self.error = None
            if action == 'connect':
                self.log, self._cancel, self.apply_report = [], False, None
        self._jobs.put(action)
        return True

    def state(self) -> dict:
        with self.lock:
            connected = self.phase in ('connected', 'reading', 'writing', 'adjusting')
            opts = edit.options(self.snapshot) if connected and self.snapshot else {}
            return {'phase': self.phase, 'log': self.log[-50:], 'error': self.error,
                    'snapshot': self.snapshot, 'idle_disconnect_s': IDLE_DISCONNECT_S,
                    'connected_since': self.session and self.session.connected_at,
                    'options': {str(k): {f: {str(v): lbl for v, lbl in vals.items()} for f, vals in fields.items()}
                                for k, fields in opts.items()},
                    'apply_report': self.apply_report,
                    'adjust': self.adjust, 'adjust_result': self.adjust_result}

    def check(self, changes: list) -> dict:
        with self.lock:
            snap = self.snapshot
        if not snap:
            return {'errors': ['no snapshot; connect first'], 'warnings': [], 'changes': []}
        errors, warnings, out = edit.check(snap, changes)
        return {'errors': errors, 'warnings': warnings, 'changes': out}

    def request_apply(self, changes: list) -> bool:
        with self.lock:
            if self.phase != 'connected':
                return False
            self.phase, self.error, self._pending = 'writing', None, changes
        self._jobs.put('apply')
        return True

    def request_adjust(self, action: str, delta: int = 0) -> bool:
        with self.lock:
            if action == 'begin':
                if self.phase != 'connected':
                    return False
                self.phase, self.error, self.adjust_result = 'adjusting', None, None
                self.adjust = {'stage': 'preparing', 'busy': True}
            elif self.phase != 'adjusting' or not self.adjust or self.adjust.get('busy'):
                return False
            else:
                self.adjust['busy'] = True
        self._jobs.put(('adjust', action, delta))
        return True

    def link(self) -> dict:
        with self.lock:
            if self.phase != 'disconnected':
                return {'busy': True, 'phase': self.phase}
        try:
            return interface_status(self.dev)
        except Exception as e:   # port vanished mid-check, permissions, ...
            return {'usb': False, 'error': f'{e.__class__.__name__}: {e}'}

    # --- worker ---------------------------------------------------------------------

    def _progress(self, msg: str) -> None:
        with self.lock:
            self.log.append(f'{time.strftime("%H:%M:%S")} {msg}')
            if self._cancel and self.phase == 'connecting':
                raise Cancelled()

    def _worker(self) -> None:
        while True:
            action = self._jobs.get()
            try:
                if isinstance(action, tuple):
                    self._do_adjust(*action[1:])
                    continue
                getattr(self, '_do_' + action)()
            except Cancelled:
                self._progress_quiet('cancelled')
                self._close()
            except Exception as e:   # surface any failure in the UI, then make sure we're released
                traceback.print_exc()
                with self.lock:
                    self.error = f'{e.__class__.__name__}: {e}'
                self._close()

    def _progress_quiet(self, msg: str) -> None:
        with self.lock:
            self.log.append(f'{time.strftime("%H:%M:%S")} {msg}')

    def _read_all(self) -> None:
        snap = scan(self.session, self._progress)
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps(snap, indent=1))
        with self.lock:
            self.snapshot, self.phase = snap, 'connected'
            self.last_activity = time.time()

    def _do_connect(self) -> None:
        self._progress('connecting: recognising units')
        steps = {'reset': 'resetting the SM-BCR2', 'current limit release': 'switching the SM-BCR2 to PC mode',
                 'power state': 'checking power', 'discharge start (BCR2 powers the bus)': 'powering the bus from the SM-BCR2',
                 'slave mode': 'joining the bike\'s bus', 'slot map': 'waiting for every unit to register',
                 'pc link': 'putting the bike into PC link mode'}

        def log(*a):
            # Show connect steps in the progress line (one line per step, not per frame).
            msg = steps.get(str(a[0])) if a else None
            if msg and (not self.log or not self.log[-1].endswith(msg)):
                self._progress(msg)
        s = Session(self.dev, log=log)
        with self.lock:
            self.session = s
        s.connect()
        s.connected_at = time.time()
        self._progress('connected; reading units')
        self._read_all()
        self._progress_quiet('connected')

    def _do_refresh(self) -> None:
        self._progress('reading units')
        self._read_all()
        self._progress_quiet('done')

    def _do_apply(self) -> None:
        with self.lock:
            snap, changes = self.snapshot, self._pending
        errors, warnings, out = edit.check(snap, changes)
        if errors:
            raise ValueError('; '.join(errors))
        BACKUPS.mkdir(parents=True, exist_ok=True)
        backup = BACKUPS / f'{time.strftime("%Y%m%d-%H%M%S")}.json'
        backup.write_text(json.dumps(snap, indent=1))
        self._progress_quiet(f'backed up all settings to {backup.name}')
        report = edit.apply(self.session, snap, out, self._progress_quiet)
        # Re-read the units that were touched so the page shows what's really on the bike.
        for slot in sorted({c['slot'] for c in out}):
            unit = next(u for u in snap['units'] if u['slot'] == slot)
            reader = settings.reader_for(unit['model'])
            if reader:
                self._progress_quiet(f'slot {slot}: re-reading {unit["model"]}')
                unit['settings'] = reader(self.session, slot)
        CACHE.write_text(json.dumps(snap, indent=1))
        ok = all(r['verified'] for r in report)
        with self.lock:
            self.snapshot, self.phase = snap, 'connected'
            self.apply_report = {'at': time.strftime('%H:%M:%S'), 'ok': ok, 'backup': backup.name,
                                 'items': report, 'warnings': warnings}
            self.last_activity = time.time()
        self._progress_quiet('settings written and verified' if ok else 'some settings did not verify')

    def _do_adjust(self, action: str, delta: int) -> None:
        try:
            if action == 'begin':
                snap = self.snapshot
                rd = next(u for u in snap['units'] if u.get('kind') == 'rear_derailleur' and u.get('responding'))
                fd = next((u for u in snap['units'] if u.get('kind') == 'front_derailleur' and u.get('responding')), None)
                self.adjuster = RearAdjust(self.session, rd['slot'], fd and fd['slot'])
                st, stage = self.adjuster.prepare(), 'ready'
                self._progress_quiet(f'rear adjust: gear {st["gear"]}/{st["max_gear"]}, trim {st["trim"]}')
            elif action == 'start':
                self._progress_quiet('rear adjust: shifting to the start position')
                st, stage = self.adjuster.start(), 'adjusting'
            elif action in ('shift', 'trim'):
                st, stage = getattr(self.adjuster, action)(delta), 'adjusting'
                self._progress_quiet(f'rear adjust: gear {st["gear"]}, trim {st["trim"]}')
            elif action == 'finish':
                st = self.adjuster.finish()
                self._end_adjust({'ok': True, 'trim': st['trim'], 'original': st['original_trim']})
                return
            elif action == 'cancel':
                self.adjuster.cancel() if self.adjuster else None
                self._end_adjust({'ok': True, 'cancelled': True})
                return
            with self.lock:
                self.adjust = {'stage': stage, 'busy': False, **st, 'power': self.adjuster.power()}
                self.last_activity = time.time()
        except Exception as e:   # leave the derailleur in normal mode with its original trim
            traceback.print_exc()
            msg = f'{e}' if isinstance(e, Di2Error) else f'{e.__class__.__name__}: {e}'
            try:
                if self.adjuster:
                    self.adjuster.cancel()
            except Exception as e2:
                msg += f' (and restoring failed: {e2})'
            self._end_adjust({'ok': False, 'error': msg})

    def _end_adjust(self, result: dict) -> None:
        with self.lock:
            self.phase, self.adjuster, self.adjust, self.adjust_result = 'connected', None, None, result
            self.last_activity = time.time()
        self._progress_quiet('rear adjust: ' + ('cancelled' if result.get('cancelled') else
                                                'done' if result['ok'] else 'stopped: ' + result['error']))

    def _do_disconnect(self) -> None:
        self._progress_quiet('disconnecting')
        self._close()
        self._progress_quiet('disconnected')

    def _close(self) -> None:
        """Disconnect if a session is open, and record which units confirmed release."""
        s = self.session
        if s is not None:
            try:
                s.close()
            finally:
                report = s.close_report or {}
                with self.lock:
                    if self.snapshot is not None and report:
                        names = {u['slot']: u.get('model') for u in self.snapshot['units']}
                        self.snapshot['disconnect'] = {
                            'master_released': report.get(0, False),
                            'units': [{'slot': k, 'model': names.get(k), 'acked': v}
                                      for k, v in sorted(report.items()) if k != 0],
                            'at': time.strftime('%Y-%m-%dT%H:%M:%S'),
                        }
                        CACHE.write_text(json.dumps(self.snapshot, indent=1))
        with self.lock:
            self.session, self.phase = None, 'disconnected'

    def _watchdog(self) -> None:
        while True:
            time.sleep(30)
            with self.lock:
                idle = (self.phase in ('connected', 'adjusting') and not (self.adjust or {}).get('busy')
                        and time.time() - self.last_activity > IDLE_DISCONNECT_S)
                adjusting = self.phase == 'adjusting'
            if idle and adjusting:
                self.request_adjust('cancel')
                continue
            if idle and self.request('disconnect'):
                self._progress_quiet(f'idle for {IDLE_DISCONNECT_S // 60} min: disconnecting')

    def shutdown(self) -> None:
        """Called when the server stops: never leave the bike in PC link mode."""
        with self.lock:
            self._cancel = True
        end = time.time() + 60
        while time.time() < end:          # let a running job finish (or cancel) on the worker
            with self.lock:
                if self.phase in ('disconnected', 'connected') or (
                        self.phase == 'adjusting' and not (self.adjust or {}).get('busy')):
                    break
            time.sleep(0.2)
        with self.lock:
            busy = self.session is not None
            adjuster = self.adjuster
        if adjuster:
            print('cancelling the derailleur adjustment before exit...', flush=True)
            try:
                adjuster.cancel()
            except Exception as e:
                print('  could not restore:', e, flush=True)
        if busy:
            print('disconnecting from the bike before exit...', flush=True)
            self._close()


def make_handler(bike: Bike):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, body: bytes, ctype: str = 'application/json') -> None:
            self.send_response(code)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path in ('/', '/index.html'):
                self._send(200, (WEB / 'index.html').read_bytes(), 'text/html; charset=utf-8')
            elif self.path == '/api/state':
                self._send(200, json.dumps(bike.state()).encode())
            elif self.path == '/api/link':
                self._send(200, json.dumps(bike.link()).encode())
            else:
                self._send(404, b'{"error": "not found"}')

        def _body(self) -> dict:
            n = int(self.headers.get('Content-Length') or 0)
            return json.loads(self.rfile.read(n) or b'{}') if n else {}

        def do_POST(self):
            action = self.path.removeprefix('/api/')
            if action in ('check', 'apply'):
                try:
                    changes = [{'slot': int(c['slot']), 'key': str(c['key']), 'value': int(c['value'])}
                               for c in self._body().get('changes', [])]
                except (ValueError, KeyError, TypeError):
                    self._send(400, b'{"error": "bad changes"}')
                    return
                if action == 'check':
                    self._send(200, json.dumps(bike.check(changes)).encode())
                else:
                    ok = bike.request_apply(changes)
                    self._send(202 if ok else 409, json.dumps(bike.state()).encode())
            elif action == 'adjust':
                body = self._body()
                act, delta = str(body.get('action', '')), int(body.get('delta', 0) or 0)
                if act not in ('begin', 'start', 'shift', 'trim', 'finish', 'cancel'):
                    self._send(400, b'{"error": "bad action"}')
                    return
                ok = bike.request_adjust(act, delta)
                self._send(202 if ok else 409, json.dumps(bike.state()).encode())
            elif action in ('connect', 'refresh', 'disconnect', 'cancel'):
                ok = bike.request(action)
                self._send(202 if ok else 409, json.dumps(bike.state()).encode())
            else:
                self._send(404, b'{"error": "not found"}')

        def log_message(self, fmt, *args):   # keep the console quiet
            pass

    return Handler


def serve(host: str = '127.0.0.1', port: int = 8095, dev: str = '/dev/di2') -> None:
    bike = Bike(dev)
    httpd = ThreadingHTTPServer((host, port), make_handler(bike))
    print(f'di2 UI on http://{host}:{port}/  (Ctrl-C disconnects from the bike and exits)', flush=True)
    try:
        httpd.serve_forever()
    finally:
        bike.shutdown()
