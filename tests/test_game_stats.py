"""Osobní herní staty: agregace per hráč (Mines + PvP duely/piškvorky).

    .venv/Scripts/python.exe -m pytest tests/test_game_stats.py -v
"""
import secrets
from datetime import datetime, timezone, timedelta

from app.config import SESSION_COOKIE


def _user():
    from app.db import get_conn, now_iso
    conn = get_conn()
    try:
        u = f"gs_{secrets.token_hex(4)}"
        uid = conn.execute(
            "INSERT INTO users (kick_username, username, role, points, created_at) VALUES (?,?,?,?,?)",
            (u, u, "user", 0, now_iso())).lastrowid
        tok = secrets.token_hex(24)
        conn.execute("INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?,?,?,?)",
                     (tok, uid, now_iso(), (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()))
        conn.commit()
        return tok, uid
    finally:
        conn.close()


def test_game_stats_aggregates(client):
    from app.db import get_conn, now_iso
    tok, uid = _user()
    _, opp = _user()
    conn = get_conn()
    try:
        # Mines: 1 cashed (bet 100, payout 250) + 1 busted (bet 100, payout 0)
        conn.execute("INSERT INTO mines_games (user_id,bet,mines,layout,revealed,status,payout,created_at) "
                     "VALUES (?,?,?,?,?,?,?,?)", (uid, 100, 3, "[]", "[]", "cashed", 250, now_iso()))
        conn.execute("INSERT INTO mines_games (user_id,bet,mines,layout,revealed,status,payout,created_at) "
                     "VALUES (?,?,?,?,?,?,?,?)", (uid, 100, 3, "[]", "[]", "busted", 0, now_iso()))
        # Duel coinflip: uid = p1, vyhrál (winner=1), stake 500
        conn.execute("INSERT INTO duels (type,status,stake,p1_id,p2_id,winner,state,created_at,updated_at) "
                     "VALUES ('coinflip','finished',500,?,?,1,'',?,?)", (uid, opp, now_iso(), now_iso()))
        # Piškvorky: uid = p2, prohrál (winner=1 = p1), stake 200
        conn.execute("INSERT INTO games (type,status,stake,board,turn,p1_id,p2_id,winner,created_at,updated_at) "
                     "VALUES ('gomoku','finished',200,'.',1,?,?,1,?,?)", (opp, uid, now_iso(), now_iso()))
        conn.commit()
    finally:
        conn.close()

    s = client.get("/api/me/game-stats", headers={"Cookie": f"{SESSION_COOKIE}={tok}"}).json()

    mn = s["mines"]
    assert (mn["games"], mn["wagered"], mn["won"], mn["net"], mn["biggest"], mn["win_rate"]) == (2, 200, 250, 50, 250, 50)

    pvp = s["pvp"]
    assert (pvp["games"], pvp["won"], pvp["lost"], pvp["net"], pvp["biggest"]) == (2, 1, 1, 300, 500)

    assert s["overall"]["net"] == 350   # mines +50 + pvp +300
