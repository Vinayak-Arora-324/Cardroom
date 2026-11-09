"""Contract Bridge: four seats, two partnerships, duplicate scoring.

Seats are numbered clockwise: 0 South, 1 West, 2 North, 3 East, so a player's
left-hand opponent is pid + 1 and their partner is pid + 2.
"""
import random

from . import config
from .base import BaseGame
from .cards import RANK_VALUE, new_deck, pretty

COMPASS = ["South", "West", "North", "East"]
LETTER = "SWNE"
STRAINS = ["C", "D", "H", "S", "NT"]
SYMBOL = {"C": "♣", "D": "♦", "H": "♥", "S": "♠", "NT": "NT"}
SUITS = "shdc"
SUIT_SYMBOL = {"s": "♠", "h": "♥", "d": "♦", "c": "♣"}
ALL_BIDS = [f"{level}{strain}" for level in range(1, 8) for strain in STRAINS]
# Standard duplicate board rotation. Board 1 is dealt by North.
DEALER_CYCLE = [2, 3, 0, 1]
VULNERABILITY = ["", "NS", "EW", "Both", "NS", "EW", "Both", "", "EW", "Both", "", "NS", "Both", "", "NS", "EW"]
HCP = {"A": 4, "K": 3, "Q": 2, "J": 1}

RULES = """GAME: Contract Bridge, duplicate scoring. Four players in two partnerships: North-South against East-West. 13 cards each.
AUCTION: calls go clockwise from the dealer. A bid is a level (1-7) and a strain, ranked C < D < H < S < NT, and must outrank the previous bid. "X" doubles an opponent's bid, "XX" redoubles an opponent's double. Three passes after a bid end the auction; four opening passes throw the board in. A level N contract means winning 6+N tricks. The player of the declaring side who FIRST named the strain is declarer.
BIDDING SYSTEM (your partner assumes this): Standard American. 5-card majors, 1NT = 15-17 balanced, 2NT = 20-21 balanced, 2C = strong and artificial; a new suit by a responder is forcing for one round.
PLAY: the player on declarer's left leads, then dummy (declarer's partner) lays their hand face up and declarer plays both hands. You must follow suit if you can. Highest trump wins the trick, otherwise the highest card of the suit led. The winner leads next.
SCORING: game bonus for 100+ contract points (3NT, 4H/4S, 5C/5D), slam bonuses at the 6 and 7 levels, and penalties for going down, which are larger when vulnerable or doubled."""


def team(pid):
    return "NS" if pid % 2 == 0 else "EW"


def is_bid(call):
    return call[0].isdigit()


def bid_rank(call):
    return (int(call[0]) - 1) * 5 + STRAINS.index(call[1:])


def pretty_call(call):
    return f"{call[0]}{SYMBOL[call[1:]]}" if is_bid(call) else call


def legal_calls(auction, pid):
    """auction is a list of (pid, call). Returns the calls open to pid."""
    calls = ["Pass"]
    meaningful = [(p, c) for p, c in auction if c != "Pass"]
    last_bid = next((c for _, c in reversed(meaningful) if is_bid(c)), None)
    if meaningful:
        who, last = meaningful[-1]
        if team(who) != team(pid):
            if is_bid(last):
                calls.append("X")
            elif last == "X":
                calls.append("XX")
    floor = bid_rank(last_bid) if last_bid else -1
    return calls + [b for b in ALL_BIDS if bid_rank(b) > floor]


def auction_over(auction):
    calls = [c for _, c in auction]
    if len(calls) < 4:
        return False
    if all(c == "Pass" for c in calls):
        return True
    return calls[-3:] == ["Pass"] * 3


def contract_from(auction):
    """Returns the contract dict, or None if the board was passed out."""
    last_index = next((i for i in range(len(auction) - 1, -1, -1) if is_bid(auction[i][1])), None)
    if last_index is None:
        return None
    bidder, call = auction[last_index]
    level, strain = int(call[0]), call[1:]
    after = [c for _, c in auction[last_index + 1:]]
    doubled = 2 if "XX" in after else 1 if "X" in after else 0
    declarer = next(p for p, c in auction if is_bid(c) and c[1:] == strain and team(p) == team(bidder))
    return {"level": level, "strain": strain, "doubled": doubled, "declarer": declarer, "dummy": (declarer + 2) % 4}


