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
from table.blackjack import Blackjack  # noqa: E402

config.update({"ai_pace_seconds": 0})


def bots(n):
    return [{"kind": "bot", "name": f"Bot{i}"} for i in range(n)]


def humans(n):
    return [{"kind": "human", "name": f"Human{i}"} for i in range(n)]


def wait_for(cond, timeout=30):
    end = time.time() + timeout
    while not cond() and time.time() < end:
        time.sleep(0.02)
    return cond()


def wait_hands(game, n, timeout=60):
    end = time.time() + timeout
    while game.hand_no < n and not game.finished and time.time() < end:
        time.sleep(0.05)


def pending(game, pid, kind=None):
    with game.lock:
        req = game.human_requests.get(pid)
    return req is not None and (kind is None or req["type"] == kind)


def answer(game, pid, data):
    """Submit, tolerating the race where the request was already answered or replaced."""
    try:
        game.submit_human(data, pid=pid)
    except ValueError:
        pass


def opts(**extra):
    return {"starting_chips": 1000, "hand_pause_seconds": 0, "min_bet": 10, **extra}


class MultiHumanBlackjack(unittest.TestCase):
    def stop(self, g):
        g.stop()
        g.thread.join(5)
        self.assertFalse(g.status.startswith("Error"), g.status)

    def test_two_humans_play_rounds_and_wait_together(self):
        g = Blackjack(humans(2) + bots(3), opts())
        a, b = g.humans
        both_waiting = []  # rounds in which both humans had a bet request open at once
        lock = threading.Lock()

        def human(h):
            while not g.finished and not g.stopped:
                if pending(g, h.id, "bj_bet"):
                    # Hold the bet until the other human is waiting too (proves they are asked in parallel).
                    other = b if h is a else a
                    if wait_for(lambda: pending(g, other.id, "bj_bet"), timeout=5):
                        with lock:
                            both_waiting.append(g.hand_no)
                    answer(g, h.id, {"amount": g.min_bet})
                elif pending(g, h.id, "bj_play"):
                    legal = g.human_requests[h.id]["legal"]
                    answer(g, h.id, {"action": random.choice(legal)})
                elif pending(g, h.id, "busted"):
                    answer(g, h.id, {"action": "rebuy"})
                time.sleep(0.01)

        for h in (a, b):
            threading.Thread(target=human, args=(h,), daemon=True).start()
        g.start()
        wait_hands(g, 5, timeout=120)
        self.stop(g)
        self.assertGreaterEqual(g.hand_no, 5)
        self.assertGreaterEqual(len(both_waiting), 5, "both humans should be asked for bets at the same time")
        self.assertTrue(all(p.chips >= 0 for p in g.players))

    def test_snapshot_only_shows_the_viewers_request(self):
        g = Blackjack(humans(2) + bots(2), opts())
        a, b = g.humans
        release = threading.Event()

        def hold(h):
            # Both humans sit on their bet until the test has looked at the snapshots.
            release.wait(30)
            answer(g, h.id, {"amount": g.min_bet})
            while not g.finished and not g.stopped:
                if pending(g, h.id, "bj_play"):
                    answer(g, h.id, {"action": "stand"})
                time.sleep(0.01)

        for h in (a, b):
            threading.Thread(target=hold, args=(h,), daemon=True).start()
        g.start()
        self.assertTrue(wait_for(lambda: pending(g, a.id, "bj_bet") and pending(g, b.id, "bj_bet")))

        snap_a = g.snapshot(viewer=a.id)
        snap_b = g.snapshot(viewer=b.id)
        snap_bot = g.snapshot(viewer=None)
        self.assertEqual(snap_a["you"], a.id)
        self.assertEqual(snap_a["human_request"]["pid"], a.id)
        self.assertEqual(snap_a["human_request"]["type"], "bj_bet")
        self.assertEqual(snap_b["human_request"]["pid"], b.id)
        self.assertEqual(snap_b["human_request"]["type"], "bj_bet")
        self.assertIsNone(snap_bot["human_request"])
        self.assertEqual(snap_a["waiting_on"], sorted([a.id, b.id]))

        release.set()
        wait_hands(g, 1, timeout=60)
        self.stop(g)

    def test_unanswered_humans_do_not_stall_rounds(self):
        g = Blackjack(humans(2) + bots(2), opts(turn_seconds=1))
        g.start()  # nobody ever answers; the timer must carry every prompt
        wait_hands(g, 3, timeout=90)
        self.stop(g)
        self.assertGreaterEqual(g.hand_no, 3)
        self.assertTrue(any("ran out of time" in e["text"] for e in g.log))

    def test_busted_humans_are_asked_together_and_decide_separately(self):
        g = Blackjack(humans(2) + bots(2), opts())
        a, b = g.humans
        a.chips = 0
        b.chips = 0  # both busted before the first round
        g.start()
        self.assertTrue(wait_for(lambda: pending(g, a.id, "busted") and pending(g, b.id, "busted")))
        answer(g, a.id, {"action": "watch"})
        answer(g, b.id, {"action": "rebuy"})
        self.assertTrue(wait_for(lambda: pending(g, b.id, "bj_bet")))
        self.assertEqual(g.watching, {a.id})
        self.assertEqual(a.chips, 0)
        self.assertEqual(b.chips, 1000)

        def human_b():
            while not g.finished and not g.stopped:
                if pending(g, b.id, "bj_bet"):
                    answer(g, b.id, {"amount": g.min_bet})
                elif pending(g, b.id, "bj_play"):
                    answer(g, b.id, {"action": "stand"})
                time.sleep(0.01)

        threading.Thread(target=human_b, daemon=True).start()
        wait_hands(g, 2, timeout=60)
        self.stop(g)
        self.assertGreaterEqual(g.hand_no, 2)
        self.assertEqual(g.watching, {a.id})
        self.assertFalse(any(e["text"].startswith(a.name + " bets") for e in g.log))


if __name__ == "__main__":
    unittest.main(verbosity=2)
