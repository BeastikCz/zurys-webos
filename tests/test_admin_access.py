"""Bezpečnostní testy přístupu do administrace.

Politika přístupu:
  * PŘEHLED (/admin/overview, /admin/checklist) – citlivý souhrn (IP, risk skóre,
    zůstatky, audit). Vidí JEN role `admin`. Ani moderátor, ani broadcaster.
  * EKONOMIKA dashboard (/admin/economy/dashboard) – body v oběhu, top zisky/zůstatky.
    Vidí `admin` i `broadcaster` (sekce "economy"). NE moderátor / sub / user.

Tahle pojistka chytí, kdyby v budoucnu někdo omylem rozšířil nebo zúžil přístup
oproti tomu, co je tu zadrátované. Testuje se přihlášený uživatel s konkrétní rolí.

    .venv/Scripts/python.exe -m pytest tests/test_admin_access.py -v
"""
import secrets
from datetime import datetime, timezone, timedelta

import pytest

from app.config import SESSION_COOKIE

# Citlivý PŘEHLED – striktně jen admin.
ADMIN_ONLY_PATHS = ["/api/admin/overview", "/api/admin/checklist"]

# Ekonomika dashboard – admin + broadcaster.
ECONOMY_DASHBOARD = "/api/admin/economy/dashboard"


def _login_as(role: str) -> str:
    """Založí uživatele s danou rolí + platnou relaci a vrátí session token (cookie)."""
    from app.db import get_conn, now_iso
    conn = get_conn()
    try:
        suffix = secrets.token_hex(4)
        cur = conn.execute(
            "INSERT INTO users (kick_username, username, role, points, created_at) "
            "VALUES (?, ?, ?, 0, ?)",
            (f"{role}_{suffix}", f"{role}_{suffix}", role, now_iso()),
        )
        token = secrets.token_hex(24)
        conn.execute(
            "INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (token, cur.lastrowid, now_iso(),
             (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()),
        )
        conn.commit()
        return token
    finally:
        conn.close()


def _get(client, path: str, token: str):
    # Cookie posíláme explicitně hlavičkou → žádné znečištění sdílené session jar mezi testy.
    return client.get(path, headers={"Cookie": f"{SESSION_COOKIE}={token}"})


# ---------------- PŘEHLED: jen admin ----------------

@pytest.mark.parametrize("path", ADMIN_ONLY_PATHS)
def test_overview_admin_can_see(client, path):
    """Admin Přehled vidí (200)."""
    r = _get(client, path, _login_as("admin"))
    assert r.status_code == 200, f"admin {path} -> {r.status_code} (měl by 200)"


@pytest.mark.parametrize("role", ["broadcaster", "mod", "vip", "sub", "user"])
@pytest.mark.parametrize("path", ADMIN_ONLY_PATHS)
def test_overview_blocked_for_non_admin(client, path, role):
    """Moderátor, broadcaster ani nikdo jiný Přehled NEVIDÍ (403)."""
    r = _get(client, path, _login_as(role))
    assert r.status_code == 403, (
        f"BEZPEČNOSTNÍ DÍRA: role '{role}' dostala {r.status_code} na {path} "
        f"– PŘEHLED musí být 403 (jen admin)!"
    )


# ---------------- EKONOMIKA dashboard: admin + broadcaster ----------------

@pytest.mark.parametrize("role", ["admin", "broadcaster"])
def test_economy_dashboard_allowed(client, role):
    """Ekonomiku vidí admin i broadcaster (200)."""
    r = _get(client, ECONOMY_DASHBOARD, _login_as(role))
    assert r.status_code == 200, f"{role} {ECONOMY_DASHBOARD} -> {r.status_code} (měl by 200)"


@pytest.mark.parametrize("role", ["mod", "vip", "sub", "user"])
def test_economy_dashboard_blocked(client, role):
    """Moderátor / sub / user ekonomiku NEVIDÍ (403)."""
    r = _get(client, ECONOMY_DASHBOARD, _login_as(role))
    assert r.status_code == 403, (
        f"role '{role}' dostala {r.status_code} na {ECONOMY_DASHBOARD} – měl by 403!"
    )


