"""Tajný sedlák (stream-word easter egg): slovo nastavuje admin (app_settings), vyhlásí okno na N min,
slovo NIKDY nejde na frontend (/nx/q ani /nx/state) → F12 nic nenajde. Claim ověřuje server, gate 1×/user.

    .venv/Scripts/python.exe -m pytest tests/test_egg_streamword.py -v
"""
import secrets
from datetime import datetime, timezone, timedelta


def _user(conn, role="user"):
    from app.db import now_iso
    u = f"egg_{secrets.token_hex(3)}"
    uid = conn.execute("INSERT INTO users (kick_username, username, role, points, created_at) VALUES (?,?,?,0,?)",
                       (u, u, role, now_iso())).lastrowid
    token = secrets.token_hex(24)
    conn.execute("INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?,?,?,?)",
                 (token, uid, now_iso(), (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()))
    conn.commit()
    return uid, token


def _hdr(token):
    from app.config import SESSION_COOKIE
    return {"Cookie": f"{SESSION_COOKIE}={token}"}


def _arm(conn, word, minutes=10):
    from app.db import set_setting
    until = (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()
    set_setting(conn, "egg_word", word)
    set_setting(conn, "egg_armed_until", until)
    conn.commit()


def _disarm(conn):
    from app.db import set_setting
    set_setting(conn, "egg_armed_until", "")
    conn.commit()


def test_nx_q_leaks_no_answer(client):
    """F12 fix: /nx/q vrací jen generickou výzvu – ŽÁDNÉ slovo/hádanku s odpovědí."""
    from app.db import get_conn
    conn = get_conn()
    try:
        _, tok = _user(conn)
        _arm(conn, "TAJEMSTVI")     # i když je slovo nastavené, /nx/q ho NESMÍ prozradit
    finally:
        conn.close()
    r = client.get("/api/nx/q", headers=_hdr(tok)).json()
    assert "riddle" not in r and "word" not in r and "answer" not in r, r
    assert "tajemstvi" not in str(r).lower(), "slovo prosáklo do /nx/q!"
    assert r.get("hint"), "měla by být generická výzva"


def test_state_leaks_no_word(client):
    """/nx/state (public) vrací jen {active} – nikdy slovo."""
    from app.db import get_conn
    conn = get_conn()
    try:
        _arm(conn, "SUPERTAJNE")
    finally:
        conn.close()
    r = client.get("/api/nx/state").json()
    assert set(r.keys()) == {"active"} and "supertajne" not in str(r).lower(), r


def test_claim_locked_without_window(client):
    from app.db import get_conn
    conn = get_conn()
    try:
        _, tok = _user(conn)
        _disarm(conn)
    finally:
        conn.close()
    r = client.post("/api/nx/s", json={"word": "cokoliv"}, headers=_hdr(tok)).json()
    assert r.get("locked"), r


def test_claim_flow_reward_and_once(client):
    from app.db import get_conn
    conn = get_conn()
    try:
        uid, tok = _user(conn)
        _arm(conn, "TAJNESLOVO")
    finally:
        conn.close()
    h = _hdr(tok)
    assert client.post("/api/nx/s", json={"word": "spatne"}, headers=h).status_code == 400  # špatné v okně → 400
    r = client.post("/api/nx/s", json={"word": "tajneslovo"}, headers=h).json()              # case-insensitive
    assert r.get("found") and r.get("reward") == 1500, r
    conn = get_conn()
    try:
        row = conn.execute("SELECT points, egg_found_at FROM users WHERE id=?", (uid,)).fetchone()
        assert row["points"] == 1500 and row["egg_found_at"], "odměna + badge"
    finally:
        conn.close()
    r2 = client.post("/api/nx/s", json={"word": "tajneslovo"}, headers=h).json()             # podruhé → already
    assert r2.get("already"), r2


def test_normalization_forgiving(client):
    """Slovo s diakritikou a mezerou se chytí i bez nich (bez háčků/mezer)."""
    from app.db import get_conn
    conn = get_conn()
    try:
        _, tok = _user(conn)
        _arm(conn, "Zlaté Vejce")
    finally:
        conn.close()
    r = client.post("/api/nx/s", json={"word": "zlatevejce"}, headers=_hdr(tok)).json()
    assert r.get("found"), r


def test_admin_arm_endpoint(client):
    from app.db import get_conn
    conn = get_conn()
    try:
        _, adtok = _user(conn, role="admin")
        uid, tok = _user(conn)
    finally:
        conn.close()
    ar = client.post("/api/admin/egg/arm", json={"word": "STREAMWORD", "minutes": 5}, headers=_hdr(adtok))
    assert ar.status_code == 200, ar.text
    assert client.get("/api/nx/state").json().get("active") is True
    r = client.post("/api/nx/s", json={"word": "streamword"}, headers=_hdr(tok)).json()
    assert r.get("found"), r
    # slovo vidí jen admin (/admin/egg), ne veřejné endpointy
    assert "streamword" not in str(client.get("/api/nx/q", headers=_hdr(tok)).json()).lower()
    assert client.get("/api/admin/egg", headers=_hdr(adtok)).json().get("word") == "STREAMWORD"
