"""Údržbový (maintenance) režim.

Když je ZAPNUTÝ, běžní návštěvníci dostanou statickou údržbovou stránku a API
vrací 503. Staff (přihlášený admin/broadcaster/moderátor) vidí web normálně –
může tak v klidu testovat, zatímco ostatní čekají.

Stav se drží v paměti (rychlá kontrola v middleware bez DB na hot-path) a
zrcadlí se do app_settings (`maintenance_mode`), takže přežije restart/deploy.

Pojistka proti zamčení (escape hatch): middleware VŽDY pustí /api/health,
/api/auth/*, /api/admin/* (to hlídá admin_guard – jen staff/admin) a
/api/kick/webhook. Admin tak může režim vypnout i přímo přes
/api/admin/maintenance?to=off, i kdyby se SPA nenačetlo.
"""
import sqlite3
from datetime import datetime, timezone

from .config import SESSION_COOKIE, ROLE_ADMIN, WEB_DIR
from .db import get_conn, get_setting, set_setting, now_iso

_on = False
_until = ""        # ISO čas konce odpočtu, "" = bez odpočtu (napořád)
_html_cache = None


def is_on() -> bool:
    """Běží údržba? Když má odpočet a ten vypršel, sama se vypne (auto-switch zpět na web)."""
    if not _on:
        return False
    if _until:
        try:
            if datetime.now(timezone.utc) >= datetime.fromisoformat(_until):
                _auto_off()
                return False
        except Exception:
            return True
    return True


def until() -> str:
    return _until


def load(conn: sqlite3.Connection) -> None:
    """Načte stav z app_settings při startu appky."""
    global _on, _until
    _on = get_setting(conn, "maintenance_mode", "0") == "1"
    _until = get_setting(conn, "maintenance_until", "") or ""


def set_on(conn: sqlite3.Connection, value: bool, until_iso: str = "") -> None:
    """Přepne režim (paměť + app_settings). until_iso = ISO čas konce odpočtu (volitelné)."""
    global _on, _until
    _on = bool(value)
    _until = (until_iso or "") if value else ""
    set_setting(conn, "maintenance_mode", "1" if _on else "0")
    set_setting(conn, "maintenance_until", _until)
    conn.commit()


def _auto_off() -> None:
    """Odpočet vypršel → vypni údržbu (paměť + DB). Spustí se max jednou."""
    global _on, _until
    _on = False
    _until = ""
    try:
        conn = get_conn()
        try:
            set_setting(conn, "maintenance_mode", "0")
            set_setting(conn, "maintenance_until", "")
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass


def _allow_uids(conn) -> set:
    """Ručně whitelistnutá uid co smí na web i během údržby (maintenance_allow_uids = JSON list)."""
    import json
    try:
        return set(json.loads(get_setting(conn, "maintenance_allow_uids", "") or "[]"))
    except (ValueError, TypeError):
        return set()


def bypasses_maintenance(request) -> bool:
    """Vidí web i během údržby? ADMIN (vlastník) VŽDY + ručně whitelistnutá uid
    (maintenance_allow_uids – např. tester / důvěryhodný hráč). Záměrně NE celý staff
    (mod/broadcaster taky vidí údržbu). Krátký dotaz mimo Depends (middleware běží před
    routingem). Spouští se jen když je údržba zapnutá."""
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return False
    conn = get_conn()
    try:
        sess = conn.execute(
            "SELECT user_id, expires_at FROM sessions WHERE token = ?", (token,)
        ).fetchone()
        if not sess or sess["expires_at"] < now_iso():
            return False
        uid = sess["user_id"]
        u = conn.execute("SELECT role FROM users WHERE id = ?", (uid,)).fetchone()
        if u and u["role"] == ROLE_ADMIN:
            return True
        return uid in _allow_uids(conn)
    except Exception:
        return False
    finally:
        conn.close()


def page_html() -> str:
    """Obsah údržbové stránky (přečte web/maintenance.html, cachuje v paměti)."""
    global _html_cache
    if _html_cache is None:
        try:
            _html_cache = (WEB_DIR / "maintenance.html").read_text(encoding="utf-8")
        except Exception:
            _html_cache = ("<!doctype html><meta charset=utf-8><title>Údržba</title>"
                           "<h1 style='font-family:sans-serif;text-align:center;margin-top:20vh'>"
                           "🛠️ Probíhá údržba. Brzy jsme zpátky!</h1>")
    return _html_cache.replace("__MAINT_UNTIL__", _until or "")
