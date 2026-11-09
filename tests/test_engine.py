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

from table import agents, config  # noqa: E402
from table.blackjack import Blackjack, basic_strategy, hand_value, is_blackjack  # noqa: E402
from table.handeval import CATEGORY_NAMES, best_hand, describe  # noqa: E402
from table.poker import Holdem  # noqa: E402

config.update({"ai_pace_seconds": 0})


def wait_hands(game, n, timeout=60):
    end = time.time() + timeout
    while game.hand_no < n and not game.finished and time.time() < end:
        time.sleep(0.05)


class HandEval(unittest.TestCase):
    def cat(self, cards):
        return CATEGORY_NAMES[best_hand(cards.split())[0][0]]

    def test_describe(self):
        self.assertEqual(describe(best_hand("Ah Ad Kc 7s 2h".split())[0]), "Pair of Aces")
        self.assertEqual(describe(best_hand("As Ks Qs Js Ts".split())[0]), "Royal Flush")
        self.assertEqual(describe(best_hand("Ah 2d 3c 4s 5h".split())[0]), "Five-high Straight")

    def test_categories(self):
        self.assertEqual(self.cat("As Ks Qs Js Ts 2d 3c"), "Straight Flush")
        self.assertEqual(self.cat("Ah 2d 3c 4s 5h Kd Kc"), "Straight")
        self.assertEqual(self.cat("Ah Ad Ac Kd Kc 2s 3s"), "Full House")
        self.assertEqual(self.cat("2h 2d 2c 2s Kc 3s 4s"), "Four of a Kind")
        self.assertEqual(self.cat("2h 7h 9h Jh Kh Ad Ac"), "Flush")
        self.assertEqual(self.cat("2h 2d 5c 5s 9c Js Qd"), "Two Pair")

    def test_ordering(self):
        wheel = best_hand("Ah 2d 3c 4s 5h".split())[0]
        six = best_hand("2d 3c 4s 5h 6h".split())[0]
        self.assertLess(wheel, six)
        self.assertGreater(best_hand("Ah Ad Kc 7s 2h".split())[0], best_hand("Ah Ad Qc 7s 2h".split())[0])


class BlackjackRules(unittest.TestCase):
    def test_values(self):
        self.assertEqual(hand_value(["Ah", "6d"]), (17, True))
        self.assertEqual(hand_value(["Ah", "6d", "9c"]), (16, False))
        self.assertEqual(hand_value(["Ah", "Ad", "9c"]), (21, True))
        self.assertTrue(is_blackjack(["As", "Kd"]))

    def test_strategy(self):
        self.assertEqual(basic_strategy(["8h", "8d"], "Ts", ["hit", "stand", "double", "split"]), "split")
        self.assertEqual(basic_strategy(["6h", "5d"], "6s", ["hit", "stand", "double"]), "double")
        self.assertEqual(basic_strategy(["Th", "6d"], "6s", ["hit", "stand"]), "stand")
        self.assertEqual(basic_strategy(["Th", "6d"], "Ts", ["hit", "stand"]), "hit")


def bots(n):
    return [{"kind": "bot", "name": f"Bot{i}"} for i in range(n)]


class PokerSim(unittest.TestCase):
    def test_chip_conservation(self):
        g = Holdem(bots(7), {"starting_chips": 500, "small_blind": 10, "big_blind": 20, "hand_pause_seconds": 0, "blind_increase_every": 5})
        total = sum(p.chips for p in g.players)
        g.start()
        wait_hands(g, 40, timeout=240)
        g.stop()
        g.thread.join(5)
        self.assertFalse(any(e["kind"] == "error" for e in g.log), list(g.log)[-5:])
        # chips may be mid-hand in the pot when stopped
        self.assertEqual(sum(p.chips for p in g.players) + g.pot_total(), total)
        self.assertGreaterEqual(g.hand_no, 5)

    def test_ai_replies_validated(self):
        """Fake AI backends returning messy replies must never break the engine."""
        replies = [
            {"action": "raise", "amount": 999999, "table_talk": "all of it"},
            {"action": "check", "amount": 0},
            {"action": "CALL"},
            {"action": "bet", "amount": "60"},
            {"action": "all-in"},
            {"nonsense": True},
            {"action": "fold", "notes": "they bluff a lot"},
        ]
        calls = []

        def fake_ask(seat, system, prompt, schema):
            calls.append(seat["kind"])
            if random.random() < 0.1:
                raise agents.AgentError("simulated timeout")
            return dict(random.choice(replies)), {"seconds": 0.0}

        orig = agents.ask
        agents.ask = fake_ask
        try:
            specs = [{"kind": "claude", "name": "C1"}, {"kind": "claude", "name": "C2"}, {"kind": "codex", "name": "X1"},
                     {"kind": "codex", "name": "X2"}, {"kind": "openrouter", "name": "O1"}, {"kind": "bot", "name": "B"}]
            g = Holdem(specs, {"starting_chips": 300, "hand_pause_seconds": 0})
            total = sum(p.chips for p in g.players)
            g.start()
            wait_hands(g, 15, timeout=120)
            g.stop()
            g.thread.join(5)
            self.assertFalse(g.status.startswith("Error"), g.status)
            self.assertEqual(sum(p.chips for p in g.players) + g.pot_total(), total)
            self.assertTrue(calls)
        finally:
            agents.ask = orig

    def test_human_seat(self):
        g = Holdem([{"kind": "human", "name": "Me"}] + bots(4), {"starting_chips": 300, "hand_pause_seconds": 0})
        g.start()

        def player():
            while not g.finished and not g.stopped:
                req = g.human_request
                if req and req["type"] == "poker":
                    g.submit_human({"action": random.choice(req["legal"]), "amount": req["min_to"]})
                elif req and req["type"] == "busted":
                    g.submit_human({"action": "watch"})
                time.sleep(0.01)

        t = threading.Thread(target=player, daemon=True)
        t.start()
        wait_hands(g, 10, timeout=120)
        g.stop()
        g.thread.join(5)
        self.assertFalse(g.status.startswith("Error"), g.status)
        self.assertGreaterEqual(g.hand_no, 3)


class BlackjackSim(unittest.TestCase):
    def test_rounds(self):
        g = Blackjack([{"kind": "human", "name": "Me"}] + bots(6), {"starting_chips": 1000, "hand_pause_seconds": 0, "min_bet": 10})
        g.start()

        def player():
            while not g.finished and not g.stopped:
                req = g.human_request
                if req and req["type"] == "bj_bet":
                    g.submit_human({"amount": req["min"]})
                elif req and req["type"] == "bj_play":
                    g.submit_human({"action": random.choice(req["legal"])})
                elif req and req["type"] == "busted":
                    g.submit_human({"action": "rebuy"})
                time.sleep(0.01)

        threading.Thread(target=player, daemon=True).start()
        wait_hands(g, 25, timeout=180)
        g.stop()
        g.thread.join(5)
        self.assertFalse(g.status.startswith("Error"), g.status)
        self.assertGreaterEqual(g.hand_no, 10)
        self.assertTrue(all(p.chips >= 0 for p in g.players))


if __name__ == "__main__":
    unittest.main(verbosity=2)
