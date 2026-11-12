#!/usr/bin/env python3
"""My Money Desk: tiny local save server (Python standard library only).

It does two things:
  1. serves the app (index.html and its few static files), and
  2. keeps the data in one JSON file next to the app, with safety nets.

API used by the app (all on 127.0.0.1, nothing leaves the device):
  GET  /api/load      the saved data, plus an X-Rev header (the save counter)
  POST /api/save      save the data (see rules below)
  GET  /api/backups   list restore points
  GET  /api/backup?f= one restore point

Save rules (the same ones the Android build applies on its own):
  * The body must be a JSON object with the lists the app needs (max 5 MB).
  * X-Base-Rev must match the server's revision, otherwise 409 (an old copy of
    the app cannot overwrite newer data). X-Restore: 1 skips that check.
  * Writes are atomic (temp file, fsync, replace) and the previous file is kept
    as money-data.json.bak.
  * Restore points: a daily copy, a half-hour snapshot, a copy kept before a
    restore, and a copy kept before an unusually large drop in entries.

Usage:  python3 server.py [--port 8080] [--data-dir PATH]
"""
import argparse
import json
import os
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

APP_DIR = os.path.dirname(os.path.abspath(__file__))

# Only these files are ever served. Everything else (server.py, the data file,
# backups, dotfiles) answers 404.
STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/native.js": ("native.js", "application/javascript; charset=utf-8"),
    "/sw.js": ("sw.js", "application/javascript; charset=utf-8"),
    "/manifest.json": ("manifest.json", "application/manifest+json"),
    "/icon.svg": ("icon.svg", "image/svg+xml"),
    "/icon-192.png": ("icon-192.png", "image/png"),
    "/icon-512.png": ("icon-512.png", "image/png"),
}

LISTS = ["expenses", "income", "bills", "debts", "cards", "goals", "recurring",
         "jobs", "work", "assets", "autoexp", "scores", "rewards"]
REQUIRED = ["expenses", "income", "cards", "debts"]
MAX_BYTES = 5 * 1024 * 1024
KEEP = {"daily": 14, "snap": 6, "drop": 10, "undo": 5}
SNAP_EVERY = 30 * 60  # seconds between half-hour snapshots
NAME_OK = re.compile(r"^[A-Za-z0-9._-]+\.json$")


def total(obj):
    """Number of entries across all lists (used by the shrink guard)."""
    return sum(len(obj.get(k) or []) for k in LISTS if isinstance(obj.get(k), list))


def counts(obj):
    return {k: len(obj.get(k) or []) if isinstance(obj.get(k), list) else 0 for k in LISTS}


def kind_of(name):
    if name.startswith("snap-"):
        return "snap"
    if name.startswith("before-drop-"):
        return "drop"
    if name.startswith("before-restore-"):
        return "undo"
    return "daily"


class Store:
    """The data file, its revision counter and its restore points."""

    def __init__(self, data_dir):
        self.dir = data_dir
        self.data_path = os.path.join(data_dir, "money-data.json")
        self.rev_path = os.path.join(data_dir, "rev.txt")
        self.bk_dir = os.path.join(data_dir, "backups")
        self.idx_path = os.path.join(self.bk_dir, "index.json")
        self.lock = threading.Lock()
        os.makedirs(self.bk_dir, exist_ok=True)
        self.rev = self._read_int(self.rev_path)
        self.idx = self._load_index()

    # ---- small helpers -------------------------------------------------
    @staticmethod
    def _read_int(path):
        try:
            with open(path, encoding="utf-8") as f:
                return int(f.read().strip() or 0)
        except (OSError, ValueError):
            return 0

    @staticmethod
    def _atomic_write(path, text):
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)

    def _load_index(self):
        try:
            with open(self.idx_path, encoding="utf-8") as f:
                idx = json.load(f)
            if not isinstance(idx, dict):
                idx = {}
        except (OSError, ValueError):
            idx = {}
        on_disk = {n for n in os.listdir(self.bk_dir) if NAME_OK.match(n) and n != "index.json"}
        idx = {n: v for n, v in idx.items() if n in on_disk}
        for n in on_disk - set(idx):
            st = os.stat(os.path.join(self.bk_dir, n))
            idx[n] = {"mtime": int(st.st_mtime), "size": st.st_size, "counts": None}
        return idx

    def _put_backup(self, name, raw, cnt):
        self._atomic_write(os.path.join(self.bk_dir, name), raw)
        self.idx[name] = {"mtime": int(time.time()), "size": len(raw), "counts": cnt}

    def _prune(self, prefix_re, keep):
        names = sorted(n for n in self.idx if re.match(prefix_re, n))
        for n in names[: max(0, len(names) - keep)]:
            self.idx.pop(n, None)
            try:
                os.remove(os.path.join(self.bk_dir, n))
            except OSError:
                pass

    def _last_snap(self):
        return max((v["mtime"] for n, v in self.idx.items() if n.startswith("snap-")), default=0)

    # ---- the four API calls ---------------------------------------------
    def load(self):
        with self.lock:
            try:
                with open(self.data_path, encoding="utf-8") as f:
                    return f.read() or "{}", self.rev
            except OSError:
                return "{}", self.rev

    def save(self, raw, base_rev, restore):
        """Returns (status_code, response_dict)."""
        if not raw or len(raw) > MAX_BYTES:
            return 413, {}
        try:
            obj = json.loads(raw)
            if not isinstance(obj, dict) or not all(isinstance(obj.get(k), list) for k in REQUIRED):
                raise ValueError
        except ValueError:
            return 400, {"error": "bad data"}
        with self.lock:
            if not restore and base_rev is not None and base_rev != self.rev:
                return 409, {"conflict": True, "rev": self.rev}
            shrink = False
            old = None
            try:
                with open(self.data_path, encoding="utf-8") as f:
                    old = f.read()
            except OSError:
                pass
            if old:
                try:
                    old_obj = json.loads(old)
                except ValueError:
                    old_obj = {}
                stamp = time.strftime("%Y%m%d-%H%M%S")
                if restore:
                    self._put_backup(f"before-restore-{stamp}.json", old, counts(old_obj))
                    self._prune(r"^before-restore-", KEEP["undo"])
                elif total(old_obj) - total(obj) >= 10 and total(obj) < total(old_obj) * 0.6:
                    shrink = True
                    self._put_backup(f"before-drop-{stamp}.json", old, counts(old_obj))
                    self._prune(r"^before-drop-", KEEP["drop"])
                self._atomic_write(self.data_path + ".bak", old)
            self._atomic_write(self.data_path, raw)
            self.rev += 1
            self._atomic_write(self.rev_path, str(self.rev))
            cnt = counts(obj)
            if not shrink:
                self._put_backup(f"money-data-{time.strftime('%Y-%m-%d')}.json", raw, cnt)
                self._prune(r"^money-data-", KEEP["daily"])
            if time.time() - self._last_snap() >= SNAP_EVERY:
                self._put_backup(f"snap-{time.strftime('%Y%m%d-%H%M%S')}.json", raw, cnt)
                self._prune(r"^snap-", KEEP["snap"])
            try:
                self._atomic_write(self.idx_path, json.dumps(self.idx))
            except OSError:
                pass
            return 200, {"ok": True, "rev": self.rev, "shrink": shrink}

    def backups(self):
        with self.lock:
            items = [{"file": n, "kind": kind_of(n), "mtime": v["mtime"], "size": v["size"],
                      "counts": v.get("counts")} for n, v in self.idx.items()]
            items.sort(key=lambda x: (x["mtime"], x["file"]), reverse=True)
            return {"rev": self.rev, "items": items}

    def backup(self, name):
        if not NAME_OK.match(name or "") or name == "index.json":
            return None
        try:
            with open(os.path.join(self.bk_dir, name), encoding="utf-8") as f:
                return f.read()
        except OSError:
            return None


