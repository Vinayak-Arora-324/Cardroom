"""Multi-seat Blackjack. AI seats bet and play their hands in parallel."""
import random
import threading

from .base import BaseGame, pick_option
from .cards import new_deck, pretty, pretty_list
from . import config

RULES = """GAME: Blackjack vs the dealer, {decks}-deck shoe. Get closer to 21 than the dealer without going over. Number cards = face value, J/Q/K = 10, Ace = 1 or 11.
Blackjack (Ace + 10-value as first two cards) pays 3:2. Other wins pay 1:1, ties push. Dealer {soft17} soft 17 and peeks for blackjack.
ACTIONS: "hit" (take a card), "stand", "double" (first two cards only: double the bet, take exactly one card), "split" (a pair: split into two hands, each with the original bet; split aces get one card each).
You play against the dealer, not against the other players, but chips are the score — the biggest stack wins bragging rights."""

BET_SCHEMA = {
    "type": "object",
    "properties": {"bet": {"type": "integer"}, "table_talk": {"type": "string"}, "notes": {"type": "string"}},
    "required": ["bet", "table_talk", "notes"],
    "additionalProperties": False,
}
PLAY_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["hit", "stand", "double", "split"]},
        "table_talk": {"type": "string"},
        "notes": {"type": "string"},
    },
    "required": ["action", "table_talk", "notes"],
    "additionalProperties": False,
}


def card_value(card):
    r = card[0]
    if r == "A":
        return 11
    if r in "TJQK":
        return 10
    return int(r)


def hand_value(cards):
    """Returns (total, is_soft)."""
    total = sum(card_value(c) for c in cards)
    aces = sum(1 for c in cards if c[0] == "A")
    while total > 21 and aces:
        total -= 10
        aces -= 1
    return total, aces > 0


def is_blackjack(cards):
    return len(cards) == 2 and hand_value(cards)[0] == 21


def describe_hand(cards):
    total, soft = hand_value(cards)
    if is_blackjack(cards):
        return "Blackjack"
    if total > 21:
        return f"Bust ({total})"
    return f"{'soft ' if soft else ''}{total}"


class Hand:
    def __init__(self, cards, bet, split_aces=False, from_split=False):
        self.cards = cards
        self.bet = bet
        self.done = False
        self.doubled = False
        self.split_aces = split_aces
        self.from_split = from_split
        self.result = ""     # win | lose | push | blackjack | bust
        self.payout = 0      # net chips won/lost

    def public(self):
        return {
            "cards": list(self.cards), "bet": self.bet, "done": self.done, "doubled": self.doubled,
            "value": describe_hand(self.cards) if self.cards else "", "result": self.result, "payout": self.payout,
        }


