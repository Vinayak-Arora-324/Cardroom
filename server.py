#!/usr/bin/env python3
"""AI Card Table: local web server (standard library only).

    python3 server.py            # then open the host link it prints
    python3 server.py --lan      # also let friends on your LAN / tailnet join with invite links
    python3 server.py --tunnel   # friends join over HTTPS from anywhere (Cloudflare quick tunnel)
    python3 server.py --port 9000 --no-browser

Two kinds of callers:
  * the host (X-Cardroom-Host header, secret in the git-ignored `host_token` file) runs the table;
  * guests (X-Cardroom-Seat header, one secret per human seat, made at each game start) can only
    watch, chat and play their own seat. They never reach settings, game control or the AI CLIs.
    A seat link works on one device only: the first device that uses it claims the seat
    (X-Cardroom-Device header, a random id the browser keeps). The host can reissue a link.
"""
import argparse
import atexit
import collections
import hmac
import json
import mimetypes
import os
import re
import secrets
import signal
import socket
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from table import agents, config, personas, tunnel
from table.blackjack import Blackjack
from table.bridge import Bridge
from table.poker import Holdem

ROOT = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(ROOT, "static")
HOST_TOKEN_PATH = os.environ.get("AITABLE_HOST_TOKEN") or os.path.join(ROOT, "host_token")
HOST_HEADER = "X-Cardroom-Host"
SEAT_HEADER = "X-Cardroom-Seat"
DEVICE_HEADER = "X-Cardroom-Device"
DEVICE_RE = re.compile(r"[A-Za-z0-9_-]{16,128}")
GAMES = {"holdem": Holdem, "blackjack": Blackjack, "bridge": Bridge}
PLAYER_LIMITS = {"holdem": (2, 7), "blackjack": (2, 7), "bridge": (4, 4)}
# Routes only the host may use (checked in GateHandler.gate).
HOST_ONLY = {
    ("GET", "/api/settings"), ("POST", "/api/settings"), ("POST", "/api/test-seat"),
    ("POST", "/api/game/start"), ("POST", "/api/game/stop"), ("GET", "/api/invites"),
    ("POST", "/api/invites/reissue"),
}
# Only set True when the server sits behind a proxy that sets CF-Connecting-IP (a Cloudflare tunnel).
TRUST_PROXY_HEADERS = False
# The address friends open (a tunnel, or your own tailscale serve / proxy), or None for LAN addresses only.
PUBLIC_URL = None
FAIL_LIMIT = 20  # failed sign-ins per client...
FAIL_WINDOW = 60  # ...within this many seconds

_game = None
_game_lock = threading.Lock()
# Seat tokens of the current game as ({token: pid}, host_pid). Replaced as one object on each start.
_seat_auth = ({}, None)
# Device that claimed each guest seat ({pid: device id}). Guards claims, _seat_auth and _failures.
_seat_claims = {}
_failures = {}  # client key -> deque of time.monotonic() stamps of recent failed sign-ins
_auth_lock = threading.Lock()
_host_token = None
_token_lock = threading.Lock()


def current_game():
    return _game


# ---------------------------------------------------------------- tokens
def host_token():
    """The host's secret. Created once in HOST_TOKEN_PATH, readable only by this user."""
    global _host_token
    with _token_lock:
        if _host_token is None:
            try:
                with open(HOST_TOKEN_PATH) as f:
                    _host_token = f.read().strip()
            except FileNotFoundError:
                _host_token = ""
            if not _host_token:
                _host_token = secrets.token_urlsafe(32)
                fd = os.open(HOST_TOKEN_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, "w") as f:
                    f.write(_host_token + "\n")
            os.chmod(HOST_TOKEN_PATH, 0o600)
        return _host_token


def same_secret(given, expected):
    return bool(given) and bool(expected) and hmac.compare_digest(given.encode(), expected.encode())


def is_host_request(headers):
    return same_secret(headers.get(HOST_HEADER, ""), host_token())


