"""No-limit Texas Hold'em engine."""
import random
from collections import deque

from .base import BaseGame, pick_option
from .cards import new_deck, pretty, pretty_list
from .handeval import best_hand, describe

RULES = """GAME: No-limit Texas Hold'em. Each player has 2 private hole cards; 5 community cards are dealt (flop 3, turn 1, river 1). Best 5-card hand from your 2 + the board wins the pot. Betting is no-limit.
ACTIONS: "fold", "check" (only when nothing to call), "call" (match the current bet; if you lack chips you go all-in), "raise" (amount = the TOTAL you will have bet THIS ROUND after raising, i.e. "raise to"; use it for an opening bet too). Put "amount": 0 unless raising. To go all-in, raise to your maximum."""

SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["fold", "check", "call", "raise"]},
        "amount": {"type": "integer"},
        "table_talk": {"type": "string"},
        "notes": {"type": "string"},
    },
    "required": ["action", "amount", "table_talk", "notes"],
    "additionalProperties": False,
}


class HandState:
    def __init__(self):
        self.hole = []
        self.in_hand = False
        self.folded = False
        self.all_in = False
        self.bet = 0          # chips committed this betting round
        self.contrib = 0      # chips committed this hand
        self.last_action = ""
        self.revealed = False
        self.hand_name = ""
        self.won = 0


