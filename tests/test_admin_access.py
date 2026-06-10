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
