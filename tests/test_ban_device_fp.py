"""Ban účtu device-banuje otisk JEN když ho nesdílí moc účtů (slabý/sdílený otisk = false positive).

Bug, který tohle hlídá: otisk zařízení je slabý (model+prohlížeč+jazyk), takže ho sdílí i různí
lidé na stejném mobilu. Banování účtu dřív přidalo jeho otisk na blacklist zařízení → tím se
automaticky zabanoval každý se stejným otiskem (i nevinní). Nově se sdílený otisk přeskočí.

    .venv/Scripts/python.exe -m pytest tests/test_ban_device_fp.py -v
"""
import secrets
from datetime import datetime, timezone, timedelta

from app.config import SESSION_COOKIE
from app.db import get_conn, now_iso


def _login(role: str) -> str:
    conn = get_conn()
    try:
        suf = secrets.token_hex(4)
        cur = conn.execute(
            "INSERT INTO users (kick_username, username, role, points, created_at) VALUES (?,?,?,0,?)",
            (f"{role}_{suf}", f"{role}_{suf}", role, now_iso()))
        token = secrets.token_hex(24)
        conn.execute(
            "INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?,?,?,?)",
            (token, cur.lastrowid, now_iso(), (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()))
        conn.commit()
        return token
    finally:
        conn.close()


def _mkuser() -> int:
    conn = get_conn()
    try:
        suf = secrets.token_hex(4)
        cur = conn.execute(
            "INSERT INTO users (kick_username, username, role, points, created_at) VALUES (?,?,?,0,?)",
            (f"u_{suf}", f"u_{suf}", "user", now_iso()))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _sig(uid, fp):
    conn = get_conn()
    try:
        conn.execute("INSERT INTO client_signals (user_id, webdriver, fp_hash, ua, created_at) VALUES (?,0,?,?,?)",
                     (uid, fp, "ua", now_iso()))
        conn.commit()
    finally:
        conn.close()


def _fp_banned(fp) -> bool:
    conn = get_conn()
    try:
        return conn.execute("SELECT 1 FROM fingerprint_bans WHERE fp_hash=?", (fp,)).fetchone() is not None
    finally:
        conn.close()


def _hdr(t):
    return {"Cookie": f"{SESSION_COOKIE}={t}"}


def test_shared_fingerprint_is_not_device_banned(client):
    """3 účty sdílí otisk → ban jednoho NESMÍ otisk dát na blacklist zařízení."""
    a, b, c = _mkuser(), _mkuser(), _mkuser()
    fp = "shared_" + secrets.token_hex(8)
    for u in (a, b, c):
        _sig(u, fp)
    r = client.post(f"/api/admin/users/{a}/ban", json={"banned": True, "reason": "test"}, headers=_hdr(_login("admin")))
    assert r.status_code == 200, r.text
    assert not _fp_banned(fp), "sdílený otisk (3 účty) se NESMÍ device-banovat (false positive)"


def test_unique_fingerprint_is_device_banned(client):
    """Unikátní otisk (1 účet) se device-banuje normálně (legit anti-alt)."""
    d = _mkuser()
    fp = "uniq_" + secrets.token_hex(8)
    _sig(d, fp)
    r = client.post(f"/api/admin/users/{d}/ban", json={"banned": True, "reason": "test"}, headers=_hdr(_login("admin")))
    assert r.status_code == 200, r.text
    assert _fp_banned(fp), "unikátní otisk (1 účet) se má device-banovat"


def test_unban_lifts_device_ban(client):
    """Odban účtu uvolní i otisk ze zařízení-banu."""
    d = _mkuser()
    fp = "uniq2_" + secrets.token_hex(8)
    _sig(d, fp)
    tok = _login("admin")
    client.post(f"/api/admin/users/{d}/ban", json={"banned": True, "reason": "x"}, headers=_hdr(tok))
    assert _fp_banned(fp)
    client.post(f"/api/admin/users/{d}/ban", json={"banned": False, "reason": "x"}, headers=_hdr(tok))
    assert not _fp_banned(fp), "odban má otisk uvolnit"