def resolve_identity(headers):
    """Who is calling? Returns (is_host, seat, problem). Used by every /api/* request.

    seat is the pid from a valid seat token, granted only to the device that claimed the seat
    (the first device to use it). problem is None, "seat_taken" (another device holds the seat)
    or "no_device" (the device header is missing or malformed); in both cases the seat is None.
    A host without a (valid) seat token sits in the host's own seat, which is None when no
    human seat is the host's.
    """
    is_host = is_host_request(headers)
    seat = None
    problem = None
    presented = headers.get(SEAT_HEADER, "")
    device = headers.get(DEVICE_HEADER, "")
    with _auth_lock:
        tokens, host_pid = _seat_auth
        if presented:
            for token, pid in tokens.items():
                if same_secret(presented, token):
                    seat = pid
        if seat is not None:
            if not DEVICE_RE.fullmatch(device):
                seat, problem = None, "no_device"
            elif _seat_claims.setdefault(seat, device) != device:
                seat, problem = None, "seat_taken"
    if seat is None and is_host:
        seat = host_pid
    return is_host, seat, problem


def client_key(handler):
    """The address to count failed sign-ins against."""
    if TRUST_PROXY_HEADERS:
        forwarded = handler.headers.get("CF-Connecting-IP", "").strip()
        if forwarded:
            return forwarded
    return handler.client_address[0]


def _prune(stamps, now):
    while stamps and now - stamps[0] > FAIL_WINDOW:
        stamps.popleft()
    return stamps


def too_many_failures(key):
    with _auth_lock:
        stamps = _failures.get(key)
        return bool(stamps) and len(_prune(stamps, time.monotonic())) >= FAIL_LIMIT


def record_failure(key):
    now = time.monotonic()
    with _auth_lock:
        _prune(_failures.setdefault(key, collections.deque()), now).append(now)
        if len(_failures) > 1000:  # keep the table small: forget addresses with nothing recent
            for stale in [k for k, stamps in _failures.items() if not _prune(stamps, now)]:
                del _failures[stale]


# ---------------------------------------------------------------- game control
def start_game(body):
    """Start a game from a request body. Returns (game, invites)."""
    global _game, _seat_auth, _seat_claims
    kind = body.get("game")
    if kind not in GAMES:
        raise ValueError("unknown game")
    seats = body.get("seats") or []
    low, high = PLAYER_LIMITS[kind]
    if not (low <= len(seats) <= high):
        raise ValueError(f"need exactly {low} players" if low == high else f"need {low}-{high} players")
    for s in seats:
        if s.get("kind") not in ("human", *agents.BACKENDS):
            raise ValueError(f"unknown seat type {s.get('kind')}")
        if s.get("host") and s.get("kind") != "human":
            raise ValueError("the host seat must be a human seat")
    if sum(1 for s in seats if s.get("host")) > 1:
        raise ValueError("only one seat can be the host's")

    humans = [pid for pid, s in enumerate(seats) if s.get("kind") == "human"]
    host_pid = next((pid for pid in humans if seats[pid].get("host")), humans[0] if humans else None)
    # A seat that was human last game keeps its invite link, so friends don't need a new one per game.
    previous = {pid: token for token, pid in _seat_auth[0].items()}
    tokens = {previous.get(pid) or secrets.token_urlsafe(18): pid for pid in humans}
    # A carried-over seat keeps the device that claimed it; a seat that was not human last game is unclaimed.
    carried = {pid for pid in humans if pid in previous}

    with _game_lock:
        if _game:
            _game.stop()
        game = GAMES[kind](seats, body.get("options") or {})
        with _auth_lock:
            _seat_auth = (tokens, host_pid)
            _seat_claims = {pid: dev for pid, dev in _seat_claims.items() if pid in carried}
        _game = game
        game.start()
    return game, invites_for(game, tokens, host_pid)


def reissue_invite(pid):
    """Replace one guest seat's link. The old link stops working at once and the seat is unclaimed."""
    global _seat_auth, _seat_claims
    game = current_game()
    if not game:
        raise ValueError("no game running")
    with _auth_lock:
        tokens, host_pid = _seat_auth
        old = next((token for token, owner in tokens.items() if owner == pid), None)
        if old is None:
            raise ValueError("that seat has no invite link")
        tokens = {token: owner for token, owner in tokens.items() if token != old}
        tokens[secrets.token_urlsafe(18)] = pid
        _seat_claims = {owner: dev for owner, dev in _seat_claims.items() if owner != pid}
        _seat_auth = (tokens, host_pid)
    return next(i for i in invites_for(game, tokens, host_pid) if i["pid"] == pid)


def invites_for(game, tokens, host_pid):
    with _auth_lock:
        claimed = set(_seat_claims)
    return [
        {"pid": pid, "name": game.players[pid].name, "token": token, "host": pid == host_pid,
         "claimed": pid in claimed}
        for token, pid in sorted(tokens.items(), key=lambda kv: kv[1])
    ]