# ---------------- Úprava bodů: mod SMÍ; ban/import NE; důvod povinný ----------------

def _make_target(role: str = "user") -> int:
    from app.db import get_conn, now_iso
    conn = get_conn()
    try:
        cur = conn.execute(
            "INSERT INTO users (kick_username, username, role, points, created_at) VALUES (?,?,?,0,?)",
            (f"t_{secrets.token_hex(4)}", f"t_{secrets.token_hex(4)}", role, now_iso()))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _post(client, path: str, token: str, body=None):
    return client.post(path, json=(body or {}), headers={"Cookie": f"{SESSION_COOKIE}={token}"})


def test_mod_can_adjust_points(client):
    uid = _make_target()
    r = _post(client, f"/api/admin/users/{uid}/points", _login_as("mod"), {"change": 100, "reason": "test"})
    assert r.status_code == 200, f"mod má moct upravit body, dostal {r.status_code}: {r.text}"


def test_points_require_reason(client):
    uid = _make_target()
    r = _post(client, f"/api/admin/users/{uid}/points", _login_as("admin"), {"change": 100, "reason": "  "})
    assert r.status_code == 400, f"prázdný důvod měl dát 400, dal {r.status_code}"


def test_mod_cannot_ban(client):
    uid = _make_target()
    r = _post(client, f"/api/admin/users/{uid}/ban", _login_as("mod"), {"banned": True, "reason": "x"})
    assert r.status_code == 403, f"BEZPEČNOST: mod nesmí banovat, dostal {r.status_code}"


def test_mod_cannot_import(client):
    r = _post(client, "/api/admin/import/legacy", _login_as("mod"), {"users": []})
    assert r.status_code == 403, f"BEZPEČNOST: mod nesmí importovat, dostal {r.status_code}"


def test_broadcaster_can_still_ban(client):
    uid = _make_target()
    r = _post(client, f"/api/admin/users/{uid}/ban", _login_as("broadcaster"), {"banned": True, "reason": "x"})
    assert r.status_code == 200, f"broadcaster má pořád moct banovat, dostal {r.status_code}: {r.text}"


def test_mod_points_over_limit_blocked(client):
    """Moderátor nesmí přidat víc než MOD_POINTS_MAX na jeden zásah (anti-zneužití)."""
    from app.config import MOD_POINTS_MAX
    uid = _make_target()
    r = _post(client, f"/api/admin/users/{uid}/points", _login_as("mod"),
              {"change": MOD_POINTS_MAX + 1, "reason": "test"})
    assert r.status_code == 403, f"mod nad strop měl dostat 403, dal {r.status_code}"


def test_admin_points_no_limit(client):
    """Admin má body bez stropu."""
    from app.config import MOD_POINTS_MAX
    uid = _make_target()
    r = _post(client, f"/api/admin/users/{uid}/points", _login_as("admin"),
              {"change": MOD_POINTS_MAX + 1000000, "reason": "test"})
    assert r.status_code == 200, f"admin nemá mít strop, dostal {r.status_code}: {r.text}"


def test_mod_cannot_access_economy_section(client):
    """Moderátor nesmí na ekonomiku (jiná sekce) – ani číst rake."""
    r = _get(client, "/api/admin/economy/games-rake", _login_as("mod"))
    assert r.status_code == 403, f"mod nesmí na ekonomiku, dostal {r.status_code}"


def test_mod_can_manage_orders(client):
    """Moderátor smí na objednávky VČETNĚ pomocného /order-products – jinak se sekce v UI nenačte."""
    tok = _login_as("mod")
    assert _get(client, "/api/admin/orders?status=all", tok).status_code == 200, "mod má vidět objednávky"
    assert _get(client, "/api/admin/order-products", tok).status_code == 200, "mod potřebuje i order-products (filtr)"


