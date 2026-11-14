import os
import random
import sys
import tempfile
import threading
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("AITABLE_SETTINGS", os.path.join(tempfile.mkdtemp(), "settings.json"))

from table import config  # noqa: E402
from table.bridge import Bridge  # noqa: E402

config.update({"ai_pace_seconds": 0})


def wait(game, timeout=120):
    end = time.time() + timeout
    while not game.finished and time.time() < end:
        time.sleep(0.02)


def one_notrump(legal):
    return "1NT" if "1NT" in legal else "Pass"


def answer(req):
    """What a human at the table does: bid 1NT when it is legal, otherwise play a random legal card."""
    if req["type"] == "bridge_bid":
        return {"call": one_notrump(req["legal"])}
    return {"card": random.choice(req["legal"])}


class HumanSeats:
    """Stands in for the humans: answers every pending request once, via submit_human(pid=...).

    Records each request it saw, with the contract's declarer and dummy at that moment.
    """

    def __init__(self, game, policy=answer):
        self.game = game
        self.policy = policy
        self.seen = []
        self.thread = threading.Thread(target=self.loop, daemon=True)

    def start(self):
        self.thread.start()

    def loop(self):
        g = self.game
        answered = {}
        while not g.finished and not g.stopped:
            with g.lock:
                pending = list(g.human_requests.items())
            for pid, req in pending:
                if answered.get(pid) is req:
                    continue
                answered[pid] = req
                c = g.contract
                self.seen.append({**req, "declarer": c["declarer"] if c else None, "dummy": c["dummy"] if c else None})
                try:
                    g.submit_human(self.policy(req), pid=pid)
                except ValueError:
                    pass
            time.sleep(0.002)


class HumanPartners(unittest.TestCase):
    def test_partners_play_every_board(self):
        # Seats 0 and 2 are humans (partners); the bots always pass, so every board is declared by a human
        # and the dummy is the other human. Dummy must never be asked for a card.
        specs = [{"kind": "human", "name": "Ann"}, {"kind": "bot", "name": "Bo"},
                 {"kind": "human", "name": "Cy"}, {"kind": "bot", "name": "Di"}]
        g = Bridge(specs, {"boards": 4, "hand_pause_seconds": 0})
        g.bot_call = lambda pid, legal: "Pass"
        humans = HumanSeats(g)
        humans.start()
        g.start()
        wait(g)
        self.assertTrue(g.finished)
        self.assertFalse(any(e["kind"] == "error" for e in g.log), list(g.log)[-5:])
        self.assertEqual(g.hand_no, 4)
        self.assertEqual(sum(g.tricks.values()), 13)

        play = [r for r in humans.seen if r["type"] == "bridge_play"]
        self.assertTrue(play)
        self.assertEqual({r["declarer"] for r in play}, {0, 2})
        for r in play:
            self.assertEqual(r["pid"], r["declarer"], r)
            self.assertNotEqual(r["pid"], r["dummy"], r)
        dummy_plays = [r for r in play if r["from_dummy"]]
        self.assertTrue(dummy_plays, "declarer should have played dummy's cards")
        for r in dummy_plays:
            self.assertEqual(r["hand_owner"], r["dummy"])
            self.assertEqual(r["pid"], r["declarer"])


class HumanOpponents(unittest.TestCase):
    def test_viewer_never_sees_other_humans_hidden_cards(self):
        # Seats 0 and 1 are humans and opponents; the dealer always opens 1NT, so every seat declares once
        # in four boards (seat 1 is dummy on board 2).
        specs = [{"kind": "human", "name": "Ann"}, {"kind": "human", "name": "Bea"},
                 {"kind": "bot", "name": "Cy"}, {"kind": "bot", "name": "Di"}]
        g = Bridge(specs, {"boards": 4, "hand_pause_seconds": 0})
        g.bot_call = lambda pid, legal: one_notrump(legal)
        humans = HumanSeats(g)
        problems = []
        exposed_seen = [0]

        def allowed(seat, s):
            # Seat 1's cards may be shown to viewer 0 only when seat 1 is the exposed dummy.
            return s["seats"][seat]["is_dummy"]

        def spy():
            while not g.finished and not g.stopped:
                for viewer in (0, None):
                    s = g.snapshot(viewer=viewer)
                    if viewer == 0 and s["seats"][0]["cards"] is None:
                        problems.append(("own hand hidden", s["hand_no"]))
                    if s["seats"][1]["cards"] is not None:
                        if viewer == 0 and s["seats"][1]["is_dummy"]:
                            exposed_seen[0] += 1
                        elif not allowed(1, s):
                            problems.append((viewer, s["hand_no"], s["phase"], s["contract"]))
                    if s["seats"][0]["cards"] is not None and viewer is None and not allowed(0, s):
                        problems.append(("spectator sees seat 0", s["hand_no"]))
                time.sleep(0.001)

        spy_thread = threading.Thread(target=spy, daemon=True)
        humans.start()
        spy_thread.start()
        g.start()
        wait(g)
        spy_thread.join(5)
        self.assertTrue(g.finished)
        self.assertFalse(any(e["kind"] == "error" for e in g.log), list(g.log)[-5:])
        self.assertEqual(problems, [])
        self.assertGreater(exposed_seen[0], 0, "dummy (seat 1) should have been exposed at some point")


class HumansNeverAnswer(unittest.TestCase):
    def test_deal_completes_on_turn_timer(self):
        specs = [{"kind": "human", "name": "Ann"}, {"kind": "bot", "name": "Bo"},
                 {"kind": "human", "name": "Cy"}, {"kind": "bot", "name": "Di"}]
        g = Bridge(specs, {"boards": 1, "hand_pause_seconds": 0, "turn_seconds": 1})
        g.start()
        wait(g, timeout=180)
        self.assertTrue(g.finished)
        self.assertFalse(any(e["kind"] == "error" for e in g.log), list(g.log)[-5:])
        self.assertEqual(g.hand_no, 1)
        self.assertTrue(any("ran out of time" in e["text"] for e in g.log))
        if g.contract:
            self.assertEqual(sum(g.tricks.values()), 13)
        self.assertEqual(g.human_requests, {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