def lan_ips():
    """This machine's non-loopback IPv4 addresses (stdlib only, nothing is sent)."""
    ips = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("10.255.255.255", 1))  # UDP connect only picks the outgoing interface
            ips.append(s.getsockname()[0])
        finally:
            s.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.append(info[4][0])
    except OSError:
        pass
    return [ip for ip in dict.fromkeys(ips) if ip != "0.0.0.0" and not ip.startswith("127.")]


def base_urls(bind_host, port):
    """Addresses friends can open, and whether they reach this server over the network."""
    urls, reachable = bind_urls(bind_host, port)
    if PUBLIC_URL:  # the public address goes first: it is the default in the lobby
        # Bound to this computer only (a tunnel), the local address is no use to friends: offer just the public one.
        rest = [u for u in urls if u != PUBLIC_URL] if reachable else []
        return [PUBLIC_URL] + rest, True
    return urls, reachable


def bind_urls(bind_host, port):
    if bind_host in ("0.0.0.0", ""):
        return [f"http://{ip}:{port}" for ip in lan_ips()] + [f"http://127.0.0.1:{port}"], True
    if bind_host == "localhost" or bind_host.startswith("127."):
        return [f"http://127.0.0.1:{port}"], False
    return [f"http://{bind_host}:{port}"], True


# ---------------------------------------------------------------- HTTP
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
        if urlparse(origin).netloc == host:
            return True
        # Behind a tunnel or proxy the Host header may be rewritten, so also accept the public address.
        if not PUBLIC_URL:
            return False
        given, public = urlparse(origin), urlparse(PUBLIC_URL)
        return (given.scheme, given.netloc) == (public.scheme, public.netloc)

    def gate(self, method, path):
        """Auth for /api/* routes. Returns (is_host, seat), or None after sending a 401/403/409/429."""
        key = client_key(self)
        if not is_host_request(self.headers) and too_many_failures(key):
            self.send_json({"error": "Too many attempts. Try again in a minute."}, 429)
            return None
        is_host, seat, problem = resolve_identity(self.headers)
        if not is_host and seat is None:
            record_failure(key)
            if problem == "seat_taken":
                self.send_json({"error": "This seat is already open on another device. Ask the host for a new link.",
                                "code": "seat_taken"}, 409)
            else:
                self.send_json({"error": "invite required"}, 401)
            return None
        if (method, path) in HOST_ONLY and not is_host:
            self.send_json({"error": "only the host can do that"}, 403)
            return None
        return is_host, seat

    # ---------------------------------------------------------------- GET
    def do_GET(self):
        url = urlparse(self.path)
        path = url.path
        if path.startswith("/api/"):
            who = self.gate("GET", path)
            if who is None:
                return
            is_host, seat = who
            g = current_game()
            if path == "/api/state":
                if not g:
                    return self.send_json({"game": None})
                since = int(parse_qs(url.query).get("v", ["-1"])[0])
                if since >= 0:  # long-poll: wait until something changes
                    g.wait_version(since, timeout=20)
                return self.send_json(g.snapshot(viewer=seat))
            if path == "/api/whoami":
                name = g.players[seat].name if g and seat is not None and seat < len(g.players) else None
                return self.send_json({"host": is_host, "seat": seat, "seat_name": name})
            if path == "/api/invites":
                tokens, host_pid = _seat_auth
                invites = invites_for(g, tokens, host_pid) if g else []
                base, lan = base_urls(self.server.server_address[0], self.server.server_address[1])
                return self.send_json({"invites": invites, "base_urls": base, "lan": lan})
            if path == "/api/settings":
                return self.send_json({"settings": config.public(), "clis": agents.detect_clis(), "catalog": agents.model_catalog(), "personas": personas.menu()})
            return self.send_json({"error": "not found"}, 404)

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
        if not path.startswith("/api/"):
            return self.send_json({"error": "not found"}, 404)
        who = self.gate("POST", path)
        if who is None:
            return
        _, seat = who
        try:
            if path == "/api/game/start":
                game, invites = start_game(self.read_json())
                return self.send_json({"ok": True, "version": game.version, "invites": invites})
            if path == "/api/game/stop":
                if current_game():
                    current_game().stop()
                return self.send_json({"ok": True})
            if path == "/api/action":
                if seat is None:
                    return self.send_json({"error": "you don't have a seat at this table"}, 403)
                g = current_game()
                if not g:
                    raise ValueError("no game running")
                g.submit_human(self.read_json(), pid=seat)
                return self.send_json({"ok": True})
            if path == "/api/chat":
                if seat is None:
                    return self.send_json({"error": "you don't have a seat at this table"}, 403)
                body = self.read_json()
                g = current_game()
                if g:
                    g.human_chat(str(body.get("text", "")), pid=seat)
                return self.send_json({"ok": True})
            if path == "/api/invites/reissue":
                invite = reissue_invite(self.read_json().get("pid"))
                return self.send_json({"ok": True, "invite": invite})
            if path == "/api/settings":
                config.update(self.read_json())
                return self.send_json({"settings": config.public(), "clis": agents.detect_clis(), "catalog": agents.model_catalog()})
            if path == "/api/test-seat":
                return self.send_json(agents.test_seat(self.read_json()))
            return self.send_json({"error": "not found"}, 404)
        except ValueError as e:
            return self.send_json({"error": str(e)}, 400)


