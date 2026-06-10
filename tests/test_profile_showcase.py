"""Veřejný profil – vitrína: vyhrané (tomboly) + vlastněné (objednávky) předměty s obrázkem.

    .venv/Scripts/python.exe -m pytest tests/test_profile_showcase.py -v
"""
import secrets
from datetime import datetime, timezone, timedelta

from app.config import SESSION_COOKIE
from app.db import get_conn, now_iso


def _mk_user():
    conn = get_conn()
    try:
        suf = secrets.token_hex(4)
        uname = f"u_{suf}"
        cur = conn.execute(
            "INSERT INTO users (kick_username, username, role, points, created_at) VALUES (?,?,?,?,?)",
            (uname, uname, "user", 100, now_iso()))
        conn.commit()
        return cur.lastrowid, uname
    finally:
        conn.close()


def _login(uid):
    conn = get_conn()
    try:
        token = secrets.token_hex(24)
        conn.execute(
            "INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?,?,?,?)",
            (token, uid, now_iso(), (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()))
        conn.commit()
        return token
    finally:
        conn.close()


def _hdr(token):
    return {"Cookie": f"{SESSION_COOKIE}={token}"}


def _mk_product(name, image):
    conn = get_conn()
    try:
        cur = conn.execute(
            "INSERT INTO products (name, image_url, cost_points, type, created_at) VALUES (?,?,?,?,?)",
            (name, image, 100, "instant", now_iso()))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _mk_order(uid, pid):
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO orders (user_id, product_id, product_name, points_spent, status, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (uid, pid, "snapshot", 100, "fulfilled", now_iso()))
        conn.commit()
    finally:
        conn.close()


def _mk_raffle_win(uid, pid):
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO raffle_winners (product_id, user_id, created_at) VALUES (?,?,?)",
            (pid, uid, now_iso()))
        conn.commit()
    finally:
        conn.close()


def test_showcase_lists_won_and_owned_with_images(client):
    viewer, _ = _mk_user()
    vtok = _login(viewer)
    target, tnick = _mk_user()
    p_owned = _mk_product("AWP Skin", "/img/products/awp-printstream.png")
    p_won = _mk_product("Navaja Vanilla", "/img/products/navaja-vanilla.png")
    p_noimg = _mk_product("Bez obrazku", "")
    _mk_order(target, p_owned)
    _mk_order(target, p_noimg)        # bez obrázku → NESMÍ být ve vitríně
    _mk_raffle_win(target, p_won)
    r = client.get(f"/api/profile/public?nick={tnick}", headers=_hdr(vtok))
    assert r.status_code == 200, r.text
    sc = r.json()["showcase"]
    names = [i["name"] for i in sc]
    assert "AWP Skin" in names
    assert "Navaja Vanilla" in names
    assert "Bez obrazku" not in names, "předmět bez obrázku se ve vitríně nesmí objevit"
    won = next(i for i in sc if i["name"] == "Navaja Vanilla")
    assert won["won"] is True, "výhra z tomboly má mít flag won=True"
    owned = next(i for i in sc if i["name"] == "AWP Skin")
    assert owned["won"] is False


def test_showcase_dedupes_same_product(client):
    viewer, _ = _mk_user()
    vtok = _login(viewer)
    target, tnick = _mk_user()
    pid = _mk_product("Stiletto", "/img/products/stiletto-vanilla.png")
    _mk_order(target, pid)
    _mk_order(target, pid)            # 2× stejný produkt → ve vitríně jen jednou
    r = client.get(f"/api/profile/public?nick={tnick}", headers=_hdr(vtok))
    assert r.status_code == 200, r.text
    same = [i for i in r.json()["showcase"] if i["name"] == "Stiletto"]
    assert len(same) == 1, "stejný produkt se nesmí ve vitríně opakovat"


def test_public_profile_requires_login(client):
    _, tnick = _mk_user()
    r = client.get(f"/api/profile/public?nick={tnick}")
    assert r.status_code == 401, "veřejný profil je jen pro přihlášené (bez IP/e-mailu)"