def contract_text(c):
    return f"{c['level']}{SYMBOL[c['strain']]}{['', ' X', ' XX'][c['doubled']]}"


def score_contract(level, strain, doubled, vulnerable, tricks):
    """Duplicate score for the declaring side (negative when the contract fails)."""
    need = 6 + level
    if tricks < need:
        down = need - tricks
        if doubled == 0:
            return -down * (100 if vulnerable else 50)
        if vulnerable:
            penalty = 200 + 300 * (down - 1)
        else:
            penalty = 100 + 200 * min(down - 1, 2) + 300 * max(down - 3, 0)
        return -penalty * doubled
    per_trick = 20 if strain in ("C", "D") else 30
    base = 40 + 30 * (level - 1) if strain == "NT" else per_trick * level
    contract_points = base * [1, 2, 4][doubled]
    score = contract_points
    score += (500 if vulnerable else 300) if contract_points >= 100 else 50
    if level == 6:
        score += 750 if vulnerable else 500
    elif level == 7:
        score += 1500 if vulnerable else 1000
    score += [0, 50, 100][doubled]
    over = tricks - need
    if doubled == 0:
        score += over * per_trick
    else:
        score += over * (200 if vulnerable else 100) * doubled
    return score


def trump_suit(strain):
    return None if strain == "NT" else strain.lower()


def trick_winner(trick, trump):
    """trick is a list of (pid, card). Returns the winning pid."""
    led = trick[0][1][1]

    def strength(card):
        suit_bonus = 200 if card[1] == trump else 100 if card[1] == led else 0
        return suit_bonus + RANK_VALUE[card[0]]

    return max(trick, key=lambda play: strength(play[1]))[0]


def sort_hand(cards):
    return sorted(cards, key=lambda c: (SUITS.index(c[1]), -RANK_VALUE[c[0]]))


def legal_cards(hand, trick):
    if trick:
        led = trick[0][1][1]
        follow = [c for c in hand if c[1] == led]
        if follow:
            return follow
    return list(hand)


def hand_text(cards):
    rows = []
    for suit in SUITS:
        ranks = [pretty(c)[:-1] for c in sort_hand(cards) if c[1] == suit]
        rows.append(f"{SUIT_SYMBOL[suit]} {' '.join(ranks) if ranks else '-'}")
    return "   ".join(rows)


def high_card_points(cards):
    return sum(HCP.get(c[0], 0) for c in cards)


def suit_lengths(cards):
    return {s: sum(1 for c in cards if c[1] == s) for s in SUITS}


def is_balanced(cards):
    return sorted(suit_lengths(cards).values()) in ([3, 3, 3, 4], [2, 3, 4, 4], [2, 3, 3, 5])


def normalize_call(raw, legal):
    text = str(raw or "").strip().upper().replace(" ", "")
    for symbol, letter in (("♠", "S"), ("♥", "H"), ("♦", "D"), ("♣", "C")):
        text = text.replace(symbol, letter)
    text = {"P": "Pass", "PASS": "Pass", "DOUBLE": "X", "REDOUBLE": "XX"}.get(text, text)
    if len(text) == 2 and text.endswith("N") and text[0].isdigit():
        text += "T"
    if text not in legal:
        raise ValueError(f"{raw!r} is not a legal call; choose one of {', '.join(legal)}")
    return text


def normalize_card(raw, legal):
    text = str(raw or "").strip().replace(" ", "")
    for symbol, letter in (("♠", "s"), ("♥", "h"), ("♦", "d"), ("♣", "c")):
        text = text.replace(symbol, letter)
    if text.startswith("10"):
        text = "T" + text[2:]
    if len(text) == 2:
        text = text[0].upper() + text[1].lower()
    if text not in legal:
        raise ValueError(f"{raw!r} is not a legal card; choose one of {', '.join(legal)}")
    return text