def main():
    global PUBLIC_URL, TRUST_PROXY_HEADERS
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1", help="bind address (default 127.0.0.1: only this computer)")
    ap.add_argument("--lan", action="store_true", help="bind 0.0.0.0 so friends on your LAN or tailnet can join")
    ap.add_argument("--tunnel", action="store_true",
                    help="friends join over HTTPS from anywhere through a Cloudflare tunnel (needs cloudflared)")
    ap.add_argument("--public-url", metavar="URL",
                    help="the address friends use when you run your own tunnel or tailscale serve")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()
    if args.tunnel and args.lan:
        ap.error("--tunnel already reaches friends from anywhere; don't combine it with --lan")
    if args.tunnel and args.host != "127.0.0.1":
        ap.error("--tunnel only listens on 127.0.0.1; don't combine it with --host")
    if args.tunnel and args.public_url:
        ap.error("--tunnel makes its own public address; don't combine it with --public-url")
    if args.public_url:
        args.public_url = args.public_url.rstrip("/")
        if not re.fullmatch(r"https?://[^/\s]+", args.public_url):
            ap.error("--public-url must look like https://your-address (no path)")
    if args.tunnel and not tunnel.find_cloudflared():
        print("--tunnel needs cloudflared: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/  (Arch: sudo pacman -S cloudflared)")
        sys.exit(1)

    bind = "0.0.0.0" if args.lan else args.host
    server = ThreadingHTTPServer((bind, args.port), Handler)
    server.daemon_threads = True
    port = server.server_address[1]
    tunnel_proc = None
    if args.tunnel:
        print("  starting Cloudflare tunnel...")
        try:
            tunnel_proc, PUBLIC_URL = tunnel.start(port)
        except tunnel.TunnelError as e:
            server.server_close()
            print(f"--tunnel failed: {e}")
            sys.exit(1)
        atexit.register(tunnel.stop, tunnel_proc)
        # Every tunnel request reaches us from 127.0.0.1, so the real address is only in CF-Connecting-IP.
        TRUST_PROXY_HEADERS = True
    elif args.public_url:
        PUBLIC_URL = args.public_url  # our own tunnel or proxy: its headers are not trusted

    shown = "127.0.0.1" if bind in ("0.0.0.0", "") else bind
    local = f"http://{shown}:{port}"
    host_link = f"{local}/#host={host_token()}"
    print(f"AI Card Table running at {local}  (Ctrl+C to quit)")
    print(f"  host link (keep it private): {host_link}")
    if args.tunnel:
        print(f"  friends can open: {PUBLIC_URL}  (invite links are listed in the lobby; this address changes every run)")
        print("  host link stays on this computer. Never open or share it on the tunnel address.")
    if args.lan:
        urls, _ = base_urls("0.0.0.0", port)
        for url in urls[:-1]:
            print(f"  friends can open: {url}  (invite links are listed in the lobby)")
        print("  guests can only play their own seats, not change settings or use your accounts. Never share the host link.")
    elif args.public_url:
        print(f"  friends can open: {PUBLIC_URL}  (invite links are listed in the lobby)")
    clis = agents.detect_clis()
    for kind in ("claude", "codex", "grok", "opencode"):
        info = clis[kind]
        print(f"  {kind:<8} {'found: ' + info['version'] if info['path'] else 'not found on PATH'}")
    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(host_link)).start()
    atexit.register(agents.kill_running)

    def on_term(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, on_term)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nBye!")
        if _game:
            _game.stop()
        if tunnel_proc:
            tunnel.stop(tunnel_proc)
        sys.exit(0)


if __name__ == "__main__":
    main()