def test_mod_can_access_products(client):
    """Moderátor smí na sekci Odměny (products) – správa shop položek."""
    assert _get(client, "/api/admin/products", _login_as("mod")).status_code == 200, "mod má vidět/spravovat odměny"


# ---------------- Predikční moderátor (predictor): vidí admin, ale JEN predikce ----------------

def test_predictor_can_access_predictions(client):
    """Predikční moderátor smí na admin predikce (200)."""
    r = _get(client, "/api/predictions/admin/all", _login_as("predictor"))
    assert r.status_code == 200, f"predictor má vidět admin predikce, dostal {r.status_code}: {r.text}"


@pytest.mark.parametrize("path", [
    "/api/admin/products", "/api/admin/orders?status=all", "/api/admin/economy/dashboard",
    "/api/admin/economy/games-rake", "/api/admin/overview", "/api/admin/checklist",
])
def test_predictor_blocked_everywhere_else(client, path):
    """BEZPEČNOST: predikční moderátor NESMÍ nikam jinam než predikce (403)."""
    r = _get(client, path, _login_as("predictor"))
    assert r.status_code == 403, (
        f"BEZPEČNOSTNÍ DÍRA: predictor dostal {r.status_code} na {path} – smí JEN predikce, jinde musí 403!")


def test_predictor_cannot_adjust_points(client):
    """Predikční moderátor nesmí sahat na body uživatelů (jiná sekce)."""
    uid = _make_target()
    r = _post(client, f"/api/admin/users/{uid}/points", _login_as("predictor"), {"change": 100, "reason": "x"})
    assert r.status_code == 403, f"BEZPEČNOST: predictor nesmí na body, dostal {r.status_code}"


def test_non_staff_cannot_access_predictions_admin(client):
    """Sanity: běžný divák nesmí na admin predikce (403)."""
    r = _get(client, "/api/predictions/admin/all", _login_as("user"))
    assert r.status_code == 403, f"user nesmí na admin predikce, dostal {r.status_code}"


# ---------------- Early access gate: Crew + Statek jen pro grantnuté + admina ----------------

