#!/usr/bin/env python3
"""Regenerate the README screenshots from the made-up sample data.

Needs:  pip install playwright && playwright install chromium
Run:    python3 tools/screenshots.py [--out docs/screenshots] [--fonts-dir PATH]

It starts the real server on a throw-away copy of money-data.sample.json, freezes
the browser clock at 2026-11-12 09:30 (the day the sample data was built for) and
saves phone-sized (390x844 @2x) screenshots. Your own money-data.json is never read.

--fonts-dir is optional: a folder holding the @fontsource packages for Plus Jakarta
Sans and JetBrains Mono (use it when the machine cannot reach Google Fonts).
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NOW = datetime(2026, 11, 12, 9, 30)
TZ = "America/Chicago"

# (file name, JavaScript run before the shot, scroll-y in px)
SHOTS = [
    ("today", "", 0), ("today-more", "", 1150),
    ("cards", "app.setTab('cards')", 0), ("income", "app.setTab('income')", 0),
    ("spend", "app.setTab('spend')", 0), ("bills", "app.setTab('fixed')", 0),
    ("analytics", "app.setTab('analytics')", 0), ("debts", "app.setTab('debts')", 0),
    ("plan", "app.setTab('plan')", 0), ("networth", "app.setTab('networth')", 0),
    ("insights", "app.openMenu('insights')", 0), ("cashflow", "app.openMenu('cashflow')", 0),
    ("monthly-plan", "app.openMenu('plan')", 0), ("health-check", "app.openMenu('check')", 0),
    ("features", "app.openMenu('features')", 0), ("restore-points", "app.restoreSheet()", 0),
    ("add-expense", "app.expenseSheet()", 0), ("themes", "app.openMenu('look')", 0),
    ("remittance", "app.openMenu('remit')", 0), ("menu", "app.openMenuPop()", 0),
]
STYLES = ["midnight", "calm", "plum", "dotmx", "lavender", "midviolet", "forest", "gold", "mint"]


def font_routes(ctx, fonts_dir):
    css = ""
    for fam, pkg, ws in (("Plus Jakarta Sans", "plus-jakarta-sans", (500, 600, 700, 800)), ("JetBrains Mono", "jetbrains-mono", (500, 700))):
        for w in ws:
            css += (f"@font-face{{font-family:'{fam}';font-weight:{w};font-style:normal;"
                    f"src:url(https://fonts.gstatic.com/local/{pkg}-{w}.woff2) format('woff2');}}\n")
    ctx.route("https://fonts.googleapis.com/**", lambda r: r.fulfill(status=200, content_type="text/css", body=css))

    def woff(route):
        pkg, w = route.request.url.rsplit("/", 1)[1].replace(".woff2", "").rsplit("-", 1)
        path = os.path.join(fonts_dir, pkg, "files", f"{pkg}-latin-{w}-normal.woff2")
        route.fulfill(status=200, content_type="font/woff2", body=open(path, "rb").read(),
                      headers={"access-control-allow-origin": "*"})
    ctx.route("https://fonts.gstatic.com/local/**", woff)


def fake_restore_points(sample):
    """Deterministic restore-point list (the real server stamps real wall-clock times)."""
    def ts(y, mo, d, h, mi):
        return int(datetime(y, mo, d, h, mi, tzinfo=ZoneInfo(TZ)).timestamp())
    n = sum(len(sample[k]) for k in ("expenses", "income"))
    cnt = lambda e, i: {"expenses": e, "income": i, "cards": 5, "debts": 4, "bills": 5, "goals": 3}
    items = [
        ("snap-20261112-085600.json", "snap", ts(2026, 11, 12, 8, 56), cnt(len(sample["expenses"]), len(sample["income"]))),
        ("money-data-2026-11-12.json", "daily", ts(2026, 11, 12, 8, 40), cnt(len(sample["expenses"]), len(sample["income"]))),
        ("snap-20261111-210300.json", "snap", ts(2026, 11, 11, 21, 3), cnt(len(sample["expenses"]) - 1, len(sample["income"]))),
        ("money-data-2026-11-11.json", "daily", ts(2026, 11, 11, 20, 15), cnt(len(sample["expenses"]) - 1, len(sample["income"]))),
        ("before-restore-20261110-181200.json", "undo", ts(2026, 11, 10, 18, 12), cnt(len(sample["expenses"]) - 3, len(sample["income"]))),
        ("before-drop-20261108-073000.json", "drop", ts(2026, 11, 8, 7, 30), cnt(len(sample["expenses"]) - 6, len(sample["income"]))),
        ("money-data-2026-11-07.json", "daily", ts(2026, 11, 7, 19, 45), cnt(len(sample["expenses"]) - 9, len(sample["income"]) - 1)),
    ]
    return {"rev": 212, "items": [{"file": f, "kind": k, "mtime": m, "size": 41000, "counts": c} for f, k, m, c in items]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(ROOT, "docs", "screenshots"))
    ap.add_argument("--fonts-dir", default="")
    ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    sample_path = os.path.join(ROOT, "money-data.sample.json")
    sample = json.load(open(sample_path, encoding="utf-8"))
    data_dir = tempfile.mkdtemp(prefix="mmd-shots-")
    shutil.copy(sample_path, os.path.join(data_dir, "money-data.json"))
    url = f"http://127.0.0.1:{a.port}/"
    srv = subprocess.Popen([sys.executable, os.path.join(ROOT, "server.py"), "--port", str(a.port), "--data-dir", data_dir])
    try:
        for _ in range(60):
            try:
                urllib.request.urlopen(url + "api/load", timeout=0.5).read()
                break
            except Exception:
                time.sleep(0.1)
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            ctx = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True,
                                      has_touch=True, color_scheme="light", locale="en-US", timezone_id=TZ)
            if a.fonts_dir:
                font_routes(ctx, a.fonts_dir)
            ctx.route("**/api/backups", lambda r: r.fulfill(status=200, content_type="application/json",
                                                              body=json.dumps(fake_restore_points(sample))))
            page = ctx.new_page()
            page.clock.install(time=NOW)
            page.goto(url)
            page.wait_for_timeout(1500)

            def settle(js, y):
                page.evaluate("try{app.closeSheet();app.closeMenu()}catch(e){}")
                page.evaluate("app.setTab('today');app.setTheme('violet')")
                page.wait_for_timeout(300)
                if js:
                    page.evaluate(js)
                page.wait_for_timeout(1000)
                page.evaluate("clearTimeout(app.tt);document.getElementById('toast').classList.remove('show')")
                page.evaluate(f"window.scrollTo(0,{y})")
                page.wait_for_timeout(250)

            for name, js, y in SHOTS:
                settle(js, y)
                page.screenshot(path=os.path.join(a.out, f"{name}.png"))
                print("saved", name)
            for t in STYLES:
                settle(f"app.setTheme('{t}')", 0)
                page.screenshot(path=os.path.join(a.out, f"style-{t}.png"))
                print("saved style", t)
            browser.close()
    finally:
        srv.terminate()
        shutil.rmtree(data_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
