"""earned_total (lifetime XP → level / Battle Pass): farmení 100 %, placené/gift suby 50 %
(náskok přispěvatelů, ne koupený level), gambling výhry a vratky 0 %. Zůstatek (points) se
mění vždy plně.

    .venv/Scripts/python.exe -m pytest tests/test_earned_gambling.py -v
"""
import secrets


def test_earn_factor_classification():
    from app.deps import counts_as_earned, earn_factor
    # poctivé farmení → 100 % XP
    for r in ["Sledování streamu", "Aktivita v chatu", "Kolo štěstí 🎡",
              "Battle Pass tier 5 🎟️", "Sklizeň: Mrkev 🌾", "Úkol: Denní 📋", "Denní streak – den 3",
              "Drop #5 – 1. místo", "Partner: Sponzor 🤝", "Login kalendář – 5 dní 🗓️"]:
        assert earn_factor(r) == 1.0 and counts_as_earned(r), r
    # gambling / vratky → 0 % XP (level se nedá vygamblit)
    for r in ["Mines výhra – full clear (3 bomb)", "Mines cashout (×2.5)", "Coinflip #7 – výhra",
              "Kostky #2 – výhra", "Výhra v piškvorkách #9", "Remíza v piškvorkách #9",
              "Predikce #3 – výhra", "Predikce #3 – nikdo netipnul, vráceno", "Blackjack stůl – win 🃏",
              "Vrácení vkladu – hra #5", "Zrušená hra #5 – vrácení vkladu", "Vypršelá výzva (duel #2)"]:
        assert earn_factor(r) == 0.0 and not counts_as_earned(r), r
    # placené / gift suby → 50 % XP (náskok, ne koupený lvl 100)
    for r in ["Kick sub 🟣", "Kick resub 🔁", "Kick gift sub 🎁 ×3", "Kick sub 🟣 (happy 2×)"]:
        assert earn_factor(r) == 0.5 and counts_as_earned(r), r


def test_add_points_earned_total_weights(client):
    from app.db import get_conn, now_iso
    from app.deps import add_points
    conn = get_conn()
    try:
        u = f"et_{secrets.token_hex(3)}"
        uid = conn.execute(
            "INSERT INTO users (kick_username, username, role, points, earned_total, created_at) "
            "VALUES (?,?,?,0,0,?)", (u, u, "user", now_iso())).lastrowid
        add_points(conn, uid, 1000, "Sledování streamu")                  # farmení → 100 % XP
        add_points(conn, uid, 500, "Kolo štěstí 🎡")                       # farmení → 100 % XP
        add_points(conn, uid, 5000, "Mines výhra – full clear (3 bomb)")   # gambling → 0 % XP
        add_points(conn, uid, 2000, "Coinflip #7 – výhra")                 # gambling → 0 % XP
        add_points(conn, uid, 800, "Predikce #3 – výhra")                  # gambling → 0 % XP
        add_points(conn, uid, 300, "Vrácení vkladu – hra #5")              # vratka → 0 % XP
        add_points(conn, uid, 3000, "Kick gift sub 🎁 ×3")                 # gift sub → 50 % XP = 1500
        conn.commit()
        row = conn.execute("SELECT points, earned_total FROM users WHERE id=?", (uid,)).fetchone()
        assert row["points"] == 1000 + 500 + 5000 + 2000 + 800 + 300 + 3000   # zůstatek = úplně vše
        assert row["earned_total"] == 1000 + 500 + 1500                       # XP = farmení + 50 % subu
    finally:
        conn.close()