class Holdem(BaseGame):
    game_name = "Texas Hold'em"

    def __init__(self, player_specs, options):
        super().__init__(player_specs, options)
        self.small_blind = int(options.get("small_blind", 10))
        self.big_blind = int(options.get("big_blind", 20))
        self.blind_every = int(options.get("blind_increase_every", 0))
        self.hand_pause = float(options.get("hand_pause_seconds", 5))
        self.dealer = random.randrange(len(self.players))
        self.hs = {p.id: HandState() for p in self.players}
        self.board = []
        self.street = ""
        self.current_bet = 0
        self.to_act = None
        self.sb_id = self.bb_id = None
        self.history = []          # list of (street, text)
        self.pots = []             # showdown results for display
        self.past_hands = deque(maxlen=4)
        self.deck = []

    # ------------------------------------------------------------------ helpers
    def blinds(self):
        if self.blind_every > 0:
            level = (self.hand_no - 1) // self.blind_every
            return self.small_blind * 2 ** level, self.big_blind * 2 ** level
        return self.small_blind, self.big_blind

    def seated(self):
        return [p for p in self.players if self.hs[p.id].in_hand]

    def contenders(self):
        return [p for p in self.seated() if not self.hs[p.id].folded]

    def can_act(self, p):
        h = self.hs[p.id]
        return h.in_hand and not h.folded and not h.all_in

    def pot_total(self):
        return sum(h.contrib for h in self.hs.values())

    def next_with_chips(self, idx):
        n = len(self.players)
        for k in range(1, n + 1):
            j = (idx + k) % n
            if self.players[j].chips > 0:
                return j
        return idx

    def put(self, p, amount):
        h = self.hs[p.id]
        amount = min(amount, p.chips)
        p.chips -= amount
        h.bet += amount
        h.contrib += amount
        if p.chips == 0:
            h.all_in = True
        return amount

    def record(self, text):
        self.history.append((self.street, text))
        self.add_log(text)

    # ------------------------------------------------------------------ main loop
    def run(self):
        while True:
            human = next((p for p in self.players if p.is_human), None)
            if human and human.chips == 0 and not getattr(self, "_human_watching", False):
                choice = self.ask_human(
                    {"type": "busted", "message": "You're out of chips!", "options": ["rebuy", "watch"]},
                    pick_option(("rebuy", "watch")),
                )
                if choice == "rebuy":
                    human.chips = int(self.options.get("starting_chips", 1000))
                    self.add_log(f"{human.name} rebuys for {human.chips}.", "info")
                else:
                    self._human_watching = True
            alive = [p for p in self.players if p.chips > 0]
            if len(alive) < 2:
                winner = alive[0] if alive else None
                self.set_status(f"🏆 {winner.name} wins the tournament!" if winner else "Game over.")
                self.add_log(self.status, "win")
                return
            self.play_hand()
            for p in self.players:
                if p.chips == 0 and self.hs[p.id].in_hand:
                    self.add_log(f"{p.name} is out of chips.", "warn")
            for remaining in range(int(self.hand_pause), 0, -1):
                self.set_status(f"Next hand in {remaining}s…")
                self.sleep(1)

    def play_hand(self):
        self.hand_no += 1
        sb_amt, bb_amt = self.blinds()
        self.deck = new_deck()
        self.board, self.history, self.pots = [], [], []
        for p in self.players:
            h = self.hs[p.id] = HandState()
            h.in_hand = p.chips > 0
        self.dealer = self.next_with_chips(self.dealer)
        seated = self.seated()
        n = len(self.players)
        if len(seated) == 2:
            sb_idx = self.dealer
        else:
            sb_idx = self.next_with_chips(self.dealer)
        bb_idx = self.next_with_chips(sb_idx)
        self.sb_id, self.bb_id = self.players[sb_idx].id, self.players[bb_idx].id
        self.street = "preflop"
        self.add_log(f"— Hand #{self.hand_no} — dealer {self.players[self.dealer].name}, blinds {sb_amt}/{bb_amt}", "hand")
        sb, bb = self.players[sb_idx], self.players[bb_idx]
        self.record(f"{sb.name} posts small blind {self.put(sb, sb_amt)}")
        self.record(f"{bb.name} posts big blind {self.put(bb, bb_amt)}")
        self.hs[sb.id].last_action = f"SB {self.hs[sb.id].bet}"
        self.hs[bb.id].last_action = f"BB {self.hs[bb.id].bet}"
        for _ in range(2):
            for p in seated:
                self.hs[p.id].hole.append(self.deck.pop())
        self.set_status("Pre-flop")
        self.betting_round(self.next_with_chips(bb_idx), max(self.hs[bb.id].bet, bb_amt), bb_amt)

        for street, count in (("flop", 3), ("turn", 1), ("river", 1)):
            if len(self.contenders()) <= 1:
                break
            self.street = street
            for p in self.seated():
                h = self.hs[p.id]
                h.bet = 0
                if not h.folded and not h.all_in:
                    h.last_action = ""
            self.deck.pop()  # burn
            self.board += [self.deck.pop() for _ in range(count)]
            self.record(f"{street.upper()}: {pretty_list(self.board)}")
            self.set_status(street.capitalize())
            if sum(1 for p in self.contenders() if self.can_act(p)) >= 2:
                self.betting_round(self.next_with_chips(self.dealer), 0, bb_amt)
            else:
                for p in self.contenders():  # all-in run-out: cards on their backs
                    self.hs[p.id].revealed = True
                self.changed()
                self.sleep(1.5)
        self.to_act = None
        self.settle()
        for h in self.hs.values():  # pot has been paid out
            h.contrib = 0
            h.bet = 0

    # ------------------------------------------------------------------ betting
    def betting_round(self, start_idx, current_bet, min_raise):
        self.current_bet = current_bet
        n = len(self.players)
        need = {p.id for p in self.seated() if self.can_act(p)}
        i = start_idx
        while need:
            if len(self.contenders()) <= 1:
                break
            p = self.players[i % n]
            i += 1
            if p.id not in need:
                continue
            need.discard(p.id)
            if not self.can_act(p):
                continue
            h = self.hs[p.id]
            to_call = self.current_bet - h.bet
            others_active = any(self.can_act(q) for q in self.seated() if q is not p)
            if to_call <= 0 and not others_active:
                continue
            max_to = h.bet + p.chips
            min_to = min(self.current_bet + min_raise, max_to)
            can_raise = p.chips > to_call and others_active
            legal = (["fold", "call"] if to_call > 0 else ["check"]) + (["raise"] if can_raise else [])
            ctx = {
                "to_call": min(to_call, p.chips), "legal": legal, "min_to": min_to, "max_to": max_to,
                "label": "raise" if self.current_bet > 0 else "bet",
            }
            self.to_act = p.id
            self.changed()
            action, amount = self.get_action(p, ctx)
            self.to_act = None
            if action == "fold":
                h.folded = True
                h.last_action = "Fold"
                self.record(f"{p.name} folds")
            elif action == "check":
                h.last_action = "Check"
                self.record(f"{p.name} checks")
            elif action == "call":
                paid = self.put(p, to_call)
                h.last_action = "All-in" if h.all_in else f"Call {paid}"
                self.record(f"{p.name} calls {paid}" + (" (all-in)" if h.all_in else ""))
            else:  # raise to `amount`
                self.put(p, amount - h.bet)
                raise_by = h.bet - self.current_bet
                verb = ctx["label"] + "s"
                if raise_by >= min_raise:
                    min_raise = raise_by
                    self.current_bet = h.bet
                    need = {q.id for q in self.seated() if q is not p and self.can_act(q)}
                else:  # short all-in: only players who haven't matched must act again
                    self.current_bet = max(self.current_bet, h.bet)
                    need |= {q.id for q in self.seated() if q is not p and self.can_act(q) and self.hs[q.id].bet < self.current_bet}
                h.last_action = f"All-in {h.bet}" if h.all_in else f"{ctx['label'].capitalize()} {h.bet}"
                self.record(f"{p.name} {verb} to {h.bet}" + (" (all-in)" if h.all_in else ""))
            if not p.is_human:
                self.pace()

    def normalize(self, ctx, action, amount, strict):
        action = str(action or "").lower().strip().replace("-", "").replace("_", "").replace(" ", "")
        if action in ("bet",):
            action = "raise"
        if action in ("allin", "shove", "jam"):
            action, amount = "raise", ctx["max_to"]
        try:
            amount = int(float(amount or 0))
        except (TypeError, ValueError):
            amount = 0
        legal = ctx["legal"]
        if action == "call" and "call" not in legal:
            action = "check"
        if action == "fold" and "fold" not in legal:
            action = "check"
        if action == "raise" and "raise" not in legal:
            if strict:
                raise ValueError("raising is not allowed right now")
            action = "call" if "call" in legal else "check"
        if action == "check" and "check" not in legal:
            raise ValueError(f"you cannot check, there is {ctx['to_call']} to call")
        if action not in ("fold", "check", "call", "raise"):
            raise ValueError(f"unknown action {action!r}; use one of {legal}")
        if action == "raise":
            if strict and not (ctx["min_to"] <= amount <= ctx["max_to"]):
                raise ValueError(f"raise-to amount must be between {ctx['min_to']} and {ctx['max_to']}")
            amount = max(ctx["min_to"], min(amount, ctx["max_to"]))
        return action, amount

    def get_action(self, p, ctx):
        if p.is_human:
            req = {
                "type": "poker", "legal": ctx["legal"], "to_call": ctx["to_call"],
                "min_to": ctx["min_to"], "max_to": ctx["max_to"], "label": ctx["label"],
                "pot": self.pot_total(), "big_blind": self.blinds()[1], "current_bet": self.current_bet,
            }
            return self.ask_human(req, lambda d: self.normalize(ctx, d.get("action"), d.get("amount"), strict=True))
        system = self.system_prompt(p, RULES)
        prompt = self.build_prompt(p, ctx)
        return self.ai_decide(
            p, system, prompt, SCHEMA,
            validate=lambda r: self.normalize(ctx, r.get("action"), r.get("amount"), strict=False),
            fallback=lambda: self.bot_action(p, ctx),
        )

    # ------------------------------------------------------------------ prompts
    def build_prompt(self, p, ctx):
        h = self.hs[p.id]
        sb, bb = self.blinds()
        lines = [f"Hand #{self.hand_no}. Blinds {sb}/{bb}. Street: {self.street.upper()}."]
        made = ""
        if len(self.board) >= 3:
            made = f"  (your current best hand: {describe(best_hand(h.hole + self.board)[0])})"
        lines.append(f"Your hole cards: {pretty_list(h.hole)}{made}")
        lines.append(f"Board: {pretty_list(self.board)}")
        lines.append(f"Pot (all chips committed so far): {self.pot_total()}")
        lines.append("\nPlayers in seat order (action moves down the list, wrapping around):")
        for q in self.players:
            qh = self.hs[q.id]
            if not qh.in_hand:
                continue
            tags = []
            if self.players[self.dealer].id == q.id:
                tags.append("dealer")
            if q.id == self.sb_id:
                tags.append("SB")
            if q.id == self.bb_id:
                tags.append("BB")
            status = "folded" if qh.folded else ("ALL-IN" if qh.all_in else "active")
            who = f"{q.name}{' (YOU)' if q is p else ''}"
            kind = "human" if q.is_human else q.kind
            lines.append(f"- {who} [{kind}{', ' + '/'.join(tags) if tags else ''}]: stack {q.chips}, bet this round {qh.bet}, {status}")
        lines.append("\nAction this hand:")
        for street, text in self.history:
            lines.append(f"  [{street}] {text}")
        if self.past_hands:
            lines.append("\nRecent hands:")
            lines += [f"  {s}" for s in self.past_hands]
        lines.append(f"\nYour private notes: {p.notes or '(none yet)'}")
        lines.append(f"Recent table talk:\n{self.recent_chat(8)}")
        opts = []
        if "fold" in ctx["legal"]:
            opts.append("fold")
        if "check" in ctx["legal"]:
            opts.append("check")
        if "call" in ctx["legal"]:
            opts.append(f"call ({ctx['to_call']} more{', ALL-IN' if ctx['to_call'] >= p.chips else ''})")
        if "raise" in ctx["legal"]:
            opts.append(f"raise (as a {ctx['label']}; amount = total for this round, from {ctx['min_to']} to {ctx['max_to']}; {ctx['max_to']} = all-in)")
        lines.append(f"\nIT IS YOUR TURN. To call: {ctx['to_call']}. Legal actions: " + "; ".join(opts))
        lines.append('Reply JSON: {"action": "fold|check|call|raise", "amount": <raise-to total or 0>, "table_talk": "...", "notes": "..."}')
        return "\n".join(lines)

    # ------------------------------------------------------------------ local bot
    def equity(self, p, samples=120):
        h = self.hs[p.id]
        opponents = len(self.contenders()) - 1
        if opponents <= 0:
            return 1.0
        known = set(h.hole + self.board)
        rest = [c for c in new_deck() if c not in known]
        wins = 0.0
        need_board = 5 - len(self.board)
        for _ in range(samples):
            draw = random.sample(rest, need_board + 2 * opponents)
            board = self.board + draw[:need_board]
            mine = best_hand(h.hole + board)[0]
            best_other = max(best_hand(draw[need_board + 2 * k: need_board + 2 * k + 2] + board)[0] for k in range(opponents))
            if mine > best_other:
                wins += 1
            elif mine == best_other:
                wins += 0.5
        return wins / samples

    def bot_action(self, p, ctx):
        eq = self.equity(p)
        pot = self.pot_total()
        to_call = ctx["to_call"]
        r = random.random()
        bb = self.blinds()[1]

        def raise_to(size):
            return "raise", max(ctx["min_to"], min(ctx["max_to"], int(size)))

        if to_call == 0:
            if "raise" in ctx["legal"] and (eq > 0.62 or (eq > 0.45 and r < 0.2) or r < 0.05):
                return raise_to(self.current_bet + max(bb * 2, pot * 0.6))
            return "check", 0
        pot_odds = to_call / (pot + to_call)
        if "raise" in ctx["legal"] and (eq > 0.72 or (eq > 0.55 and r < 0.15)):
            return raise_to(self.current_bet * 2.5 + pot * 0.3)
        if eq >= pot_odds + 0.04 or (r < 0.06 and to_call <= bb * 2):
            return "call", 0
        return "fold", 0

    # ------------------------------------------------------------------ showdown
    def settle(self):
        seated = self.seated()
        contenders = self.contenders()
        # Return any uncalled part of the biggest bet.
        by_contrib = sorted(seated, key=lambda q: self.hs[q.id].contrib, reverse=True)
        if len(by_contrib) >= 2:
            top, second = by_contrib[0], by_contrib[1]
            excess = self.hs[top.id].contrib - self.hs[second.id].contrib
            if excess > 0:
                self.hs[top.id].contrib -= excess
                top.chips += excess
                if excess and top.chips > 0:
                    self.hs[top.id].all_in = False
        total = self.pot_total()

        if len(contenders) == 1:
            w = contenders[0]
            w.chips += total
            self.hs[w.id].won = total
            self.pots = [{"name": "Pot", "amount": total, "winners": [w.id]}]
            self.add_log(f"{w.name} wins {total} (everyone else folded)", "win")
            self.past_hands.append(f"Hand #{self.hand_no}: {w.name} won {total} uncontested.")
            self.set_status(f"{w.name} wins {total}")
            return

        scores = {}
        for q in contenders:
            h = self.hs[q.id]
            h.revealed = True
            score, _best5 = best_hand(h.hole + self.board)
            scores[q.id] = score
            h.hand_name = describe(score)
            self.add_log(f"{q.name} shows {pretty_list(h.hole)} — {h.hand_name}")

        levels = sorted({self.hs[q.id].contrib for q in seated if self.hs[q.id].contrib > 0})
        order = self.players[self.dealer + 1:] + self.players[:self.dealer + 1]
        prev = 0
        pots = []
        for lvl in levels:
            amount = sum(min(self.hs[q.id].contrib, lvl) - min(self.hs[q.id].contrib, prev) for q in seated)
            prev = lvl
            eligible = [q for q in contenders if self.hs[q.id].contrib >= lvl] or contenders
            ids = sorted(q.id for q in eligible)
            if pots and pots[-1]["eligible"] == ids:
                pots[-1]["amount"] += amount
            else:
                pots.append({"amount": amount, "eligible": ids})
        summaries = []
        for idx, pot in enumerate(pots):
            best = max(scores[i] for i in pot["eligible"])
            winners = [q for q in order if q.id in pot["eligible"] and scores[q.id] == best]
            share, rem = divmod(pot["amount"], len(winners))
            for k, w in enumerate(winners):
                won = share + (rem if k == 0 else 0)
                w.chips += won
                self.hs[w.id].won += won
            name = "Main pot" if idx == 0 else f"Side pot {idx}"
            pot.update(name=name, winners=[w.id for w in winners])
            names = " & ".join(w.name for w in winners)
            self.add_log(f"{names} win{'s' if len(winners) == 1 else ''} {name.lower()} ({pot['amount']}) with {describe(best)}", "win")
            summaries.append(f"{names} won {pot['amount']} with {describe(best)}")
        self.pots = pots
        shown = "; ".join(f"{q.name} showed {pretty_list(self.hs[q.id].hole)}" for q in contenders)
        self.past_hands.append(f"Hand #{self.hand_no}: board {pretty_list(self.board)}. {shown}. " + "; ".join(summaries))
        self.set_status(" · ".join(summaries))

    # ------------------------------------------------------------------ snapshot
    def game_state(self):
        seats = {}
        for p in self.players:
            h = self.hs[p.id]
            visible = self.show_cards_of(p, h.revealed)
            seats[p.id] = {
                "in_hand": h.in_hand, "folded": h.folded, "all_in": h.all_in,
                "bet": h.bet, "last_action": h.last_action,
                "cards": list(h.hole) if visible else ["??"] * len(h.hole),
                "hand_name": h.hand_name, "won": h.won,
            }
        human = next((p for p in self.players if p.is_human), None)
        human_hand = ""
        if human and self.hs[human.id].hole and len(self.board) >= 3 and not self.hs[human.id].folded:
            human_hand = describe(best_hand(self.hs[human.id].hole + self.board)[0])
        return {
            "board": list(self.board),
            "pot": self.pot_total(),
            "street": self.street,
            "dealer_id": self.players[self.dealer].id,
            "sb_id": self.sb_id, "bb_id": self.bb_id,
            "to_act": self.to_act,
            "seats": seats,
            "pots": self.pots,
            "blinds": list(self.blinds()),
            "human_hand": human_hand,
        }
