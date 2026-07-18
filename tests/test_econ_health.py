"""Zdraví ekonomiky: kategorizace faucet/sink/transfer + admin dashboard endpoint.

    .venv/Scripts/python.exe -m pytest tests/test_econ_health.py -v
"""
import secrets
from datetime import datetime, timezone, timedelta

from app import economy
from app.config import SESSION_COOKIE
from app.db import get_conn, now_iso
from app.econ_health import categorize, health


def _login_as(role: str) -> str:
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


def _hdr(token):
    return {"Cookie": f"{SESSION_COOKIE}={token}"}


def _mk_user(points: int = 0) -> int:
    conn = get_conn()
    try:
        suf = secrets.token_hex(4)
        cur = conn.execute(
            "INSERT INTO users (kick_username, username, role, points, created_at) VALUES (?,?,?,?,?)",
            (f"u_{suf}", f"u_{suf}", "user", points, now_iso()))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _mk_log(uid: int, change: int, reason: str) -> None:
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO points_log (user_id, change, reason, created_at) VALUES (?,?,?,?)",
            (uid, change, reason, now_iso()))
        conn.commit()
    finally:
        conn.close()


def test_categorize_known_reasons():
    """Reálné reason stringy z appky se zařadí do správné kategorie + faucet/sink/transfer."""
    assert categorize("Sledování streamu")[0] == "watch"
    assert categorize("Sledování streamu")[3] == "faucet"
    assert categorize("Aktivita v chatu")[0] == "chat"
    assert categorize("Komunitní chat cíl 🎉")[0] == "chat"
    assert categorize("Top Chatter dne (1. místo) 🗣️")[0] == "topchat"
    assert categorize("Denní streak – den 3 (×5 liga)")[0] == "daily"
    assert categorize("Snídaně na statku – den 7 🎁 truhla ⭐sub")[0] == "daily"
    assert categorize("Kolo štěstí 🎡")[0] == "wheel"
    assert categorize("Drop #12 – 1. místo (přes kód)")[0] == "drops"
    assert categorize("Redeem kód VITEJ100")[0] == "codes"
    assert categorize("Kick gift sub 🎁 ×3")[0] == "kick"
    assert categorize("Partner: Sponzor 🤝")[0] == "partners"
    assert categorize("Statek: vejce")[0] == "farm_h"
    assert categorize("Statek: krmivo")[0] == "farm_s"
    assert categorize("Hnojivo: mrkev")[0] == "garden_s"
    # sink
    assert categorize("Nákup odměn (2 ks)")[0] == "shop"
    assert categorize("Nákup odměn (2 ks)")[3] == "sink"
    assert categorize("Prestige 3 – spáleno 🔥")[0] == "prestige"
    assert categorize("Prestige 3 – spáleno 🔥")[3] == "sink"
    # transfer (net ~0)
    assert categorize("Predikce #5 – sázka")[0] == "predictions"
    assert categorize("Predikce #5 – výhra")[0] == "predictions"
    assert categorize("Coinflip duel – vklad")[0] == "games"
    assert categorize("Výhra v piškvorkách #3")[0] == "games"
    assert categorize("Blackjack stůl – sázka 🃏")[0] == "blackjack"
    assert categorize("Mines sázka (3 bomb)")[0] == "mines"
    assert categorize("Mines cashout (×2.5)")[0] == "mines"
    assert categorize("Dar pro Honza 🎁")[0] == "gifts"
    assert categorize("Dar od Honza 🎁")[3] == "transfer"
    assert categorize("Dar → Honza (čeká na schválení) 🎁")[0] == "gifts"
    # neznámý / freeform ruční admin důvod → other
    assert categorize("náhodný ruční důvod od admina")[0] == "other"


def test_health_aggregates_faucet_sink_and_categories(client):
    uid = _mk_user()
    _mk_log(uid, 1000, "Sledování streamu")
    _mk_log(uid, 500, "Kolo štěstí 🎡")
    _mk_log(uid, -300, "Nákup odměn (1 ks)")
    conn = get_conn()
    try:
        h = health(conn, 14)
    finally:
        conn.close()
    # moje řádky přidaly aspoň tolik (jiné testy můžou přidat víc → >=)
    assert h["faucet_total"] >= 1500
    assert h["sink_total"] >= 300
    keys = {c["key"] for c in h["by_category"]}
    assert "watch" in keys and "wheel" in keys and "shop" in keys
    shop = next(c for c in h["by_category"] if c["key"] == "shop")
    assert shop["kind"] == "sink" and shop["burned"] >= 300
    assert isinstance(h["series"], list)
    assert h["active_users"] >= 1
    # net zahrnuje i hry a jiné převody, které nejsou ani faucet, ani sink.
    assert h["net_total"] == sum(c["net"] for c in h["by_category"])


def test_soft_faucet_guard_disabled():
    """Inflační brzda je vypnutá (soft_faucet_factor vrací vždy 1.0) → plná odměna
    i za podmínek, které by ji dřív spustily. Znovuzapnutí = smaž `return 1.0`."""
    uid = _mk_user(10_000)
    _mk_log(uid, 1, "Sledování streamu")
    old_pct = economy.SOFT_FAUCET_GUARD_PCT
    old_cache = dict(economy._soft_faucet_guard_cache)
    conn = get_conn()
    try:
        economy.SOFT_FAUCET_GUARD_PCT = 0.000001
        economy._soft_faucet_guard_cache.update(checked_at=0.0, factor=1.0)
        award = economy.award_soft_faucet(conn, uid, 100, "Kolo štěstí 🎡")
        conn.commit()
        balance = conn.execute("SELECT points FROM users WHERE id = ?", (uid,)).fetchone()["points"]
    finally:
        economy.SOFT_FAUCET_GUARD_PCT = old_pct
        economy._soft_faucet_guard_cache.clear()
        economy._soft_faucet_guard_cache.update(old_cache)
        conn.close()
    assert award == {"amount": 100, "guarded": False}
    assert balance == 10_100


def test_health_endpoint_access_control(client):
    uid = _mk_user()
    _mk_log(uid, 50, "Sledování streamu")
    r = client.get("/api/admin/economy/health?days=7", headers=_hdr(_login_as("admin")))
    assert r.status_code == 200, r.text
    body = r.json()
    for k in ("by_category", "series", "inflation_pct", "circulation", "faucet_total", "sink_total", "days"):
        assert k in body, f"chybí klíč {k}"
    # broadcaster smí (sekce economy), moderátor NE
    assert client.get("/api/admin/economy/health", headers=_hdr(_login_as("broadcaster"))).status_code == 200
    assert client.get("/api/admin/economy/health", headers=_hdr(_login_as("mod"))).status_code == 403
    # nepřihlášený → 401
    assert client.get("/api/admin/economy/health").status_code == 401
