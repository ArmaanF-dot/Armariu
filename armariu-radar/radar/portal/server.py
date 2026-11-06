"""Armariu for clients: a read-only portal, fully separate from the owner's review app.

    python -m radar.portal                   # build data if missing, serve http://127.0.0.1:8790
    python -m radar.portal --rebuild         # rebuild the data snapshot first (about 30 seconds)
    python -m radar.portal export DIR        # write a static copy (index.html, assets, data.json)

What it shows is company-level facts only: lender league tables, signals with their filing links, lender
profiles, sector cuts and the backtest scorecard, with every caveat. It has no accounts, payments,
review queues or write endpoints. Individuals are never named (they are masked when data is loaded).
Hosting it publicly is a decision for after the validation gate; by default it only listens on this PC.
"""
from __future__ import annotations

import argparse
import json
import shutil
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from .. import snapshot

STATIC = Path(__file__).resolve().parent / "static"
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
         ".svg": "image/svg+xml", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
         ".webp": "image/webp", ".json": "application/json"}
PUBLIC_KEYS = ("built", "rule_version", "coverage_start", "caveat", "sample_caveat", "bias_note", "not_advice",
               "class_labels", "signal_labels", "totals", "weeks", "series", "lenders", "signals", "scorecard", "refi_watch", "working_capital", "insights")


def public_data(snap: dict) -> dict:
    """Only what a client may see. The owner's review list and alias coverage stay out."""
    out = {k: snap.get(k) for k in PUBLIC_KEYS}
    out["signals"] = [s for s in out.get("signals") or []
                      if "private individual" not in (s.get("lender_names", "") + s.get("other_lender", "")).lower()]
    if out.get("refi_watch"):   # never name individuals: drop rows whose newest lender is one
        rw = dict(out["refi_watch"])
        rw["rows"] = [r for r in rw.get("rows") or [] if "private individual" not in r.get("newest_lender", "").lower()]
        out["refi_watch"] = rw
    return out


def _data(rebuild: bool = False) -> bytes:
    snap = None if rebuild else snapshot.load()
    if snap is None:
        from ..db import connect
        snap = snapshot.build_snapshot(connect())
        snapshot.write(snap)
    return json.dumps(public_data(snap), separators=(",", ":"), default=str).encode()


def make_handler(state: dict):
    class Handler(BaseHTTPRequestHandler):
        server_version = "ArmariuPortal/1"

        def log_message(self, fmt, *args):
            pass

        def _send(self, status, body: bytes, ctype: str):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = urlparse(self.path).path
            if path in ("/", "/index.html"):
                return self._send(HTTPStatus.OK, (STATIC / "index.html").read_bytes(), TYPES[".html"])
            if path == "/data.json":
                return self._send(HTTPStatus.OK, state["data"], TYPES[".json"])
            f = (STATIC / path.lstrip("/")).resolve()
            if STATIC in f.parents and f.is_file():
                return self._send(HTTPStatus.OK, f.read_bytes(), TYPES.get(f.suffix.lower(), "application/octet-stream"))
            return self._send(HTTPStatus.NOT_FOUND, b"Not found", "text/plain")

        def do_POST(self):  # read-only by design
            return self._send(HTTPStatus.METHOD_NOT_ALLOWED, b"Read-only", "text/plain")

    return Handler


def export(out_dir, rebuild: bool = False) -> Path:
    out = Path(out_dir)
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(STATIC, out)
    (out / "data.json").write_bytes(_data(rebuild))
    return out


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m radar.portal")
    p.add_argument("cmd", nargs="?", default="serve", choices=("serve", "export"))
    p.add_argument("dir", nargs="?", help="export folder")
    p.add_argument("--port", type=int, default=8790)
    p.add_argument("--host", default="127.0.0.1", help="keep 127.0.0.1 until you decide to host it")
    p.add_argument("--rebuild", action="store_true")
    p.add_argument("--no-browser", action="store_true")
    a = p.parse_args(argv)
    if a.cmd == "export":
        print("Exported to", export(a.dir or "portal_site", a.rebuild))
        return
    state = {"data": _data(a.rebuild)}
    server = ThreadingHTTPServer((a.host, a.port), make_handler(state))
    url = f"http://{'127.0.0.1' if a.host in ('0.0.0.0', '') else a.host}:{a.port}/"
    print(f"Armariu client portal at {url}  (read-only; Ctrl+C to stop)")
    if not a.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()
