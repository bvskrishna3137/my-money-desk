#!/usr/bin/env python3
"""My Money Desk local server. Run in the same folder as index.html, then open http://127.0.0.1:8080"""
import http.server, json, os, re, shutil, tempfile, datetime, glob, time
from urllib.parse import urlparse, parse_qs

PORT = 8080
HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, 'money-data.json')
BACKUPS = os.path.join(HERE, 'backups')
KEEP_DAYS = 90
KEEP_SNAPS = 12
SNAP_EVERY = 30 * 60
KEEP_DROPS = 20
KEEP_UNDO = 10
REV = os.path.join(HERE, 'money-data.rev')
LISTS = ('expenses', 'income', 'bills', 'debts', 'cards', 'goals', 'recurring', 'jobs', 'work')
NAME_OK = re.compile(r'^[A-Za-z0-9._-]+\.json$')
MAX_BYTES = 5 * 1024 * 1024
REQUIRED_KEYS = ('expenses', 'income', 'cards', 'debts')
BLOCKED = ('/server.py', '/reminders.py', '/money-data.json', '/money-data.json.bak', '/money-data.rev', '/notify-state.json', '/update.sh', '/start-money-desk.sh')
os.chdir(HERE)

def read_rev():
    try:
        with open(REV) as f: return int(f.read().strip() or 0)
    except Exception: return 0

def write_rev(n):
    tmp = REV + '.tmp'
    with open(tmp, 'w') as f: f.write(str(n))
    os.replace(tmp, REV)

def total(obj):
    try: return sum(len(obj.get(k) or []) for k in LISTS)
    except Exception: return 0

def counts(path):
    try:
        with open(path, 'rb') as f: o = json.loads(f.read())
        return {k: len(o.get(k) or []) for k in LISTS}
    except Exception: return None

def prune(pattern, keep):
    files = sorted(glob.glob(os.path.join(BACKUPS, pattern)))
    for f in files[:-keep] if keep > 0 else files:
        try: os.remove(f)
        except OSError: pass

def stamp():
    return datetime.datetime.now().strftime('%Y%m%d-%H%M%S')

def daily_backup():
    os.makedirs(BACKUPS, exist_ok=True)
    name = os.path.join(BACKUPS, 'money-data-%s.json' % datetime.date.today().isoformat())
    shutil.copy2(DATA, name)  # latest save of the day wins
    prune('money-data-*.json', KEEP_DAYS)

def snapshot():
    """A copy at most every 30 minutes, so today can be recovered too."""
    os.makedirs(BACKUPS, exist_ok=True)
    snaps = sorted(glob.glob(os.path.join(BACKUPS, 'snap-*.json')))
    if snaps and time.time() - os.path.getmtime(snaps[-1]) < SNAP_EVERY: return
    shutil.copy2(DATA, os.path.join(BACKUPS, 'snap-%s.json' % stamp()))
    prune('snap-*.json', KEEP_SNAPS)

def keep_copy(prefix, keep):
    """Copy the file that is about to be replaced. Never part of the normal daily/snap clean-up."""
    if not os.path.exists(DATA): return
    os.makedirs(BACKUPS, exist_ok=True)
    shutil.copy2(DATA, os.path.join(BACKUPS, '%s-%s.json' % (prefix, stamp())))
    prune(prefix + '-*.json', keep)

def kind_of(fn):
    return 'snap' if fn.startswith('snap-') else 'drop' if fn.startswith('before-drop-') else 'undo' if fn.startswith('before-restore-') else 'daily'

_cache = {}
def list_backups():
    out = []
    if not os.path.isdir(BACKUPS): return out
    pats = ['money-data-*.json', 'snap-*.json', 'before-drop-*.json', 'before-restore-*.json']
    for p in pats:
        for f in glob.glob(os.path.join(BACKUPS, p)):
            fn = os.path.basename(f)
            try: st = os.stat(f)
            except OSError: continue
            key = (fn, st.st_mtime, st.st_size)
            if key not in _cache: _cache[key] = counts(f)
            out.append({'file': fn, 'kind': kind_of(fn), 'mtime': int(st.st_mtime), 'size': st.st_size, 'counts': _cache[key]})
    out.sort(key=lambda x: -x['mtime'])
    return out

