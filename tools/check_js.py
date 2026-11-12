#!/usr/bin/env python3
"""Syntax-check every inline <script> in index.html plus native.js and sw.js (needs Node.js)."""
import os
import re
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
html = open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()
blocks = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, re.S)
files = [os.path.join(ROOT, "native.js"), os.path.join(ROOT, "sw.js")]
bad = 0
with tempfile.TemporaryDirectory() as tmp:
    for i, code in enumerate(blocks):
        path = os.path.join(tmp, f"inline-{i}.js")
        open(path, "w", encoding="utf-8").write(code)
        files.append(path)
    for path in files:
        r = subprocess.run(["node", "--check", path], capture_output=True, text=True)
        status = "ok" if r.returncode == 0 else "FAILED"
        print(f"{status:6} {os.path.basename(path)}")
        if r.returncode:
            print(r.stderr)
            bad += 1
sys.exit(1 if bad else 0)
