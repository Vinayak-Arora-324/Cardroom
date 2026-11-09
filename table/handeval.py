"""7-card poker hand evaluator. Higher tuple == better hand."""
from collections import Counter
from itertools import combinations

from .cards import RANK_VALUE

CATEGORY_NAMES = [
    "High Card", "Pair", "Two Pair", "Three of a Kind", "Straight",
    "Flush", "Full House", "Four of a Kind", "Straight Flush",
]


def _straight_high(values):
    """values: set of ints. Returns high card of best straight or None."""
    vals = set(values)
    if 14 in vals:
        vals.add(1)
    for high in range(14, 4, -1):
        if all(v in vals for v in range(high - 4, high + 1)):
            return high
    return None


def eval5(cards):
    values = sorted((RANK_VALUE[c[0]] for c in cards), reverse=True)
    suits = [c[1] for c in cards]
    flush = len(set(suits)) == 1
    straight = _straight_high(values) if len(set(values)) == 5 else None
    counts = Counter(values)
    # sort by (count, value) desc
    groups = sorted(counts.items(), key=lambda kv: (kv[1], kv[0]), reverse=True)
    shape = [g[1] for g in groups]
    ranked = [g[0] for g in groups]
    if straight and flush:
        return (8, straight)
    if shape[0] == 4:
        return (7, *ranked)
    if shape == [3, 2]:
        return (6, *ranked)
    if flush:
        return (5, *values)
    if straight:
        return (4, straight)
    if shape[0] == 3:
        return (3, *ranked)
    if shape == [2, 2, 1]:
        return (2, *ranked)
    if shape[0] == 2:
        return (1, *ranked)
    return (0, *values)


def best_hand(cards):
    """Best 5-card score from 5-7 cards. Returns (score_tuple, best5_cards)."""
    best = None
    best_combo = None
    for combo in combinations(cards, 5):
        s = eval5(combo)
        if best is None or s > best:
            best, best_combo = s, combo
    return best, list(best_combo)


RANK_NAMES = {2: "Twos", 3: "Threes", 4: "Fours", 5: "Fives", 6: "Sixes", 7: "Sevens", 8: "Eights",
              9: "Nines", 10: "Tens", 11: "Jacks", 12: "Queens", 13: "Kings", 14: "Aces"}
HIGH_NAMES = {**{v: n[:-1] for v, n in RANK_NAMES.items()}, 6: "Six"}


def describe(score):
    cat, ranks = score[0], score[1:]
    if cat == 0:
        return f"{HIGH_NAMES[ranks[0]]} High"
    if cat == 1:
        return f"Pair of {RANK_NAMES[ranks[0]]}"
    if cat == 2:
        return f"Two Pair, {RANK_NAMES[ranks[0]]} & {RANK_NAMES[ranks[1]]}"
    if cat == 3:
        return f"Three {RANK_NAMES[ranks[0]]}"
    if cat == 4:
        return f"{HIGH_NAMES[ranks[0]]}-high Straight"
    if cat == 5:
        return f"{HIGH_NAMES[ranks[0]]}-high Flush"
    if cat == 6:
        return f"Full House, {RANK_NAMES[ranks[0]]} full of {RANK_NAMES[ranks[1]]}"
    if cat == 7:
        return f"Four {RANK_NAMES[ranks[0]]}"
    return "Royal Flush" if ranks[0] == 14 else f"{HIGH_NAMES[ranks[0]]}-high Straight Flush"
