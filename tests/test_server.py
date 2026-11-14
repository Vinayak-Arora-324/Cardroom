import collections
import json
import os
import stat
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
TMP = tempfile.mkdtemp()
os.environ["AITABLE_SETTINGS"] = os.path.join(TMP, "settings.json")
os.environ["AITABLE_HOST_TOKEN"] = os.path.join(TMP, "host_token")

import server  # noqa: E402
from table import config  # noqa: E402
from table.poker import Holdem  # noqa: E402

HOLDEM_OPTIONS = {"hand_pause_seconds": 0}
DEVICE_A = "device-aaaaaaaaaaaaaaaa"
DEVICE_B = "device-bbbbbbbbbbbbbbbb"


def call(base, method, path, host=None, seat=None, body=None, device=DEVICE_A, extra=None):
    """One request to the server. Returns (status, parsed JSON). device=None sends no device header."""
    headers = {}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if host:
        headers["X-Cardroom-Host"] = host
    if seat:
        headers["X-Cardroom-Seat"] = seat
    if device is not None:
        headers["X-Cardroom-Device"] = device
    headers.update(extra or {})
    req = urllib.request.Request(base + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        finally:
            e.close()


class ServerTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        config.update({"ai_pace_seconds": 0})
        server.HOST_TOKEN_PATH = os.environ["AITABLE_HOST_TOKEN"]
        server._host_token = None
        cls.host = server.host_token()
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.httpd.daemon_threads = True
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def setUp(self):
        # Each test starts with no seat claims, no failed sign-ins and the default proxy settings.
        server._seat_auth = ({}, None)
        server._seat_claims = {}
        server._failures.clear()
        server.TRUST_PROXY_HEADERS = False
        server.PUBLIC_URL = None

    def tearDown(self):
        call(self.base, "POST", "/api/game/stop", host=self.host, body={})
        server.PUBLIC_URL = None
        server.TRUST_PROXY_HEADERS = False

    def start(self, seats, game="holdem"):
        return call(self.base, "POST", "/api/game/start", host=self.host,
                    body={"game": game, "seats": seats, "options": dict(HOLDEM_OPTIONS)})

    def two_humans(self):
        """Host plus one guest, with two bots. Returns the start response."""
        seats = [
            {"kind": "human", "name": "Host", "host": True},
            {"kind": "human", "name": "Ann"},
            {"kind": "bot", "name": "Bot1"},
            {"kind": "bot", "name": "Bot2"},
        ]
        status, resp = self.start(seats)
        self.assertEqual(status, 200, resp)
        return resp

    def invite(self, resp, pid):
        return next(i["token"] for i in resp["invites"] if i["pid"] == pid)


class TestAuth(ServerTestCase):
    def test_host_token_file_is_private(self):
        path = server.HOST_TOKEN_PATH
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        with open(path) as f:
            self.assertEqual(f.read().strip(), self.host)

    def test_no_token_is_401(self):
        self.assertEqual(call(self.base, "GET", "/api/state")[0], 401)
        self.assertEqual(call(self.base, "GET", "/api/settings")[0], 401)
        self.assertEqual(call(self.base, "GET", "/api/whoami")[0], 401)
        self.assertEqual(call(self.base, "POST", "/api/game/start", body={"game": "holdem"})[0], 401)
        self.assertEqual(call(self.base, "GET", "/api/state", host="wrong", seat="also-wrong")[0], 401)

    def test_static_files_stay_public(self):
        with urllib.request.urlopen(self.base + "/", timeout=30) as r:
            self.assertEqual(r.status, 200)

    def test_guest_cannot_use_host_routes(self):
        resp = self.two_humans()
        guest = self.invite(resp, 1)
        self.assertEqual(call(self.base, "GET", "/api/settings", seat=guest)[0], 403)
        self.assertEqual(call(self.base, "POST", "/api/settings", seat=guest, body={})[0], 403)
        self.assertEqual(call(self.base, "POST", "/api/test-seat", seat=guest, body={})[0], 403)
        self.assertEqual(call(self.base, "GET", "/api/invites", seat=guest)[0], 403)
        self.assertEqual(call(self.base, "POST", "/api/game/stop", seat=guest, body={})[0], 403)
        status, _ = call(self.base, "POST", "/api/game/start", seat=guest,
                         body={"game": "holdem", "seats": [{"kind": "bot"}, {"kind": "bot"}]})
        self.assertEqual(status, 403)
        # The refused start changed nothing: the guest keeps the same seat.
        self.assertEqual(call(self.base, "GET", "/api/whoami", seat=guest)[1]["seat"], 1)

    def test_invite_link_survives_a_new_game_while_the_seat_stays_human(self):
        old = self.invite(self.two_humans(), 1)
        self.assertEqual(self.invite(self.two_humans(), 1), old)
        self.assertEqual(call(self.base, "GET", "/api/state", seat=old)[1]["you"], 1)

    def test_seat_token_dies_when_the_seat_is_no_longer_human(self):
        old = self.invite(self.two_humans(), 1)
        self.start([{"kind": "human", "name": "Host", "host": True}, {"kind": "bot"}])
        self.assertEqual(call(self.base, "GET", "/api/state", seat=old)[0], 401)

    def test_stale_seat_token_falls_back_to_host(self):
        self.two_humans()
        status, body = call(self.base, "GET", "/api/whoami", host=self.host, seat="stale")
        self.assertEqual(status, 200)
        self.assertEqual(body, {"host": True, "seat": 0, "seat_name": "Host"})


class TestGame(ServerTestCase):
    def test_host_starts_game_and_gets_invites(self):
        resp = self.two_humans()
        self.assertTrue(resp["ok"])
        self.assertIn("version", resp)
        invites = resp["invites"]
        self.assertEqual(len(invites), 2)
        host_seat, guest_seat = invites
        self.assertEqual((host_seat["pid"], host_seat["name"], host_seat["host"]), (0, "Host", True))
        self.assertEqual((guest_seat["pid"], guest_seat["name"], guest_seat["host"]), (1, "Ann", False))
        self.assertNotEqual(host_seat["token"], guest_seat["token"])
        self.assertNotEqual(guest_seat["token"], self.host)

    def test_invites_endpoint_matches_start(self):
        resp = self.two_humans()
        status, body = call(self.base, "GET", "/api/invites", host=self.host)
        self.assertEqual(status, 200)
        self.assertEqual(body["invites"], resp["invites"])
        self.assertEqual(body["base_urls"], [f"http://127.0.0.1:{self.httpd.server_address[1]}"])
        self.assertFalse(body["lan"])

    def test_only_one_host_seat(self):
        seats = [
            {"kind": "human", "name": "A", "host": True},
            {"kind": "human", "name": "B", "host": True},
        ]
        status, body = self.start(seats)
        self.assertEqual(status, 400)
        self.assertIn("host", body["error"])

    def test_guest_state_and_whoami_use_their_seat(self):
        resp = self.two_humans()
        guest = self.invite(resp, 1)
        status, state = call(self.base, "GET", "/api/state", seat=guest)
        self.assertEqual(status, 200)
        self.assertEqual(state["you"], 1)
        self.assertEqual(state["game"], Holdem.game_name)
        self.assertEqual(call(self.base, "GET", "/api/state", host=self.host)[1]["you"], 0)
        self.assertEqual(call(self.base, "GET", "/api/whoami", seat=guest)[1],
                         {"host": False, "seat": 1, "seat_name": "Ann"})
        self.assertEqual(call(self.base, "GET", "/api/whoami", host=self.host)[1],
                         {"host": True, "seat": 0, "seat_name": "Host"})

    def test_guest_chat_speaks_as_their_seat(self):
        resp = self.two_humans()
        guest = self.invite(resp, 1)
        self.assertEqual(call(self.base, "POST", "/api/chat", seat=guest, body={"text": "hello table"})[0], 200)
        _, state = call(self.base, "GET", "/api/state", host=self.host)
        self.assertIn({"name": "Ann", "pid": 1, "text": "hello table"}, [
            {k: c[k] for k in ("name", "pid", "text")} for c in state["chat"]
        ])

    def test_guest_action_is_bound_to_their_seat(self):
        resp = self.two_humans()
        guest = self.invite(resp, 1)
        status, _ = call(self.base, "POST", "/api/action", seat=guest, body={"action": "fold"})
        self.assertIn(status, (200, 400))  # 400 if it is not their turn yet; never an auth error

    def test_host_without_a_seat_cannot_act(self):
        seats = [{"kind": "bot", "name": "Bot1"}, {"kind": "bot", "name": "Bot2"}]
        status, body = self.start(seats)
        self.assertEqual(status, 200, body)
        self.assertEqual(body["invites"], [])
        self.assertEqual(call(self.base, "GET", "/api/whoami", host=self.host)[1]["seat"], None)
        self.assertEqual(call(self.base, "POST", "/api/action", host=self.host, body={"action": "fold"})[0], 403)
        self.assertEqual(call(self.base, "POST", "/api/chat", host=self.host, body={"text": "hi"})[0], 403)
        self.assertEqual(call(self.base, "GET", "/api/state", host=self.host)[0], 200)

    def test_state_without_game(self):
        call(self.base, "POST", "/api/game/stop", host=self.host, body={})
        server._game = None
        status, body = call(self.base, "GET", "/api/state", host=self.host)
        self.assertEqual((status, body), (200, {"game": None}))


class TestSeatClaims(ServerTestCase):
    def test_first_device_claims_a_seat(self):
        guest = self.invite(self.two_humans(), 1)
        self.assertEqual(call(self.base, "GET", "/api/state", seat=guest, device=DEVICE_A)[0], 200)
        invites = call(self.base, "GET", "/api/invites", host=self.host)[1]["invites"]
        self.assertEqual([i["claimed"] for i in invites], [False, True])

    def test_second_device_gets_409(self):
        guest = self.invite(self.two_humans(), 1)
        self.assertEqual(call(self.base, "GET", "/api/state", seat=guest, device=DEVICE_A)[0], 200)
        status, body = call(self.base, "GET", "/api/state", seat=guest, device=DEVICE_B)
        self.assertEqual(status, 409)
        self.assertEqual(body["code"], "seat_taken")
        self.assertIn("another device", body["error"])
        self.assertEqual(call(self.base, "GET", "/api/whoami", seat=guest, device=DEVICE_B)[0], 409)

    def test_same_device_keeps_working(self):
        guest = self.invite(self.two_humans(), 1)
        for _ in range(3):
            self.assertEqual(call(self.base, "GET", "/api/whoami", seat=guest, device=DEVICE_A)[1]["seat"], 1)

    def test_missing_or_malformed_device_is_401(self):
        guest = self.invite(self.two_humans(), 1)
        self.assertEqual(call(self.base, "GET", "/api/state", seat=guest, device=None)[0], 401)
        self.assertEqual(call(self.base, "GET", "/api/state", seat=guest, device="short")[0], 401)
        self.assertEqual(call(self.base, "GET", "/api/state", seat=guest, device="bad device id!!!!")[0], 401)
        # The refused attempts claimed nothing, so a real device can still take the seat.
        self.assertEqual(call(self.base, "GET", "/api/state", seat=guest, device=DEVICE_A)[0], 200)

    def test_host_fallback_needs_no_device(self):
        self.two_humans()
        self.assertEqual(call(self.base, "GET", "/api/whoami", host=self.host, device=None)[1]["seat"], 0)

    def test_reissue_replaces_the_link_and_frees_the_seat(self):
        old = self.invite(self.two_humans(), 1)
        self.assertEqual(call(self.base, "GET", "/api/state", seat=old, device=DEVICE_A)[0], 200)
        status, body = call(self.base, "POST", "/api/invites/reissue", host=self.host, body={"pid": 1})
        self.assertEqual(status, 200, body)
        new = body["invite"]
        self.assertTrue(body["ok"])
        self.assertEqual((new["pid"], new["name"], new["host"], new["claimed"]), (1, "Ann", False, False))
        self.assertNotEqual(new["token"], old)
        self.assertEqual(call(self.base, "GET", "/api/state", seat=old, device=DEVICE_A)[0], 401)
        # The seat is free again: a new device can claim it with the new link.
        self.assertEqual(call(self.base, "GET", "/api/state", seat=new["token"], device=DEVICE_B)[1]["you"], 1)
        self.assertEqual(call(self.base, "GET", "/api/state", seat=new["token"], device=DEVICE_A)[0], 409)

    def test_reissue_is_host_only(self):
        guest = self.invite(self.two_humans(), 1)
        self.assertEqual(call(self.base, "POST", "/api/invites/reissue", seat=guest, body={"pid": 1})[0], 403)
        self.assertEqual(call(self.base, "GET", "/api/whoami", seat=guest)[1]["seat"], 1)

    def test_reissue_needs_a_seat_with_a_link(self):
        self.two_humans()
        self.assertEqual(call(self.base, "POST", "/api/invites/reissue", host=self.host, body={"pid": 9})[0], 400)

    def test_claims_survive_restart_for_carried_over_seats(self):
        guest = self.invite(self.two_humans(), 1)
        self.assertEqual(call(self.base, "GET", "/api/state", seat=guest, device=DEVICE_A)[0], 200)
        resp = self.two_humans()
        self.assertEqual(self.invite(resp, 1), guest)
        self.assertEqual(call(self.base, "GET", "/api/state", seat=guest, device=DEVICE_B)[0], 409)
        self.assertEqual(call(self.base, "GET", "/api/state", seat=guest, device=DEVICE_A)[0], 200)

    def test_claim_is_dropped_when_the_seat_is_not_human_next_game(self):
        guest = self.invite(self.two_humans(), 1)
        self.assertEqual(call(self.base, "GET", "/api/state", seat=guest, device=DEVICE_A)[0], 200)
        self.start([{"kind": "human", "name": "Host", "host": True}, {"kind": "bot"}])
        resp = self.start([{"kind": "human", "name": "Host", "host": True}, {"kind": "human", "name": "Ann"}])
        new = self.invite(resp[1], 1)
        self.assertNotEqual(new, guest)
        self.assertEqual(call(self.base, "GET", "/api/state", seat=new, device=DEVICE_B)[1]["you"], 1)

    def test_too_many_failures_is_429_but_host_still_allowed(self):
        guest = self.invite(self.two_humans(), 1)
        for _ in range(server.FAIL_LIMIT):
            self.assertEqual(call(self.base, "GET", "/api/state", seat="nope", device=DEVICE_A)[0], 401)
        status, body = call(self.base, "GET", "/api/state", seat="nope", device=DEVICE_A)
        self.assertEqual(status, 429)
        self.assertEqual(body["error"], "Too many attempts. Try again in a minute.")
        # Even a valid guest on the same address is held back, but the host never is.
        self.assertEqual(call(self.base, "GET", "/api/state", seat=guest, device=DEVICE_A)[0], 429)
        self.assertEqual(call(self.base, "GET", "/api/state", host=self.host)[0], 200)

    def test_seat_taken_counts_as_a_failure(self):
        guest = self.invite(self.two_humans(), 1)
        call(self.base, "GET", "/api/state", seat=guest, device=DEVICE_A)
        for _ in range(server.FAIL_LIMIT):
            self.assertEqual(call(self.base, "GET", "/api/state", seat=guest, device=DEVICE_B)[0], 409)
        self.assertEqual(call(self.base, "GET", "/api/state", seat=guest, device=DEVICE_B)[0], 429)

    def test_cf_connecting_ip_is_ignored_unless_trusted(self):
        for _ in range(server.FAIL_LIMIT):
            call(self.base, "GET", "/api/state", seat="nope", device=DEVICE_A,
                 extra={"CF-Connecting-IP": "203.0.113.1"})
        # Rotating the forwarded address does not dodge the limit while proxy headers are not trusted.
        self.assertEqual(call(self.base, "GET", "/api/state", seat="nope", device=DEVICE_A,
                              extra={"CF-Connecting-IP": "203.0.113.2"})[0], 429)
        server.TRUST_PROXY_HEADERS = True
        self.assertEqual(call(self.base, "GET", "/api/state", seat="nope", device=DEVICE_A,
                              extra={"CF-Connecting-IP": "203.0.113.3"})[0], 401)

    def test_client_key_uses_the_socket_address_by_default(self):
        class FakeHandler:
            client_address = ("192.0.2.7", 5000)
            headers = {"CF-Connecting-IP": "198.51.100.4"}
        self.assertEqual(server.client_key(FakeHandler()), "192.0.2.7")
        server.TRUST_PROXY_HEADERS = True
        self.assertEqual(server.client_key(FakeHandler()), "198.51.100.4")

    def test_failure_table_forgets_quiet_addresses_when_it_grows(self):
        old = time.monotonic() - server.FAIL_WINDOW - 5
        for i in range(1001):
            server._failures[f"10.0.{i // 256}.{i % 256}"] = collections.deque([old])
        server.record_failure("192.0.2.9")
        self.assertEqual(list(server._failures), ["192.0.2.9"])


class TestPublicUrlRequests(ServerTestCase):
    PUBLIC = "https://abc-def-ghi.trycloudflare.com"

    def test_guest_post_from_the_public_origin_is_accepted_when_host_is_rewritten(self):
        server.PUBLIC_URL = self.PUBLIC
        guest = self.invite(self.two_humans(), 1)
        # urllib sends Host: 127.0.0.1:<port>, as a proxy that rewrites Host would.
        status, _ = call(self.base, "POST", "/api/chat", seat=guest, body={"text": "hi"},
                         extra={"Origin": self.PUBLIC})
        self.assertEqual(status, 200)

    def test_other_origins_are_still_refused(self):
        server.PUBLIC_URL = self.PUBLIC
        guest = self.invite(self.two_humans(), 1)
        status, _ = call(self.base, "POST", "/api/chat", seat=guest, body={"text": "hi"},
                         extra={"Origin": "https://evil.example"})
        self.assertEqual(status, 403)


class FakeRequest:
    def __init__(self, headers):
        self.headers = headers


class TestPublicUrl(unittest.TestCase):
    PUBLIC = "https://abc-def-ghi.trycloudflare.com"

    def setUp(self):
        server.PUBLIC_URL = None
        server.TRUST_PROXY_HEADERS = False

    def tearDown(self):
        server.PUBLIC_URL = None
        server.TRUST_PROXY_HEADERS = False

    def test_public_url_alone_when_bound_to_this_computer(self):
        server.PUBLIC_URL = self.PUBLIC
        self.assertEqual(server.base_urls("127.0.0.1", 9), ([self.PUBLIC], True))

    def test_public_url_first_on_lan_without_duplicates(self):
        server.PUBLIC_URL = self.PUBLIC
        urls, lan = server.base_urls("0.0.0.0", 9)
        self.assertTrue(lan)
        self.assertEqual(urls[0], self.PUBLIC)
        self.assertEqual(len(urls), len(set(urls)))

    def test_same_origin_accepts_the_public_origin_with_any_host(self):
        server.PUBLIC_URL = self.PUBLIC
        req = FakeRequest({"Origin": self.PUBLIC, "Host": "127.0.0.1:8765"})
        self.assertTrue(server.Handler.same_origin(req))

    def test_same_origin_rejects_other_origins_and_schemes(self):
        server.PUBLIC_URL = self.PUBLIC
        for origin in ("https://evil.example", "http://abc-def-ghi.trycloudflare.com", "null"):
            req = FakeRequest({"Origin": origin, "Host": "127.0.0.1:8765"})
            self.assertFalse(server.Handler.same_origin(req), origin)

    def test_same_origin_unchanged_without_public_url(self):
        req = FakeRequest({"Origin": self.PUBLIC, "Host": "127.0.0.1:8765"})
        self.assertFalse(server.Handler.same_origin(req))
        self.assertTrue(server.Handler.same_origin(FakeRequest({"Host": "127.0.0.1:8765"})))
        self.assertTrue(server.Handler.same_origin(FakeRequest({"Origin": "http://127.0.0.1:8765", "Host": "127.0.0.1:8765"})))


class TestBaseUrls(unittest.TestCase):
    def test_loopback_bind(self):
        self.assertEqual(server.base_urls("127.0.0.1", 9), (["http://127.0.0.1:9"], False))

    def test_lan_bind_lists_loopback_last_without_duplicates(self):
        urls, lan = server.base_urls("0.0.0.0", 9)
        self.assertTrue(lan)
        self.assertEqual(urls[-1], "http://127.0.0.1:9")
        self.assertEqual(len(urls), len(set(urls)))
        self.assertTrue(all(u.startswith("http://") and u.endswith(":9") for u in urls))

    def test_specific_address(self):
        self.assertEqual(server.base_urls("192.168.1.5", 9), (["http://192.168.1.5:9"], True))


if __name__ == "__main__":
    unittest.main()
