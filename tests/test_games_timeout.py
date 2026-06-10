"""Piškvorky – grace na PRVNÍ tah po nalezení soupeře.

Bug, který tohle hlídá: zakladatel (p1) čeká na soupeře (často kouká na stream v jiném
tabu). Jakmile se někdo přidá, hra je 'active', p1 je na tahu a odpočet běží. Když má
první tah jen blitz limit (MOVE_TIMEOUT), p1 prohraje dřív, než si match vůbec všimne.
Proto má první tah (move_count==0) delší grace (FIRST_MOVE_TIMEOUT). Další tahy už blitz.

Časy se odvozují z konstant, aby test přežil i ladění hodnot (10–13 s na tah apod.).

    .venv/Scripts/python.exe -m pytest tests/test_games_timeout.py -v
"""
import secrets
from datetime import datetime, timezone, timedelta

from app.db import get_conn, now_iso
from app.routers import games


def _mkuser(conn) -> int:
    u = f"g_{secrets.token_hex(4)}"
    return conn.execute(
        "INSERT INTO users (kick_username, username, role, points, created_at) VALUES (?,?,?,1000,?)",
        (u, u, "user", now_iso())).lastrowid


def _mk_active_game(conn, move_count: int, secs_since_move: float) -> int:
    """Aktivní hra, p1 je na tahu (turn=1), poslední tah/start byl `secs_since_move` s zpět."""
    p1, p2 = _mkuser(conn), _mkuser(conn)
    past = (datetime.now(timezone.utc) - timedelta(seconds=secs_since_move)).isoformat()
    gid = conn.execute(
        "INSERT INTO games (type,status,stake,board,turn,p1_id,p2_id,move_count,last_move_at,active_at,created_at,updated_at) "
        "VALUES ('gomoku','active',10,?,1,?,?,?,?,?,?,?)",
        (games._empty_board(), p1, p2, move_count, past, past, now_iso(), now_iso())).lastrowid
    conn.commit()
    return gid


def test_first_move_has_grace(client):
    """První tah ZA blitz limitem, ale v rámci grace → hra běží dál (p1 nesmí hned prohrát)."""
    assert games.MOVE_TIMEOUT + 5 < games.FIRST_MOVE_TIMEOUT, "grace musí být znatelně delší než blitz"
    conn = get_conn()
    try:
        gid = _mk_active_game(conn, move_count=0, secs_since_move=games.MOVE_TIMEOUT + 5)
        g = games._resolve_timeouts(conn, games._get_game(conn, gid))
        assert g["status"] == "active", "první tah má grace, nesmí prohrát hned po blitz limitu"
    finally:
        conn.close()


def test_first_move_grace_expires(client):
    """Po překročení grace prohrává ten, kdo je na tahu (p1) → vítěz p2."""
    conn = get_conn()
    try:
        gid = _mk_active_game(conn, move_count=0, secs_since_move=games.FIRST_MOVE_TIMEOUT + 5)
        g = games._resolve_timeouts(conn, games._get_game(conn, gid))
        assert g["status"] == "finished" and g["winner"] == 2, "po grace má p1 (na tahu) prohrát"
    finally:
        conn.close()


def test_subsequent_move_normal_blitz_timeout(client):
    """Běžný tah (move_count>=1) má rychlý blitz limit → po jeho překročení je prohra."""
    conn = get_conn()
    try:
        gid = _mk_active_game(conn, move_count=3, secs_since_move=games.MOVE_TIMEOUT + 5)
        g = games._resolve_timeouts(conn, games._get_game(conn, gid))
        assert g["status"] == "finished", "běžný tah po blitz limitu timeoutuje"
    finally:
        conn.close()


def test_move_left_s_reflects_first_move_grace(client):
    """Veřejný stav: u prvního tahu se zbývající čas počítá z grace, ne z blitz limitu."""
    conn = get_conn()
    try:
        gid = _mk_active_game(conn, move_count=0, secs_since_move=2)
        pub = games._game_public(conn, games._get_game(conn, gid), me_id=0)
        assert pub["move_left_s"] > games.MOVE_TIMEOUT, "u prvního tahu má zbývat víc než blitz limit"
        assert pub["move_timeout_s"] == games.FIRST_MOVE_TIMEOUT, "první tah hlásí grace limit"
    finally:
        conn.close()
