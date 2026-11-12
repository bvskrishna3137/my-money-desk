"""Tests for server.py (standard library only):  python3 -m unittest discover -s tests -v"""
import http.client
import json
import os
import re
import socket
import sys
import tempfile
import threading
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import server  # noqa: E402


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def sample(n_expenses=3):
    return {"v": 2, "income": [], "cards": [{"id": "c1", "name": "Cash", "type": "cash", "balance": 0}],
            "debts": [], "expenses": [{"id": f"e{i}", "amount": 1} for i in range(n_expenses)]}


class ServerCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.port = free_port()
        self.httpd = server.serve(self.port, self.tmp.name)
        threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.tmp.cleanup()

    def call(self, method, path, body=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.request(method, path, body=body, headers=headers or {})
        r = c.getresponse()
        data = r.read()
        c.close()
        return r.status, dict(r.getheaders()), data

    def save(self, obj, **hdr):
        return self.call("POST", "/api/save", json.dumps(obj), hdr)


class TestSaveAndLoad(ServerCase):
    def test_empty_load(self):
        status, h, body = self.call("GET", "/api/load")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {})
        self.assertEqual(h["X-Rev"], "0")

    def test_roundtrip_and_revision(self):
        status, _, body = self.save(sample())
        self.assertEqual((status, json.loads(body)["rev"]), (200, 1))
        _, h, body = self.call("GET", "/api/load")
        self.assertEqual(h["X-Rev"], "1")
        self.assertEqual(len(json.loads(body)["expenses"]), 3)

    def test_rejects_bad_data(self):
        self.assertEqual(self.call("POST", "/api/save", "not json", {})[0], 400)
        self.assertEqual(self.call("POST", "/api/save", json.dumps({"expenses": []}), {})[0], 400)
        self.assertEqual(self.call("POST", "/api/save", json.dumps([1, 2]), {})[0], 400)

    def test_rejects_oversized_body(self):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.putrequest("POST", "/api/save")
        c.putheader("Content-Length", str(server.MAX_BYTES + 1))
        c.endheaders()
        self.assertEqual(c.getresponse().status, 413)
        c.close()

    def test_previous_file_kept_as_bak(self):
        self.save(sample(3))
        self.save(sample(4))
        with open(os.path.join(self.tmp.name, "money-data.json.bak"), encoding="utf-8") as f:
            self.assertEqual(len(json.load(f)["expenses"]), 3)

    def test_revision_survives_restart(self):
        self.save(sample())
        self.save(sample())
        self.assertEqual(server.Store(self.tmp.name).rev, 2)


class TestConflicts(ServerCase):
    def test_stale_copy_cannot_overwrite(self):
        self.save(sample(3))                                   # rev 1
        self.save(sample(4), **{"X-Base-Rev": "1"})            # rev 2
        status, _, body = self.save(sample(9), **{"X-Base-Rev": "1"})  # stale
        self.assertEqual(status, 409)
        self.assertEqual(json.loads(body), {"conflict": True, "rev": 2})
        _, _, body = self.call("GET", "/api/load")
        self.assertEqual(len(json.loads(body)["expenses"]), 4)  # newer data untouched

    def test_restore_skips_conflict_check_and_keeps_undo_copy(self):
        self.save(sample(5))
        status, _, _ = self.save(sample(2), **{"X-Base-Rev": "99", "X-Restore": "1"})
        self.assertEqual(status, 200)
        kinds = [i["kind"] for i in json.loads(self.call("GET", "/api/backups")[2])["items"]]
        self.assertIn("undo", kinds)