class Blackjack(BaseGame):
    game_name = "Blackjack"

    def __init__(self, player_specs, options):
        super().__init__(player_specs, options)
        self.min_bet = int(options.get("min_bet", 10))
        self.max_bet = int(options.get("max_bet", 500))
        self.decks = int(options.get("decks", 6))
        self.h17 = bool(options.get("dealer_hits_soft17", False))
        self.round_pause = float(options.get("hand_pause_seconds", 5))
        self.shoe = []
        self.shoe_lock = threading.Lock()
        self.dealer = []
        self.dealer_hidden = True
        self.phase = ""
        self.hands = {p.id: [] for p in self.players}
        self.active = {p.id: None for p in self.players}  # index of hand being played
        self.in_round = {p.id: False for p in self.players}
        self.watching = False
        self.history = []
        self.shuffle()

    # ------------------------------------------------------------------ shoe
    def shuffle(self):
        self.shoe = new_deck(self.decks)
        self.shoe_total = len(self.shoe)
        self.cut = int(self.shoe_total * 0.25)
        self.add_log(f"Shuffling a fresh {self.decks}-deck shoe.", "hand")

    def draw(self):
        with self.shoe_lock:
            if not self.shoe:
                self.shuffle()
            return self.shoe.pop()

    def rules_text(self):
        return RULES.format(decks=self.decks, soft17="hits" if self.h17 else "stands on")

    # ------------------------------------------------------------------ main loop
    def run(self):
        while True:
            human = next((p for p in self.players if p.is_human), None)
            if human and human.chips < self.min_bet and not self.watching:
                choice = self.ask_human(
                    {"type": "busted", "message": "You don't have enough chips for the minimum bet.", "options": ["rebuy", "watch"]},
                    pick_option(("rebuy", "watch")),
                )
                if choice == "rebuy":
                    human.chips = int(self.options.get("starting_chips", 1000))
                    self.add_log(f"{human.name} rebuys for {human.chips}.")
                else:
                    self.watching = True
            players = [p for p in self.players if p.chips >= self.min_bet]
            if not players:
                best = max(self.players, key=lambda p: p.chips)
                self.set_status(f"Everyone is broke. Game over — {best.name} lasted longest.")
                return
            self.play_round(players)
            for remaining in range(int(self.round_pause), 0, -1):
                self.set_status(f"Next round in {remaining}s…")
                self.sleep(1)

    def play_round(self, players):
        self.hand_no += 1
        if len(self.shoe) < self.cut:
            self.shuffle()
        self.dealer, self.dealer_hidden = [], True
        for p in self.players:
            self.hands[p.id] = []
            self.active[p.id] = None
            self.in_round[p.id] = p in players
        self.add_log(f"— Round #{self.hand_no} —", "hand")

        # 1. Bets (everyone at once)
        self.phase = "betting"
        self.set_status("Place your bets")
        bets = {}
        handle = self.run_parallel([(lambda p=p: bets.__setitem__(p.id, self.get_bet(p))) for p in players if not p.is_human])
        for p in players:
            if p.is_human:
                bets[p.id] = self.get_bet(p)
        self.join_parallel(handle)
        for p in players:
            bet = bets[p.id]
            p.chips -= bet
            self.hands[p.id] = [Hand([], bet)]
            self.add_log(f"{p.name} bets {bet}")

        # 2. Deal
        self.phase = "dealing"
        self.set_status("Dealing…")
        for _ in range(2):
            for p in players:
                self.hands[p.id][0].cards.append(self.draw())
            self.dealer.append(self.draw())
            self.changed()
            self.sleep(0.3)

        up = self.dealer[0]
        self.add_log(f"Dealer shows {pretty(up)}")
        if card_value(up) >= 10 and is_blackjack(self.dealer):
            self.dealer_hidden = False
            self.add_log("Dealer has Blackjack!", "warn")
            self.settle(players)
            return

        for p in players:
            h = self.hands[p.id][0]
            if is_blackjack(h.cards):
                h.done = True
                self.add_log(f"{p.name} has Blackjack!", "win")

        # 3. Players act (AI seats in parallel with you, unless disabled)
        self.phase = "playing"
        self.set_status("Players are playing their hands")
        ai = [p for p in players if not p.is_human]
        humans = [p for p in players if p.is_human]
        if config.get().get("parallel_blackjack", True):
            handle = self.run_parallel([(lambda p=p: self.play_seat(p)) for p in ai])
            for p in humans:
                self.play_seat(p)
            self.join_parallel(handle)
        else:
            for p in players:
                self.play_seat(p)

        # 4. Dealer
        self.phase = "dealer"
        self.dealer_hidden = False
        self.add_log(f"Dealer reveals {pretty_list(self.dealer)} ({describe_hand(self.dealer)})")
        self.changed()
        self.sleep(0.8)
        live = any(h.result == "" and hand_value(h.cards)[0] <= 21 and not (is_blackjack(h.cards) and not h.from_split)
                   for p in players for h in self.hands[p.id])
        if live:
            while True:
                total, soft = hand_value(self.dealer)
                if total < 17 or (self.h17 and total == 17 and soft):
                    self.dealer.append(self.draw())
                    self.add_log(f"Dealer hits: {pretty(self.dealer[-1])} ({describe_hand(self.dealer)})")
                    self.changed()
                    self.sleep(0.8)
                else:
                    break
        self.settle(players)

    # ------------------------------------------------------------------ betting
    def clamp_bet(self, p, bet, strict):
        try:
            bet = int(float(bet))
        except (TypeError, ValueError):
            raise ValueError("bet must be a number")
        hi = min(self.max_bet, p.chips)
        if strict and not (self.min_bet <= bet <= hi):
            raise ValueError(f"bet must be between {self.min_bet} and {hi}")
        return max(self.min_bet, min(bet, hi))

    def get_bet(self, p):
        if p.is_human:
            return self.ask_human(
                {"type": "bj_bet", "min": self.min_bet, "max": min(self.max_bet, p.chips), "chips": p.chips},
                lambda d: self.clamp_bet(p, d.get("amount"), strict=True),
            )
        lines = [
            f"Round #{self.hand_no}. Betting phase.",
            f"Your stack: {p.chips}. Table limits: min {self.min_bet}, max {min(self.max_bet, p.chips)}.",
            f"Shoe: {len(self.shoe)} of {self.shoe_total} cards remain (reshuffle at {self.cut}).",
            "Stacks: " + ", ".join(f"{q.name} {q.chips}" for q in self.players),
        ]
        if self.history:
            lines.append("Recent rounds:\n" + "\n".join(f"  {h}" for h in self.history[-4:]))
        lines.append(f"Your private notes: {p.notes or '(none yet)'}")
        lines.append(f"Recent table talk:\n{self.recent_chat(8)}")
        lines.append('How much do you bet? Reply JSON: {"bet": <integer>, "table_talk": "...", "notes": "..."}')
        return self.ai_decide(
            p, self.system_prompt(p, self.rules_text()), "\n".join(lines), BET_SCHEMA,
            validate=lambda r: self.clamp_bet(p, r.get("bet"), strict=False),
            fallback=lambda: self.clamp_bet(p, max(self.min_bet, round(p.chips * 0.05 / self.min_bet) * self.min_bet), False),
        )

    # ------------------------------------------------------------------ playing
    def legal_actions(self, p, hands, i):
        h = hands[i]
        acts = ["hit", "stand"]
        if len(h.cards) == 2 and p.chips >= h.bet:
            acts.append("double")
            if card_value(h.cards[0]) == card_value(h.cards[1]) and len(hands) < 4 and not h.split_aces:
                acts.append("split")
        return acts

    def play_seat(self, p):
        hands = self.hands[p.id]
        i = 0
        while i < len(hands):
            h = hands[i]
            self.active[p.id] = i
            self.changed()
            if len(h.cards) == 1:  # second hand of a split gets its card now
                h.cards.append(self.draw())
                self.changed()
                if h.split_aces:
                    h.done = True
            while not h.done:
                total, _ = hand_value(h.cards)
                if total >= 21:
                    h.done = True
                    break
                legal = self.legal_actions(p, hands, i)
                action = self.get_play(p, hands, i, legal)
                if action == "stand":
                    h.done = True
                    self.add_log(f"{p.name} stands on {describe_hand(h.cards)}")
                elif action == "hit":
                    h.cards.append(self.draw())
                    self.add_log(f"{p.name} hits: {pretty(h.cards[-1])} ({describe_hand(h.cards)})")
                elif action == "double":
                    with self.lock:
                        p.chips -= h.bet
                        h.bet *= 2
                    h.doubled = True
                    h.cards.append(self.draw())
                    h.done = True
                    self.add_log(f"{p.name} doubles: {pretty(h.cards[-1])} ({describe_hand(h.cards)})")
                elif action == "split":
                    with self.lock:
                        p.chips -= h.bet
                        aces = h.cards[0][0] == "A"
                        second = Hand([h.cards.pop()], h.bet, split_aces=aces, from_split=True)
                        h.split_aces, h.from_split = aces, True
                        hands.insert(i + 1, second)
                    h.cards.append(self.draw())
                    self.add_log(f"{p.name} splits")
                    if aces:
                        h.done = True
                self.changed()
                if hand_value(h.cards)[0] > 21:
                    h.done = True
                    h.result = "bust"
                    h.payout = -h.bet
                    self.add_log(f"{p.name} busts with {hand_value(h.cards)[0]}", "warn")
                if not p.is_human:
                    self.pace()
            i += 1
        self.active[p.id] = None
        self.changed()

    def get_play(self, p, hands, i, legal):
        h = hands[i]
        if p.is_human:
            def validate(d):
                a = d.get("action")
                if a not in legal:
                    raise ValueError(f"choose one of {', '.join(legal)}")
                return a
            return self.ask_human({"type": "bj_play", "legal": legal, "hand_index": i, "value": describe_hand(h.cards)}, validate)

        def validate(r):
            a = str(r.get("action", "")).lower().strip()
            if a == "double" and "double" not in legal:
                a = "hit"
            if a not in legal:
                raise ValueError(f"action must be one of {legal}")
            return a

        lines = [
            f"Round #{self.hand_no}. Your turn.",
            f"Dealer up-card: {pretty(self.dealer[0])} (other card face down).",
        ]
        for k, hk in enumerate(hands):
            marker = "  <-- PLAYING THIS HAND" if k == i else (" (finished)" if hk.done else "")
            lines.append(f"Your hand {k + 1}: {pretty_list(hk.cards)} = {describe_hand(hk.cards)}, bet {hk.bet}{marker}")
        lines.append(f"Your remaining stack: {p.chips}.")
        others = []
        for q in self.players:
            if q is p or not self.hands[q.id]:
                continue
            others.append(f"{q.name}: " + " | ".join(pretty_list(hk.cards) for hk in self.hands[q.id]))
        if others:
            lines.append("Other players' visible cards: " + "; ".join(others))
        lines.append(f"Shoe: {len(self.shoe)} of {self.shoe_total} cards remain.")
        lines.append(f"Your private notes: {p.notes or '(none yet)'}")
        lines.append(f"Recent table talk:\n{self.recent_chat(6)}")
        lines.append(f"Legal actions: {', '.join(legal)}.")
        lines.append('Reply JSON: {"action": "hit|stand|double|split", "table_talk": "...", "notes": "..."}')
        return self.ai_decide(
            p, self.system_prompt(p, self.rules_text()), "\n".join(lines), PLAY_SCHEMA,
            validate=validate, fallback=lambda: basic_strategy(h.cards, self.dealer[0], legal),
        )

    # ------------------------------------------------------------------ settle
    def settle(self, players):
        self.phase = "settle"
        self.dealer_hidden = False
        d_total = hand_value(self.dealer)[0]
        d_bj = is_blackjack(self.dealer)
        summary = []
        for p in players:
            net = 0
            for h in self.hands[p.id]:
                total = hand_value(h.cards)[0]
                natural = is_blackjack(h.cards) and not h.from_split
                if h.result == "bust":
                    pass
                elif natural and not d_bj:
                    h.result, h.payout = "blackjack", h.bet * 3 // 2
                elif d_bj:
                    h.result, h.payout = ("push", 0) if natural else ("lose", -h.bet)
                elif total > 21:
                    h.result, h.payout = "bust", -h.bet
                elif d_total > 21 or total > d_total:
                    h.result, h.payout = "win", h.bet
                elif total == d_total:
                    h.result, h.payout = "push", 0
                else:
                    h.result, h.payout = "lose", -h.bet
                h.done = True
                if h.payout >= 0:
                    p.chips += h.bet + h.payout
                net += h.payout
            word = f"+{net}" if net > 0 else str(net)
            summary.append(f"{p.name} {word}")
            self.add_log(f"{p.name}: {', '.join(h.result for h in self.hands[p.id])} ({word})", "win" if net > 0 else "info")
        self.history.append(f"Round #{self.hand_no}: dealer {describe_hand(self.dealer)}; " + ", ".join(summary))
        self.set_status(f"Dealer: {describe_hand(self.dealer)} · " + " · ".join(summary))

    # ------------------------------------------------------------------ snapshot
    def game_state(self):
        seats = {}
        for p in self.players:
            seats[p.id] = {
                "in_round": self.in_round.get(p.id, False),
                "hands": [h.public() for h in self.hands[p.id]],
                "active_hand": self.active.get(p.id),
            }
        dealer = list(self.dealer)
        if self.dealer_hidden and len(dealer) > 1:
            dealer = [dealer[0], "??"] + dealer[2:]
        return {
            "phase": self.phase,
            "dealer": dealer,
            "dealer_value": "" if self.dealer_hidden else describe_hand(self.dealer),
            "seats": seats,
            "shoe_left": len(self.shoe),
            "shoe_total": self.shoe_total,
            "limits": [self.min_bet, self.max_bet],
        }


