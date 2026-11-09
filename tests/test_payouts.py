import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("AITABLE_SETTINGS", os.path.join(tempfile.mkdtemp(), "settings.json"))

from table.blackjack import Blackjack, Hand  # noqa: E402
from table.poker import Holdem  # noqa: E402


def bots(n):
    return [{"kind": "bot", "name": f"B{i}"} for i in range(n)]


class SidePots(unittest.TestCase):
    def hand(self, contribs, holes, board, folded=()):
        g = Holdem(bots(len(contribs)), {"starting_chips": 0})
        g.dealer = 0
        g.board = board.split()
        for pid, amount in enumerate(contribs):
            h = g.hs[pid]
            h.in_hand = True
            h.contrib = amount
            h.hole = holes[pid].split()
            h.folded = pid in folded
        return g

    def chips(self, g):
        return [p.chips for p in g.players]

    def test_short_stack_wins_only_main_pot(self):
        # B0 is all-in for 100 with the best hand; B1 and B2 put in 300 each.
        g = self.hand([100, 300, 300], ["As Ah", "Ks Kh", "Qs Qh"], "2c 7d 9h Jc 3s")
        g.settle()
        self.assertEqual(self.chips(g), [300, 400, 0])

    def test_uncalled_bet_is_returned(self):
        g = self.hand([500, 200], ["Ks Kh", "As Ah"], "2c 7d 9h Jc 3s")
        g.settle()
        self.assertEqual(self.chips(g), [300, 400])

    def test_board_plays_splits_evenly(self):
        g = self.hand([50, 50, 50], ["2s 3s", "4h 5h", "4d 5d"], "Ac Kd Qh Js Tc")
        g.settle()
        self.assertEqual(self.chips(g), [50, 50, 50])

    def test_odd_chip_goes_left_of_dealer(self):
        # B0 (the dealer) folds early; B1 and B2 tie and 151 chips can't split evenly.
        g = self.hand([25, 63, 63], ["2s 3s", "4h 5h", "4d 5d"], "Ac Kd Qh Js Tc", folded=(0,))
        g.settle()
        self.assertEqual(self.chips(g)[1:], [76, 75])

    def test_folded_money_stays_in_pot(self):
        g = self.hand([100, 100, 40], ["Ks Kh", "As Ah", "Qs Qh"], "2c 7d 9h Jc 3s", folded=(2,))
        g.settle()
        self.assertEqual(self.chips(g), [0, 240, 0])


class BlackjackPayouts(unittest.TestCase):
    def settle(self, dealer, cards, bet=100, from_split=False, result=""):
        g = Blackjack(bots(1), {"starting_chips": 900})
        p = g.players[0]
        g.dealer = dealer.split()
        hand = Hand(cards.split(), bet, from_split=from_split)
        hand.result = result
        if result == "bust":
            hand.payout = -bet
        g.hands[p.id] = [hand]
        g.settle([p])
        return p.chips, hand.result, hand.payout

    def test_outcomes(self):
        self.assertEqual(self.settle("Ts 8d", "Th Kd"), (1100, "win", 100))
        self.assertEqual(self.settle("Ts 9d", "Th 8d"), (900, "lose", -100))
        self.assertEqual(self.settle("Ts 8d", "Th 8c"), (1000, "push", 0))
        self.assertEqual(self.settle("Ts 6d Kc", "Th 2c"), (1100, "win", 100))

    def test_blackjack_pays_three_to_two(self):
        self.assertEqual(self.settle("Ts 8d", "Ah Kd"), (1150, "blackjack", 150))

    def test_blackjack_against_dealer_blackjack_pushes(self):
        self.assertEqual(self.settle("As Kc", "Ah Kd"), (1000, "push", 0))

    def test_dealer_blackjack_beats_twenty_one(self):
        self.assertEqual(self.settle("As Kc", "7h 7d 7c"), (900, "lose", -100))

    def test_twenty_one_after_split_is_not_blackjack(self):
        self.assertEqual(self.settle("Ts 9d", "Ah Kd", from_split=True), (1100, "win", 100))

    def test_bust_loses_even_if_dealer_busts(self):
        self.assertEqual(self.settle("Ts 6d Kc", "Th 6c Kd", result="bust"), (900, "bust", -100))


if __name__ == "__main__":
    unittest.main(verbosity=2)