class TestRestorePoints(ServerCase):
    def test_big_drop_keeps_a_copy_first(self):
        self.save(sample(40))
        _, _, body = self.save(sample(5))
        self.assertTrue(json.loads(body)["shrink"])
        items = json.loads(self.call("GET", "/api/backups")[2])["items"]
        drop = [i for i in items if i["kind"] == "drop"]
        self.assertEqual(len(drop), 1)
        self.assertEqual(drop[0]["counts"]["expenses"], 40)
        _, _, raw = self.call("GET", "/api/backup?f=" + drop[0]["file"])
        self.assertEqual(len(json.loads(raw)["expenses"]), 40)

    def test_small_change_is_not_a_drop(self):
        self.save(sample(40))
        self.assertFalse(json.loads(self.save(sample(35))[2])["shrink"])

    def test_daily_and_snapshot_created(self):
        self.save(sample())
        kinds = {i["kind"] for i in json.loads(self.call("GET", "/api/backups")[2])["items"]}
        self.assertTrue({"daily", "snap"} <= kinds)

    def test_old_snapshots_are_pruned(self):
        for _ in range(3):
            self.save(sample())
        store = self.httpd.store
        for i in range(10):
            store._put_backup(f"snap-2026010{i}-000000.json", "{}", None)
        store._prune(r"^snap-", server.KEEP["snap"])
        self.assertEqual(len([n for n in store.idx if n.startswith("snap-")]), server.KEEP["snap"])


class TestSecurity(ServerCase):
    def test_path_traversal_and_odd_names_refused(self):
        self.save(sample())
        for f in ("../money-data.json", "..%2Fmoney-data.json", "index.json", "x.txt", "", "a/b.json"):
            self.assertEqual(self.call("GET", "/api/backup?f=" + f)[0], 404, f)

    def test_only_app_files_are_served(self):
        self.save(sample())
        self.assertEqual(self.call("GET", "/")[0], 200)
        self.assertEqual(self.call("GET", "/manifest.json")[0], 200)
        for path in ("/server.py", "/money-data.json", "/money-data.json.bak", "/rev.txt", "/backups/index.json",
                     "/../server.py", "/.gitignore", "/tests/test_server.py"):
            self.assertEqual(self.call("GET", path)[0], 404, path)

    def test_wrong_host_or_origin_refused(self):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.request("GET", "/api/load", headers={"Host": "evil.example"})
        self.assertEqual(c.getresponse().status, 403)
        c.close()
        self.assertEqual(self.call("POST", "/api/save", json.dumps(sample()), {"Origin": "https://evil.example"})[0], 403)

    def test_correct_origin_allowed(self):
        origin = f"http://127.0.0.1:{self.port}"
        self.assertEqual(self.call("POST", "/api/save", json.dumps(sample()), {"Origin": origin})[0], 200)


class TestRepoHygiene(unittest.TestCase):
    """Guards for a public repo: no secrets, no private data, sample data is valid."""

    def test_sample_data_is_valid_and_complete(self):
        with open(os.path.join(ROOT, "money-data.sample.json"), encoding="utf-8") as f:
            d = json.load(f)
        for k in server.REQUIRED:
            self.assertTrue(d[k], k)
        for card in d["cards"]:
            self.assertIn(card["type"], {"debit", "credit", "cash", "savings", "india"})

    def test_no_secrets_or_private_files_in_repo(self):
        bad_names = (".keystore", ".jks", ".env", "money-data.json")
        pattern = re.compile(r"apps\.googleusercontent\.com|storePassword|keyPassword|BEGIN (RSA |EC )?PRIVATE KEY|ghp_[A-Za-z0-9]{20,}")
        for base, dirs, files in os.walk(ROOT):
            dirs[:] = [d for d in dirs if d not in (".git", "backups", "__pycache__")]
            for name in files:
                self.assertFalse(name.endswith(bad_names[:3]) or name == bad_names[3], os.path.join(base, name))
                if name.endswith((".png", ".jpg", ".woff2", ".pyc")):
                    continue
                path = os.path.join(base, name)
                if os.path.abspath(path) == os.path.abspath(__file__):
                    continue
                with open(path, encoding="utf-8", errors="ignore") as f:
                    self.assertIsNone(pattern.search(f.read()), path)


if __name__ == "__main__":
    unittest.main()
