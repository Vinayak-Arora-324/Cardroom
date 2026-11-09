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

from table import agents, config  # noqa: E402
from table.bridge import (Bridge, auction_over, contract_from, hand_text, legal_calls,  # noqa: E402
                          legal_cards, normalize_call, normalize_card, score_contract, trick_winner)

config.update({"ai_pace_seconds": 0})


def auction_of(dealer, calls):
    return [((dealer + i) % 4, c) for i, c in enumerate(calls)]


class Scoring(unittest.TestCase):
    def test_made_contracts(self):
        # (level, strain, doubled, vulnerable, tricks) -> duplicate score
        cases = [
            ((1, "NT", 0, False, 7), 90),
            ((3, "NT", 0, False, 9), 400),
            ((3, "NT", 0, True, 10), 630),
            ((4, "S", 0, False, 10), 420),
            ((4, "H", 0, True, 11), 650),
            ((5, "D", 0, False, 11), 400),
            ((2, "C", 1, False, 8), 180),
            ((2, "S", 1, False, 8), 470),
            ((5, "D", 1, False, 11), 550),
            ((1, "C", 2, False, 7), 230),
            ((6, "S", 0, False, 12), 980),
            ((6, "NT", 0, True, 12), 1440),
            ((7, "NT", 0, True, 13), 2220),
            ((1, "S", 1, False, 8), 260),
            ((1, "S", 1, True, 8), 360),
            ((1, "S", 2, False, 7), 520),
            ((1, "S", 2, False, 8), 720),
        ]
        for args, expected in cases:
            self.assertEqual(score_contract(*args), expected, args)

    def test_penalties(self):
        cases = [
            ((4, "S", 0, False, 9), -50),
            ((4, "S", 0, True, 8), -200),
            ((4, "S", 1, False, 9), -100),
            ((4, "S", 1, False, 8), -300),
            ((4, "S", 1, False, 7), -500),
            ((4, "S", 1, False, 6), -800),
            ((4, "S", 1, True, 9), -200),
            ((4, "H", 1, True, 7), -800),
            ((4, "S", 2, False, 9), -200),
            ((4, "S", 2, True, 8), -1000),
        ]
        for args, expected in cases:
            self.assertEqual(score_contract(*args), expected, args)


class Auction(unittest.TestCase):
    def test_opening_has_no_double(self):
        calls = legal_calls([], 0)
        self.assertNotIn("X", calls)
        self.assertEqual(len(calls), 36)

    def test_bids_must_outrank(self):
        calls = legal_calls(auction_of(0, ["2H"]), 1)
        self.assertNotIn("2D", calls)
        self.assertNotIn("2H", calls)
        self.assertIn("2S", calls)

    def test_double_only_against_opponents(self):
        self.assertIn("X", legal_calls(auction_of(0, ["1H"]), 1))
        self.assertNotIn("X", legal_calls(auction_of(0, ["1H", "Pass"]), 2))
        self.assertIn("X", legal_calls(auction_of(0, ["1H", "Pass", "Pass"]), 3))

    def test_redouble_only_your_side_doubled(self):
        self.assertIn("XX", legal_calls(auction_of(0, ["1H", "X"]), 2))
        self.assertNotIn("XX", legal_calls(auction_of(0, ["1H", "X", "Pass"]), 3))
        self.assertIn("XX", legal_calls(auction_of(0, ["1H", "X", "Pass", "Pass"]), 0))

    def test_end_of_auction(self):
        self.assertFalse(auction_over(auction_of(0, ["Pass", "Pass", "Pass"])))
        self.assertTrue(auction_over(auction_of(0, ["Pass"] * 4)))
        self.assertFalse(auction_over(auction_of(0, ["Pass", "Pass", "Pass", "1C"])))
        self.assertFalse(auction_over(auction_of(0, ["1C", "Pass", "Pass"])))
        self.assertTrue(auction_over(auction_of(0, ["1C", "Pass", "Pass", "Pass"])))

    def test_declarer_is_first_to_name_strain(self):
        # South opens 1H, North raises to 4H: South declares, North is dummy.
        c = contract_from(auction_of(0, ["1H", "Pass", "4H", "Pass", "Pass", "Pass"]))
        self.assertEqual((c["level"], c["strain"], c["declarer"], c["dummy"], c["doubled"]), (4, "H", 0, 2, 0))
        # North named spades first, so South's raise doesn't make South declarer.
        c = contract_from(auction_of(2, ["1S", "Pass", "4S", "Pass", "Pass", "Pass"]))
        self.assertEqual(c["declarer"], 2)
        # A strain first named by the opponents doesn't count for this side:
        # West opens 1H, South overcalls 2H, so South (the first of NS to name hearts) declares.
        c = contract_from(auction_of(1, ["1H", "Pass", "Pass", "2H", "Pass", "Pass", "Pass"]))
        self.assertEqual(c["declarer"], 0)

    def test_doubled_flags(self):
        self.assertEqual(contract_from(auction_of(0, ["1H", "X", "Pass", "Pass", "Pass"]))["doubled"], 1)
        self.assertEqual(contract_from(auction_of(0, ["1H", "X", "XX", "Pass", "Pass", "Pass"]))["doubled"], 2)
        self.assertEqual(contract_from(auction_of(0, ["1H", "X", "1S", "Pass", "Pass", "Pass"]))["doubled"], 0)

    def test_passed_out(self):
        self.assertIsNone(contract_from(auction_of(0, ["Pass"] * 4)))


