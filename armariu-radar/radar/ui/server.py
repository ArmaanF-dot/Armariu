"""Local web server for the review app. Only listens on this computer (127.0.0.1).

    python -m radar.ui                 # opens http://127.0.0.1:8765 in your browser
    python -m radar.ui --port 9000 --no-browser
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import socket
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from ..db import connect
from . import api

STATIC = Path(__file__).resolve().parent / "static"
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
         ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
         ".png": "image/png", ".webp": "image/webp", ".json": "application/json"}


class App:
    def __init__(self, sync_enabled: bool = True):
        self.token = secrets.token_urlsafe(24)
        self.db_lock = threading.RLock()
        self.git = api.Git()
        self.sync = api.Sync(self.git, self.db_lock)
        self.sync_enabled = sync_enabled

    def start_sync(self):
        if self.sync_enabled:
            threading.Thread(target=self.sync.loop, daemon=True).start()

    def with_db(self, fn, *args, **kwargs):
        with self.db_lock:
            conn = connect()
            try:
                return fn(conn, *args, **kwargs)
            finally:
                conn.close()


def make_handler(app: App, port: int):
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    class Handler(BaseHTTPRequestHandler):
        server_version = "ArmariuReview/1"

        def log_message(self, fmt, *args):  # keep the console quiet
            pass

        # ------------------------------------------------------------ helpers
        def _send(self, status, body: bytes, ctype: str):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, data, status=HTTPStatus.OK):
            self._send(status, json.dumps(data, default=str).encode(), "application/json")

        def _error(self, status, message):
            self._json({"error": message}, status)

        def _host_ok(self) -> bool:
            # Blocks DNS-rebinding: a web page can't reach this server under another host name.
            return self.headers.get("Host", "") in allowed_hosts

        # ------------------------------------------------------------ GET
        def do_GET(self):
            if not self._host_ok():
                return self._error(HTTPStatus.FORBIDDEN, "Wrong host")
            url = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(url.query).items()}
            try:
                if url.path in ("/", "/index.html"):
                    html = (STATIC / "index.html").read_text(encoding="utf-8")
                    return self._send(HTTPStatus.OK, html.replace("__TOKEN__", app.token).encode(),
                                      TYPES[".html"])
                if url.path.startswith("/static/"):
                    f = (STATIC / url.path[len("/static/"):]).resolve()
                    if STATIC not in f.parents or not f.is_file():
                        return self._error(HTTPStatus.NOT_FOUND, "Not found")
                    return self._send(HTTPStatus.OK, f.read_bytes(),
                                      TYPES.get(f.suffix, "application/octet-stream"))
                if url.path == "/api/version":
                    return self._json({"version": api.version(), "sync": app.sync.status,
                                       "git": app.git.status})
                if url.path == "/api/summary":
                    return self._json(app.with_db(api.summary, app.git, app.sync))
                if url.path == "/api/overview":
                    return self._json(app.with_db(api.overview, app.git))
                if url.path == "/api/decisions":
                    return self._json(app.with_db(api.decisions))
                if url.path == "/api/documents":
                    return self._json(api.documents())
                if url.path == "/api/charges":
                    return self._json(app.with_db(
                        api.charges, q=q.get("q", ""), tier=q.get("tier", ""),
                        category=q.get("category", ""), check=q.get("check", ""),
                        date_from=q.get("from", ""), date_to=q.get("to", ""), status=q.get("status", ""),
                        sector=q.get("sector", ""), hide_property=q.get("hide_property", "") in ("1", "true"),
                        limit=min(int(q.get("limit", 100)), 500), offset=int(q.get("offset", 0))))
                if url.path == "/api/lenders":
                    return self._json(app.with_db(api.lenders, q=q.get("q", ""),
                                                  category=q.get("category", ""), status=q.get("status", "")))
                if url.path == "/api/directory":
                    return self._json(app.with_db(api.directory))
                if url.path == "/api/signals":
                    return self._json(app.with_db(api.signals, q.get("type", "")))
                if url.path == "/api/company":
                    return self._json(app.with_db(api.company, q.get("number", "")))
                if url.path == "/api/additions":
                    return self._json(api.additions())
                if url.path == "/api/pack":
                    return self._json(api.pack())
                if url.path == "/api/lender":
                    return self._json(app.with_db(api.lender, q.get("name", "")))
                return self._error(HTTPStatus.NOT_FOUND, "Not found")
            except KeyError as exc:
                return self._error(HTTPStatus.NOT_FOUND, str(exc).strip("'\""))
            except ValueError as exc:
                return self._error(HTTPStatus.BAD_REQUEST, str(exc))

        # ------------------------------------------------------------ POST
        def do_POST(self):
            if not self._host_ok():
                return self._error(HTTPStatus.FORBIDDEN, "Wrong host")
            if not secrets.compare_digest(self.headers.get("X-Radar-Token", ""), app.token):
                return self._error(HTTPStatus.FORBIDDEN, "Reload the page and try again")
            try:
                length = min(int(self.headers.get("Content-Length", 0)), 2_000_000)
                body = json.loads(self.rfile.read(length) or b"{}")
                path = urlparse(self.path).path
                if path == "/api/decide":
                    res = app.with_db(api.decide, app.git, body["name"], body["category"],
                                      body.get("parent", ""), body.get("note", ""),
                                      [str(n) for n in body.get("also") or []], str(body.get("future") or ""))
                elif path == "/api/file-as":
                    res = app.with_db(api.file_as, app.git, [str(n) for n in body.get("names") or []][:500],
                                      body["category"])
                elif path == "/api/approve":
                    names = body.get("names") or [body["name"]]
                    res = app.with_db(api.approve, app.git, [str(n) for n in names][:500])
                elif path == "/api/lender/note":
                    res = app.with_db(api.save_note, app.git, body["name"], str(body.get("note", "")))
                elif path == "/api/message/done":
                    res = api.message_done(app.git, body["path"])
                elif path == "/api/draft/save":
                    res = api.save_draft(app.git, body["path"], str(body["content"]))
                elif path == "/api/draft/status":
                    res = api.set_draft_status(app.git, body["path"], body["status"])
                elif path == "/api/additions/rebuild":
                    res = api.rebuild_additions(app.db_lock)
                elif path == "/api/pack/build":
                    res = api.build_pack(app.db_lock, str(body.get("week_start") or ""))
                elif path == "/api/sync":
                    app.sync.wake() if app.sync_enabled else app.sync.run_once()
                    res = {"ok": True}
                else:
                    return self._error(HTTPStatus.NOT_FOUND, "Not found")
                return self._json(res)
            except KeyError as exc:
                return self._error(HTTPStatus.BAD_REQUEST, f"Missing or unknown: {str(exc).strip(chr(39))}")
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                return self._error(HTTPStatus.BAD_REQUEST, str(exc))

    return Handler


class Server(ThreadingHTTPServer):
    """Claims its port exclusively. On Windows a plain HTTPServer may bind a port another copy is
    already listening on, and the browser then keeps talking to the old copy."""
    daemon_threads = True
    allow_reuse_address = os.name != "nt"

    def server_bind(self):
        if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def serve(port: int = 8765, open_browser: bool = True, sync: bool = True):
    app = App(sync_enabled=sync)
    server = None
    for p in range(port, port + 10):
        try:
            server = Server(("127.0.0.1", p), make_handler(app, p))
            break
        except OSError:
            continue
    if server is None:
        raise SystemExit(f"Ports {port}-{port + 9} are all in use. Close the other review app windows and try again.")
    if p != port:
        print(f"Another copy of the review app is still running on port {port} (probably an old black window).\n"
              "Close that window when you can. This copy uses the next free port instead.\n")
    port = p
    app.start_sync()
    url = f"http://127.0.0.1:{port}/"
    print(f"Armariu review app running at {url}\nLeave this window open while you use it. "
          "Press Ctrl+C to stop.")
    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m radar.ui")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--no-browser", action="store_true")
    p.add_argument("--no-sync", action="store_true", help="don't pull from GitHub (offline use)")
    args = p.parse_args(argv)
    serve(args.port, not args.no_browser, not args.no_sync)
