"""Sdílená karetní logika blackjacku (hodnota ruky / esa / blackjack-check).
Solo blackjack byl odstraněn; herní logiku stolu kryje test_bj_room.py.

    .venv/Scripts/python.exe -m pytest tests/test_blackjack.py -v
"""
from app import blackjack


def test_hand_value_aces(client):
    assert blackjack.hand_value(["AS", "KD"]) == 21
    assert blackjack.hand_value(["AS", "AD", "9C"]) == 21       # 11 + 1 + 9
    assert blackjack.hand_value(["AS", "AD", "AC", "8H"]) == 21
    assert blackjack.hand_value(["KS", "QD", "2C"]) == 22        # přebral
    assert blackjack.hand_value(["AS", "6D"]) == 17             # soft 17


def test_is_blackjack(client):
    assert blackjack._is_bj(["AS", "KD"]) is True
    assert blackjack._is_bj(["AS", "AD", "9C"]) is False        # 3 karty
    assert blackjack._is_bj(["KS", "QD"]) is False