def basic_strategy(cards, up_card, legal):
    """Simplified multi-deck basic strategy."""
    up = card_value(up_card)
    total, soft = hand_value(cards)
    can_double = "double" in legal

    def dbl(otherwise):
        return "double" if can_double else otherwise

    if "split" in legal:
        r = card_value(cards[0])
        if r in (11, 8):
            return "split"
        if r == 9 and up not in (7, 10, 11):
            return "split"
        if r == 7 and up <= 7:
            return "split"
        if r == 6 and up <= 6:
            return "split"
        if r == 4 and up in (5, 6):
            return "split"
        if r in (2, 3) and up <= 7:
            return "split"
    if soft:
        if total >= 19:
            return "stand"
        if total == 18:
            if 3 <= up <= 6:
                return dbl("stand")
            return "stand" if up in (2, 7, 8) else "hit"
        if total == 17:
            return dbl("hit") if 3 <= up <= 6 else "hit"
        if total in (15, 16):
            return dbl("hit") if 4 <= up <= 6 else "hit"
        return dbl("hit") if 5 <= up <= 6 else "hit"
    if total >= 17:
        return "stand"
    if total >= 13:
        return "stand" if up <= 6 else "hit"
    if total == 12:
        return "stand" if 4 <= up <= 6 else "hit"
    if total == 11:
        return dbl("hit")
    if total == 10:
        return dbl("hit") if up <= 9 else "hit"
    if total == 9:
        return dbl("hit") if 3 <= up <= 6 else "hit"
    return "hit"
