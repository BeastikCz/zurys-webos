"""Výkon/provoz: throttle zápisu session, DB indexy, ekonomika v denním digestu.

    .venv/Scripts/python.exe -m pytest tests/test_perf_ops.py -v
"""
import secrets
from datetime import datetime, timezone, timedelta

from app.config import SESSION_COOKIE
from app.db import get_conn, now_iso


def _mk_session(role: str = "user") -> str:
    conn = get_conn()
    try:
        suf = secrets.token_hex(4)
        cur = conn.execute(
            "INSERT INTO users (kick_username, username, role, points, created_at) VALUES (?,?,?,?,?)",
            (f"u_{suf}", f"u_{suf}", role, 100, now_iso()))
        tok = secrets.token_hex(24)
        conn.execute(
            "INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?,?,?,?)",
            (tok, cur.lastrowid, now_iso(), (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()))
        conn.commit()
        return tok
    finally:
        conn.close()


def _last_seen(tok: str):
    conn = get_conn()
    try:
        r = conn.execute("SELECT last_seen FROM sessions WHERE token = ?", (tok,)).fetchone()
        return r["last_seen"] if r else None
    finally:
        conn.close()


def test_session_last_seen_throttled(client):
    """„naposledy viděn" se nesmí přepisovat při KAŽDÉM requestu (šetří zápisy na hot-path)."""
    tok = _mk_session()
    h = {"Cookie": f"{SESSION_COOKIE}={tok}"}
    assert client.get("/api/auth/me", headers=h).status_code == 200
    first = _last_seen(tok)
    assert first, "po prvním requestu má být last_seen nastaveno"
    # druhý request hned za sebou → throttle (< SESSION_TOUCH_SEC) → žádný nový zápis
    assert client.get("/api/auth/me", headers=h).status_code == 200
    second = _last_seen(tok)
    assert second == first, "last_seen se nemá přepisovat při každém requestu (throttle)"


def test_session_touch_after_window(client):
    """Po uplynutí okna se last_seen zase aktualizuje (necachuje se napořád)."""
    tok = _mk_session()
    h = {"Cookie": f"{SESSION_COOKIE}={tok}"}
    client.get("/api/auth/me", headers=h)
    # uměle „zestarni" last_seen za hranici throttlu
    old = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    conn = get_conn()
    try:
        conn.execute("UPDATE sessions SET last_seen = ? WHERE token = ?", (old, tok))
        conn.commit()
    finally:
        conn.close()
    client.get("/api/auth/me", headers=h)
    assert _last_seen(tok) != old, "po překročení okna se má last_seen znovu zapsat"


def test_new_indexes_exist(client):
    conn = get_conn()
    try:
        pidx = [r["name"] for r in conn.execute("PRAGMA index_list('points_log')")]
        didx = [r["name"] for r in conn.execute("PRAGMA index_list('duels')")]
        assert "idx_points_log_created" in pidx, "chybí index points_log(created_at)"
        assert "idx_duels_players" in didx, "chybí index duels(p1_id,p2_id)"
    finally:
        conn.close()


def test_digest_includes_weekly_economy(client):
    from app import digest
    conn = get_conn()
    try:
        uid = conn.execute(
            "INSERT INTO users (kick_username, username, role, points, created_at) VALUES (?,?,?,?,?)",
            (f"d_{secrets.token_hex(3)}", "digx", "user", 0, now_iso())).lastrowid
        conn.execute("INSERT INTO points_log (user_id, change, reason, created_at) VALUES (?,?,?,?)",
                     (uid, 5000, "Sledování streamu", now_iso()))
        conn.execute("INSERT INTO points_log (user_id, change, reason, created_at) VALUES (?,?,?,?)",
                     (uid, -1000, "Nákup odměn (1 ks)", now_iso()))
        conn.commit()
        txt = digest.compose(conn)
        lines, ping = digest._econ_week(conn)
    finally:
        conn.close()
    assert "Ekonomika 7 dní" in txt, "digest má obsahovat 7denní ekonomiku"
    assert "vytvořeno" in txt
    assert isinstance(ping, bool)


def test_inflation_alert_threshold(client, monkeypatch):
    """Při zralém okně a inflaci nad prahem se zapne ping (alert)."""
    from app import digest, econ_health
    # nasimuluj „zralý" web s vysokou inflací (series délky 7 = okno → není young)
    fake = {
        "days": 7, "circulation": 1000, "faucet_total": 800, "sink_total": 100,
        "net_total": 700, "inflation_pct": 70.0,
        "by_category": [{"key": "watch", "emoji": "📺", "label": "Sledování",
                         "kind": "faucet", "minted": 800, "burned": 0, "net": 800}],
        "series": [{"date": f"2026-06-0{d}", "minted": 1, "burned": 0, "net": 1,
                    "dau": 1, "circulation": d} for d in range(1, 8)],
        "active_users": 1, "dau_peak": 1, "dau_avg": 1,
    }
    monkeypatch.setattr(econ_health, "health", lambda conn, days=7: fake)
    conn = get_conn()
    try:
        lines, ping = digest._econ_week(conn)
    finally:
        conn.close()
    assert ping is True, "inflace 70 % nad prahem 25 % má spustit ping"
    assert any("INFLACE" in ln for ln in lines)