def _login_early(early: int = 1) -> str:
    """Uživatel s early_access flagem + relace."""
    from app.db import get_conn, now_iso
    from datetime import datetime, timezone, timedelta
    conn = get_conn()
    try:
        s = secrets.token_hex(4)
        uid = conn.execute("INSERT INTO users (kick_username, username, role, points, early_access, created_at) "
                           "VALUES (?,?,?,0,?,?)", (f"ea_{s}", f"ea_{s}", "user", early, now_iso())).lastrowid
        tok = secrets.token_hex(24)
        conn.execute("INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?,?,?,?)",
                     (tok, uid, now_iso(), (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()))
        conn.commit()
        return tok
    finally:
        conn.close()


@pytest.mark.parametrize("path", ["/api/crews/mine", "/api/farm"])
def test_early_access_blocks_normal_user(client, path):
    """BEZPEČNOST: běžný uživatel bez early_access NEVIDÍ Crew ani Statek (403)."""
    r = _get(client, path, _login_early(early=0))
    assert r.status_code == 403, f"bez early_access má být 403 na {path}, dostal {r.status_code}"


def test_early_access_granted_user_allowed(client):
    """Grantnutý uživatel (early_access=1) vidí Crew (200) — Statek NE (ten je zatím jen admin)."""
    tok = _login_early(early=1)
    r = _get(client, "/api/crews/mine", tok)
    assert r.status_code == 200, f"s early_access má být 200 na crews, dostal {r.status_code}: {r.text}"
    r2 = _get(client, "/api/farm", tok)
    assert r2.status_code == 403, f"Statek má být i pro grantnuté 403 (jen admin), dostal {r2.status_code}"


@pytest.mark.parametrize("path", ["/api/crews/mine", "/api/farm"])
def test_early_access_admin_bypass(client, path):
    """Admin vidí early-access featury vždy (i bez flagu)."""
    r = _get(client, path, _login_as("admin"))
    assert r.status_code == 200, f"admin má vidět {path} vždy, dostal {r.status_code}"


# ---------------- Admin přehled part (crews): admin + broadcaster ----------------

@pytest.mark.parametrize("role", ["admin", "broadcaster"])
def test_admin_crews_allowed(client, role):
    """Přehled part vidí admin i broadcaster (200)."""
    r = _get(client, "/api/admin/crews", _login_as(role))
    assert r.status_code == 200, f"{role} má vidět přehled part, dostal {r.status_code}"


def test_admin_crews_broadcaster_redacted(client):
    """Broadcaster vidí jen složení part (kdo s kým) — ekonomické staty (xp/level/příspěvky/kód) JEN admin."""
    from app.db import get_conn, now_iso
    from app import crews as crews_mod
    conn = get_conn()
    try:
        s = secrets.token_hex(4)
        uid = conn.execute("INSERT INTO users (kick_username, username, role, points, created_at) "
                           "VALUES (?,?,?,?,?)", (f"cl_{s}", f"cl_{s}", "user", 50000, now_iso())).lastrowid
        conn.commit()
        crews_mod.create(conn, uid, f"cl_{s}", f"RedactParta_{s}", None)
    finally:
        conn.close()
    a = _get(client, "/api/admin/crews", _login_as("admin")).json()
    b = _get(client, "/api/admin/crews", _login_as("broadcaster")).json()
    assert a and "xp" in a[0] and "code" in a[0] and "contributed" in a[0]["members"][0]   # admin vše
    assert b and "xp" not in b[0] and "level" not in b[0] and "code" not in b[0]           # broadcaster bez ekonomiky
    assert "contributed" not in b[0]["members"][0] and "week_xp" not in b[0]["members"][0]
    assert b[0]["members"][0]["username"] and b[0]["member_count"] >= 1                     # složení vidí


@pytest.mark.parametrize("role", ["mod", "predictor", "vip", "sub", "user"])
def test_admin_crews_blocked(client, role):
    """BEZPEČNOST: mod / predictor / divák na přehled part NESMÍ (403)."""
    r = _get(client, "/api/admin/crews", _login_as(role))
    assert r.status_code == 403, f"{role} dostal {r.status_code} na /admin/crews – má být 403"


# ---------------- Happy Hour (start streamu): broadcaster si to řídí sám ----------------
# HH panel žije v „drops" tabu → broadcaster (co má sekci drops) musí projít i na /live-happy a /egg,
# jinak mu Promise.all celý tab shodí. Sekce drops = broadcaster-only (mod/predictor NE).

@pytest.mark.parametrize("role", ["admin", "broadcaster"])
@pytest.mark.parametrize("path", ["/api/admin/live-happy", "/api/admin/egg"])
def test_happy_hour_broadcaster_allowed(client, role, path):
    """Broadcaster i admin řídí Happy Hour + egg sám (200)."""
    r = _get(client, path, _login_as(role))
    assert r.status_code == 200, f"{role} {path} -> {r.status_code} (měl by 200 — drops tab)"


def test_happy_hour_start_broadcaster(client):
    """Broadcaster smí ručně spustit Happy Hour TEĎ (POST /live-happy/start)."""
    r = _post(client, "/api/admin/live-happy/start", _login_as("broadcaster"))
    assert r.status_code == 200, f"broadcaster má moct spustit HH, dostal {r.status_code}: {r.text}"


@pytest.mark.parametrize("role", ["mod", "predictor", "vip", "sub", "user"])
def test_happy_hour_blocked_for_others(client, role):
    """BEZPEČNOST: mod / predictor / divák Happy Hour NEovládají (403)."""
    r = _get(client, "/api/admin/live-happy", _login_as(role))
    assert r.status_code == 403, f"{role} dostal {r.status_code} na /admin/live-happy – má být 403"