def make_handler(store, port):
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    allowed_origins = {f"http://{h}" for h in allowed_hosts}

    class Handler(BaseHTTPRequestHandler):
        server_version = "MoneyDesk"
        protocol_version = "HTTP/1.1"

        def log_message(self, *_):  # keep the console quiet
            pass

        # ---- response helpers ----
        def _send(self, code, body=b"", ctype="application/json", extra=None):
            if isinstance(body, str):
                body = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            if getattr(self, "_close", False):  # request body was not read: do not reuse the connection
                self.send_header("Connection", "close")
                self.close_connection = True
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, code, obj, extra=None):
            self._send(code, json.dumps(obj), extra=extra)

        def _trusted(self):
            """Reject requests that were not meant for this local app."""
            if self.headers.get("Host", "") not in allowed_hosts:
                return False
            origin = self.headers.get("Origin")
            return origin is None or origin in allowed_origins

        # ---- routes ----
        def do_GET(self):
            self._handle()

        do_HEAD = do_GET

        def do_POST(self):
            self._handle()

        def _handle(self):
            url = urlsplit(self.path)
            path = url.path
            self._close = self.command == "POST"  # cleared below once the body is actually read
            if not self._trusted():
                return self._json(403, {"error": "forbidden"})
            if path == "/api/load" and self.command in ("GET", "HEAD"):
                raw, rev = store.load()
                return self._send(200, raw, extra={"X-Rev": str(rev)})
            if path == "/api/backups" and self.command in ("GET", "HEAD"):
                return self._json(200, store.backups())
            if path == "/api/backup" and self.command in ("GET", "HEAD"):
                raw = store.backup((parse_qs(url.query).get("f") or [""])[0])
                return self._send(200, raw) if raw is not None else self._json(404, {})
            if path == "/api/save" and self.command == "POST":
                try:
                    size = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    size = 0
                if size <= 0 or size > MAX_BYTES:
                    return self._json(413, {})
                raw = self.rfile.read(size).decode("utf-8", "replace")
                self._close = False
                base = self.headers.get("X-Base-Rev")
                try:
                    base = int(base) if base is not None else None
                except ValueError:
                    base = None
                code, body = store.save(raw, base, self.headers.get("X-Restore") == "1")
                return self._json(code, body)
            if path in STATIC and self.command in ("GET", "HEAD"):
                name, ctype = STATIC[path]
                try:
                    with open(os.path.join(APP_DIR, name), "rb") as f:
                        return self._send(200, f.read(), ctype)
                except OSError:
                    pass
            self._json(404, {})

    return Handler


def serve(port=8080, data_dir=None):
    """Create the server (not started). Used by main() and by the tests."""
    store = Store(data_dir or APP_DIR)
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(store, port))
    httpd.store = store
    return httpd


def main(argv=None):
    ap = argparse.ArgumentParser(description="My Money Desk local server")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--data-dir", default=APP_DIR, help="where money-data.json and backups/ live")
    args = ap.parse_args(argv)
    try:
        httpd = serve(args.port, args.data_dir)
    except OSError as e:
        sys.exit(f"Could not start on port {args.port}: {e}")
    print(f"My Money Desk is running: http://127.0.0.1:{args.port}   (Ctrl+C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
