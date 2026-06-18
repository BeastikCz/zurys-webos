"""Ban účtu je JEN na účet – NEdevice-banuje otisk (zrušeno 2026-06-18). Slabý otisk
(model+prohlížeč+jazyk) sdílí i různí lidé / sourozenci na stejném zařízení, takže device-ban
střílel nevinné (false positive). Nově se `fingerprint_bans` z banu vůbec neplní; odban pro
jistotu pořád uvolní i případný STARÝ device-ban (legacy cleanup).

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


def test_ban_does_not_device_ban_unique_fp(client):
    """Ban účtu s UNIKÁTNÍM otiskem (1 účet) už NEpřidá otisk na blacklist – ban je jen na účet."""
    d = _mkuser()
    fp = "uniq_" + secrets.token_hex(8)
    _sig(d, fp)
    r = client.post(f"/api/admin/users/{d}/ban", json={"banned": True, "reason": "test"}, headers=_hdr(_login("admin")))
    assert r.status_code == 200, r.text
    assert not _fp_banned(fp), "ban už NESMÍ device-banovat (jen účet)"


def test_ban_does_not_device_ban_shared_fp(client):
    """Ani sdílený otisk se nedevice-banuje (nikdy se neplní)."""
    a, b = _mkuser(), _mkuser()
    fp = "shared_" + secrets.token_hex(8)
    _sig(a, fp)
    _sig(b, fp)
    r = client.post(f"/api/admin/users/{a}/ban", json={"banned": True, "reason": "test"}, headers=_hdr(_login("admin")))
    assert r.status_code == 200, r.text
    assert not _fp_banned(fp)


def test_unban_clears_legacy_device_ban(client):
    """Odban pořád uvolní případný STARÝ device-ban v tabulce (legacy cleanup)."""
    d = _mkuser()
    fp = "legacy_" + secrets.token_hex(8)
    _sig(d, fp)
    conn = get_conn()
    try:
        conn.execute("INSERT OR IGNORE INTO fingerprint_bans (fp_hash, reason, created_at) VALUES (?,?,?)",
                     (fp, "legacy", now_iso()))
        conn.execute("UPDATE users SET banned=1 WHERE id=?", (d,))
        conn.commit()
    finally:
        conn.close()
    assert _fp_banned(fp)
    client.post(f"/api/admin/users/{d}/ban", json={"banned": False, "reason": "x"}, headers=_hdr(_login("admin")))
    assert not _fp_banned(fp), "odban má uvolnit i starý device-ban"