class Play(unittest.TestCase):
    def test_follow_suit(self):
        hand = ["As", "Ks", "2h"]
        self.assertEqual(legal_cards(hand, [(0, "3s")]), ["As", "Ks"])
        self.assertEqual(legal_cards(hand, [(0, "3d")]), hand)
        self.assertEqual(legal_cards(hand, []), hand)

    def test_trick_winner(self):
        self.assertEqual(trick_winner([(0, "2s"), (1, "As"), (2, "Ks"), (3, "3s")], None), 1)
        self.assertEqual(trick_winner([(0, "As"), (1, "2h"), (2, "Ks"), (3, "3s")], "h"), 1)
        # A higher card off-suit, with no trumps, cannot win.
        self.assertEqual(trick_winner([(0, "2s"), (1, "Ah"), (2, "3s"), (3, "Kd")], None), 2)

    def test_normalize(self):
        self.assertEqual(normalize_card("10♥", ["Th"]), "Th")
        self.assertEqual(normalize_card("AS", ["As"]), "As")
        self.assertEqual(normalize_call("1 nt", ["1NT"]), "1NT")
        self.assertEqual(normalize_call("3N", ["3NT"]), "3NT")
        self.assertEqual(normalize_call("pass", ["Pass"]), "Pass")
        with self.assertRaises(ValueError):
            normalize_card("2c", ["As"])


def wait(game, timeout=120):
    end = time.time() + timeout
    while not game.finished and time.time() < end:
        time.sleep(0.02)


def bots():
    return [{"kind": "bot", "name": n} for n in ("Sam", "Wes", "Nia", "Eve")]