class Bridge(BaseGame):
    game_name = "Contract Bridge"

    def __init__(self, player_specs, options):
        if len(player_specs) != 4:
            raise ValueError("Bridge needs exactly 4 players")
        super().__init__(player_specs, {**options, "starting_chips": 0})
        self.boards = max(1, int(options.get("boards", 4)))
        self.board_pause = float(options.get("hand_pause_seconds", 8))
        self.hands = {p.id: [] for p in self.players}
        self.auction = []
        self.contract = None
        self.dealer = 0
        self.to_act = None
        self.phase = ""
        self.trick = []
        self.completed = []       # finished tricks: (leader_pid, [(pid, card)...], winner_pid)
        self.played = set()
        self.tricks = {"NS": 0, "EW": 0}
        self.scores = {"NS": 0, "EW": 0}
        self.dummy_visible = False
        self.result = ""
        self.board_results = []

    # ------------------------------------------------------------------ helpers
    def vulnerable(self, side):
        v = VULNERABILITY[(self.hand_no - 1) % 16]
        return v == "Both" or v == side

    def seat_label(self, pid):
        return f"{COMPASS[pid]} ({self.players[pid].name})"

    def system_prompt(self, player, game_rules):
        persona = player.personality or "You are a sharp, sociable card player with a dry sense of humour."
        return (
            f"You are \"{player.name}\", an AI player at a casual {self.game_name} table on someone's laptop. "
            "Your goal is to score as many points as you can for your partnership.\n"
            f"Personality: {persona}\n\n{game_rules}\n\n"
            "Respond with ONE JSON object only, matching the requested schema. "
            "\"table_talk\" is optional banter (under 20 words, empty string to stay quiet). Never mention your cards, your points or your plans: signalling to your partner is cheating in bridge. "
            "\"notes\" is your private memory for later boards (max 300 chars: opponents' tendencies, partnership lessons); it replaces your previous notes."
        )

    # ------------------------------------------------------------------ main loop
    def run(self):
        for _ in range(self.boards):
            self.play_board()
            if self.hand_no < self.boards:
                for remaining in range(int(self.board_pause), 0, -1):
                    self.set_status(f"{self.result} · Next board in {remaining}s…")
                    self.sleep(1)
        ns, ew = self.scores["NS"], self.scores["EW"]
        names = {side: " & ".join(p.name for p in self.players if team(p.id) == side) for side in ("NS", "EW")}
        if ns == ew:
            final = f"Match tied at {ns}."
        else:
            winner = "NS" if ns > ew else "EW"
            final = f"{names[winner]} win the match, {max(ns, ew)} to {min(ns, ew)}."
        self.add_log(final, "win")
        self.set_status(final)

    def play_board(self):
        self.hand_no += 1
        self.dealer = DEALER_CYCLE[(self.hand_no - 1) % 4]
        deck = new_deck()
        with self.lock:
            for i, p in enumerate(self.players):
                self.hands[p.id] = sort_hand(deck[i * 13:(i + 1) * 13])
            self.auction, self.trick, self.completed = [], [], []
            self.contract, self.result = None, ""
            self.played = set()
            self.tricks = {"NS": 0, "EW": 0}
            self.dummy_visible = False
            self.phase = "auction"
        vul = VULNERABILITY[(self.hand_no - 1) % 16] or "None"
        self.add_log(f"— Board #{self.hand_no} — dealer {self.seat_label(self.dealer)}, vulnerable: {vul}", "hand")
        self.run_auction()
        if self.contract is None:
            self.finish_board("Passed out", 0)
            return
        self.add_log(f"Contract: {contract_text(self.contract)} by {self.seat_label(self.contract['declarer'])}", "hand")
        self.run_play()
        self.score_board()

    def finish_board(self, text, score_line):
        self.phase = "done"
        self.to_act = None
        self.result = text
        self.board_results.append(f"Board #{self.hand_no}: {text}")
        self.add_log(text, "win" if score_line else "info")
        self.set_status(text)

    def score_board(self):
        c = self.contract
        side = team(c["declarer"])
        other = "EW" if side == "NS" else "NS"
        made = self.tricks[side]
        score = score_contract(c["level"], c["strain"], c["doubled"], self.vulnerable(side), made)
        winner, points = (side, score) if score > 0 else (other, -score)
        with self.lock:
            self.scores[winner] += points
            for p in self.players:
                p.chips = self.scores[team(p.id)]
        diff = made - (6 + c["level"])
        outcome = "made exactly" if diff == 0 else f"made +{diff}" if diff > 0 else f"down {-diff}"
        self.finish_board(f"{contract_text(c)} by {COMPASS[c['declarer']]}, {outcome}: {winner} +{points}", points)

    # ------------------------------------------------------------------ auction
    def run_auction(self):
        self.set_status("Auction")
        pid = self.dealer
        while not auction_over(self.auction):
            legal = legal_calls(self.auction, pid)
            self.to_act = pid
            self.changed()
            call = self.get_call(pid, legal)
            self.to_act = None
            with self.lock:
                self.auction.append((pid, call))
            verb = "passes" if call == "Pass" else "doubles" if call == "X" else "redoubles" if call == "XX" else f"bids {pretty_call(call)}"
            self.add_log(f"{self.players[pid].name} {verb}")
            if not self.players[pid].is_human:
                self.pace()
            pid = (pid + 1) % 4
        self.contract = contract_from(self.auction)

    def call_schema(self, legal):
        return {
            "type": "object",
            "properties": {
                "call": {"type": "string", "enum": legal},
                "table_talk": {"type": "string"},
                "notes": {"type": "string"},
            },
            "required": ["call", "table_talk", "notes"],
            "additionalProperties": False,
        }

    def card_schema(self, legal):
        return {
            "type": "object",
            "properties": {
                "card": {"type": "string", "enum": legal},
                "table_talk": {"type": "string"},
                "notes": {"type": "string"},
            },
            "required": ["card", "table_talk", "notes"],
            "additionalProperties": False,
        }

    def header(self, pid):
        side = team(pid)
        other = "EW" if side == "NS" else "NS"
        vul = f"your side {'IS' if self.vulnerable(side) else 'is NOT'} vulnerable, opponents {'ARE' if self.vulnerable(other) else 'are NOT'}"
        return (
            f"Board #{self.hand_no} of {self.boards}. You are {COMPASS[pid]}; your partner is {COMPASS[(pid + 2) % 4]}; "
            f"left-hand opponent {COMPASS[(pid + 1) % 4]}, right-hand opponent {COMPASS[(pid + 3) % 4]}. {vul.capitalize()}.\n"
            f"Match score: NS {self.scores['NS']}, EW {self.scores['EW']}."
        )

    def auction_text(self):
        return "\n".join(f"  {COMPASS[p]}: {pretty_call(c)}" for p, c in self.auction) or "  (no calls yet)"

    def memory_text(self, p):
        # Table talk is left out on purpose: passing banter to the bots would be a channel between partners.
        recent = "\n".join(f"  {r}" for r in self.board_results[-3:]) or "  (first board)"
        return f"Recent boards:\n{recent}\nYour private notes: {p.notes or '(none yet)'}"

    def get_call(self, pid, legal):
        p = self.players[pid]
        if p.is_human:
            return self.ask_human(
                {"type": "bridge_bid", "legal": legal},
                lambda d: normalize_call(d.get("call"), legal),
            )
        hand = self.hands[pid]
        lengths = suit_lengths(hand)
        prompt = "\n".join([
            self.header(pid),
            f"Dealer: {COMPASS[self.dealer]}.",
            f"Your hand: {hand_text(hand)}",
            f"High-card points: {high_card_points(hand)}. Shape (S-H-D-C): {lengths['s']}-{lengths['h']}-{lengths['d']}-{lengths['c']}.",
            f"Auction so far:\n{self.auction_text()}",
            self.memory_text(p),
            f"IT IS YOUR CALL. Legal calls: {', '.join(legal)}",
            'Reply JSON: {"call": "<one legal call>", "table_talk": "...", "notes": "..."}',
        ])
        return self.ai_decide(
            p, self.system_prompt(p, RULES), prompt, self.call_schema(legal),
            validate=lambda r: normalize_call(r.get("call"), legal),
            fallback=lambda: self.bot_call(pid, legal),
        )

    # ------------------------------------------------------------------ play
    def controller(self, pid):
        """Declarer plays dummy's cards."""
        return self.contract["declarer"] if pid == self.contract["dummy"] else pid

    def run_play(self):
        self.phase = "play"
        leader = (self.contract["declarer"] + 1) % 4
        trump = trump_suit(self.contract["strain"])
        for number in range(1, 14):
            with self.lock:
                self.trick = []
            self.set_status(f"Trick {number} of 13")
            pid = leader
            for _ in range(4):
                card = self.get_card(pid)
                with self.lock:
                    self.hands[pid].remove(card)
                    self.trick.append((pid, card))
                    self.played.add(card)
                    self.dummy_visible = True  # dummy goes down after the opening lead
                self.to_act = None
                self.add_log(f"{self.players[self.controller(pid)].name} plays {pretty(card)}" + (" from dummy" if pid == self.contract["dummy"] else ""))
                if not self.players[self.controller(pid)].is_human:
                    self.pace()
                pid = (pid + 1) % 4
            winner = trick_winner(self.trick, trump)
            with self.lock:
                self.tricks[team(winner)] += 1
                self.completed.append((leader, list(self.trick), winner))
            self.add_log(f"{self.players[winner].name} wins the trick (NS {self.tricks['NS']}, EW {self.tricks['EW']})")
            self.sleep(min(1.5, float(config.get().get("ai_pace_seconds", 1.0)) * 1.5))
            leader = winner

    def get_card(self, pid):
        hand = self.hands[pid]
        legal = legal_cards(hand, self.trick)
        ctrl = self.controller(pid)
        self.to_act = pid
        self.changed()
        if len(legal) == 1:  # no decision to make, so don't spend a model call on it
            self.sleep(min(0.4, float(config.get().get("ai_pace_seconds", 1.0))))
            return legal[0]
        p = self.players[ctrl]
        if p.is_human:
            return self.ask_human(
                {"type": "bridge_play", "legal": legal, "hand_owner": pid, "from_dummy": pid != ctrl},
                lambda d: normalize_card(d.get("card"), legal),
            )
        return self.ai_decide(
            p, self.system_prompt(p, RULES), self.play_prompt(pid, ctrl, legal), self.card_schema(legal),
            validate=lambda r: normalize_card(r.get("card"), legal),
            fallback=lambda: self.bot_card(pid, legal),
        )

    def play_prompt(self, pid, ctrl, legal):
        c = self.contract
        dummy = c["dummy"]
        side = team(c["declarer"])
        lines = [
            self.header(ctrl),
            f"Contract: {contract_text(c)} by {COMPASS[c['declarer']]}. Dummy: {COMPASS[dummy]}. "
            f"Declaring side needs {6 + c['level']} tricks; trumps: {SYMBOL[c['strain']] if c['strain'] != 'NT' else 'none'}.",
            f"Tricks so far: {side} {self.tricks[side]}, defenders {self.tricks['EW' if side == 'NS' else 'NS']}.",
            f"Auction:\n{self.auction_text()}",
        ]
        if pid == ctrl:
            lines.append(f"You are playing from YOUR OWN hand: {hand_text(self.hands[pid])}")
            if self.dummy_visible and pid != dummy:
                lines.append(f"Dummy ({COMPASS[dummy]}), face up: {hand_text(self.hands[dummy])}")
        else:
            lines.append(f"You are declarer and are playing from DUMMY ({COMPASS[pid]}): {hand_text(self.hands[pid])}")
            lines.append(f"Your own hand ({COMPASS[ctrl]}): {hand_text(self.hands[ctrl])}")
        if self.completed:
            lines.append("Previous tricks:")
            for n, (_, plays, winner) in enumerate(self.completed, 1):
                played = ", ".join(f"{COMPASS[p]} {pretty(card)}" for p, card in plays)
                lines.append(f"  {n}. {played} -> won by {COMPASS[winner]}")
        if self.trick:
            lines.append("Current trick: " + ", ".join(f"{COMPASS[p]} {pretty(card)}" for p, card in self.trick))
        else:
            lines.append("Current trick: you lead.")
        lines.append(self.memory_text(self.players[ctrl]))
        lines.append(f"IT IS YOUR PLAY. Legal cards (rank + suit, T = ten): {', '.join(legal)}")
        lines.append('Reply JSON: {"card": "<one legal card>", "table_talk": "...", "notes": "..."}')
        return "\n".join(lines)

    # ------------------------------------------------------------------ local bot
    def bot_call(self, pid, legal):
        hand = self.hands[pid]
        hcp = high_card_points(hand)
        lengths = suit_lengths(hand)
        partner = (pid + 2) % 4
        points = hcp + sum(max(0, n - 4) for n in lengths.values())
        bids = [(p, c) for p, c in self.auction if is_bid(c)]
        partner_bids = [c for p, c in bids if p == partner]

        def cheapest(strain, max_level):
            for level in range(1, max_level + 1):
                call = f"{level}{strain}"
                if call in legal:
                    return call
            return None

        if not bids:
            if is_balanced(hand) and 15 <= hcp <= 17:
                return "1NT"
            if is_balanced(hand) and 20 <= hcp <= 21:
                return "2NT"
            if hcp >= 22:
                return "2C"
            if hcp < 12:
                return "Pass"
            if lengths["s"] >= 5 or lengths["h"] >= 5:
                return "1S" if lengths["s"] >= lengths["h"] else "1H"
            return "1D" if lengths["d"] >= 4 and lengths["d"] >= lengths["c"] else "1C"

        if not partner_bids:
            if any(p == pid for p, _ in bids) or hcp < 9:
                return "Pass"
            if is_balanced(hand) and hcp >= 15:
                return cheapest("NT", 2) or "Pass"
            suit = max(SUITS, key=lambda s: (lengths[s], "cdhs".index(s)))
            if lengths[suit] >= 5:
                return cheapest(suit.upper(), 2) or "Pass"
            return "Pass"

        # Partner has shown something: estimate the combined strength and pick a target contract.
        first_index = next(i for i, (p, _) in enumerate(bids) if p == partner)
        first = bids[first_index][1]
        if first == "2C":
            partner_points = 22
        elif first == "2NT":
            partner_points = 20
        elif first == "1NT":
            partner_points = 16
        elif first_index == 0:
            partner_points = 13
        elif any(p == pid for p, _ in bids[:first_index]):
            partner_points = 7
        else:
            partner_points = 10
        total = points + partner_points
        last_suit = next((c[1:] for c in reversed(partner_bids) if c[1:] != "NT"), None)
        strain = "NT"
        if last_suit and lengths[last_suit.lower()] >= 3:
            strain = last_suit
        else:
            longest = max("hs", key=lambda s: lengths[s])
            if lengths[longest] >= 5:
                strain = longest.upper()
        if strain in ("C", "D") and 25 <= total < 29:
            strain = "NT"

        if total >= 37:
            level = 7
        elif total >= 33:
            level = 6
        elif strain == "NT":
            level = 3 if total >= 25 else 2 if total >= 23 else 1
        elif strain in ("H", "S"):
            level = 4 if total >= 25 else 3 if total >= 22 else 2 if total >= 19 else 1
        else:
            level = 5 if total >= 29 else 3 if total >= 22 else 2 if total >= 19 else 1
        target = f"{level}{strain}"
        if target in legal:
            return target
        last_bidder, last_bid = bids[-1]
        if "X" in legal and team(last_bidder) != team(pid) and bid_rank(last_bid) >= bid_rank("3C") and hcp >= 13:
            return "X"
        return "Pass"

    def is_master(self, card, hand):
        """True when no unseen card of the suit outranks this one."""
        value = RANK_VALUE[card[0]]
        for rank, other in RANK_VALUE.items():
            code = rank + card[1]
            if other > value and code not in self.played and code not in hand:
                return False
        return True

    def bot_card(self, pid, legal, rng=random):
        hand = self.hands[pid]
        trump = trump_suit(self.contract["strain"])
        low_first = sorted(legal, key=lambda c: RANK_VALUE[c[0]])
        lowest = low_first[0]
        lengths = suit_lengths(hand)

        def discard():
            options = [c for c in low_first if c[1] != trump] or low_first
            return min(options, key=lambda c: (-lengths[c[1]], RANK_VALUE[c[0]]))

        if not self.trick:
            declaring = team(pid) == team(self.contract["declarer"])
            if declaring and trump:
                ours = set(self.hands[pid]) | set(self.hands[(pid + 2) % 4])
                outstanding = [r + trump for r in RANK_VALUE if r + trump not in self.played and r + trump not in ours]
                held = [c for c in low_first if c[1] == trump]
                if outstanding and held:
                    return held[-1] if self.is_master(held[-1], ours) else held[0]
            for card in reversed(low_first):
                if card[1] != trump and self.is_master(card, hand):
                    return card
            pool = [c for c in low_first if c[1] != trump] or low_first
            suit = max({c[1] for c in pool}, key=lambda s: (lengths[s], rng.random()))
            in_suit = [c for c in pool if c[1] == suit]
            top = in_suit[-1]
            if len(in_suit) >= 2 and RANK_VALUE[top[0]] >= 10 and RANK_VALUE[top[0]] - RANK_VALUE[in_suit[-2][0]] == 1:
                return top
            return in_suit[0]

        current = trick_winner(self.trick, trump)
        partner_winning = current == (pid + 2) % 4
        wins = [c for c in low_first if trick_winner(self.trick + [(pid, c)], trump) == pid]
        if partner_winning:
            return lowest if low_first[0][1] == self.trick[0][1][1] else discard()
        if wins:
            if len(self.trick) == 2 and wins[-1][1] != trump:
                return wins[-1]
            if len(self.trick) == 1 and wins[0][1] != trump and not self.is_master(wins[-1], hand):
                return lowest
            return wins[0]
        return lowest if low_first[0][1] == self.trick[0][1][1] else discard()

    # ------------------------------------------------------------------ snapshot
    def game_state(self):
        has_human = any(p.is_human for p in self.players)
        seats = {}
        c = self.contract
        for p in self.players:
            is_dummy = bool(c and c["dummy"] == p.id and self.dummy_visible)
            visible = self.show_cards_of(p, is_dummy or not has_human)
            seats[p.id] = {
                "compass": LETTER[p.id],
                "compass_name": COMPASS[p.id],
                "team": team(p.id),
                "cards": list(self.hands[p.id]) if visible else None,
                "count": len(self.hands[p.id]),
                "is_dummy": is_dummy,
                "is_declarer": bool(c and c["declarer"] == p.id),
            }
        last = self.completed[-1] if self.completed else None
        return {
            "phase": self.phase,
            "boards": self.boards,
            "dealer_id": self.dealer,
            "vulnerable": VULNERABILITY[(self.hand_no - 1) % 16] if self.hand_no else "",
            "auction": [{"pid": p, "call": call, "text": pretty_call(call)} for p, call in self.auction],
            "contract": ({**c, "text": contract_text(c)} if c else None),
            "to_act": self.to_act,
            "seats": seats,
            "trick": [{"pid": p, "card": card} for p, card in self.trick],
            "last_winner": last[2] if last and len(self.trick) == 4 else None,
            "tricks": dict(self.tricks),
            "scores": dict(self.scores),
            "result": self.result,
        }
