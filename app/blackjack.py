"""Sdílená karetní logika blackjacku (hodnota ruky, esa, blackjack-check).

Používá ji multiplayer stůl (app.bj_room). Solo blackjack proti dealerovi byl odstraněn.
"""
MIN_BET = 10
MAX_BET = 2000
_RANKS = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "T", "J", "Q", "K"]
_SUITS = ["S", "H", "D", "C"]


def _val(rank: str) -> int:
    if rank == "A":
        return 11
    if rank in ("T", "J", "Q", "K"):
        return 10
    return int(rank)


def hand_value(cards) -> int:
    """Nejlepší hodnota ruky (esa 11 nebo 1, aby se nepřebralo, když to jde)."""
    total = sum(_val(c[0]) for c in cards)
    aces = sum(1 for c in cards if c[0] == "A")
    while total > 21 and aces:
        total -= 10
        aces -= 1
    return total


def _is_bj(cards) -> bool:
    """Blackjack = 21 ze dvou karet."""
    return len(cards) == 2 and hand_value(cards) == 21
