"""earned_total (lifetime XP → level / Battle Pass) počítá JEN poctivé farmení,
ne gambling výhry ani vratky. Zůstatek (points) se mění normálně.

    .venv/Scripts/python.exe -m pytest tests/test_earned_gambling.py -v
"""
import secrets


def test_counts_as_earned_classification():
    from app.deps import counts_as_earned
    # farmení → počítá
    for r in ["Sledování streamu", "Aktivita v chatu", "Kick gift sub 🎁 ×3", "Kolo štěstí 🎡",
              "Battle Pass tier 5 🎟️", "Sklizeň: Mrkev 🌾", "Úkol: Denní 📋", "Denní streak – den 3",
              "Drop #5 – 1. místo", "Partner: Sponzor 🤝", "Login kalendář – 5 dní 🗓️"]:
        assert counts_as_earned(r), r
    # gambling / vratky → NEpočítá
    for r in ["Mines výhra – full clear (3 bomb)", "Mines cashout (×2.5)", "Coinflip #7 – výhra",
              "Kostky #2 – výhra", "Výhra v piškvorkách #9", "Remíza v piškvorkách #9",
              "Predikce #3 – výhra", "Predikce #3 – nikdo netipnul, vráceno", "Blackjack stůl – win 🃏",
              "Vrácení vkladu – hra #5", "Zrušená hra #5 – vrácení vkladu", "Vypršelá výzva (duel #2)"]:
        assert not counts_as_earned(r), r


def test_add_points_earned_total_excludes_gambling(client):
    from app.db import get_conn, now_iso
    from app.deps import add_points
    conn = get_conn()
    try:
        u = f"et_{secrets.token_hex(3)}"
        uid = conn.execute(
            "INSERT INTO users (kick_username, username, role, points, earned_total, created_at) "
            "VALUES (?,?,?,0,0,?)", (u, u, "user", now_iso())).lastrowid
        add_points(conn, uid, 1000, "Sledování streamu")                  # +XP
        add_points(conn, uid, 500, "Kolo štěstí 🎡")                       # +XP (free daily)
        add_points(conn, uid, 5000, "Mines výhra – full clear (3 bomb)")   # gambling, NE XP
        add_points(conn, uid, 2000, "Coinflip #7 – výhra")                 # gambling, NE XP
        add_points(conn, uid, 800, "Predikce #3 – výhra")                  # gambling, NE XP
        add_points(conn, uid, 300, "Vrácení vkladu – hra #5")              # vratka, NE XP
        conn.commit()
        row = conn.execute("SELECT points, earned_total FROM users WHERE id=?", (uid,)).fetchone()
        assert row["points"] == 1000 + 500 + 5000 + 2000 + 800 + 300       # zůstatek = úplně vše
        assert row["earned_total"] == 1000 + 500                           # XP = jen farmení + kolo
    finally:
        conn.close()
