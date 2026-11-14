import json
import os
import random
import sys
import tempfile
import threading
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ["AITABLE_SETTINGS"] = os.path.join(tempfile.mkdtemp(), "settings.json")

from table import config  # noqa: E402
from table.poker import Holdem  # noqa: E402

config.update({"ai_pace_seconds": 0})


def wait_hands(game, n, timeout=60):
    end = time.time() + timeout
    while game.hand_no < n and not game.finished and time.time() < end:
        time.sleep(0.05)


def bots(n):
    return [{"kind": "bot", "name": f"Bot{i}"} for i in range(n)]


def humans(*names):
    return [{"kind": "human", "name": name} for name in names]


def answer_forever(game, pid, stop):
    """Stand in for one human at the browser: answer whatever request this seat has."""
    while not stop.is_set() and not game.finished and not game.stopped:
        with game.lock:
            req = game.human_requests.get(pid)
        if req and req["type"] == "poker":
            data = {"action": random.choice(req["legal"]), "amount": req["min_to"]}
        elif req and req["type"] == "busted":
            data = {"action": random.choice(["rebuy", "watch"])}
        else:
            data = None
        if data is not None:
            try:
                game.submit_human(data, pid=pid)
            except ValueError:
                pass  # the request was answered or replaced in the meantime
        time.sleep(0.01)


class TwoHumans(unittest.TestCase):
    def test_two_humans_and_bots_play_several_hands(self):
        specs = humans("Ann", "Ben") + bots(3)
        g = Holdem(specs, {"starting_chips": 300, "hand_pause_seconds": 0})
        total = sum(p.chips for p in g.players)
        stop = threading.Event()
        g.start()
        for human in g.humans:
            threading.Thread(target=answer_forever, args=(g, human.id, stop), daemon=True).start()
        wait_hands(g, 8, timeout=180)
        stop.set()
        g.stop()
        g.thread.join(5)
        self.assertFalse(g.status.startswith("Error"), g.status)
        self.assertFalse(any(e["kind"] == "error" for e in g.log), list(g.log)[-5:])
        self.assertGreaterEqual(g.hand_no, 5)
        self.assertEqual(sum(p.chips for p in g.players) + g.pot_total(), total)

    def test_busted_humans_are_asked_at_once(self):
        g = Holdem(humans("Ann", "Ben") + bots(2), {"starting_chips": 100, "hand_pause_seconds": 0})
        for human in g.humans:
            human.chips = 0
        seen = []
        real = g.ask_human

        def record(request, validate, player=None, auto=None):
            if request["type"] == "busted":
                seen.append(player.id)
                # Both humans must be waiting together before either one is answered.
                end = time.time() + 5
                while len(seen) < 2 and time.time() < end:
                    time.sleep(0.01)
            return real(request, validate, player=player, auto=auto)

        g.ask_human = record
        stop = threading.Event()
        g.start()
        for human in g.humans:
            threading.Thread(target=answer_forever, args=(g, human.id, stop), daemon=True).start()
        wait_hands(g, 2, timeout=60)
        stop.set()
        g.stop()
        g.thread.join(5)
        self.assertEqual(sorted(seen[:2]), [0, 1])
        self.assertFalse(g.status.startswith("Error"), g.status)


class ViewerPrivacy(unittest.TestCase):
    def test_snapshots_never_show_the_other_humans_hidden_cards(self):
        g = Holdem(humans("Ann", "Ben") + bots(3), {"starting_chips": 400, "hand_pause_seconds": 0})
        a, b = g.humans
        problems = []
        checked = {"a": 0, "b": 0}
        stop = threading.Event()

        def check(viewer, other):
            snap = g.snapshot(viewer=viewer.id)
            if snap["human_request"] is not None and snap["human_request"]["pid"] != viewer.id:
                problems.append(f"{viewer.name} was sent someone else's request")
            if snap["you"] != viewer.id:
                problems.append("snapshot names the wrong viewer")
            if g.hs[other.id].revealed:
                return
            # Only check hands where the other human actually holds cards.
            if not g.hs[other.id].in_hand or not g.hs[other.id].hole:
                return
            seat = snap["seats"][other.id]
            if seat["cards"] != ["??"] * len(g.hs[other.id].hole):
                problems.append(f"{viewer.name} can see {other.name}'s cards: {seat['cards']}")
            text = json.dumps(snap)
            for card in g.hs[other.id].hole:
                if f'"{card}"' in text:
                    problems.append(f"{card} of {other.name} leaked to {viewer.name}")
            checked["a" if viewer is a else "b"] += 1

        def watch(viewer, other):
            while not stop.is_set() and not g.finished:
                with g.lock:
                    waiting = viewer.id in g.human_requests
                if waiting:
                    check(viewer, other)
                    with g.lock:
                        req = g.human_requests.get(viewer.id)
                    if req and req["type"] == "poker":
                        g.submit_human({"action": random.choice(req["legal"]), "amount": req["min_to"]}, pid=viewer.id)
                    elif req and req["type"] == "busted":
                        g.submit_human({"action": "watch"}, pid=viewer.id)
                time.sleep(0.005)

        g.start()
        threading.Thread(target=watch, args=(a, b), daemon=True).start()
        threading.Thread(target=watch, args=(b, a), daemon=True).start()
        wait_hands(g, 6, timeout=120)
        stop.set()
        g.stop()
        g.thread.join(5)
        self.assertEqual(problems, [])
        self.assertGreater(checked["a"] + checked["b"], 0)

    def test_viewer_gets_only_their_own_request_and_hand(self):
        # Both humans are busted, so both rebuy prompts are open at the same time.
        g = Holdem(humans("Ann", "Ben") + bots(2), {"starting_chips": 400, "hand_pause_seconds": 0})
        a, b = g.humans
        for human in g.humans:
            human.chips = 0
        g.start()
        end = time.time() + 30
        while time.time() < end:
            with g.lock:
                if len(g.human_requests) == 2:
                    break
            time.sleep(0.01)
        snap_a = g.snapshot(viewer=a.id)
        snap_b = g.snapshot(viewer=b.id)
        self.assertEqual(snap_a["human_request"]["pid"], a.id)
        self.assertEqual(snap_b["human_request"]["pid"], b.id)
        self.assertEqual(snap_a["waiting_on"], sorted([a.id, b.id]))
        spectator = g.snapshot()
        self.assertIsNone(spectator["human_request"])
        self.assertIsNone(spectator["you"])
        g.stop()
        g.thread.join(5)


class SlowHumans(unittest.TestCase):
    def test_game_goes_on_when_a_human_never_answers(self):
        g = Holdem(humans("Ghost", "Ben") + bots(3),
                   {"starting_chips": 300, "hand_pause_seconds": 0, "turn_seconds": 1})
        stop = threading.Event()
        g.start()
        # Ben answers; Ghost never does, so every turn of Ghost's must time out and auto-play.
        threading.Thread(target=answer_forever, args=(g, g.humans[1].id, stop), daemon=True).start()
        wait_hands(g, 4, timeout=120)
        stop.set()
        g.stop()
        g.thread.join(5)
        self.assertFalse(g.status.startswith("Error"), g.status)
        self.assertGreaterEqual(g.hand_no, 4)
        self.assertTrue(any("ran out of time" in e["text"] for e in g.log))


if __name__ == "__main__":
    unittest.main(verbosity=2)
