import random

RANKS = "23456789TJQKA"
SUITS = "shdc"  # spades hearts diamonds clubs
RANK_VALUE = {r: i + 2 for i, r in enumerate(RANKS)}


def new_deck(n_decks=1, rng=None):
    cards = [r + s for _ in range(n_decks) for r in RANKS for s in SUITS]
    (rng or random).shuffle(cards)
    return cards


def pretty(card):
    """'Ah' -> 'A♥' (for prompts and logs)."""
    sym = {"s": "♠", "h": "♥", "d": "♦", "c": "♣"}[card[1]]
    rank = "10" if card[0] == "T" else card[0]
    return rank + sym


def pretty_list(cards):
    return " ".join(pretty(c) for c in cards) if cards else "(none)"
