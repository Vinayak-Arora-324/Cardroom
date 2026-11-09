#!/usr/bin/env python3
"""AI Card Table: local web server (standard library only).

    python3 server.py            # then open http://127.0.0.1:8765
    python3 server.py --port 9000 --no-browser
"""
import argparse
import json
import mimetypes
import os
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from table import agents, config, personas
from table.blackjack import Blackjack
from table.bridge import Bridge
from table.poker import Holdem

ROOT = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(ROOT, "static")
GAMES = {"holdem": Holdem, "blackjack": Blackjack, "bridge": Bridge}
PLAYER_LIMITS = {"holdem": (2, 7), "blackjack": (2, 7), "bridge": (4, 4)}

_game = None
_game_lock = threading.Lock()


def current_game():
    return _game


def start_game(body):
    global _game
    kind = body.get("game")
    if kind not in GAMES:
        raise ValueError("unknown game")
    seats = body.get("seats") or []
    low, high = PLAYER_LIMITS[kind]
    if not (low <= len(seats) <= high):
        raise ValueError(f"need exactly {low} players" if low == high else f"need {low}-{high} players")
    if sum(1 for s in seats if s.get("kind") == "human") > 1:
        raise ValueError("only one human seat is supported")
    for s in seats:
        if s.get("kind") not in ("human", *agents.BACKENDS):
            raise ValueError(f"unknown seat type {s.get('kind')}")
    with _game_lock:
        if _game:
            _game.stop()
        _game = GAMES[kind](seats, body.get("options") or {})
        _game.start()
    return _game


class Handler(BaseHTTPRequestHandler):
    server_version = "AICardTable/1.0"

    def log_message(self, fmt, *args):  # keep the console quiet
        if os.environ.get("AITABLE_DEBUG"):
            super().log_message(fmt, *args)

    # ---------------------------------------------------------------- helpers
    def send_json(self, obj, status=200):
        data = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        return json.loads(self.rfile.read(length) or b"{}")

    def same_origin(self):
        """Reject cross-site POSTs (another website poking your local server)."""
        origin = self.headers.get("Origin")
        if not origin:
            return True
        host = self.headers.get("Host", "")
        return urlparse(origin).netloc == host

    # ---------------------------------------------------------------- GET
    def do_GET(self):
        url = urlparse(self.path)
        path = url.path
        if path == "/api/state":
            g = current_game()
            if not g:
                return self.send_json({"game": None})
            since = int(parse_qs(url.query).get("v", ["-1"])[0])
            if since >= 0:  # long-poll: wait until something changes
                g.wait_version(since, timeout=20)
            return self.send_json(g.snapshot())
        if path == "/api/settings":
            return self.send_json({"settings": config.public(), "clis": agents.detect_clis(), "catalog": agents.model_catalog(), "personas": personas.menu()})
        if path == "/api/openrouter/models":
            return self.send_json({"models": agents.openrouter_models()})
        if path == "/" or path == "/index.html":
            path = "/index.html"
        file = os.path.normpath(os.path.join(STATIC, path.lstrip("/")))
        if not file.startswith(STATIC) or not os.path.isfile(file):
            return self.send_json({"error": "not found"}, 404)
        with open(file, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(file)[0] or "application/octet-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    # ---------------------------------------------------------------- POST
    def do_POST(self):
        if not self.same_origin():
            return self.send_json({"error": "cross-origin request refused"}, 403)
        path = urlparse(self.path).path
        try:
            body = self.read_json()
            g = current_game()
            if path == "/api/game/start":
                game = start_game(body)
                return self.send_json({"ok": True, "version": game.version})
            if path == "/api/game/stop":
                if g:
                    g.stop()
                return self.send_json({"ok": True})
            if path == "/api/action":
                if not g:
                    raise ValueError("no game running")
                g.submit_human(body)
                return self.send_json({"ok": True})
            if path == "/api/chat":
                if g:
                    g.human_chat(str(body.get("text", "")))
                return self.send_json({"ok": True})
            if path == "/api/settings":
                config.update(body)
                return self.send_json({"settings": config.public(), "clis": agents.detect_clis()})
            if path == "/api/test-seat":
                return self.send_json(agents.test_seat(body))
            return self.send_json({"error": "not found"}, 404)
        except ValueError as e:
            return self.send_json({"error": str(e)}, 400)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1", help="bind address (default 127.0.0.1: only this laptop)")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.daemon_threads = True
    url = f"http://{'127.0.0.1' if args.host in ('0.0.0.0', '') else args.host}:{args.port}"
    print(f"AI Card Table running at {url}  (Ctrl+C to quit)")
    clis = agents.detect_clis()
    for kind in ("claude", "codex"):
        info = clis[kind]
        print(f"  {kind:<7} {'found: ' + info['version'] if info['path'] else 'not found on PATH'}")
    print(f"  openrouter key {'set' if clis['openrouter']['key_set'] else 'not set (add it in Settings)'}")
    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nBye!")
        if _game:
            _game.stop()
        sys.exit(0)


if __name__ == "__main__":
    main()