class H(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()
    def _ok_host(self):
        host = (self.headers.get('Host') or '').split(':')[0]
        return host in ('127.0.0.1', 'localhost')
    def _send(self, code, body=b'', ctype='application/json'):
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        self.wfile.write(body)
    def do_GET(self):
        if not self._ok_host(): return self._send(403, b'{}')
        u = urlparse(self.path); path = u.path
        if path == '/api/load':
            body = b'{}'
            if os.path.exists(DATA):
                with open(DATA, 'rb') as f: body = f.read()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Rev', str(read_rev()))
            self.end_headers(); self.wfile.write(body); return
        if path == '/api/backups':
            return self._send(200, json.dumps({'rev': read_rev(), 'items': list_backups()}).encode())
        if path == '/api/backup':
            fn = (parse_qs(u.query).get('f') or [''])[0]
            full = os.path.join(BACKUPS, fn)
            if not NAME_OK.match(fn) or not os.path.isfile(full): return self._send(404, b'{}')
            with open(full, 'rb') as f: return self._send(200, f.read())
        if path in BLOCKED or path.startswith('/backups') or path.startswith('/api/'): return self._send(404, b'{}')
        return super().do_GET()
    def do_POST(self):
        if not self._ok_host(): return self._send(403, b'{}')
        if self.path.split('?')[0] != '/api/save': return self._send(404, b'{}')
        origin = self.headers.get('Origin')
        if origin and origin.split('://')[-1].split(':')[0] not in ('127.0.0.1', 'localhost'): return self._send(403, b'{}')
        if 'application/json' not in (self.headers.get('Content-Type') or ''): return self._send(415, b'{}')
        try: n = int(self.headers.get('Content-Length') or 0)
        except ValueError: return self._send(400, b'{}')
        if n <= 0 or n > MAX_BYTES: return self._send(413, b'{}')
        raw = self.rfile.read(n)
        try:
            obj = json.loads(raw)
            assert isinstance(obj, dict) and all(isinstance(obj.get(k), list) for k in REQUIRED_KEYS)
        except Exception: return self._send(400, b'{"error":"bad data"}')
        restore = self.headers.get('X-Restore') == '1'
        cur = read_rev(); base = self.headers.get('X-Base-Rev')
        if not restore and base is not None:
            try: stale = int(base) != cur
            except ValueError: stale = True
            if stale: return self._send(409, json.dumps({'conflict': True, 'rev': cur}).encode())
        shrink = False
        old_total = 0
        if os.path.exists(DATA):
            try:
                with open(DATA, 'rb') as f: old_total = total(json.loads(f.read()))
            except Exception: old_total = 0
            new_total = total(obj)
            if restore: keep_copy('before-restore', KEEP_UNDO)
            elif old_total - new_total >= 10 and new_total < old_total * 0.6:
                shrink = True; keep_copy('before-drop', KEEP_DROPS)
            shutil.copy2(DATA, DATA + '.bak')
        fd, tmp = tempfile.mkstemp(dir=HERE, suffix='.tmp')
        with os.fdopen(fd, 'wb') as f: f.write(raw); f.flush(); os.fsync(f.fileno())
        os.replace(tmp, DATA)
        rev = cur + 1
        try: write_rev(rev)
        except Exception: pass
        try:
            if not shrink: daily_backup()
            snapshot()
        except Exception: pass
        return self._send(200, json.dumps({'ok': True, 'rev': rev, 'shrink': shrink}).encode())
    def log_message(self, *a): pass

if __name__ == '__main__':
    print('My Money Desk running: http://127.0.0.1:%d  (Ctrl+C to stop)' % PORT)
    try:
        import reminders; reminders.start(DATA)
    except Exception: pass
    http.server.ThreadingHTTPServer(('127.0.0.1', PORT), H).serve_forever()