class Games(unittest.TestCase):
    def test_bots_complete_matches(self):
        contracts = 0
        for _ in range(15):
            g = Bridge(bots(), {"boards": 4, "hand_pause_seconds": 0})
            original = g.score_board

            def checked(g=g, original=original):
                self.assertEqual(g.tricks["NS"] + g.tricks["EW"], 13)
                self.assertTrue(all(len(h) == 0 for h in g.hands.values()))
                self.assertEqual(len(g.played), 52)
                original()

            g.score_board = checked
            g.start()
            wait(g)
            self.assertTrue(g.finished)
            self.assertFalse(any(e["kind"] == "error" for e in g.log), list(g.log)[-5:])
            self.assertEqual(g.hand_no, 4)
            for p in g.players:
                self.assertEqual(p.chips, g.scores["NS" if p.id % 2 == 0 else "EW"])
            contracts += sum(1 for r in g.board_results if "Passed out" not in r)
        self.assertGreater(contracts, 30, "bots should reach a contract on most boards")

    def test_messy_ai_replies(self):
        calls = []

        def fake_ask(seat, system, prompt, schema):
            calls.append(prompt)
            r = random.random()
            if r < 0.15:
                raise agents.AgentError("simulated timeout")
            if r < 0.3:
                return {"nonsense": True}, {"seconds": 0}
            key = "call" if "call" in schema["properties"] else "card"
            legal = schema["properties"][key]["enum"]
            return {key: random.choice(legal[:6]), "table_talk": "hm", "notes": "n"}, {"seconds": 0}

        orig = agents.ask
        agents.ask = fake_ask
        try:
            specs = [{"kind": "claude", "name": "A"}, {"kind": "codex", "name": "B"},
                     {"kind": "openrouter", "name": "C"}, {"kind": "bot", "name": "D"}]
            g = Bridge(specs, {"boards": 3, "hand_pause_seconds": 0})
            g.start()
            wait(g)
            self.assertTrue(g.finished)
            self.assertFalse(g.status.startswith("Error"), g.status)
            self.assertEqual(g.hand_no, 3)
            self.assertTrue(calls)
        finally:
            agents.ask = orig

    def deal(self, g):
        deck = [f"{r}{s}" for r in "23456789TJQKA" for s in "shdc"]
        random.shuffle(deck)
        g.hands = {i: sorted(deck[i * 13:(i + 1) * 13]) for i in range(4)}

    def test_prompts_only_show_permitted_hands(self):
        g = Bridge([{"kind": "claude", "name": n} for n in "ABCD"], {"boards": 1})
        g.hand_no = 1
        self.deal(g)
        g.auction = auction_of(0, ["1S", "Pass", "2S", "Pass", "Pass", "Pass"])
        g.contract = contract_from(g.auction)   # South declares, North is dummy
        g.phase = "play"
        text = {i: hand_text(g.hands[i]) for i in range(4)}

        # Opening leader (West) has not seen dummy yet.
        p = g.play_prompt(1, 1, g.hands[1])
        self.assertIn(text[1], p)
        for other in (0, 2, 3):
            self.assertNotIn(text[other], p)

        # After the lead, East sees dummy but neither West nor declarer.
        g.dummy_visible = True
        p = g.play_prompt(3, 3, g.hands[3])
        self.assertIn(text[2], p)
        self.assertNotIn(text[0], p)
        self.assertNotIn(text[1], p)

        # Declarer playing dummy sees both of their side's hands, not the defenders'.
        p = g.play_prompt(2, 0, g.hands[2])
        self.assertIn(text[2], p)
        self.assertIn(text[0], p)
        self.assertNotIn(text[1], p)
        self.assertNotIn(text[3], p)

    def test_browser_state_hides_ai_cards(self):
        specs = [{"kind": "human", "name": "Me"}] + [{"kind": "bot", "name": n} for n in "WNE"]
        g = Bridge(specs, {"boards": 1})
        g.hand_no = 1
        g.hands = {i: ["As"] for i in range(4)}
        g.contract = {"level": 1, "strain": "NT", "doubled": 0, "declarer": 1, "dummy": 3}
        s = g.game_state()
        self.assertEqual(s["seats"][0]["cards"], ["As"])
        for pid in (1, 2, 3):
            self.assertIsNone(s["seats"][pid]["cards"])
        g.dummy_visible = True
        s = g.game_state()
        self.assertEqual(s["seats"][3]["cards"], ["As"])
        self.assertIsNone(s["seats"][1]["cards"])
        self.assertIsNone(s["seats"][2]["cards"])

    def test_human_declarer_plays_dummy_too(self):
        specs = [{"kind": "human", "name": "Me"}] + [{"kind": "bot", "name": n} for n in "WNE"]
        g = Bridge(specs, {"boards": 1, "hand_pause_seconds": 0})
        owners = set()
        g.bot_call = lambda pid, legal: "Pass"

        def player():
            while not g.finished and not g.stopped:
                req = g.human_request
                if req and req["type"] == "bridge_bid":
                    g.submit_human({"call": "1NT" if "1NT" in req["legal"] else "Pass"})
                elif req and req["type"] == "bridge_play":
                    owners.add(req["hand_owner"])
                    g.submit_human({"card": random.choice(req["legal"])})
                time.sleep(0.005)

        threading.Thread(target=player, daemon=True).start()
        g.start()
        wait(g)
        self.assertTrue(g.finished)
        self.assertEqual(g.contract["declarer"], 0)
        self.assertEqual(owners, {0, 2})


if __name__ == "__main__":
    unittest.main(verbosity=2)
