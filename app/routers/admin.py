"""Admin API: odměny (CRUD), uživatelé, objednávky, tomboly, redeem kódy, statistiky."""
import csv
import base64
import io
import json
import ipaddress
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response
from starlette.background import BackgroundTask

from ..config import (ALL_ROLES, PRODUCT_TYPES, PRODUCT_PERIODS, ORDER_PENDING, ORDER_FULFILLED,
                      UNLIMITED_STOCK, ROLE_ADMIN, ROLE_MOD, MOD_POINTS_MAX, STAFF_ROLES, DB_PATH, DATA_DIR, UPLOAD_DIR,
                      ANTICHEAT_RULES, DATACENTER_CIDRS)
from ..db import now_iso, set_setting, get_setting
from ..deps import db_dep, require_admin, require_user, require_broadcaster, admin_guard, to_public, add_points, record_audit, client_ip, notify
from .. import kickbot, economy, ipban, ddos, iprep, live, steam, cs_skins, autodrop, maintenance, alerts, digest, partners_flash, live_events, econ_health, mines_anticheat
from .games import list_games_admin, cancel_game_admin, games_history, refund_game_admin, refund_duel_admin
from ..models import (ProductIn, SkinLookupIn, SkinSearchIn, ImageUploadIn, UserRoleIn, UserFlagsIn, UserPointsIn, UserAdminMetaIn, OrderStatusIn, CodeGenIn,
                      BanIn, DropCreateIn, AutoDropIn, RuleIn, EconomyIn, IpBanIn, IpUnbanIn, BotToggleIn,
                      LiveModeIn, LegacyImportIn, PatchNoteIn, CommunityGoalIn, SubGoalIn, ModAppDecideIn, ManualOrderIn, ManualOrderBulkIn,
                      PointsLogPurgeIn, PartnerLinkIn, PartnerFlashConfigIn, GamesRakeIn, LiveHappyIn,
                      SelfExcludeIn, TimeoutIn, ShopDiscountIn, BanClusterIn, MinesBanIn, BroadcastIn)
from ..services import product_public, shop_discount_pct
from ..security import new_code, secure_choice

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(admin_guard)])


@router.api_route("/maintenance", methods=["GET", "POST"])
def maintenance_toggle(request: Request, to: str = "", mins: int = 0,
                       conn: sqlite3.Connection = Depends(db_dep),
                       user: sqlite3.Row = Depends(require_admin)):
    """Údržbový režim – GET vrátí stav, ?to=on|off|extend přepne. Jen admin.

    ?to=on&mins=30 → zapne s odpočtem 30 min (po vypršení se web SÁM vrátí).
    ?to=on&mins=0  → zapne napořád (bez odpočtu).
    ?to=extend&mins=15 → prodlouží odpočet o 15 min.

    Escape hatch proti zamčení: admin to může otevřít přímo v prohlížeči
    https://zurys.live/api/admin/maintenance?to=off (autorizuje admin session)."""
    if to == "on":
        until_iso = ""
        if mins and mins > 0:
            until_iso = (datetime.now(timezone.utc) + timedelta(minutes=mins)).isoformat()
        record_audit(conn, user, request, "maintenance.on", details=(f"{mins} min" if mins else "bez odpočtu"))
        maintenance.set_on(conn, True, until_iso)
    elif to == "extend":
        base = maintenance.until()
        try:
            base_dt = datetime.fromisoformat(base) if base else datetime.now(timezone.utc)
        except Exception:
            base_dt = datetime.now(timezone.utc)
        if base_dt < datetime.now(timezone.utc):
            base_dt = datetime.now(timezone.utc)
        maintenance.set_on(conn, True, (base_dt + timedelta(minutes=(mins or 15))).isoformat())
    elif to == "off":
        record_audit(conn, user, request, "maintenance.off")
        maintenance.set_on(conn, False)
    return {"ok": True, "maintenance": maintenance.is_on(), "until": maintenance.until()}


# ---------------- Statistiky ----------------
@router.get("/stats")
def stats(conn: sqlite3.Connection = Depends(db_dep)):
    def count(sql, params=()):
        return conn.execute(sql, params).fetchone()["c"]
    return {
        "users": count("SELECT COUNT(*) AS c FROM users"),
        "products": count("SELECT COUNT(*) AS c FROM products"),
        "active_products": count("SELECT COUNT(*) AS c FROM products WHERE active = 1"),
        "orders": count("SELECT COUNT(*) AS c FROM orders"),
        "pending_orders": count("SELECT COUNT(*) AS c FROM orders WHERE status = ?", (ORDER_PENDING,)),
        "codes": count("SELECT COUNT(*) AS c FROM redeem_codes"),
        "points_total": (conn.execute("SELECT COALESCE(SUM(points),0) AS c FROM users").fetchone()["c"]),
    }


# ---------------- Ekonomika (pasivní výdělek) ----------------
def _risk_for_user(conn: sqlite3.Connection, user_id: int) -> dict:
    u = conn.execute("SELECT id, role, banned, created_at FROM users WHERE id = ?", (user_id,)).fetchone()
    if not u:
        return {"score": 0, "level": "ok", "reasons": []}
    reasons = []
    score = 0
    if u["banned"]:
        score += 100
        reasons.append("ban")
    meta = conn.execute("SELECT watchlisted FROM admin_user_meta WHERE user_id = ?", (user_id,)).fetchone()
    if meta and meta["watchlisted"]:
        score += 25
        reasons.append("watchlist")
    try:
        age_h = (datetime.now(timezone.utc) - datetime.fromisoformat(u["created_at"])).total_seconds() / 3600
        if age_h < 1:
            score += 20
            reasons.append("novy ucet <1h")
        elif age_h < 24:
            score += 10
            reasons.append("novy ucet <24h")
    except Exception:
        pass
    ip_count = conn.execute(
        "SELECT COUNT(DISTINCT ip) AS c FROM login_events WHERE user_id = ? AND ip IS NOT NULL AND ip != ''",
        (user_id,),
    ).fetchone()["c"]
    if ip_count >= 5:
        score += 25
        reasons.append(f"{ip_count} IP")
    elif ip_count >= 3:
        score += 12
        reasons.append(f"{ip_count} IP")
    shared = conn.execute(
        "SELECT COALESCE(MAX(cnt), 0) AS c FROM ("
        "SELECT COUNT(DISTINCT user_id) AS cnt FROM login_events "
        "WHERE ip IN (SELECT DISTINCT ip FROM login_events WHERE user_id = ? AND ip IS NOT NULL AND ip != '') "
        "GROUP BY ip)",
        (user_id,),
    ).fetchone()["c"]
    if shared >= 4:
        score += 35
        reasons.append(f"sdilena IP {shared} uctu")
    elif shared >= 2:
        score += 15
        reasons.append(f"sdilena IP {shared} uctu")
    fp_shared = conn.execute(
        "SELECT COALESCE(MAX(cnt), 0) AS c FROM ("
        "SELECT COUNT(DISTINCT user_id) AS cnt FROM client_signals "
        "WHERE fp_hash IN (SELECT DISTINCT fp_hash FROM client_signals WHERE user_id = ? AND fp_hash IS NOT NULL) "
        "GROUP BY fp_hash)",
        (user_id,),
    ).fetchone()["c"]
    if fp_shared >= 3:
        score += 40
        reasons.append(f"stejne zarizeni {fp_shared} uctu")
    elif fp_shared >= 2:
        score += 20
        reasons.append("stejne zarizeni")
    if conn.execute("SELECT 1 FROM client_signals WHERE user_id = ? AND webdriver = 1 LIMIT 1", (user_id,)).fetchone():
        score += 45
        reasons.append("headless/webdriver")
    cutoff60 = (datetime.now(timezone.utc) - timedelta(minutes=60)).isoformat()
    recent = conn.execute(
        "SELECT COUNT(*) AS c, COALESCE(SUM(change), 0) AS gained FROM points_log "
        "WHERE user_id = ? AND change > 0 AND created_at >= ?",
        (user_id, cutoff60),
    ).fetchone()
    if recent["c"] >= 8 or recent["gained"] >= 5000:
        score += 25
        reasons.append(f"rychly zisk +{recent['gained']}")
    orders5 = conn.execute(
        "SELECT COUNT(*) AS c FROM orders WHERE user_id = ? AND created_at >= ?",
        (user_id, (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()),
    ).fetchone()["c"]
    if orders5 >= 10:
        score += 35
        reasons.append(f"{orders5} nakupu/5m")
    score = min(100, score)
    return {"score": score, "level": "danger" if score >= 70 else ("warn" if score >= 35 else "ok"),
            "reasons": reasons[:5]}


def _risk_user_payload(conn: sqlite3.Connection, user_id: int) -> dict:
    u = conn.execute("SELECT id, username, role, points, banned, avatar_url FROM users WHERE id = ?", (user_id,)).fetchone()
    if not u:
        return {}
    return {**dict(u), "banned": bool(u["banned"]), "risk": _risk_for_user(conn, user_id)}


def _economy_dashboard(conn: sqlite3.Connection, c24: str, c7: str) -> dict:
    def sums(cutoff):
        r = conn.execute(
            "SELECT COALESCE(SUM(CASE WHEN change > 0 THEN change ELSE 0 END),0) AS minted, "
            "COALESCE(SUM(CASE WHEN change < 0 THEN -change ELSE 0 END),0) AS burned "
            "FROM points_log WHERE created_at >= ?", (cutoff,)).fetchone()
        return {"minted": r["minted"], "burned": r["burned"], "net": r["minted"] - r["burned"]}
    return {
        "day": sums(c24),
        "week": sums(c7),
        "points_total": conn.execute("SELECT COALESCE(SUM(points),0) AS c FROM users").fetchone()["c"],
        "orders_24h": conn.execute("SELECT COUNT(*) AS c FROM orders WHERE created_at >= ?", (c24,)).fetchone()["c"],
        "top_earners": [dict(r) for r in conn.execute(
            "SELECT u.id, u.username, SUM(l.change) AS gained FROM points_log l JOIN users u ON u.id = l.user_id "
            "WHERE l.change > 0 AND l.created_at >= ? GROUP BY l.user_id ORDER BY gained DESC LIMIT 8", (c24,))],
        "top_holders": [dict(r) for r in conn.execute(
            "SELECT id, username, points FROM users ORDER BY points DESC, username ASC LIMIT 8")],
        "top_spenders": [dict(r) for r in conn.execute(
            "SELECT u.id, u.username, SUM(o.points_spent) AS spent FROM orders o JOIN users u ON u.id = o.user_id "
            "GROUP BY o.user_id ORDER BY spent DESC LIMIT 8")],
    }


def _admin_checklist(conn: sqlite3.Connection) -> list:
    backup_dir = DATA_DIR / "backups"
    try:
        backup_count = len(list(backup_dir.glob("*.sqlite*"))) if backup_dir.exists() else 0
    except Exception:
        backup_count = 0
    active_drop = conn.execute("SELECT code FROM drops WHERE active = 1 ORDER BY id DESC LIMIT 1").fetchone()
    return [
        {"key": "db", "label": "DB odpovida", "ok": True, "detail": "SQLite OK"},
        {"key": "backup", "label": "Zalohy existuji", "ok": backup_count > 0, "detail": f"{backup_count} souboru"},
        {"key": "alerts", "label": "Discord alerty", "ok": alerts.enabled(), "detail": "configured" if alerts.enabled() else "off"},
        {"key": "proxycheck", "label": "Proxycheck VPN", "ok": iprep.enabled(), "detail": "configured" if iprep.enabled() else "off"},
        {"key": "maintenance", "label": "Udrzba vypnuta", "ok": not maintenance.is_on(), "detail": "off" if not maintenance.is_on() else "ON"},
        {"key": "drop", "label": "Aktivni drop pod kontrolou", "ok": active_drop is None, "detail": active_drop["code"] if active_drop else "zadny aktivni"},
    ]


@router.get("/overview")
def admin_overview(conn: sqlite3.Connection = Depends(db_dep),
                   admin: sqlite3.Row = Depends(require_admin)):
    now = datetime.now(timezone.utc)
    c24 = (now - timedelta(hours=24)).isoformat()
    c7 = (now - timedelta(days=7)).isoformat()
    stats24 = {
        "new_users": conn.execute("SELECT COUNT(*) AS c FROM users WHERE created_at >= ?", (c24,)).fetchone()["c"],
        "orders": conn.execute("SELECT COUNT(*) AS c FROM orders WHERE created_at >= ?", (c24,)).fetchone()["c"],
        "pending_orders": conn.execute("SELECT COUNT(*) AS c FROM orders WHERE status = ?", (ORDER_PENDING,)).fetchone()["c"],
        "drop_claims": conn.execute("SELECT COUNT(*) AS c FROM drop_claims WHERE created_at >= ?", (c24,)).fetchone()["c"],
        "earned": conn.execute("SELECT COALESCE(SUM(change),0) AS c FROM points_log WHERE change > 0 AND created_at >= ?", (c24,)).fetchone()["c"],
        "spent": abs(conn.execute("SELECT COALESCE(SUM(change),0) AS c FROM points_log WHERE change < 0 AND created_at >= ?", (c24,)).fetchone()["c"]),
        "flags": conn.execute("SELECT COUNT(*) AS c FROM admin_audit WHERE action = 'anticheat.block' AND created_at >= ?", (c24,)).fetchone()["c"],
    }
    candidate_ids = set()
    for sql, params in (
        ("SELECT user_id FROM admin_user_meta WHERE watchlisted = 1 LIMIT 30", ()),
        ("SELECT DISTINCT user_id FROM client_signals WHERE webdriver = 1 LIMIT 30", ()),
        ("SELECT user_id FROM points_log WHERE change > 0 AND created_at >= ? GROUP BY user_id HAVING SUM(change) >= 3000 OR COUNT(*) >= 6 LIMIT 30", (c24,)),
        ("SELECT user_id FROM orders WHERE created_at >= ? GROUP BY user_id HAVING COUNT(*) >= 5 LIMIT 30", (c24,)),
    ):
        for r in conn.execute(sql, params):
            candidate_ids.add(r["user_id"])
    risky = [_risk_user_payload(conn, uid) for uid in candidate_ids]
    risky = [r for r in risky if r and r["risk"]["score"] > 0]
    risky.sort(key=lambda x: x["risk"]["score"], reverse=True)
    watchlist = []
    for r in conn.execute(
        "SELECT m.user_id, m.note, m.updated_at, u.username, u.role, u.points, u.banned, u.avatar_url "
        "FROM admin_user_meta m JOIN users u ON u.id = m.user_id "
        "WHERE m.watchlisted = 1 ORDER BY m.updated_at DESC LIMIT 12"):
        d = dict(r)
        d["banned"] = bool(d["banned"])
        d["risk"] = _risk_for_user(conn, d["user_id"])
        watchlist.append(d)
    recent = [dict(r) for r in conn.execute(
        "SELECT admin_name, action, target, details, ip, created_at FROM admin_audit ORDER BY id DESC LIMIT 8")]
    return {"stats24": stats24, "risky": risky[:10], "watchlist": watchlist,
            "recent_audit": recent, "checklist": _admin_checklist(conn),
            "economy": _economy_dashboard(conn, c24, c7)}


@router.get("/economy/dashboard")
def economy_dashboard(conn: sqlite3.Connection = Depends(db_dep)):
    # Přístup hlídá admin_guard na úrovni routeru: sekce "economy" = admin + broadcaster
    # (NE moderátor). Citlivý PŘEHLED (/admin/overview) je samostatný endpoint a zůstává jen admin.
    now = datetime.now(timezone.utc)
    return _economy_dashboard(conn, (now - timedelta(hours=24)).isoformat(),
                              (now - timedelta(days=7)).isoformat())


@router.get("/economy/health")
def economy_health_endpoint(days: int = Query(14, ge=1, le=90),
                            conn: sqlite3.Connection = Depends(db_dep)):
    """Zdraví ekonomiky: faucet vs sink podle kategorií, denní trend, DAU, inflace %.
    Přístup: sekce 'economy' (admin + broadcaster) – hlídá admin_guard na routeru."""
    return econ_health.health(conn, days)


@router.get("/economy/garden")
def economy_garden(conn: sqlite3.Connection = Depends(db_dep)):
    """Ekonomika zahrádky: výdaje (semínka + dekorace) vs příjmy (sklizně), net faucet/sink,
    okna 24h/7d/celkem + rozpad podle plodin. Sekce 'economy' (admin + broadcaster)."""
    return econ_health.garden_economy(conn)


@router.get("/economy/insights")
def economy_insights(days: int = Query(1, ge=1, le=30),
                     conn: sqlite3.Connection = Depends(db_dep)):
    """Farm vs gambling vs garden overview for economy admins."""
    data = econ_health.insights(conn, days)
    for flag in data.get("red_flags", [])[:5]:
        alerts.send(
            "Economy red flag",
            f"{flag.get('username')} - {flag.get('detail')}",
            key=f"econ-redflag:{flag.get('id')}:{flag.get('reason')}",
            cooldown=21600,
            ping=False,
        )
    return data


_retention_cache = {"at": 0.0, "data": None}


@router.get("/analytics/retention")
def analytics_retention(conn: sqlite3.Connection = Depends(db_dep)):
    """Retence: DAU/WAU/MAU (z sessions.last_seen), stickiness, noví vs vracející se,
    a týden-na-týden retence (cohort z points_log). Read-only + cache 120 s."""
    import time
    nowm = time.monotonic()
    if _retention_cache["data"] is not None and nowm - _retention_cache["at"] < 120:
        return _retention_cache["data"]
    now = datetime.now(timezone.utc)
    t0 = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    d7 = (now - timedelta(days=7)).isoformat()
    d14 = (now - timedelta(days=14)).isoformat()
    d30 = (now - timedelta(days=30)).isoformat()

    def one(sql, *p):
        return conn.execute(sql, p).fetchone()[0]

    dau = one("SELECT COUNT(DISTINCT user_id) FROM sessions WHERE last_seen >= ?", t0)
    wau = one("SELECT COUNT(DISTINCT user_id) FROM sessions WHERE last_seen >= ?", d7)
    mau = one("SELECT COUNT(DISTINCT user_id) FROM sessions WHERE last_seen >= ?", d30)
    total = one("SELECT COUNT(*) FROM users")
    # cohort retence: aktivní tento týden ∩ aktivní minulý týden / aktivní minulý týden
    cur_w = {r[0] for r in conn.execute("SELECT DISTINCT user_id FROM points_log WHERE change!=0 AND created_at >= ?", (d7,))}
    prev_w = {r[0] for r in conn.execute("SELECT DISTINCT user_id FROM points_log WHERE change!=0 AND created_at >= ? AND created_at < ?", (d14, d7))}
    retained = len(cur_w & prev_w)
    data = {
        "dau": dau, "wau": wau, "mau": mau, "total_users": total,
        "stickiness": round(dau * 100 / mau) if mau else 0,        # DAU/MAU %
        "new_today": one("SELECT COUNT(*) FROM users WHERE created_at >= ?", t0),
        "new_7d": one("SELECT COUNT(*) FROM users WHERE created_at >= ?", d7),
        "new_30d": one("SELECT COUNT(*) FROM users WHERE created_at >= ?", d30),
        "prev_week_active": len(prev_w),
        "retained": retained,
        "retention_pct": round(retained * 100 / len(prev_w)) if prev_w else 0,
        "churned": len(prev_w) - retained,
    }
    _retention_cache.update(at=nowm, data=data)
    return data


@router.post("/economy/points-log/purge")
def purge_points_log(data: PointsLogPurgeIn, request: Request,
                     conn: sqlite3.Connection = Depends(db_dep),
                     admin: sqlite3.Row = Depends(require_admin)):
    """Smaže KONKRÉTNÍ řádky points_logu podle ID (úklid testovacích/omylem vytvořených pohybů).
    NEMĚNÍ zůstatky uživatelů – jen odstraní záznamy z logu (a tím z 24h přehledu ekonomiky).
    Pojistka: když je vyplněný confirm_reason, smažou se jen řádky přesně s tím důvodem.
    Vše (vč. obsahu smazaných řádků) se zapíše do admin auditu kvůli reverzi. Admin only."""
    ids = list(dict.fromkeys(int(i) for i in data.ids))[:50]      # dedup + strop
    if not ids:
        raise HTTPException(status_code=400, detail="Žádná ID ke smazání.")
    ph = ",".join("?" for _ in ids)
    rows = conn.execute(
        f"SELECT id, user_id, change, reason, created_at FROM points_log WHERE id IN ({ph})", ids).fetchall()
    cr = (data.confirm_reason or "").strip()
    if cr:
        rows = [r for r in rows if (r["reason"] or "").strip() == cr]   # pojistka proti omylu
    if not rows:
        raise HTTPException(status_code=404, detail="Žádné odpovídající řádky (zkontroluj ID/důvod).")
    del_ids = [r["id"] for r in rows]
    ph2 = ",".join("?" for _ in del_ids)
    snap = " | ".join(f"#{r['id']} u{r['user_id']} {r['change']:+d} '{r['reason']}' @{r['created_at']}" for r in rows)
    conn.execute(f"DELETE FROM points_log WHERE id IN ({ph2})", del_ids)
    record_audit(conn, admin, request, "economy.pointslog_purge", f"{len(rows)} řádků", snap[:1400])
    conn.commit()
    return {"deleted": len(rows), "rows": [dict(r) for r in rows]}


# ---------------- Partnerské/sponzorské odkazy (klikni-a-ber bonus) ----------------
def _validate_partner_url(url: str) -> str:
    u = (url or "").strip()
    if not (u.startswith("http://") or u.startswith("https://")):
        raise HTTPException(status_code=400, detail="URL musí začínat http:// nebo https://")
    return u[:500]


@router.get("/economy/partner-links")
def admin_partner_links(conn: sqlite3.Connection = Depends(db_dep),
                        admin: sqlite3.Row = Depends(require_user)):
    """Všechny partnerské odkazy (i vypnuté) + počet kliknutí (vyzvednutí) u každého."""
    rows = conn.execute(
        "SELECT pl.id, pl.label, pl.url, pl.reward, pl.icon, pl.enabled, pl.sort_order, "
        "COALESCE(pl.mode,'once') AS mode, "
        "(SELECT COUNT(*) FROM partner_link_claims c WHERE c.link_id = pl.id) AS claims, "
        "(SELECT COUNT(*) FROM partner_flash_claims f WHERE f.link_id = pl.id) AS flash_claims "
        "FROM partner_links pl ORDER BY pl.sort_order ASC, pl.id ASC").fetchall()
    return {"links": [dict(r) for r in rows]}


@router.post("/economy/partner-links")
def admin_partner_link_create(data: PartnerLinkIn, request: Request,
                              conn: sqlite3.Connection = Depends(db_dep),
                              admin: sqlite3.Row = Depends(require_user)):
    url = _validate_partner_url(data.url)
    cur = conn.execute(
        "INSERT INTO partner_links (label, url, reward, icon, enabled, mode, sort_order, created_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (data.label.strip()[:80], url, max(0, data.reward), (data.icon or "🤝").strip()[:8],
         1 if data.enabled else 0, data.mode, data.sort_order, now_iso()))
    record_audit(conn, admin, request, "partner.create", f"#{cur.lastrowid} {data.label}",
                 f"{url} (+{data.reward})")
    conn.commit()
    return {"ok": True, "id": cur.lastrowid}


@router.post("/economy/partner-links/{link_id}")
def admin_partner_link_update(link_id: int, data: PartnerLinkIn, request: Request,
                              conn: sqlite3.Connection = Depends(db_dep),
                              admin: sqlite3.Row = Depends(require_user)):
    if not conn.execute("SELECT 1 FROM partner_links WHERE id = ?", (link_id,)).fetchone():
        raise HTTPException(status_code=404, detail="Odkaz nenalezen.")
    url = _validate_partner_url(data.url)
    conn.execute(
        "UPDATE partner_links SET label=?, url=?, reward=?, icon=?, enabled=?, mode=?, sort_order=? WHERE id=?",
        (data.label.strip()[:80], url, max(0, data.reward), (data.icon or "🤝").strip()[:8],
         1 if data.enabled else 0, data.mode, data.sort_order, link_id))
    record_audit(conn, admin, request, "partner.update", f"#{link_id} {data.label}", f"{url} (+{data.reward})")
    conn.commit()
    return {"ok": True}


@router.delete("/economy/partner-links/{link_id}")
def admin_partner_link_delete(link_id: int, request: Request,
                              conn: sqlite3.Connection = Depends(db_dep),
                              admin: sqlite3.Row = Depends(require_user)):
    row = conn.execute("SELECT label FROM partner_links WHERE id = ?", (link_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Odkaz nenalezen.")
    conn.execute("DELETE FROM partner_link_claims WHERE link_id = ?", (link_id,))
    conn.execute("DELETE FROM partner_links WHERE id = ?", (link_id,))
    record_audit(conn, admin, request, "partner.delete", f"#{link_id} {row['label']}", "")
    conn.commit()
    return {"ok": True}


@router.get("/economy/partner-flash")
def admin_partner_flash_get(conn: sqlite3.Connection = Depends(db_dep),
                            admin: sqlite3.Row = Depends(require_user)):
    """Stav + konfig Flash bonusu (random obnova 'flash' odkazů + bot do chatu)."""
    return partners_flash.status(conn)


@router.post("/economy/partner-flash")
def admin_partner_flash_set(data: PartnerFlashConfigIn, request: Request,
                            conn: sqlite3.Connection = Depends(db_dep),
                            admin: sqlite3.Row = Depends(require_user)):
    cfg = partners_flash.set_config(conn, data.model_dump(exclude_none=True))
    record_audit(conn, admin, request, "partner.flash_config", "flash",
                 str(data.model_dump(exclude_none=True))[:200])
    conn.commit()
    return cfg


@router.post("/economy/partner-flash/trigger")
def admin_partner_flash_trigger(request: Request,
                                conn: sqlite3.Connection = Depends(db_dep),
                                admin: sqlite3.Row = Depends(require_user)):
    """Ručně spustí flash kolo HNED (test / na přání) – ignoruje interval i live."""
    res = partners_flash.open_round(conn, force=True)
    record_audit(conn, admin, request, "partner.flash_trigger", "flash", str(res)[:200])
    conn.commit()
    return res


@router.get("/checklist")
def admin_checklist(conn: sqlite3.Connection = Depends(db_dep),
                    admin: sqlite3.Row = Depends(require_admin)):
    return {"items": _admin_checklist(conn)}


@router.get("/economy")
def get_economy(conn: sqlite3.Connection = Depends(db_dep)):
    return economy.get_eco(conn)


@router.post("/economy")
def set_economy(data: EconomyIn, request: Request,
                conn: sqlite3.Connection = Depends(db_dep),
                admin: sqlite3.Row = Depends(require_user)):
    vals = {k: v for k, v in data.model_dump().items() if v is not None}
    cur = economy.set_eco(conn, vals)
    record_audit(conn, admin, request, "economy.update", "ekonomika",
                 ", ".join(f"{k}={v}" for k, v in vals.items())[:380])
    conn.commit()
    return cur


@router.get("/economy/games-rake")
def get_games_rake(conn: sqlite3.Connection = Depends(db_dep),
                   admin: sqlite3.Row = Depends(require_user)):
    """Aktuální rake (% z banku) na hrách/duelech (coinflip, kostky, piškvorky)."""
    try:
        pct = max(0, min(50, int(get_setting(conn, "games_rake_pct", "0") or "0")))
    except (TypeError, ValueError):
        pct = 0
    return {"rake_pct": pct}


@router.post("/economy/games-rake")
def set_games_rake(data: GamesRakeIn, request: Request,
                   conn: sqlite3.Connection = Depends(db_dep),
                   admin: sqlite3.Row = Depends(require_user)):
    pct = max(0, min(50, int(data.rake_pct)))
    set_setting(conn, "games_rake_pct", str(pct))
    record_audit(conn, admin, request, "games.rake", "rake hry/duely", f"{pct}%")
    conn.commit()
    return {"ok": True, "rake_pct": pct}


@router.get("/economy/live")
def get_live_status(conn: sqlite3.Connection = Depends(db_dep)):
    """Stav detekce živého streamu (režim + jestli teď běží + jde-li detekovat)."""
    return live.status(conn)


@router.post("/economy/live")
def set_live_mode(data: LiveModeIn, request: Request,
                  conn: sqlite3.Connection = Depends(db_dep),
                  admin: sqlite3.Row = Depends(require_user)):
    """Nastaví režim: auto (Kick API) / on (vždy přičítat) / off (nikdy)."""
    set_setting(conn, "stream_live_override", data.mode)
    record_audit(conn, admin, request, "economy.live_mode", "stream live", data.mode)
    conn.commit()
    return live.status(conn)


@router.post("/news/broadcast")
def broadcast_notification(data: BroadcastIn, request: Request,
                           conn: sqlite3.Connection = Depends(db_dep),
                           admin: sqlite3.Row = Depends(require_broadcaster)):
    """Rozešle in-app notifikaci (zvoneček) segmentu uživatelů. broadcaster+admin (sekce 'news').

    segment: all (všichni) · active (login za 14 dní) · subs (jen subové). Jeden hromadný
    INSERT (ne notify() v cyklu) → škáluje i na tisíce uživatelů bez zátěže writeru."""
    seg = data.segment if data.segment in ("all", "active", "subs") else "all"
    icon = (data.icon or "📣").strip()[:8] or "📣"
    title = data.title.strip()[:120]
    body = (data.body or "").strip()[:300]
    link = (data.link or "").strip()[:80]
    if seg == "subs":
        where, params = "is_sub = 1", []
    elif seg == "active":
        cutoff = (datetime.now(timezone.utc) - timedelta(days=14)).isoformat()
        where, params = "id IN (SELECT user_id FROM sessions WHERE last_seen >= ?)", [cutoff]
    else:
        where, params = "1 = 1", []
    cur = conn.execute(
        "INSERT INTO notifications (user_id, icon, title, body, link, created_at) "
        f"SELECT id, ?, ?, ?, ?, ? FROM users WHERE {where}",
        [icon, title, body, link, now_iso(), *params])
    sent = cur.rowcount
    record_audit(conn, admin, request, "news.broadcast", f"segment:{seg}", f"{sent} příjemců · {title}")
    conn.commit()
    return {"ok": True, "sent": sent, "segment": seg}


@router.post("/import/legacy")
def import_legacy(data: LegacyImportIn, request: Request,
                  conn: sqlite3.Connection = Depends(db_dep),
                  admin: sqlite3.Row = Depends(require_broadcaster)):   # import: broadcaster+admin, mod ne
    """Import uživatelů ze staré platformy (zurys.store / Firebase).

    Zakládá 'ghost' účty (kick_username + body). Když se pak člověk přihlásí přes Kick
    stejným nickem, login si účet PŘEVEZME (match dle kick_username) i s body.
    Existující účty se NEpřepisují → idempotentní, lze pustit víckrát bez duplicit.
    """
    if admin["role"] == ROLE_MOD:
        raise HTTPException(status_code=403, detail="Import smí jen admin/broadcaster.")
    created = skipped = 0
    ts = now_iso()
    for u in data.users:
        key = (u.nick or "").strip().lstrip("@").lower()
        if not key or key.startswith("$(") or len(key) > 64:
            skipped += 1
            continue
        if conn.execute("SELECT 1 FROM users WHERE kick_username = ?", (key,)).fetchone():
            skipped += 1            # už existuje (reálný i dříve naimportovaný) – nepřepisuj
            continue
        pts = max(0, int(u.points or 0))
        display = ((u.nick or "").strip().lstrip("@")[:32]) or key
        cur = conn.execute(
            "INSERT INTO users (kick_username, username, points, role, created_at) "
            "VALUES (?, ?, ?, 'user', ?)",
            (key, display, pts, ts),
        )
        if pts:
            conn.execute(
                "INSERT INTO points_log (user_id, change, reason, created_at) VALUES (?, ?, ?, ?)",
                (cur.lastrowid, pts, "Import ze staré platformy (zurys.store)", ts),
            )
        created += 1
    record_audit(conn, admin, request, "import.legacy", f"{created} účtů",
                 f"created={created}, skipped={skipped}, total={len(data.users)}")
    conn.commit()
    return {"ok": True, "created": created, "skipped": skipped, "total": len(data.users)}


@router.post("/import/badges")
def import_badges(data: LegacyImportIn, request: Request,
                  conn: sqlite3.Connection = Depends(db_dep),
                  admin: sqlite3.Row = Depends(require_broadcaster)):   # import: broadcaster+admin, mod ne
    """Doplní SUB/VIP odznáčky existujícím účtům (dle nicku). Display-only, NEmění roli/oprávnění."""
    if admin["role"] == ROLE_MOD:
        raise HTTPException(status_code=403, detail="Import smí jen admin/broadcaster.")
    updated = subs = vips = 0
    for u in data.users:
        key = (u.nick or "").strip().lstrip("@").lower()
        if not key:
            continue
        s = 1 if u.is_sub else 0
        v = 1 if u.is_vip else 0
        cur = conn.execute(
            "UPDATE users SET is_sub = ?, is_vip = ? WHERE kick_username = ?", (s, v, key)
        )
        if cur.rowcount:
            updated += 1
            subs += s
            vips += v
    record_audit(conn, admin, request, "import.badges", f"{updated} účtů",
                 f"subs={subs}, vips={vips}")
    conn.commit()
    return {"ok": True, "updated": updated, "subs": subs, "vips": vips, "total": len(data.users)}


# ---------------- Odměny (produkty) ----------------
@router.get("/products")
def admin_products(conn: sqlite3.Connection = Depends(db_dep)):
    rows = conn.execute("SELECT * FROM products ORDER BY id DESC").fetchall()
    return [product_public(r) for r in rows]


def _validate_product(data: ProductIn):
    if data.type not in PRODUCT_TYPES:
        raise HTTPException(status_code=400, detail=f"Neplatný typ. Povolené: {PRODUCT_TYPES}")
    if (data.period or "") not in PRODUCT_PERIODS:
        raise HTTPException(status_code=400, detail=f"Neplatná perioda. Povolené: {PRODUCT_PERIODS}")


def _norm_ends(s):
    """Znormalizuje 'k dispozici do' na ISO (UTC) string, nebo None když prázdné/neplatné."""
    s = (s or "").strip()
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    except ValueError:
        raise HTTPException(status_code=400, detail="Neplatné datum (k dispozici do).")


@router.post("/products")
def create_product(data: ProductIn, request: Request,
                   conn: sqlite3.Connection = Depends(db_dep),
                   admin: sqlite3.Row = Depends(require_user)):
    _validate_product(data)
    cur = conn.execute(
        "INSERT INTO products (name, image_url, cost_points, category, type, period, "
        "subs_only, vip_only, stock, description, ends_at, max_per_person_pct, hot, active, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (data.name, data.image_url or "", data.cost_points, data.category or "",
         data.type, data.period or "", int(data.subs_only), int(data.vip_only), data.stock,
         data.description or "", _norm_ends(data.ends_at), int(data.max_per_person_pct or 0),
         int(data.hot), int(data.active), now_iso()),
    )
    record_audit(conn, admin, request, "product.create", f"#{cur.lastrowid} {data.name}",
                 f"{data.cost_points} PTS, typ {data.type}")
    conn.commit()
    row = conn.execute("SELECT * FROM products WHERE id = ?", (cur.lastrowid,)).fetchone()
    return product_public(row)


@router.post("/products/skin-lookup")
def product_skin_lookup(data: SkinLookupIn,
                        admin: sqlite3.Row = Depends(require_user)):
    """Najde obrázek CS2 skinu na Steam marketu podle názvu (auto-vyplnění ve formuláři).

    Sekci 'products' hlídá admin_guard (broadcaster + admin). Cena je jen orientační
    (NEukládá se, nezobrazuje veřejně — kvůli „body nemají peněžní hodnotu").
    """
    r = steam.lookup_skin(data.name)
    if not r:
        return {"ok": False, "image_url": "", "name": "", "price": ""}
    return {"ok": True, **r}


@router.post("/products/skin-search")
def product_skin_search(data: SkinSearchIn,
                        admin: sqlite3.Row = Depends(require_user)):
    """Našeptávač CS2 skinů z lokálního katalogu (jméno + obrázek, víc shod).

    Rychlé a spolehlivé (žádný Steam rate-limit) – pro vizuální pickr ve formuláři.
    """
    return {"results": cs_skins.search(data.query, 24), "ready": cs_skins.ready()}


_IMG_MIME = {"image/png": "png", "image/jpeg": "jpg", "image/jpg": "jpg", "image/webp": "webp", "image/gif": "gif"}
_MAX_IMG_BYTES = 6 * 1024 * 1024


def _decode_image_dataurl(s: str):
    """Data URL (data:image/...;base64,...) → (raw_bytes, ext). Vyhodí HTTPException při chybě.

    Limit 6 MB, jen PNG/JPG/WEBP/GIF, přípona dle OVĚŘENÉHO MIME (ne dle user filename).
    """
    s = (s or "").strip()
    header, _, b64 = s.partition(",")
    mime = header[5:].split(";")[0].lower() if header.startswith("data:") else ""
    ext = _IMG_MIME.get(mime)
    if not ext or "base64" not in header or not b64:
        raise HTTPException(status_code=400, detail="Nepodporovaný formát. Povolené: PNG, JPG, WEBP, GIF.")
    try:
        raw = base64.b64decode(b64, validate=True)
    except Exception:
        raise HTTPException(status_code=400, detail="Poškozená data obrázku.")
    if not raw or len(raw) > _MAX_IMG_BYTES:
        raise HTTPException(status_code=400, detail=f"Obrázek je příliš velký (nejvýše {_MAX_IMG_BYTES // (1024 * 1024)} MB).")
    return raw, ext


@router.post("/products/upload-image")
def upload_product_image(data: ImageUploadIn, request: Request,
                         conn: sqlite3.Connection = Depends(db_dep),
                         admin: sqlite3.Row = Depends(require_user)):
    """Nahraje obrázek odměny z PC (data URL) → uloží na trvalý disk, vrátí /uploads/<jméno>."""
    raw, ext = _decode_image_dataurl(data.data)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    name = f"{secrets.token_hex(8)}.{ext}"
    (UPLOAD_DIR / name).write_bytes(raw)
    record_audit(conn, admin, request, "image.upload", name, f"{len(raw) // 1024} kB")
    conn.commit()
    return {"url": f"/uploads/{name}"}


@router.post("/economy/coin-icon")
def upload_coin_icon(data: ImageUploadIn, request: Request,
                     conn: sqlite3.Connection = Depends(db_dep),
                     admin: sqlite3.Row = Depends(require_user)):
    """Nahraje ikonu měny („sedlák") → pevné /uploads/coin.png (ukáže se všude místo kuličky)."""
    raw, _ext = _decode_image_dataurl(data.data)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    (UPLOAD_DIR / "coin.png").write_bytes(raw)   # pevné jméno → CSS .coin ho bere napevno
    record_audit(conn, admin, request, "coin.icon", "coin.png", f"{len(raw) // 1024} kB")
    conn.commit()
    return {"ok": True, "url": "/uploads/coin.png"}


@router.put("/products/{product_id}")
def update_product(product_id: int, data: ProductIn, request: Request,
                   conn: sqlite3.Connection = Depends(db_dep),
                   admin: sqlite3.Row = Depends(require_user)):
    _validate_product(data)
    exists = conn.execute("SELECT id FROM products WHERE id = ?", (product_id,)).fetchone()
    if not exists:
        raise HTTPException(status_code=404, detail="Odměna nenalezena.")
    conn.execute(
        "UPDATE products SET name=?, image_url=?, cost_points=?, category=?, type=?, period=?, "
        "subs_only=?, vip_only=?, stock=?, description=?, ends_at=?, max_per_person_pct=?, hot=?, active=? WHERE id=?",
        (data.name, data.image_url or "", data.cost_points, data.category or "",
         data.type, data.period or "", int(data.subs_only), int(data.vip_only), data.stock,
         data.description or "", _norm_ends(data.ends_at), int(data.max_per_person_pct or 0),
         int(data.hot), int(data.active), product_id),
    )
    record_audit(conn, admin, request, "product.update", f"#{product_id} {data.name}",
                 f"{data.cost_points} PTS, typ {data.type}, aktivní {int(data.active)}")
    conn.commit()
    row = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
    return product_public(row)


@router.delete("/products/{product_id}")
def delete_product(product_id: int, request: Request,
                   conn: sqlite3.Connection = Depends(db_dep),
                   admin: sqlite3.Row = Depends(require_user)):
    p = conn.execute("SELECT name FROM products WHERE id = ?", (product_id,)).fetchone()
    conn.execute("DELETE FROM products WHERE id = ?", (product_id,))
    record_audit(conn, admin, request, "product.delete",
                 f"#{product_id} {p['name'] if p else '?'}")
    conn.commit()
    return {"ok": True}


# ---------------- Uživatelé ----------------
@router.get("/users")
def admin_users(q: str = Query("", max_length=64),
                sort: str = Query("points", max_length=12),
                conn: sqlite3.Connection = Depends(db_dep),
                admin: sqlite3.Row = Depends(require_user)):
    is_admin = admin["role"] == ROLE_ADMIN   # IP + e-mail vidí JEN admin, ne broadcaster
    # ORDER BY z whitelistu (NE z klienta přímo → žádná SQL injekce). level = dle nafarmeného XP.
    order_sql = "u.earned_total DESC, u.points DESC" if sort == "level" else "u.points DESC"
    cols = ("SELECT u.*, "
            "(SELECT ip FROM login_events e WHERE e.user_id=u.id ORDER BY e.id DESC LIMIT 1) AS last_ip, "
            "(SELECT COUNT(DISTINCT ip) FROM login_events e WHERE e.user_id=u.id) AS ip_count "
            "FROM users u ")
    if q:
        like = f"%{q.strip()}%"
        rows = conn.execute(
            cols + "WHERE u.username LIKE ? OR u.kick_username LIKE ? OR u.email LIKE ? "
            "ORDER BY " + order_sql + " LIMIT 100", (like, like, like),
        ).fetchall()
    else:
        rows = conn.execute(cols + "ORDER BY " + order_sql + " LIMIT 100").fetchall()
    out = []
    for r in rows:
        d = to_public(r, include_email=is_admin)
        if is_admin:                       # citlivé (IP) jen adminovi
            d["last_ip"] = r["last_ip"]
            d["ip_count"] = r["ip_count"]
            meta = conn.execute(
                "SELECT watchlisted, note, updated_at, updated_by_name FROM admin_user_meta WHERE user_id = ?",
                (r["id"],),
            ).fetchone()
            d["watchlisted"] = bool(meta["watchlisted"]) if meta else False
            d["admin_note"] = meta["note"] if meta else ""
            d["admin_note_updated_at"] = meta["updated_at"] if meta else None
            d["admin_note_by"] = meta["updated_by_name"] if meta else None
            d["risk"] = _risk_for_user(conn, r["id"])
        out.append(d)
    return out


@router.post("/users/{user_id}/role")
def set_role(user_id: int, data: UserRoleIn, request: Request,
             conn: sqlite3.Connection = Depends(db_dep),
             admin: sqlite3.Row = Depends(require_admin)):   # JEN admin – broadcaster role nemění (anti-eskalace)
    if data.role not in ALL_ROLES:
        raise HTTPException(status_code=400, detail=f"Neplatná role. Povolené: {ALL_ROLES}")
    target = conn.execute("SELECT username, role FROM users WHERE id = ?", (user_id,)).fetchone()
    if not target:
        raise HTTPException(status_code=404, detail="Uživatel nenalezen.")
    conn.execute("UPDATE users SET role = ? WHERE id = ?", (data.role, user_id))
    # Povýšení suba (role 'sub') na jinou roli → zachovej mu SUB odznak (is_sub=1), ať může
    # dál kupovat sub-only odměny. Mod/broadcaster, který byl sub, tím o sub výhody nepřijde.
    if target["role"] == "sub" and data.role != "sub":
        conn.execute("UPDATE users SET is_sub = 1 WHERE id = ?", (user_id,))
    record_audit(conn, admin, request, "user.role", f"#{user_id} {target['username']}",
                 f"{target['role']} → {data.role}")
    conn.commit()
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return to_public(row, include_email=True)


@router.post("/users/{user_id}/flags")
def set_flags(user_id: int, data: UserFlagsIn, request: Request,
              conn: sqlite3.Connection = Depends(db_dep),
              admin: sqlite3.Row = Depends(require_admin)):
    """Nastaví odznaky SUB/VIP/OG (nezávislé na roli – můžou být i víc naráz). Posílají se jen měněné."""
    target = conn.execute("SELECT username FROM users WHERE id = ?", (user_id,)).fetchone()
    if not target:
        raise HTTPException(status_code=404, detail="Uživatel nenalezen.")
    sets, vals, changed = [], [], []
    for col in ("is_sub", "is_vip", "is_og"):
        val = getattr(data, col)
        if val is not None:
            sets.append(f"{col} = ?")
            vals.append(1 if val else 0)
            changed.append(f"{col}={'on' if val else 'off'}")
    if sets:
        vals.append(user_id)
        conn.execute(f"UPDATE users SET {', '.join(sets)} WHERE id = ?", vals)
        record_audit(conn, admin, request, "user.flags", f"#{user_id} {target['username']}", ", ".join(changed))
        conn.commit()
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return to_public(row, include_email=True)


@router.get("/subs")
def list_subs(conn: sqlite3.Connection = Depends(db_dep),
              admin: sqlite3.Row = Depends(require_admin)):
    """Přehled aktivních subů: kdo je sub, kdy mu vyprší a jak/kdy ho získal (z points_log).
    Řazeno podle expirace (kdo vyprší nejdřív první; ruční/legacy bez data na konci). Jen admin."""
    rows = conn.execute(
        "SELECT id, username, kick_username, role, avatar_url, sub_expires_at, is_vip, is_og "
        "FROM users WHERE is_sub = 1 "
        "ORDER BY (sub_expires_at IS NULL), sub_expires_at ASC, username ASC"
    ).fetchall()
    # Poslední sub-event per uživatel JEDNÍM dotazem (místo N+1) – využije index points_log(user_id).
    events = {}
    sub_ids = [r["id"] for r in rows]
    if sub_ids:
        ph = ",".join("?" * len(sub_ids))
        for e in conn.execute(
            f"SELECT user_id, reason, created_at FROM points_log WHERE user_id IN ({ph}) "
            f"AND (reason LIKE 'Kick sub%' OR reason LIKE 'Kick resub%' "
            f"OR reason LIKE 'Kick gift sub (příjemce)%') ORDER BY created_at DESC, id DESC",
            sub_ids,
        ):
            events.setdefault(e["user_id"], e)   # první výskyt = nejnovější (díky ORDER BY DESC)
    now = datetime.now(timezone.utc)
    subs = []
    for r in rows:
        exp = r["sub_expires_at"]
        days_left = None
        if exp:
            try:
                dt = datetime.fromisoformat(str(exp).replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                days_left = round((dt - now).total_seconds() / 86400, 1)
            except (ValueError, TypeError):
                days_left = None
        ev = events.get(r["id"])   # poslední sub-event z dávkového dotazu výše (žádné N+1)
        subs.append({
            "id": r["id"],
            "username": r["username"],
            "kick_username": r["kick_username"],
            "avatar_url": r["avatar_url"],
            "role": r["role"],
            "is_sub": True,
            "is_vip": bool(r["is_vip"]),
            "is_og": bool(r["is_og"]),
            "sub_expires_at": exp,
            "days_left": days_left,
            "source": ev["reason"] if ev else None,
            "since": ev["created_at"] if ev else None,
        })
    return {"total": len(subs), "subs": subs}


@router.get("/users/{user_id}/points-log")
def user_points_log(user_id: int, limit: int = Query(50, ge=1, le=200),
                    conn: sqlite3.Connection = Depends(db_dep),
                    admin: sqlite3.Row = Depends(require_admin)):
    """Historie bodů konkrétního uživatele (vč. 0-záznamů jako gift příjemce). Admin přehled."""
    rows = conn.execute(
        "SELECT change, reason, created_at FROM points_log WHERE user_id = ? "
        "ORDER BY created_at DESC, id DESC LIMIT ?", (user_id, limit),
    ).fetchall()
    return {"entries": [dict(r) for r in rows]}


@router.post("/users/{user_id}/points")
def change_user_points(user_id: int, data: UserPointsIn, request: Request,
                       conn: sqlite3.Connection = Depends(db_dep),
                       admin: sqlite3.Row = Depends(require_user)):
    target = conn.execute("SELECT username FROM users WHERE id = ?", (user_id,)).fetchone()
    if not target:
        raise HTTPException(status_code=404, detail="Uživatel nenalezen.")
    if data.change == 0:
        raise HTTPException(status_code=400, detail="Změna bodů nesmí být nula.")
    reason = (data.reason or "").strip()
    if not reason:
        raise HTTPException(status_code=400, detail="Uveď důvod úpravy bodů (kvůli audit logu).")
    if admin["role"] == ROLE_MOD and abs(int(data.change)) > MOD_POINTS_MAX:
        raise HTTPException(status_code=403, detail=f"Moderátor smí upravit nejvýše ±{MOD_POINTS_MAX} sedláků najednou.")
    add_points(conn, user_id, data.change, reason, xp=False)   # admin grant = body ANO, level/XP NE (jinak by šel level koupit/darovat)
    record_audit(conn, admin, request, "user.points", f"#{user_id} {target['username']}",
                 f"{'+' if data.change > 0 else ''}{data.change} PTS – {reason}")
    conn.commit()
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return to_public(row, include_email=True)


# ---------------- Objednávky ----------------
@router.post("/users/{user_id}/admin-meta")
def set_user_admin_meta(user_id: int, data: UserAdminMetaIn, request: Request,
                        conn: sqlite3.Connection = Depends(db_dep),
                        admin: sqlite3.Row = Depends(require_admin)):
    target = conn.execute("SELECT username FROM users WHERE id = ?", (user_id,)).fetchone()
    if not target:
        raise HTTPException(status_code=404, detail="Uživatel nenalezen.")
    old = conn.execute("SELECT watchlisted, note FROM admin_user_meta WHERE user_id = ?", (user_id,)).fetchone()
    watchlisted = bool(old["watchlisted"]) if old else False
    note = old["note"] if old else ""
    if data.watchlisted is not None:
        watchlisted = bool(data.watchlisted)
    if data.note is not None:
        note = (data.note or "").strip()[:1000]
    conn.execute(
        "INSERT INTO admin_user_meta (user_id, watchlisted, note, updated_by, updated_by_name, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET watchlisted = excluded.watchlisted, note = excluded.note, "
        "updated_by = excluded.updated_by, updated_by_name = excluded.updated_by_name, updated_at = excluded.updated_at",
        (user_id, 1 if watchlisted else 0, note, admin["id"], admin["username"], now_iso()),
    )
    record_audit(conn, admin, request, "user.meta", f"#{user_id} {target['username']}",
                 f"watchlist={'on' if watchlisted else 'off'}, note={len(note)} znaku")
    conn.commit()
    return {"ok": True, "watchlisted": watchlisted, "note": note, "risk": _risk_for_user(conn, user_id)}


@router.get("/orders")
def admin_orders(status: str = Query("all"),
                 product_id: Optional[int] = Query(None),
                 conn: sqlite3.Connection = Depends(db_dep)):
    where_parts, params = [], []
    if status and status != "all":
        where_parts.append("o.status = ?")
        params.append(status)
    if product_id:
        where_parts.append("o.product_id = ?")
        params.append(product_id)
    where = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""
    rows = conn.execute(
        f"SELECT o.id, o.points_spent, o.status, o.created_at, o.product_id, "
        f"u.username, u.id AS user_id, u.steam_trade_url, COALESCE(p.name, o.product_name) AS product_name "
        f"FROM orders o JOIN users u ON u.id = o.user_id "
        f"LEFT JOIN products p ON p.id = o.product_id {where} "
        f"ORDER BY o.created_at DESC, o.id DESC LIMIT 300",
        params,
    ).fetchall()
    return [
        {
            "id": r["id"], "username": r["username"], "user_id": r["user_id"],
            "product_id": r["product_id"],
            "product_name": r["product_name"] or "(smazaná odměna)",
            "points_spent": r["points_spent"], "status": r["status"],
            "created_at": r["created_at"],
            "steam_trade_url": r["steam_trade_url"],
        }
        for r in rows
    ]


@router.get("/order-products")
def admin_order_products(conn: sqlite3.Connection = Depends(db_dep)):
    """Seznam položek s aspoň jednou objednávkou (+ počet) – pro filtr v Objednávkách."""
    rows = conn.execute(
        "SELECT o.product_id AS id, COALESCE(p.name, '(smazaná odměna)') AS name, "
        "COUNT(*) AS cnt FROM orders o "
        "LEFT JOIN products p ON p.id = o.product_id "
        "WHERE o.product_id IS NOT NULL "
        "GROUP BY o.product_id ORDER BY name COLLATE NOCASE"
    ).fetchall()
    return [{"id": r["id"], "name": r["name"], "count": r["cnt"]} for r in rows]


@router.post("/orders/{order_id}/status")
def set_order_status(order_id: int, data: OrderStatusIn, request: Request,
                     conn: sqlite3.Connection = Depends(db_dep),
                     admin: sqlite3.Row = Depends(require_user)):
    if data.status not in (ORDER_PENDING, ORDER_FULFILLED):
        raise HTTPException(status_code=400, detail="Neplatný stav objednávky.")
    row = conn.execute("SELECT user_id, product_name, status FROM orders WHERE id = ?", (order_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Objednávka nenalezena.")
    conn.execute("UPDATE orders SET status = ? WHERE id = ?", (data.status, order_id))
    record_audit(conn, admin, request, "order.status", f"#{order_id}", data.status)
    # notifikace majiteli při přechodu na „vyřízeno" (ne při opakovaném nastavení)
    if data.status == ORDER_FULFILLED and row["status"] != ORDER_FULFILLED and row["user_id"]:
        notify(conn, row["user_id"], "📦", "Objednávka vyřízena",
               f"Tvoje objednávka {row['product_name']} je vyřízená. 🎉", "#/profile")
    conn.commit()
    return {"ok": True, "id": order_id, "status": data.status}


@router.delete("/orders/{order_id}")
def delete_order(order_id: int, request: Request,
                 conn: sqlite3.Connection = Depends(db_dep),
                 admin: sqlite3.Row = Depends(require_user)):
    """Smaže objednávku z přehledu (úklid). Body se NEvrací – jen odstranění záznamu."""
    o = conn.execute(
        "SELECT o.status, u.username FROM orders o JOIN users u ON u.id = o.user_id WHERE o.id = ?",
        (order_id,),
    ).fetchone()
    conn.execute("DELETE FROM orders WHERE id = ?", (order_id,))
    record_audit(conn, admin, request, "order.delete",
                 f"#{order_id} {o['username'] if o else '?'}", o["status"] if o else "")
    conn.commit()
    return {"ok": True}


@router.post("/orders/clear-fulfilled")
def clear_fulfilled_orders(request: Request,
                           conn: sqlite3.Connection = Depends(db_dep),
                           admin: sqlite3.Row = Depends(require_user)):
    """Hromadně smaže všechny VYŘÍZENÉ objednávky (rychlý úklid přehledu). Body se nevrací."""
    cur = conn.execute("DELETE FROM orders WHERE status = ?", (ORDER_FULFILLED,))
    n = cur.rowcount
    record_audit(conn, admin, request, "order.clear_fulfilled", "", f"{n} smazáno")
    conn.commit()
    return {"ok": True, "deleted": n}


@router.post("/orders/fulfill-all")
def fulfill_all_orders(request: Request, product_id: Optional[int] = Query(None),
                       conn: sqlite3.Connection = Depends(db_dep),
                       admin: sqlite3.Row = Depends(require_user)):
    """Hromadně označí VŠECHNY ČEKAJÍCÍ objednávky jako vyřízené. Volitelně jen pro danou
    položku (product_id) – respektuje filtr v adminu, ať se neoznačí cizí tickety."""
    where, params = "status = ?", [ORDER_PENDING]
    if product_id:
        where += " AND product_id = ?"
        params.append(product_id)
    cur = conn.execute(f"UPDATE orders SET status = ? WHERE {where}", [ORDER_FULFILLED] + params)
    n = cur.rowcount
    record_audit(conn, admin, request, "order.fulfill_all",
                 f"product={product_id if product_id else 'vše'}", f"{n} vyřízeno")
    conn.commit()
    return {"ok": True, "fulfilled": n}


def _create_manual_order(conn, username, product_name, product_id, points_spent, note, admin, request, count=1):
    """Založí ticket(y) k vyřízení (BEZ commitu). Vrátí dict, nebo vyhodí ValueError.
    `count` = kolik objednávek (ticketů) vytvořit. Body se dopočítají z ceny odměny (jinak 0)."""
    uname = (username or "").strip().lstrip("@")
    if len(uname) < 2:
        raise ValueError("Nick chybí nebo je příliš krátký.")
    key = uname.lower()
    target = conn.execute(
        "SELECT id, username FROM users WHERE LOWER(kick_username) = ? OR LOWER(username) = ? "
        "ORDER BY (kick_username IS NOT NULL) DESC LIMIT 1", (key, key)).fetchone()
    if not target:
        raise ValueError(f"Uživatel '{uname}' nenalezen.")
    pname = (product_name or "").strip()
    pid, cost = product_id, None
    if pid:                      # navázáno na známou odměnu přes id
        p = conn.execute("SELECT name, cost_points FROM products WHERE id = ?", (pid,)).fetchone()
        if not p:
            pid = None
        else:
            cost = p["cost_points"]
            pname = pname or p["name"]
    if not pid and pname:        # zkus dohledat odměnu podle názvu (kvůli ceně do přehledu)
        p = conn.execute("SELECT id, cost_points FROM products WHERE LOWER(name) = LOWER(?) LIMIT 1", (pname,)).fetchone()
        if p:
            pid, cost = p["id"], p["cost_points"]
    if not pname:
        raise ValueError("Chybí odměna nebo důvod.")
    pts = points_spent if (points_spent and points_spent > 0) else (cost or 0)   # body = cena odměny
    cnt = max(1, min(50, int(count or 1)))
    note = (note or "").strip()
    ts = now_iso()
    ids = []
    for _ in range(cnt):
        cur = conn.execute(
            "INSERT INTO orders (user_id, product_id, product_name, points_spent, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (target["id"], pid, pname, pts, ORDER_PENDING, ts))
        ids.append(cur.lastrowid)
    details = (f"{cnt}× " if cnt > 1 else "") + pname + (f" · {note}" if note else "")
    record_audit(conn, admin, request, "order.manual_create", f"#{ids[0]} {target['username']}", details)
    return {"id": ids[0], "ids": ids, "count": cnt, "username": target["username"],
            "product_name": pname, "points_spent": pts, "status": ORDER_PENDING, "created_at": ts}


@router.post("/orders")
def create_manual_order(data: ManualOrderIn, request: Request,
                        conn: sqlite3.Connection = Depends(db_dep),
                        admin: sqlite3.Row = Depends(require_user)):
    """Ručně vytvoří objednávku/ticket (např. kompenzace za bug). NEúčtuje žádné body –
    jen založí záznam k vyřízení (status 'čeká'). Smí broadcaster + admin (mod ne)."""
    if admin["role"] == ROLE_MOD:
        raise HTTPException(status_code=403, detail="Ruční ticket smí přidat jen broadcaster nebo admin.")
    try:
        res = _create_manual_order(conn, data.username, data.product_name, data.product_id,
                                   data.points_spent, data.note, admin, request, data.count)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    conn.commit()
    return {"ok": True, **res}


@router.post("/orders/bulk")
def create_manual_orders_bulk(data: ManualOrderBulkIn, request: Request,
                              conn: sqlite3.Connection = Depends(db_dep),
                              admin: sqlite3.Row = Depends(require_user)):
    """Hromadně vytvoří tickety (víc lidí naráz). Špatný řádek se vrátí jako chyba, ostatní projdou.
    Smí broadcaster + admin (mod ne)."""
    if admin["role"] == ROLE_MOD:
        raise HTTPException(status_code=403, detail="Ruční ticket smí přidat jen broadcaster nebo admin.")
    items = (data.items or [])[:200]
    if not items:
        raise HTTPException(status_code=400, detail="Žádné řádky.")
    created, errors = [], []
    for i, it in enumerate(items):
        try:
            created.append(_create_manual_order(conn, it.username, it.product_name, it.product_id,
                                                 it.points_spent, it.note, admin, request, it.count or 1))
        except ValueError as e:
            errors.append({"line": i + 1, "username": (it.username or "").strip(), "error": str(e)})
    conn.commit()
    total = sum(c.get("count", 1) for c in created)
    return {"created": created, "errors": errors,
            "created_count": total, "lines_ok": len(created), "error_count": len(errors)}


# ---------------- Tomboly ----------------
@router.get("/raffle/products")
def raffle_products(conn: sqlite3.Connection = Depends(db_dep)):
    rows = conn.execute(
        "SELECT p.*, "
        "(SELECT COUNT(*) FROM raffle_entries e WHERE e.product_id = p.id) AS tickets, "
        "(SELECT COUNT(DISTINCT e.user_id) FROM raffle_entries e WHERE e.product_id = p.id) AS participants "
        "FROM products p WHERE p.type = 'raffle' ORDER BY p.id DESC"
    ).fetchall()
    out = []
    for r in rows:
        d = product_public(r)
        d["tickets"] = r["tickets"]
        d["participants"] = r["participants"]
        winner = conn.execute(
            "SELECT u.id, u.username FROM raffle_winners w JOIN users u ON u.id = w.user_id "
            "WHERE w.product_id = ? ORDER BY w.id DESC LIMIT 1",
            (r["id"],),
        ).fetchone()
        d["winner"] = winner["username"] if winner else None
        d["winner_id"] = winner["id"] if winner else None
        out.append(d)
    return out


@router.post("/raffle/{product_id}/draw")
def draw_winner(product_id: int, request: Request,
                conn: sqlite3.Connection = Depends(db_dep),
                admin: sqlite3.Row = Depends(require_user)):
    entries = conn.execute(
        "SELECT e.user_id, u.username, u.avatar_url FROM raffle_entries e "
        "JOIN users u ON u.id = e.user_id WHERE e.product_id = ?",
        (product_id,),
    ).fetchall()
    if not entries:
        raise HTTPException(status_code=400, detail="Tato tombola nemá žádné tikety.")
    entry = secure_choice(entries)
    conn.execute(
        "INSERT INTO raffle_winners (product_id, user_id, created_at) VALUES (?, ?, ?)",
        (product_id, entry["user_id"], now_iso()),
    )
    record_audit(conn, admin, request, "raffle.draw", f"produkt #{product_id}",
                 f"výherce: {entry['username']}")
    conn.commit()
    # oznámení účastníkům (in-app notif + web push) – mimo request v background threadu
    prow = conn.execute("SELECT name FROM products WHERE id = ?", (product_id,)).fetchone()
    pname = prow["name"] if prow and prow["name"] else "tombola"
    entrant_ids = list({e["user_id"] for e in entries})
    try:
        from .. import webpush
        webpush.notify_raffle_draw(pname, entry["user_id"], entry["username"], entrant_ids)
    except Exception:
        pass
    return {
        "ok": True,
        "winner": {"username": entry["username"], "avatar_url": entry["avatar_url"]},
        "message": f"Výherce vylosován: {entry['username']} 🎉",
    }


@router.post("/raffle/{product_id}/undo-draw")
def undo_draw(product_id: int, request: Request,
              conn: sqlite3.Connection = Depends(db_dep),
              admin: sqlite3.Row = Depends(require_user)):
    """Vrátí losování: smaže výherce dané tomboly. Tikety/účastníci zůstávají → jako před losem."""
    cur = conn.execute("DELETE FROM raffle_winners WHERE product_id = ?", (product_id,))
    record_audit(conn, admin, request, "raffle.undo_draw", f"produkt #{product_id}",
                 f"smazáno výherců: {cur.rowcount}")
    conn.commit()
    return {"ok": True, "removed": cur.rowcount}


# ---------------- Redeem kódy ----------------
@router.get("/codes")
def admin_codes(conn: sqlite3.Connection = Depends(db_dep)):
    rows = conn.execute(
        "SELECT c.*, p.name AS product_name FROM redeem_codes c "
        "LEFT JOIN products p ON p.id = c.product_id ORDER BY c.id DESC"
    ).fetchall()
    return [
        {
            "id": r["id"], "code": r["code"], "points_value": r["points_value"],
            "product_id": r["product_id"], "product_name": r["product_name"],
            "max_uses": r["max_uses"], "uses_count": r["uses_count"],
            "expires_at": r["expires_at"], "created_at": r["created_at"],
        }
        for r in rows
    ]


@router.post("/codes")
def generate_codes(data: CodeGenIn, request: Request,
                   conn: sqlite3.Connection = Depends(db_dep),
                   admin: sqlite3.Row = Depends(require_user)):
    if data.points_value == 0 and not data.product_id:
        raise HTTPException(status_code=400,
                            detail="Zadej hodnotu v bodech nebo konkrétní odměnu.")
    if data.product_id:
        p = conn.execute("SELECT id FROM products WHERE id = ?", (data.product_id,)).fetchone()
        if not p:
            raise HTTPException(status_code=400, detail="Zvolená odměna neexistuje.")
    created = []
    for i in range(data.count):
        # vlastní kód lze zadat jen při generování 1 ks
        if data.code and data.count == 1:
            code = data.code.strip().upper()
        else:
            code = new_code("STREAM-")
        # ošetření kolize unikátního kódu
        for _ in range(5):
            dup = conn.execute("SELECT 1 FROM redeem_codes WHERE UPPER(code)=UPPER(?)", (code,)).fetchone()
            if not dup:
                break
            code = new_code("STREAM-")
        else:
            raise HTTPException(status_code=400, detail="Kód už existuje, zkus jiný.")
        cur = conn.execute(
            "INSERT INTO redeem_codes (code, points_value, product_id, max_uses, "
            "uses_count, expires_at, created_at) VALUES (?, ?, ?, ?, 0, ?, ?)",
            (code, data.points_value, data.product_id, data.max_uses,
             data.expires_at or None, now_iso()),
        )
        created.append({"id": cur.lastrowid, "code": code})
    record_audit(conn, admin, request, "code.generate", f"{len(created)}× kód",
                 f"{data.points_value} PTS"
                 + (f", produkt #{data.product_id}" if data.product_id else ""))
    conn.commit()
    return {"ok": True, "created": created}


@router.delete("/codes/{code_id}")
def delete_code(code_id: int, request: Request,
                conn: sqlite3.Connection = Depends(db_dep),
                admin: sqlite3.Row = Depends(require_user)):
    c = conn.execute("SELECT code FROM redeem_codes WHERE id = ?", (code_id,)).fetchone()
    conn.execute("DELETE FROM redeem_codes WHERE id = ?", (code_id,))
    record_audit(conn, admin, request, "code.delete",
                 f"#{code_id} {c['code'] if c else '?'}")
    conn.commit()
    return {"ok": True}


# ---------------- Bezpečnost / Anticheat ----------------
@router.get("/security/logins")
def security_logins(user_id: int = Query(0),
                    username: str = Query("", max_length=64),
                    ip: str = Query("", max_length=64),
                    limit: int = Query(120, ge=1, le=500),
                    offset: int = Query(0, ge=0),
                    conn: sqlite3.Connection = Depends(db_dep)):
    """Historie přihlášení s IP adresami (filtry + paginace)."""
    where, params = [], []
    if user_id:
        where.append("e.user_id = ?"); params.append(user_id)
    if username:
        where.append("u.username LIKE ?"); params.append(f"%{username.strip()}%")
    if ip:
        where.append("e.ip LIKE ?"); params.append(f"%{ip.strip()}%")
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    rows = conn.execute(
        f"SELECT e.id, e.ip, e.user_agent, e.method, e.created_at, "
        f"u.id AS user_id, u.username, u.role, u.banned "
        f"FROM login_events e JOIN users u ON u.id = e.user_id {where_sql} "
        f"ORDER BY e.created_at DESC, e.id DESC LIMIT ? OFFSET ?",
        params + [limit, offset],
    ).fetchall()
    return [dict(r) for r in rows]


@router.get("/security/sessions")
def security_sessions(conn: sqlite3.Connection = Depends(db_dep)):
    """Aktivní (neexpirované) přihlášené relace."""
    rows = conn.execute(
        "SELECT s.ip, s.user_agent, s.last_seen, s.created_at, s.expires_at, "
        "u.id AS user_id, u.username, u.role, u.banned "
        "FROM sessions s JOIN users u ON u.id = s.user_id "
        "WHERE s.expires_at > ? ORDER BY s.last_seen DESC, s.created_at DESC",
        (now_iso(),),
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------- IP bany (full-page blok) ----------------
@router.get("/security/ip-bans")
def list_ip_bans(conn: sqlite3.Connection = Depends(db_dep)):
    """Aktivní IP bany."""
    return ipban.active_list(conn)


@router.post("/security/ip-ban")
def add_ip_ban(data: IpBanIn, request: Request,
               conn: sqlite3.Connection = Depends(db_dep),
               admin: sqlite3.Row = Depends(require_user)):
    ip = data.ip.strip()
    if not ipban.valid_ip(ip):
        raise HTTPException(status_code=400, detail="Neplatná IP adresa.")
    if ipban.is_loopback(ip):
        raise HTTPException(status_code=400, detail="Lokální IP (loopback) nelze zabanovat.")
    if ip == client_ip(request):
        raise HTTPException(status_code=400, detail="Nemůžeš zabanovat svoji vlastní IP (zamkl by ses).")
    ipban.ban(conn, ip, data.reason or "", data.hours)
    record_audit(conn, admin, request, "ip.ban", ip,
                 f"{data.reason or '—'} ({'trvale' if not data.hours else str(data.hours) + ' h'})")
    conn.commit()
    return {"ok": True, "ip": ip, "hours": data.hours}


@router.post("/security/ip-unban")
def remove_ip_ban(data: IpUnbanIn, request: Request,
                  conn: sqlite3.Connection = Depends(db_dep),
                  admin: sqlite3.Row = Depends(require_user)):
    ipban.unban(conn, data.ip.strip())
    record_audit(conn, admin, request, "ip.unban", data.ip.strip(), "")
    conn.commit()
    return {"ok": True}


# ---------------- Anti-DDoS: přehled provozu (Top IP) + auto-ban toggle ----------------
@router.get("/security/traffic")
def security_traffic():
    """Top IP podle počtu requestů (klouzavé okno) + nedávné auto-bany + souhrn/práh."""
    return {
        "top": ddos.top(20),
        "recent_autobans": ddos.recent_autobans(),
        "stats": ddos.stats(),
    }


@router.post("/digest/test")
def send_digest_now(request: Request,
                    conn: sqlite3.Connection = Depends(db_dep),
                    admin: sqlite3.Row = Depends(require_admin)):
    """Pošle denní digest OKAMŽITĚ na Discord (ruční trigger pro test/kontrolu).
    Admin only. Vrací i náhled textu, takže ho admin vidí i bez webhooku."""
    text = digest.compose(conn)
    if alerts.enabled():
        alerts.send("📊 ZURYS digest (ruční)", detail=text, key="digest-manual", cooldown=0, ping=False)
    backup_sent = digest.send_offsite_backup()
    record_audit(conn, admin, request, "digest.test", "", "manualni odeslani digestu")
    conn.commit()
    return {"ok": True, "webhook_enabled": alerts.enabled(), "backup_sent": backup_sent, "preview": text}


@router.post("/community-goal")
def set_community_goal(data: CommunityGoalIn, request: Request,
                       conn: sqlite3.Connection = Depends(db_dep),
                       admin: sqlite3.Row = Depends(require_admin)):
    """Naladí komunitní chat cíl (target / reward / zap-vyp). Admin only."""
    if data.enabled is not None:
        set_setting(conn, "cgoal_enabled", "1" if data.enabled else "0")
    if data.target is not None:
        set_setting(conn, "cgoal_target", str(data.target))
    if data.reward is not None:
        set_setting(conn, "cgoal_reward", str(data.reward))
    record_audit(conn, admin, request, "cgoal.update", "",
                 f"enabled={data.enabled} target={data.target} reward={data.reward}")
    conn.commit()
    from ..community_goal import status
    return status(conn)


@router.post("/sub-goal")
def set_sub_goal(data: SubGoalIn, request: Request,
                 conn: sqlite3.Connection = Depends(db_dep),
                 admin: sqlite3.Row = Depends(require_admin)):
    """Naladí komunitní SUB cíl (krok / odměna za tier / strop tierů / zap-vyp). Admin only."""
    if data.enabled is not None:
        set_setting(conn, "subgoal_enabled", "1" if data.enabled else "0")
    if data.target is not None:
        set_setting(conn, "subgoal_target", str(data.target))          # KROK subů na tier
    if data.reward is not None:
        set_setting(conn, "subgoal_reward", str(data.reward))          # odměna za tier
    if data.tier_max is not None:
        set_setting(conn, "subgoal_tier_max", str(data.tier_max))      # strop tierů
    record_audit(conn, admin, request, "subgoal.update", "",
                 f"enabled={data.enabled} step={data.target} reward={data.reward} tier_max={data.tier_max}")
    conn.commit()
    from ..subgoal import status
    return status(conn)


# ---------------- Nábor moderátorů (přihlášky) ----------------
@router.get("/mod-applications")
def mod_applications_list(conn: sqlite3.Connection = Depends(db_dep)):
    """Přihlášky na moderátory: čekající + posledních 20 vyřízených + staty uchazečů.
    Staty se počítají BATCH (2 dotazy místo 2×N), ať admin list nedělá N+1 na points_log."""
    base = ("SELECT a.*, u.username FROM mod_applications a JOIN users u ON u.id = a.user_id ")
    pending = conn.execute(base + "WHERE a.status = 'pending' ORDER BY a.id ASC").fetchall()
    recent = conn.execute(base + "WHERE a.status != 'pending' ORDER BY a.id DESC LIMIT 20").fetchall()
    uids = list({r["user_id"] for r in pending} | {r["user_id"] for r in recent})
    stats = {}
    if uids:
        ph = ",".join("?" * len(uids))
        for u in conn.execute(
            f"SELECT id, points, created_at, is_sub, banned, role, kick_username FROM users WHERE id IN ({ph})",
            uids):
            age_days = None
            try:
                t = datetime.fromisoformat(u["created_at"])
                if t.tzinfo is None:
                    t = t.replace(tzinfo=timezone.utc)
                age_days = int((datetime.now(timezone.utc) - t).total_seconds() // 86400)
            except Exception:
                pass
            stats[u["id"]] = {"points": u["points"], "age_days": age_days, "is_sub": bool(u["is_sub"]),
                              "banned": bool(u["banned"]), "role": u["role"], "kick": u["kick_username"],
                              "chat_msgs": 0}
        for c in conn.execute(
            f"SELECT user_id, COUNT(*) c FROM points_log WHERE user_id IN ({ph}) "
            "AND reason = 'Aktivita v chatu' GROUP BY user_id", uids):
            if c["user_id"] in stats:
                stats[c["user_id"]]["chat_msgs"] = c["c"]

    def _row(r):
        return {"id": r["id"], "user_id": r["user_id"], "username": r["username"],
                "answers": json.loads(r["answers"] or "{}"), "status": r["status"],
                "created_at": r["created_at"], "decided_at": r["decided_at"], "decided_by": r["decided_by"],
                "stats": stats.get(r["user_id"], {})}
    return {"pending": [_row(r) for r in pending], "recent": [_row(r) for r in recent],
            "pending_count": len(pending), "open": get_setting(conn, "modapp_open", "1") == "1"}


@router.post("/mod-applications/{aid}/decide")
def mod_application_decide(aid: int, data: ModAppDecideIn, request: Request,
                          conn: sqlite3.Connection = Depends(db_dep),
                          admin: sqlite3.Row = Depends(require_admin)):
    """Přijme / zamítne přihlášku. Při accept volitelně rovnou nastaví roli 'mod'. Notifikuje uchazeče."""
    a = conn.execute("SELECT * FROM mod_applications WHERE id = ?", (aid,)).fetchone()
    if not a:
        raise HTTPException(status_code=404, detail="Přihláška neexistuje.")
    if a["status"] != "pending":
        raise HTTPException(status_code=400, detail=f"Přihláška už je vyřízená ({a['status']}).")
    accepted = data.action == "accept"
    if accepted and data.set_mod:
        # roli 'mod' jen když je teď user/sub (nikdy nedemotovat admina/broadcastera)
        conn.execute("UPDATE users SET role = 'mod' WHERE id = ? AND role IN ('user', 'sub')", (a["user_id"],))
    conn.execute("UPDATE mod_applications SET status = ?, decided_at = ?, decided_by = ? WHERE id = ?",
                 ("accepted" if accepted else "rejected", now_iso(), admin["username"], aid))
    record_audit(conn, admin, request, "modapp.accept" if accepted else "modapp.reject",
                 target=str(a["user_id"]), details="set_mod" if (accepted and data.set_mod) else "")
    if accepted:
        notify(conn, a["user_id"], "🛡️", "Přijat do týmu modů!",
               "Tvoje přihláška na moderátora byla schválena. Vítej v týmu! 🎉", "#/profile")
    else:
        notify(conn, a["user_id"], "📋", "Přihláška na moda",
               "Tvoje přihláška na moderátora tentokrát nebyla vybrána. Díky za zájem! 🌾")
    conn.commit()
    return {"ok": True, "status": "accepted" if accepted else "rejected", "set_mod": accepted and data.set_mod}


@router.post("/mod-applications/toggle")
def mod_applications_toggle(request: Request,
                           conn: sqlite3.Connection = Depends(db_dep),
                           admin: sqlite3.Row = Depends(require_admin)):
    """Otevře / zavře nábor moderátorů."""
    now_open = get_setting(conn, "modapp_open", "1") == "1"
    set_setting(conn, "modapp_open", "0" if now_open else "1")
    record_audit(conn, admin, request, "modapp.toggle", "", "closed" if now_open else "open")
    conn.commit()
    return {"open": not now_open}


# ---------------- Mines historie (admin přehled) ----------------
@router.get("/mines-history")
def admin_mines_history(q: str = Query("", max_length=64), limit: int = Query(50, ge=1, le=200),
                        conn: sqlite3.Connection = Depends(db_dep)):
    """Historie Mines: house staty + feed (kdo hrál / vyhrál / prohrál) + top hráči dle net."""
    st = conn.execute(
        "SELECT COUNT(*) g, COALESCE(SUM(bet),0) w, COALESCE(SUM(payout),0) p, COUNT(DISTINCT user_id) players "
        "FROM mines_games WHERE status IN ('busted','cashed')").fetchone()
    stats = {"games": st["g"], "wagered": st["w"], "paid": st["p"],
             "house_net": st["w"] - st["p"], "players": st["players"]}
    where, params = "m.status IN ('busted','cashed')", []
    if q.strip():
        where += " AND u.username LIKE ?"
        params.append(f"%{q.strip()}%")
    rows = conn.execute(
        f"SELECT m.id, u.username, m.bet, m.mines, m.payout, m.status, m.revealed, m.created_at "
        f"FROM mines_games m JOIN users u ON u.id = m.user_id WHERE {where} ORDER BY m.id DESC LIMIT ?",
        params + [limit]).fetchall()
    feed = [{"id": r["id"], "username": r["username"], "bet": r["bet"], "mines": r["mines"],
             "payout": r["payout"], "net": r["payout"] - r["bet"], "status": r["status"],
             "safe": len(json.loads(r["revealed"] or "[]")), "created_at": r["created_at"]} for r in rows]
    agg = conn.execute(
        "SELECT u.username, COUNT(*) g, COALESCE(SUM(m.payout),0) - COALESCE(SUM(m.bet),0) net, "
        "SUM(CASE WHEN m.status='busted' THEN 1 ELSE 0 END) busts "
        "FROM mines_games m JOIN users u ON u.id = m.user_id WHERE m.status IN ('busted','cashed') "
        "GROUP BY m.user_id").fetchall()
    players = [{"username": r["username"], "games": r["g"], "net": r["net"],
                "bust_rate": round(r["busts"] * 100 / r["g"]) if r["g"] else 0} for r in agg]
    winners = sorted((p for p in players if p["net"] > 0), key=lambda x: -x["net"])[:8]
    losers = sorted((p for p in players if p["net"] < 0), key=lambda x: x["net"])[:8]
    return {"stats": stats, "feed": feed, "winners": winners, "losers": losers}


@router.get("/mines-bans")
def admin_mines_bans(conn: sqlite3.Connection = Depends(db_dep)):
    """Kdo má zákaz hraní Mines (cílený ban – zbytek webu jim funguje)."""
    ids = mines_anticheat.active_mines_ban_ids(conn)
    if not ids:
        return {"banned": []}
    expiries = mines_anticheat.mines_ban_expiries(conn)
    qm = ",".join("?" * len(ids))
    rows = conn.execute(f"SELECT id, username FROM users WHERE id IN ({qm})", list(ids)).fetchall()
    return {"banned": [{"id": r["id"], "username": r["username"],
                         "expires_at": expiries.get(str(r["id"]))} for r in rows]}


@router.post("/mines-ban")
def admin_mines_ban(data: MinesBanIn, request: Request,
                    conn: sqlite3.Connection = Depends(db_dep),
                    admin: sqlite3.Row = Depends(require_admin)):
    """Zabaní/odbaní uživatele JEN ve hře Mines (zbytek webu mu zůstává otevřený).
    Seznam zabanovaných uid drží app_settings['mines_ban_uids']."""
    key = data.username.strip().lstrip("@")
    u = conn.execute("SELECT id, username FROM users WHERE username = ? OR kick_username = ?",
                     (key, key.lower())).fetchone()
    if not u:
        raise HTTPException(status_code=404, detail=f"Uživatel '{data.username}' nenalezen.")
    if data.banned:
        mines_anticheat.ban_mines_user(conn, u["id"], "Ruční permanentní Mines ban od admina.")
    else:
        mines_anticheat.unban_mines_user(conn, u["id"])
    record_audit(conn, admin, request, "mines.ban" if data.banned else "mines.unban", u["username"])
    conn.commit()
    total = len(mines_anticheat.active_mines_ban_ids(conn))
    return {"ok": True, "username": u["username"], "banned": bool(data.banned), "total_banned": total}


@router.post("/chat-reset")
def chat_reset(request: Request,
               conn: sqlite3.Connection = Depends(db_dep),
               admin: sqlite3.Row = Depends(require_admin)):
    """Vynuluje DNEŠNÍ chat data: komunitní cíl + dnešní Top Chattery (smaže dnešní
    'Aktivita v chatu' záznamy a chat_today). Body už připsané zůstávají. Admin only."""
    today = datetime.now(timezone.utc).date().isoformat()
    day_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    n = conn.execute("DELETE FROM points_log WHERE reason='Aktivita v chatu' AND created_at >= ?",
                     (day_start,)).rowcount
    conn.execute("UPDATE activity_state SET chat_today = 0 WHERE day = ?", (today,))
    set_setting(conn, "cgoal_day", today)
    set_setting(conn, "cgoal_progress", "0")
    set_setting(conn, "cgoal_done", "0")
    record_audit(conn, admin, request, "chat.reset", "", f"smazano {n} dnesnich chat zaznamu")
    conn.commit()
    return {"ok": True, "deleted": n}


@router.post("/security/autoban")
def set_ddos_autoban(data: BotToggleIn, request: Request,
                     conn: sqlite3.Connection = Depends(db_dep),
                     admin: sqlite3.Row = Depends(require_user)):
    """Zapne/vypne opatrný auto-dočasný ban IP při náporu (volba přežije restart)."""
    ddos.set_autoban(data.enabled)
    set_setting(conn, "ddos_autoban", "1" if data.enabled else "0")
    record_audit(conn, admin, request, "ddos.autoban", "", "ON" if data.enabled else "OFF")
    conn.commit()
    return {"ok": True, "enabled": data.enabled}


def _audit_where(action: str, admin_name: str):
    """Pomocník: WHERE clauzule + params pro filtry audit logu."""
    where, params = [], []
    if action:
        where.append("action = ?"); params.append(action.strip())
    if admin_name:
        where.append("admin_name LIKE ?"); params.append(f"%{admin_name.strip()}%")
    return (("WHERE " + " AND ".join(where)) if where else ""), params


@router.get("/security/audit")
def security_audit(action: str = Query("", max_length=64),
                   admin_name: str = Query("", max_length=64),
                   limit: int = Query(150, ge=1, le=500),
                   offset: int = Query(0, ge=0),
                   conn: sqlite3.Connection = Depends(db_dep)):
    """Audit log admin akcí (kdo, kdy, co provedl) – filtry + paginace."""
    where_sql, params = _audit_where(action, admin_name)
    total = conn.execute(
        f"SELECT COUNT(*) AS c FROM admin_audit {where_sql}", params
    ).fetchone()["c"]
    rows = conn.execute(
        f"SELECT id, admin_name, action, target, details, ip, created_at "
        f"FROM admin_audit {where_sql} ORDER BY id DESC LIMIT ? OFFSET ?",
        params + [limit, offset],
    ).fetchall()
    # Seznam unikátních hodnot pro nabídku ve filtrech (úsporně, max ~30 záznamů)
    actions = [r["action"] for r in conn.execute(
        "SELECT DISTINCT action FROM admin_audit ORDER BY action LIMIT 30")]
    admins = [r["admin_name"] for r in conn.execute(
        "SELECT DISTINCT admin_name FROM admin_audit WHERE admin_name IS NOT NULL "
        "ORDER BY admin_name LIMIT 30")]
    return {"rows": [dict(r) for r in rows], "total": total,
            "limit": limit, "offset": offset,
            "filters": {"actions": actions, "admins": admins}}


# ---------------- CSV exporty ----------------
def _csv_response(rows, header, filename: str) -> Response:
    """Stáhne data jako CSV (UTF-8 BOM + středník = Excel CZ-friendly)."""
    buf = io.StringIO()
    buf.write("﻿")  # BOM – aby Excel rozpoznal UTF-8 a české znaky
    w = csv.writer(buf, delimiter=";", quoting=csv.QUOTE_MINIMAL,
                   lineterminator="\r\n")
    w.writerow(header)
    for r in rows:
        w.writerow(r)
    return Response(content=buf.getvalue(),
                    media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/export/orders.csv")
def export_orders_csv(status: str = Query("all"),
                      product_id: Optional[int] = Query(None),
                      conn: sqlite3.Connection = Depends(db_dep)):
    """Export objednávek do CSV (Excel-friendly)."""
    where_parts, params = [], []
    if status and status != "all":
        where_parts.append("o.status = ?"); params.append(status)
    if product_id:
        where_parts.append("o.product_id = ?"); params.append(product_id)
    where = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""
    rows = conn.execute(
        f"SELECT o.id, u.username, u.kick_username, "
        f"COALESCE(p.name, o.product_name, '(smazaná odměna)') AS product_name, "
        f"o.points_spent, o.status, o.created_at "
        f"FROM orders o JOIN users u ON u.id = o.user_id "
        f"LEFT JOIN products p ON p.id = o.product_id {where} "
        f"ORDER BY o.created_at DESC, o.id DESC", params,
    ).fetchall()
    out = [(r["id"], r["username"], r["kick_username"] or "",
            r["product_name"], r["points_spent"], r["status"], r["created_at"])
           for r in rows]
    return _csv_response(out,
        ["id", "uzivatel", "kick_nick", "odmena", "body", "stav", "kdy"],
        f"webos-objednavky-{status}-{datetime.now(timezone.utc).date()}.csv")


@router.get("/export/audit.csv")
def export_audit_csv(action: str = Query("", max_length=64),
                     admin_name: str = Query("", max_length=64),
                     conn: sqlite3.Connection = Depends(db_dep)):
    """Export audit logu do CSV (s filtry)."""
    where_sql, params = _audit_where(action, admin_name)
    rows = conn.execute(
        f"SELECT id, admin_name, action, target, details, ip, created_at "
        f"FROM admin_audit {where_sql} ORDER BY id DESC", params,
    ).fetchall()
    out = [(r["id"], r["admin_name"] or "", r["action"], r["target"] or "",
            r["details"] or "", r["ip"] or "", r["created_at"]) for r in rows]
    return _csv_response(out,
        ["id", "admin", "akce", "cil", "detail", "ip", "kdy"],
        f"webos-audit-{datetime.now(timezone.utc).date()}.csv")


@router.get("/export/users.csv")
def export_users_csv(conn: sqlite3.Connection = Depends(db_dep)):
    """Export uživatelů do CSV: zůstatek, utraceno (shop), nasbíráno, registrace, ban."""
    rows = conn.execute(
        "SELECT u.id, u.username, u.kick_username, u.role, u.points, u.banned, u.created_at, "
        "  (SELECT COALESCE(SUM(points_spent),0) FROM orders o WHERE o.user_id = u.id) AS spent, "
        "  (SELECT COALESCE(SUM(change),0) FROM points_log l WHERE l.user_id = u.id AND l.change > 0) AS earned "
        "FROM users u ORDER BY u.points DESC").fetchall()
    out = [(r["id"], r["username"], r["kick_username"] or "", r["role"], r["points"],
            r["spent"], r["earned"], 1 if r["banned"] else 0, r["created_at"]) for r in rows]
    return _csv_response(out,
        ["id", "uzivatel", "kick_nick", "role", "zustatek", "utraceno", "nasbirano", "ban", "registrace"],
        f"webos-uzivatele-{datetime.now(timezone.utc).date()}.csv")


_DC_NETS = []
for _c in DATACENTER_CIDRS:
    try:
        _DC_NETS.append(ipaddress.ip_network(_c))
    except ValueError:
        pass


def _is_datacenter(ip: str) -> bool:
    try:
        a = ipaddress.ip_address(ip)
        return any(a in n for n in _DC_NETS)
    except ValueError:
        return False


def _rule(conn: sqlite3.Connection, key: str):
    """(enabled, threshold) pro anticheat pravidlo z DB (fallback na default)."""
    r = conn.execute("SELECT enabled, threshold FROM anticheat_rules WHERE key = ?", (key,)).fetchone()
    if r:
        return bool(r["enabled"]), r["threshold"]
    m = next((x for x in ANTICHEAT_RULES if x["key"] == key), {})
    return (not m.get("default_off", False)), m.get("threshold")


def _users_by_ids(conn, ids):
    if not ids:
        return []
    ph = ",".join("?" * len(ids))
    return [dict(x) for x in conn.execute(
        f"SELECT id, username, role, points, banned FROM users WHERE id IN ({ph})", list(ids))]


@router.get("/security/rules")
def security_rules(conn: sqlite3.Connection = Depends(db_dep)):
    cfg = {r["key"]: r for r in conn.execute("SELECT * FROM anticheat_rules").fetchall()}
    out = []
    for m in ANTICHEAT_RULES:
        c = cfg.get(m["key"])
        out.append({
            "key": m["key"], "label": m["label"], "severity": m["severity"], "desc": m["desc"],
            "prah": m["prah"], "enforced": m.get("enforced", False),
            "enabled": bool(c["enabled"]) if c else not m.get("default_off", False),
            "threshold": (c["threshold"] if c else m["threshold"]),
        })
    return out


@router.get("/security/gifts")
def security_gifts(limit: int = Query(100, ge=1, le=300),
                   conn: sqlite3.Connection = Depends(db_dep)):
    """Přehled darů z Exchange (kdo komu poslal sedláky). Čte z points_log (sender entry „Dar pro …")."""
    total = conn.execute("SELECT COUNT(*) AS c FROM points_log WHERE reason LIKE 'Dar pro %'").fetchone()["c"]
    rows = conn.execute(
        "SELECT l.id, l.created_at, l.change, l.reason, u.username AS sender "
        "FROM points_log l JOIN users u ON u.id = l.user_id "
        "WHERE l.reason LIKE 'Dar pro %' ORDER BY l.id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    out = []
    for r in rows:
        recipient = (r["reason"] or "").removeprefix("Dar pro ").removesuffix(" 🎁").strip()
        out.append({"id": r["id"], "from": r["sender"], "to": recipient,
                    "amount": -r["change"], "created_at": r["created_at"]})
    return {"rows": out, "total": total}


# ---------------- Žádosti o dar (Exchange) – schvalování adminem ----------------
_GIFT_REQ_BASE = (
    "SELECT g.*, f.username AS from_name, t.username AS to_name, "
    "f.banned AS from_banned, t.banned AS to_banned, f.points AS from_points "
    "FROM gift_requests g JOIN users f ON f.id = g.from_user_id JOIN users t ON t.id = g.to_user_id ")


@router.get("/gift-requests")
def gift_requests_list(conn: sqlite3.Connection = Depends(db_dep)):
    """Čekající žádosti o dar (escrow) + posledních 20 vyřízených. Admin je Povolí / Zamítne.
    U čekajících přidá příznak `shared` (stejná IP/zařízení) ať admin pozná pokus o funnel."""
    from .misc import _shared_identity  # lazy – vyhne se případnému import-cyklu

    def _row(r, flags):
        d = {"id": r["id"], "amount": r["amount"], "status": r["status"],
             "from": r["from_name"], "to": r["to_name"], "note": r["note"],
             "from_id": r["from_user_id"], "to_id": r["to_user_id"],
             "from_banned": bool(r["from_banned"]), "to_banned": bool(r["to_banned"]),
             "from_points": r["from_points"], "created_at": r["created_at"],
             "decided_at": r["decided_at"], "decided_by": r["decided_by"]}
        if flags:
            d["shared"] = _shared_identity(conn, r["from_user_id"], r["to_user_id"])
        return d

    pending = conn.execute(_GIFT_REQ_BASE + "WHERE g.status='pending' ORDER BY g.id ASC").fetchall()
    recent = conn.execute(_GIFT_REQ_BASE + "WHERE g.status!='pending' ORDER BY g.id DESC LIMIT 20").fetchall()
    return {"pending": [_row(r, True) for r in pending],
            "recent": [_row(r, False) for r in recent],
            "pending_count": len(pending)}


@router.post("/gift-requests/{rid}/approve")
def gift_request_approve(rid: int, request: Request,
                         conn: sqlite3.Connection = Depends(db_dep),
                         user: sqlite3.Row = Depends(require_user)):   # sekci 'gifts' (admin+broadcaster) hlídá admin_guard
    """Povolí dar: připíše body příjemci (odesílateli odešly už při žádosti = escrow)."""
    g = conn.execute("SELECT * FROM gift_requests WHERE id = ?", (rid,)).fetchone()
    if not g:
        raise HTTPException(status_code=404, detail="Žádost o dar neexistuje.")
    if g["status"] != "pending":
        raise HTTPException(status_code=400, detail=f"Žádost už je vyřízená ({g['status']}).")
    frm = conn.execute("SELECT username FROM users WHERE id = ?", (g["from_user_id"],)).fetchone()
    rcp = conn.execute("SELECT username FROM users WHERE id = ?", (g["to_user_id"],)).fetchone()
    if not frm or not rcp:
        raise HTTPException(status_code=400, detail="Účet odesílatele nebo příjemce už neexistuje.")
    add_points(conn, g["to_user_id"], g["amount"], f"Dar od {frm['username']} 🎁", xp=False)
    # escrow řádek přejmenujeme na KANONICKÝ tvar → funnel detektor i přehled darů ho teď započítají
    if g["escrow_log_id"]:
        conn.execute("UPDATE points_log SET reason = ? WHERE id = ?",
                     (f"Dar pro {rcp['username']} 🎁", g["escrow_log_id"]))
    conn.execute("UPDATE gift_requests SET status='approved', decided_at=?, decided_by=? WHERE id=?",
                 (now_iso(), user["username"], rid))
    record_audit(conn, user, request, "gift.approve",
                 target=rcp["username"], details=f"+{g['amount']} PTS od {frm['username']}")
    notify(conn, g["from_user_id"], "✅", "Dar schválen",
           f"Tvůj dar {g['amount']} sedláků pro {rcp['username']} byl schválen. 🎁", "#/exchange")
    notify(conn, g["to_user_id"], "🎁", "Dostal jsi dar!",
           f"{frm['username']} ti poslal {g['amount']} sedláků.", "#/exchange")
    conn.commit()
    return {"ok": True, "status": "approved", "amount": g["amount"],
            "from": frm["username"], "to": rcp["username"]}


@router.post("/gift-requests/{rid}/reject")
def gift_request_reject(rid: int, request: Request,
                        conn: sqlite3.Connection = Depends(db_dep),
                        user: sqlite3.Row = Depends(require_user)):   # sekci 'gifts' (admin+broadcaster) hlídá admin_guard
    """Zamítne dar: vrátí body odesílateli (escrow zpět). Příjemce nedostane nic."""
    g = conn.execute("SELECT * FROM gift_requests WHERE id = ?", (rid,)).fetchone()
    if not g:
        raise HTTPException(status_code=404, detail="Žádost o dar neexistuje.")
    if g["status"] != "pending":
        raise HTTPException(status_code=400, detail=f"Žádost už je vyřízená ({g['status']}).")
    rcp = conn.execute("SELECT username FROM users WHERE id = ?", (g["to_user_id"],)).fetchone()
    add_points(conn, g["from_user_id"], g["amount"], "Vrácení daru (zamítnuto adminem) 🎁")
    if g["escrow_log_id"]:
        rcp_name = rcp["username"] if rcp else "?"
        conn.execute("UPDATE points_log SET reason = ? WHERE id = ?",
                     (f"Dar → {rcp_name} ZAMÍTNUTO adminem 🎁", g["escrow_log_id"]))
    conn.execute("UPDATE gift_requests SET status='rejected', decided_at=?, decided_by=? WHERE id=?",
                 (now_iso(), user["username"], rid))
    target = rcp["username"] if rcp else str(g["to_user_id"])
    record_audit(conn, user, request, "gift.reject", target=target,
                 details=f"vráceno {g['amount']} PTS odesílateli")
    notify(conn, g["from_user_id"], "↩️", "Dar zamítnut",
           f"Tvůj dar {g['amount']} sedláků pro {target} byl zamítnut adminem — body se ti vrátily.", "#/exchange")
    conn.commit()
    return {"ok": True, "status": "rejected", "amount": g["amount"]}


@router.get("/security/points-feed")
def security_points_feed(q: str = Query("", max_length=64), flow: str = Query("", max_length=4),
                         min_amount: int = Query(0, ge=0), reason: str = Query("", max_length=40),
                         limit: int = Query(60, ge=1, le=200), offset: int = Query(0, ge=0),
                         conn: sqlite3.Connection = Depends(db_dep),
                         admin: sqlite3.Row = Depends(require_admin)):
    """Plný feed POHYBŮ BODŮ – každý +/- sedlák (kdo, kolik, za co, kdy). Aby nic neuniklo.
    Filtry: q=nick, flow=in|out, min_amount=|změna|>=, reason=hledání v důvodu."""
    where, params = ["1=1"], []
    if q.strip():
        where.append("u.username LIKE ?")
        params.append(f"%{q.strip()}%")
    if flow == "in":
        where.append("l.change > 0")
    elif flow == "out":
        where.append("l.change < 0")
    if min_amount > 0:
        where.append("ABS(l.change) >= ?")
        params.append(min_amount)
    if reason.strip():
        where.append("l.reason LIKE ?")
        params.append(f"%{reason.strip()}%")
    base = f"FROM points_log l JOIN users u ON u.id = l.user_id WHERE {' AND '.join(where)}"
    total = conn.execute(f"SELECT COUNT(*) AS c {base}", params).fetchone()["c"]
    agg = conn.execute(
        f"SELECT COALESCE(SUM(CASE WHEN l.change>0 THEN l.change ELSE 0 END),0) AS s_in, "
        f"COALESCE(SUM(CASE WHEN l.change<0 THEN -l.change ELSE 0 END),0) AS s_out {base}", params).fetchone()
    rows = conn.execute(
        f"SELECT l.id, u.username, u.role, l.change, l.reason, l.created_at {base} "
        f"ORDER BY l.id DESC LIMIT ? OFFSET ?", params + [limit, offset]).fetchall()
    return {"rows": [dict(r) for r in rows], "total": total, "limit": limit, "offset": offset,
            "sum_in": agg["s_in"], "sum_out": agg["s_out"]}


@router.post("/security/rules/{key}")
def set_rule(key: str, data: RuleIn, request: Request,
             conn: sqlite3.Connection = Depends(db_dep),
             admin: sqlite3.Row = Depends(require_user)):
    if key not in [r["key"] for r in ANTICHEAT_RULES]:
        raise HTTPException(status_code=404, detail="Neznámé pravidlo.")
    row = conn.execute("SELECT enabled, threshold FROM anticheat_rules WHERE key = ?", (key,)).fetchone()
    enabled = data.enabled if data.enabled is not None else (bool(row["enabled"]) if row else True)
    threshold = data.threshold if data.threshold is not None else (row["threshold"] if row else None)
    conn.execute(
        "INSERT INTO anticheat_rules (key, enabled, threshold) VALUES (?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET enabled = excluded.enabled, threshold = excluded.threshold",
        (key, 1 if enabled else 0, threshold))
    record_audit(conn, admin, request, "rule.update", key,
                 f"{'zapnuto' if enabled else 'vypnuto'}, práh {threshold}")
    conn.commit()
    return {"ok": True, "key": key, "enabled": enabled, "threshold": threshold}


@router.get("/security/linked/{user_id}")
def security_linked(user_id: int, conn: sqlite3.Connection = Depends(db_dep)):
    """Účty propojené s daným uživatelem přes stejné ZAŘÍZENÍ (fingerprint) NEBO stejnou IP
    – pro anti-alt vyšetřování (kdo patří do clusteru). Sekce 'security' = jen admin (guard)."""
    base = conn.execute("SELECT id, username FROM users WHERE id = ?", (user_id,)).fetchone()
    if not base:
        raise HTTPException(status_code=404, detail="Uživatel nenalezen.")
    device_ids = {r["user_id"] for r in conn.execute(
        "SELECT DISTINCT user_id FROM client_signals WHERE fp_hash IN "
        "(SELECT DISTINCT fp_hash FROM client_signals WHERE user_id = ? AND fp_hash IS NOT NULL)",
        (user_id,))}
    ip_ids = {r["user_id"] for r in conn.execute(
        "SELECT DISTINCT user_id FROM login_events WHERE ip IN "
        "(SELECT DISTINCT ip FROM login_events WHERE user_id = ? AND ip IS NOT NULL AND ip != '')",
        (user_id,))}
    ids = {i for i in (device_ids | ip_ids) if i is not None}
    ids.add(user_id)
    accounts = []
    for uid in ids:
        u = conn.execute(
            "SELECT id, username, kick_username, role, points, banned, is_sub, created_at "
            "FROM users WHERE id = ?", (uid,)).fetchone()
        if not u:
            continue
        accounts.append({
            "id": u["id"], "username": u["username"], "kick_username": u["kick_username"],
            "role": u["role"], "points": u["points"], "banned": bool(u["banned"]),
            "is_sub": bool(u["is_sub"]), "created_at": u["created_at"],
            "same_device": uid in device_ids, "same_ip": uid in ip_ids, "is_self": uid == user_id,
        })
    accounts.sort(key=lambda a: -a["points"])
    return {"user_id": user_id, "username": base["username"],
            "device_linked": len(device_ids), "ip_linked": len(ip_ids),
            "total": len(accounts), "accounts": accounts}


@router.get("/security/negatives")
def security_negatives(conn: sqlite3.Connection = Depends(db_dep)):
    """Účty se ZÁPORNÝM zůstatkem (např. po opravě predikce / clawbacku). Sekce 'security' = jen admin."""
    rows = conn.execute(
        "SELECT id, username, kick_username, points, banned FROM users WHERE points < 0 "
        "ORDER BY points ASC LIMIT 200"
    ).fetchall()
    return {"count": len(rows), "users": [
        {"id": r["id"], "username": r["username"], "kick_username": r["kick_username"],
         "points": r["points"], "banned": bool(r["banned"])} for r in rows]}


@router.get("/security/funnel")
def security_funnel(conn: sqlite3.Connection = Depends(db_dep)):
    """Přelévání bodů mezi DVOJICEMI účtů (chip-dumping). Sleduje jednostranný tok přes
    duely + piškvorky + dary. Vysoký net + sdílená IP/zařízení = skoro jistý alt-farm.
    Plus 'house_pl': kdo je nejvíc v plusu vůči house (exploit / příjemce collusion)."""
    NET_MIN, MATCH_MIN, TOP = 1000, 4, 30
    pairs: dict = {}

    def acc(src, dst, amount, kind):
        """amount teče SRC -> DST (dst dostal). kind: 'duels'|'games'|'gifts'."""
        if not src or not dst or src == dst or amount <= 0:
            return
        lo, hi = (src, dst) if src < dst else (dst, src)
        p = pairs.get((lo, hi))
        if p is None:
            p = {"matches": 0, "net_lo": 0, "duels": 0, "games": 0, "gifts": 0, "lo_wins": 0, "hi_wins": 0}
            pairs[(lo, hi)] = p
        p["net_lo"] += amount if dst == lo else -amount   # kladné = tok směrem k LO
        p[kind] += 1
        if kind != "gifts":
            p["matches"] += 1
            p["lo_wins" if dst == lo else "hi_wins"] += 1

    # duely + piškvorky: vítěz bere stake od poraženého
    for table, kind in (("duels", "duels"), ("games", "games")):
        for r in conn.execute(
                f"SELECT p1_id, p2_id, stake, winner FROM {table} "
                f"WHERE status='finished' AND winner IN (1,2) AND p2_id IS NOT NULL"):
            win = r["p1_id"] if r["winner"] == 1 else r["p2_id"]
            lose = r["p2_id"] if r["winner"] == 1 else r["p1_id"]
            acc(lose, win, r["stake"], kind)

    # dary: odesílatel má řádek 'Dar pro X' (záporný change), příjemce = username X
    for r in conn.execute(
            "SELECT user_id, change, reason FROM points_log WHERE reason LIKE 'Dar pro %' AND change < 0"):
        name = (r["reason"] or "").removeprefix("Dar pro ").removesuffix(" 🎁").strip()
        rcp = conn.execute("SELECT id FROM users WHERE username = ?", (name,)).fetchone()
        if rcp:
            acc(r["user_id"], rcp["id"], -r["change"], "gifts")

    flagged = []
    for (lo, hi), p in pairs.items():
        net = abs(p["net_lo"])
        if net < NET_MIN and p["matches"] < MATCH_MIN:
            continue
        receiver = lo if p["net_lo"] >= 0 else hi
        source = hi if p["net_lo"] >= 0 else lo
        flagged.append((lo, hi, receiver, source, net, p))
    flagged.sort(key=lambda x: -x[4])
    flagged = flagged[:TOP]

    out = []
    for lo, hi, receiver, source, net, p in flagged:
        umap = {u["id"]: u for u in _users_by_ids(conn, [lo, hi])}
        shared_ip = conn.execute(
            "SELECT 1 FROM login_events a JOIN login_events b ON a.ip = b.ip "
            "WHERE a.user_id=? AND b.user_id=? AND a.ip IS NOT NULL AND a.ip!='' LIMIT 1",
            (lo, hi)).fetchone() is not None
        shared_dev = conn.execute(
            "SELECT 1 FROM client_signals a JOIN client_signals b ON a.fp_hash = b.fp_hash "
            "WHERE a.user_id=? AND b.user_id=? AND a.fp_hash IS NOT NULL LIMIT 1",
            (lo, hi)).fetchone() is not None
        recv_wins = p["lo_wins"] if receiver == lo else p["hi_wins"]
        out.append({
            "receiver": umap.get(receiver), "source": umap.get(source),
            "net": net, "matches": p["matches"], "recv_wins": recv_wins,
            "via": {"duels": p["duels"], "games": p["games"], "gifts": p["gifts"]},
            "shared_ip": shared_ip, "shared_device": shared_dev,
        })
    out.sort(key=lambda x: (not (x["shared_ip"] or x["shared_device"]), -x["net"]))

    # house P/L: čistý zisk vůči house z PvP + predikcí (bez rake; volume = objem na posouzení variance)
    pl: dict = {}

    def pli(uid):
        q = pl.get(uid)
        if q is None:
            q = {"net": 0, "vol": 0}
            pl[uid] = q
        return q

    for table in ("duels", "games"):
        for r in conn.execute(
                f"SELECT p1_id, p2_id, stake, winner FROM {table} "
                f"WHERE status='finished' AND winner IN (1,2) AND p2_id IS NOT NULL"):
            win = r["p1_id"] if r["winner"] == 1 else r["p2_id"]
            lose = r["p2_id"] if r["winner"] == 1 else r["p1_id"]
            qw = pli(win); qw["net"] += r["stake"]; qw["vol"] += r["stake"]
            ql = pli(lose); ql["net"] -= r["stake"]; ql["vol"] += r["stake"]
    for r in conn.execute("SELECT user_id, amount, payout FROM prediction_bets"):
        q = pli(r["user_id"]); q["net"] += r["payout"] - r["amount"]; q["vol"] += r["amount"]
    winners = sorted(((uid, q) for uid, q in pl.items() if q["net"] > 0),
                     key=lambda x: -x[1]["net"])[:12]
    umap2 = {u["id"]: u for u in _users_by_ids(conn, [uid for uid, _ in winners])}
    house_pl = [{"user": umap2.get(uid), "net": q["net"], "volume": q["vol"]}
                for uid, q in winners if umap2.get(uid)]

    return {"pairs": out, "house_pl": house_pl,
            "thresholds": {"net_min": NET_MIN, "match_min": MATCH_MIN}}


@router.get("/security/anticheat")
def security_anticheat(conn: sqlite3.Connection = Depends(db_dep)):
    """Detekce podezřelých vzorců – gateováno konfigurací pravidel."""
    now = datetime.now(timezone.utc)

    # multi_account – sdílené IP (>= práh účtů)
    shared_ips = []
    ok, thr = _rule(conn, "multi_account")
    if ok:
        minu = max(2, thr or 3)
        for s in conn.execute(
            "SELECT ip, COUNT(DISTINCT user_id) AS users FROM login_events "
            "WHERE ip IS NOT NULL AND ip != '' GROUP BY ip HAVING users >= ? ORDER BY users DESC, ip",
            (minu,)):
            us = conn.execute(
                "SELECT DISTINCT u.id,u.username,u.role,u.points,u.banned FROM login_events e "
                "JOIN users u ON u.id=e.user_id WHERE e.ip=? ORDER BY u.username", (s["ip"],)).fetchall()
            shared_ips.append({"ip": s["ip"], "user_count": s["users"], "users": [dict(x) for x in us]})

    # účty z mnoha IP
    multi_ip_users = []
    for m in conn.execute(
        "SELECT user_id, COUNT(DISTINCT ip) AS ips FROM login_events WHERE ip IS NOT NULL AND ip != '' "
        "GROUP BY user_id HAVING ips >= 3 ORDER BY ips DESC"):
        u = conn.execute("SELECT id,username,role,points,banned FROM users WHERE id=?", (m["user_id"],)).fetchone()
        if not u:
            continue
        ips = [x["ip"] for x in conn.execute(
            "SELECT DISTINCT ip FROM login_events WHERE user_id=? AND ip IS NOT NULL AND ip != ''", (m["user_id"],))]
        multi_ip_users.append({"user": dict(u), "ip_count": m["ips"], "ips": ips})

    # rychlé farmení bodů (poslední hodina)
    rapid_farming = []
    cutoff60 = (now - timedelta(minutes=60)).isoformat()
    for f in conn.execute(
        "SELECT user_id, COUNT(*) AS c, SUM(change) AS gained FROM points_log "
        "WHERE change>0 AND created_at>=? GROUP BY user_id HAVING c>=5 ORDER BY c DESC", (cutoff60,)):
        u = conn.execute("SELECT id,username,role,points,banned FROM users WHERE id=?", (f["user_id"],)).fetchone()
        if u:
            rapid_farming.append({"user": dict(u), "events": f["c"], "gained": f["gained"]})

    # redeem kódy ze stejné IP
    redeem_abuse = []
    for dc in conn.execute("SELECT code_id, COUNT(DISTINCT user_id) AS c FROM redeem_uses GROUP BY code_id HAVING c>=2"):
        uids = [r["user_id"] for r in conn.execute("SELECT DISTINCT user_id FROM redeem_uses WHERE code_id=?", (dc["code_id"],))]
        if not uids:
            continue
        ph = ",".join("?" * len(uids))
        ipmap = {}
        for row in conn.execute(
            f"SELECT DISTINCT user_id, ip FROM login_events WHERE user_id IN ({ph}) AND ip IS NOT NULL AND ip != ''", uids):
            ipmap.setdefault(row["ip"], set()).add(row["user_id"])
        code_row = conn.execute("SELECT code FROM redeem_codes WHERE id=?", (dc["code_id"],)).fetchone()
        for ip, us in ipmap.items():
            if len(us) >= 2:
                redeem_abuse.append({"code": code_row["code"] if code_row else "?", "ip": ip, "users": _users_by_ids(conn, us)})

    # rapid_fire – >= práh akcí za 5 min
    rapid_fire = []
    ok, thr = _rule(conn, "rapid_fire")
    if ok:
        c5 = (now - timedelta(minutes=5)).isoformat()
        for r in conn.execute(
            "SELECT user_id, COUNT(*) AS c FROM orders WHERE created_at>=? GROUP BY user_id HAVING c>=? ORDER BY c DESC",
            (c5, thr or 10)):
            u = conn.execute("SELECT id,username,role,points,banned FROM users WHERE id=?", (r["user_id"],)).fetchone()
            if u:
                rapid_fire.append({"user": dict(u), "count": r["c"]})

    # new_account_spend – účet <24h utratil >= práh PTS
    new_account_spend = []
    ok, thr = _rule(conn, "new_account_spend")
    if ok:
        c24 = (now - timedelta(hours=24)).isoformat()
        for r in conn.execute(
            "SELECT o.user_id, SUM(o.points_spent) AS spent FROM orders o JOIN users u ON u.id=o.user_id "
            "WHERE o.created_at>=? AND u.created_at>=? GROUP BY o.user_id HAVING spent>=? ORDER BY spent DESC",
            (c24, c24, thr or 1000)):
            u = conn.execute("SELECT id,username,role,points,banned FROM users WHERE id=?", (r["user_id"],)).fetchone()
            if u:
                new_account_spend.append({"user": dict(u), "spent": r["spent"]})

    # headless – klientský webdriver signál
    headless = []
    ok, _t = _rule(conn, "headless")
    if ok:
        for r in conn.execute("SELECT DISTINCT user_id FROM client_signals WHERE webdriver=1"):
            u = conn.execute("SELECT id,username,role,points,banned FROM users WHERE id=?", (r["user_id"],)).fetchone()
            if u:
                headless.append({"user": dict(u)})

    # multi-account podle ZAŘÍZENÍ (device fingerprint) – sdílí gating s multi_account
    device_accounts = []
    ok, thr = _rule(conn, "multi_account")
    if ok:
        minu = max(2, thr or 3)
        for s in conn.execute(
            "SELECT fp_hash, COUNT(DISTINCT user_id) AS users FROM client_signals "
            "WHERE fp_hash IS NOT NULL GROUP BY fp_hash HAVING users >= ? ORDER BY users DESC", (minu,)):
            uids = [r["user_id"] for r in conn.execute(
                "SELECT DISTINCT user_id FROM client_signals WHERE fp_hash = ?", (s["fp_hash"],))]
            device_accounts.append({"fp": (s["fp_hash"] or "")[:12], "user_count": s["users"],
                                    "users": _users_by_ids(conn, uids)})

    # VPN / Proxy / Datacenter IP (lokální seznam rozsahů)
    vpn_ips = []
    ok, _t = _rule(conn, "vpn_proxy")
    if ok:
        for row in conn.execute("SELECT DISTINCT ip FROM login_events WHERE ip IS NOT NULL AND ip != ''"):
            if _is_datacenter(row["ip"]):
                uids = [r["user_id"] for r in conn.execute(
                    "SELECT DISTINCT user_id FROM login_events WHERE ip = ?", (row["ip"],))]
                vpn_ips.append({"ip": row["ip"], "users": _users_by_ids(conn, uids)})

    def count(sql):
        return conn.execute(sql).fetchone()["c"]
    groups = [shared_ips, multi_ip_users, rapid_farming, redeem_abuse, rapid_fire,
              new_account_spend, headless, device_accounts, vpn_ips]
    stats = {
        "events": count("SELECT COUNT(*) AS c FROM login_events"),
        "unique_ips": count("SELECT COUNT(DISTINCT ip) AS c FROM login_events WHERE ip IS NOT NULL AND ip != ''"),
        "banned": count("SELECT COUNT(*) AS c FROM users WHERE banned = 1"),
        "flags": sum(len(g) for g in groups),
    }
    return {"shared_ips": shared_ips, "multi_ip_users": multi_ip_users, "rapid_farming": rapid_farming,
            "redeem_abuse": redeem_abuse, "rapid_fire": rapid_fire, "new_account_spend": new_account_spend,
            "headless": headless, "device_accounts": device_accounts, "vpn_ips": vpn_ips,
            "iprep_enabled": iprep.enabled(), "stats": stats}


# Otisk sdílený víc než tolika účty NEbanujeme jako „zařízení" – je slabý (model+prohlížeč+jazyk),
# takže sdílený otisk = spíš různí lidé na stejném mobilu než alty. Brání „ban 1 → sestřel 7".
FP_DEVICE_BAN_MAX_SHARED = 2


@router.get("/shop-discount")
def get_shop_discount(conn: sqlite3.Connection = Depends(db_dep)):
    """Stav happy-hour slevy na shop."""
    active_now = shop_discount_pct(conn)   # vyhodnotí i auto-expiry časovače (vyčistí prošlé)
    pct = int(get_setting(conn, "shop_discount_pct", "0") or "0")
    return {"pct": pct, "live_only": get_setting(conn, "shop_discount_live_only", "0") == "1",
            "sub_2x": get_setting(conn, "happy_sub_2x", "0") == "1",
            "until": get_setting(conn, "happy_until", "") or "",
            "is_live": live.is_live(conn), "active_now": active_now}


@router.post("/shop-discount")
def set_shop_discount(data: ShopDiscountIn, request: Request,
                      conn: sqlite3.Connection = Depends(db_dep),
                      admin: sqlite3.Row = Depends(require_admin)):
    """Admin nastaví happy-hour slevu (%) + zda jen během streamu + 2× body za subs.
    Při přechodu VYP→ZAP (a když je promo teď reálně aktivní) bot oznámí happy hour v chatu."""
    old_pct = int(get_setting(conn, "shop_discount_pct", "0") or "0")
    old_sub2x = get_setting(conn, "happy_sub_2x", "0") == "1"
    now_on = data.pct > 0 or bool(data.sub_2x)
    # Časovač: konec = teď + N min (jen když je promo zapnuté a minutes>0); jinak bez limitu.
    until_iso = ((datetime.now(timezone.utc) + timedelta(minutes=data.minutes)).isoformat()
                 if data.minutes > 0 and now_on else "")
    set_setting(conn, "shop_discount_pct", str(data.pct))
    set_setting(conn, "shop_discount_live_only", "1" if data.live_only else "0")
    set_setting(conn, "happy_sub_2x", "1" if data.sub_2x else "0")
    set_setting(conn, "happy_until", until_iso)
    conn.commit()
    record_audit(conn, admin, request, "shop.discount",
                 f"{data.pct}%" + (" (jen live)" if data.live_only else "") + (" +2× subs" if data.sub_2x else "")
                 + (f" {data.minutes}min" if until_iso else ""), "")
    # Chat oznámení: jen při ZAPNUTÍ (VYP→ZAP) a jen když je promo teď reálně aktivní
    # (respektuje „jen když live" – neoznamuj happy hour do offline chatu, když ještě neběží).
    disc_now = shop_discount_pct(conn)
    sub2x_active = bool(data.sub_2x) and (not data.live_only or live.is_live(conn))
    was_on = old_pct > 0 or old_sub2x
    if (disc_now > 0 or sub2x_active) and now_on and not was_on:
        parts = []
        if sub2x_active:
            parts.append("2× sedláků za subscribe / resub / gift sub 🟣")
        if disc_now > 0:
            parts.append(f"−{disc_now} % na všechno v shopu")
        dur = f" (jen {data.minutes} min!)" if until_iso else ""
        try:
            from .. import kickbot
            kickbot.send_message(conn, "🔴 HAPPY HOUR! Teď " + " a ".join(parts) + dur + " 🌾 — využij to!", kind="happyhour")
        except Exception:
            pass
    return {"pct": data.pct, "live_only": bool(data.live_only), "sub_2x": bool(data.sub_2x),
            "minutes": data.minutes, "until": until_iso, "active_now": disc_now}


@router.post("/users/{user_id}/gamble-block")
def admin_gamble_block(user_id: int, data: SelfExcludeIn, request: Request,
                       conn: sqlite3.Connection = Depends(db_dep),
                       admin: sqlite3.Row = Depends(require_admin)):
    """Admin nastaví/zruší sebevyloučení ze sázek libovolnému uživateli (override – i odemknout/zkrátit)."""
    u = conn.execute("SELECT username FROM users WHERE id = ?", (user_id,)).fetchone()
    if not u:
        raise HTTPException(status_code=404, detail="Uživatel nenalezen.")
    days = {"1d": 1, "7d": 7, "30d": 30}
    if data.duration == "off":
        newval = None
    elif data.duration == "perm":
        newval = "permanent"
    elif data.duration in days:
        newval = (datetime.now(timezone.utc) + timedelta(days=days[data.duration])).isoformat()
    else:
        raise HTTPException(status_code=400, detail="Neplatná délka (1d/7d/30d/perm/off).")
    conn.execute("UPDATE users SET gamble_block_until = ? WHERE id = ?", (newval, user_id))
    conn.commit()
    record_audit(conn, admin, request, "user.gamble_block", f"{u['username']} (#{user_id})", data.duration)
    return {"gamble_block_until": newval}


@router.post("/security/ban-cluster")
def ban_cluster(data: BanClusterIn, request: Request,
                conn: sqlite3.Connection = Depends(db_dep),
                admin: sqlite3.Row = Depends(require_broadcaster)):
    """Zbanuje celý cluster účtů (alt farma) naráz. Admina vždy přeskočí; staff přeskočí, pokud
    nebanuje sám admin. Sundá session. (Ban zařízení neřeší – to dělá ban po jednom.)"""
    banned, skipped = 0, 0
    for uid in data.user_ids[:200]:
        u = conn.execute("SELECT role FROM users WHERE id = ?", (uid,)).fetchone()
        if not u:
            continue
        if u["role"] == ROLE_ADMIN or (admin["role"] != ROLE_ADMIN and u["role"] in STAFF_ROLES):
            skipped += 1
            continue
        conn.execute("UPDATE users SET banned = 1, ban_reason = ? WHERE id = ?",
                     ((data.reason or "alt cluster")[:200], uid))
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (uid,))
        banned += 1
    conn.commit()
    record_audit(conn, admin, request, "ban.cluster", f"{banned} účtů", (data.reason or "")[:200])
    return {"banned": banned, "skipped": skipped}


@router.post("/users/{user_id}/ban")
def ban_user(user_id: int, data: BanIn, request: Request,
             conn: sqlite3.Connection = Depends(db_dep),
             admin: sqlite3.Row = Depends(require_broadcaster)):   # ban: broadcaster+admin ano, mod ne
    u = conn.execute("SELECT username, role, kick_id FROM users WHERE id = ?", (user_id,)).fetchone()
    if not u:
        raise HTTPException(status_code=404, detail="Uživatel nenalezen.")
    if admin["role"] == ROLE_MOD:
        raise HTTPException(status_code=403, detail="Moderátor nemůže banovat (jen admin/broadcaster).")
    if u["role"] == ROLE_ADMIN:
        raise HTTPException(status_code=400, detail="Admina nelze zabanovat.")
    if admin["role"] != ROLE_ADMIN and u["role"] in STAFF_ROLES:
        raise HTTPException(status_code=403, detail="Členy týmu (staff) může banovat jen admin.")
    conn.execute("UPDATE users SET banned = ?, ban_reason = ? WHERE id = ?",
                 (1 if data.banned else 0, (data.reason or "")[:200], user_id))
    fps = [r["fp_hash"] for r in conn.execute(
        "SELECT DISTINCT fp_hash FROM client_signals WHERE user_id = ? AND fp_hash IS NOT NULL", (user_id,))]
    if data.banned:
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))  # odhlásí
        # Ban je JEN na tento účet, NE na zařízení (fingerprint). Slabý otisk (model+prohlížeč+jazyk)
        # sdílí i cizí lidi / sourozenci / spolubydlící → device-ban střílel nevinné (false positive).
        # Zrušeno na žádost (2026-06-18): fingerprint_bans se už neplní. (Enforce zůstává off; tabulka
        # zůstává jen pro případnou budoucí PŘESNĚJŠÍ fingerprint logiku.)
    else:
        for fp in fps:  # odban pro jistotu uvolní i případný starý device-ban
            conn.execute("DELETE FROM fingerprint_bans WHERE fp_hash = ?", (fp,))
    record_audit(conn, admin, request, "user.ban" if data.banned else "user.unban",
                 f"#{user_id} {u['username']}", (data.reason or "")[:200])
    conn.commit()
    # Sync do Kick chatu (po commitu – web ban platí, i kdyby Kick API selhalo/timeoutlo)
    if u["kick_id"]:
        kick = (kickbot.moderate_ban(conn, u["kick_id"], reason=(data.reason or "Ban na zurys.live"))
                if data.banned else kickbot.moderate_unban(conn, u["kick_id"]))
    else:
        kick = {"ok": False, "skipped": True, "error": "Účet nemá propojený Kick (bez kick_id)."}
    return {"ok": True, "banned": data.banned,
            "devices_banned": len(fps) if data.banned else 0, "kick": kick}


_TIMEOUT_MIN = {"5m": 5, "15m": 15, "1h": 60, "6h": 360, "24h": 1440, "7d": 10080}


@router.post("/users/{user_id}/timeout")
def timeout_user(user_id: int, data: TimeoutIn, request: Request,
                 conn: sqlite3.Connection = Depends(db_dep),
                 admin: sqlite3.Row = Depends(require_broadcaster)):   # timeout: broadcaster+admin, mod ne (jako ban)
    """Dočasný timeout: zablokuje uživateli CELÝ web (require_user → 403) na danou dobu a zrcadlí
    timeout i do Kick chatu (moderate_ban s duration_min). 'off' = zrušit. Session se nemaže –
    po vypršení se vše samo odemkne. Admina umlčet nelze; staff jen admin."""
    u = conn.execute("SELECT username, role, kick_id FROM users WHERE id = ?", (user_id,)).fetchone()
    if not u:
        raise HTTPException(status_code=404, detail="Uživatel nenalezen.")
    if u["role"] == ROLE_ADMIN:
        raise HTTPException(status_code=400, detail="Admina nelze umlčet.")
    if admin["role"] != ROLE_ADMIN and u["role"] in STAFF_ROLES:
        raise HTTPException(status_code=403, detail="Členy týmu (staff) může timeoutovat jen admin.")
    if data.duration == "off":
        newval, minutes = None, None
    elif data.duration in _TIMEOUT_MIN:
        minutes = _TIMEOUT_MIN[data.duration]
        newval = (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()
    else:
        raise HTTPException(status_code=400, detail="Neplatná délka (5m/15m/1h/6h/24h/7d/off).")
    conn.execute("UPDATE users SET timeout_until = ? WHERE id = ?", (newval, user_id))
    record_audit(conn, admin, request, "user.timeout" if newval else "user.timeout_off",
                 f"#{user_id} {u['username']}", data.duration)
    conn.commit()
    # Kick mirror (po commitu – web timeout platí, i kdyby Kick API selhalo/timeoutlo)
    if u["kick_id"]:
        kick = (kickbot.moderate_ban(conn, u["kick_id"], reason="Timeout ze zurys.live", duration_min=minutes)
                if newval else kickbot.moderate_unban(conn, u["kick_id"]))
    else:
        kick = {"ok": False, "skipped": True, "error": "Účet nemá propojený Kick (bez kick_id)."}
    return {"ok": True, "timeout_until": newval, "duration": data.duration, "kick": kick}


# ---------------- Dropy (závod o kód) ----------------
@router.get("/drops")
def admin_drops(conn: sqlite3.Connection = Depends(db_dep)):
    rows = conn.execute("SELECT * FROM drops ORDER BY id DESC LIMIT 50").fetchall()
    out = []
    for d in rows:
        winners = conn.execute(
            "SELECT c.position, u.username FROM drop_claims c JOIN users u ON u.id = c.user_id "
            "WHERE c.drop_id = ? ORDER BY c.position", (d["id"],),
        ).fetchall()
        out.append({
            "id": d["id"], "code": d["code"], "points": d["points"],
            "max_winners": d["max_winners"], "active": bool(d["active"]), "created_at": d["created_at"],
            "winners": [{"position": w["position"], "username": w["username"]} for w in winners],
        })
    return out


@router.post("/drops")
def create_drop(data: DropCreateIn, request: Request,
                conn: sqlite3.Connection = Depends(db_dep),
                admin: sqlite3.Row = Depends(require_user)):
    code = (data.code or "").strip().lstrip("#").upper() or ("DROP-" + new_code())
    conn.execute("UPDATE drops SET active = 0, ended_at = ? WHERE active = 1", (now_iso(),))
    cur = conn.execute(
        "INSERT INTO drops (code, points, max_winners, active, created_at) VALUES (?, ?, ?, 1, ?)",
        (code, data.points, data.max_winners, now_iso()),
    )
    record_audit(conn, admin, request, "drop.create", f"#{cur.lastrowid} {code}",
                 f"{data.points} PTS, {data.max_winners} výherců")
    conn.commit()
    # Auto-post kódu do Kick chatu botem (pokud je zapnuto + bot připojen)
    bot = {"sent": False, "skipped": True}
    try:
        bot = kickbot.post_drop(conn, code, data.points, data.max_winners)
    except Exception:
        pass
    note = ""
    if bot.get("sent"):
        note = (" Bot poslal kód do chatu kick.com/" + kickbot.status(conn)["channel"]
                + (" (demo)" if not bot.get("real") else "."))
    return {"ok": True, "id": cur.lastrowid, "code": code,
            "points": data.points, "max_winners": data.max_winners, "bot": bot,
            "message": f"Drop spuštěn! Hoď do chatu kód: {code}" + note}


@router.get("/drops/auto")
def get_autodrop(conn: sqlite3.Connection = Depends(db_dep)):
    """Nastavení auto-drop scheduleru."""
    return autodrop.get_config(conn)


@router.get("/live-happy")
def get_live_happy(conn: sqlite3.Connection = Depends(db_dep)):
    """Nastavení Happy Hour (při startu streamu)."""
    return live_events.get_config(conn)


@router.post("/live-happy")
def set_live_happy(data: LiveHappyIn, request: Request,
                   conn: sqlite3.Connection = Depends(db_dep),
                   admin: sqlite3.Row = Depends(require_user)):
    """Uloží nastavení Happy Hour (posílají se jen měněná pole)."""
    cfg = live_events.set_config(conn, data.model_dump(exclude_none=True))
    record_audit(conn, admin, request, "livehappy.update", "",
                 f"enabled={cfg['livehappy_enabled']} mult={cfg['livehappy_mult']} min={cfg['livehappy_minutes']}")
    conn.commit()
    return cfg


@router.post("/live-happy/start")
def live_happy_start(request: Request,
                     conn: sqlite3.Connection = Depends(db_dep),
                     admin: sqlite3.Row = Depends(require_user)):
    """RUČNÍ spuštění Happy Hour TEĎ – countdown + ×mult + oznámení v chatu (background)."""
    res = live_events.start_now(conn)
    record_audit(conn, admin, request, "livehappy.start", "", f"{res['minutes']} min ×{res['mult']:g}")
    conn.commit()
    return {"ok": True, **res}


@router.post("/live-happy/stop")
def live_happy_stop(request: Request,
                    conn: sqlite3.Connection = Depends(db_dep),
                    admin: sqlite3.Row = Depends(require_user)):
    """Okamžitě ukončí běžící Happy Hour."""
    res = live_events.stop_now(conn)
    record_audit(conn, admin, request, "livehappy.stop", "", "")
    conn.commit()
    return {"ok": True, **res}


@router.post("/drops/auto")
def set_autodrop(data: AutoDropIn, request: Request,
                 conn: sqlite3.Connection = Depends(db_dep),
                 admin: sqlite3.Row = Depends(require_user)):
    """Uloží nastavení auto-dropu (posílají se jen měněná pole)."""
    cfg = autodrop.set_config(conn, data.model_dump(exclude_none=True))
    record_audit(conn, admin, request, "autodrop.update", "",
                 f"enabled={cfg['autodrop_enabled']} "
                 f"interval={cfg['autodrop_interval_min']}-{cfg['autodrop_interval_max']}m "
                 f"pts={cfg['autodrop_points']}-{cfg['autodrop_points_max']} "
                 f"winners={cfg['autodrop_winners']}-{cfg['autodrop_winners_max']} "
                 f"live={cfg['autodrop_only_live']}")
    conn.commit()
    return cfg


@router.post("/drops/{drop_id}/end")
def end_drop(drop_id: int, request: Request,
             conn: sqlite3.Connection = Depends(db_dep),
             admin: sqlite3.Row = Depends(require_user)):
    conn.execute("UPDATE drops SET active = 0, ended_at = ? WHERE id = ?", (now_iso(), drop_id))
    record_audit(conn, admin, request, "drop.end", f"#{drop_id}")
    conn.commit()
    return {"ok": True}


# ---------------- Patch notes / novinky (changelog) ----------------
@router.get("/news")
def admin_news(conn: sqlite3.Connection = Depends(db_dep)):
    """Všechny novinky (vč. nepublikovaných) pro správu v adminu."""
    rows = conn.execute(
        "SELECT id, title, body, tag, published, created_at FROM patch_notes "
        "ORDER BY created_at DESC, id DESC LIMIT 200"
    ).fetchall()
    return [{**dict(r), "published": bool(r["published"])} for r in rows]


@router.post("/news")
def create_news(data: PatchNoteIn, request: Request,
                conn: sqlite3.Connection = Depends(db_dep),
                admin: sqlite3.Row = Depends(require_user)):
    cur = conn.execute(
        "INSERT INTO patch_notes (title, body, tag, published, created_at) VALUES (?, ?, ?, ?, ?)",
        (data.title.strip(), data.body.strip(), data.tag, 1 if data.published else 0, now_iso()),
    )
    record_audit(conn, admin, request, "news.create", f"#{cur.lastrowid} {data.title[:60]}")
    conn.commit()
    return {"ok": True, "id": cur.lastrowid}


@router.delete("/news/{note_id}")
def delete_news(note_id: int, request: Request,
                conn: sqlite3.Connection = Depends(db_dep),
                admin: sqlite3.Row = Depends(require_user)):
    n = conn.execute("SELECT title FROM patch_notes WHERE id = ?", (note_id,)).fetchone()
    conn.execute("DELETE FROM patch_notes WHERE id = ?", (note_id,))
    record_audit(conn, admin, request, "news.delete", f"#{note_id} {n['title'] if n else '?'}")
    conn.commit()
    return {"ok": True}


# ---------------- Top Chatteři – výplata ----------------
@router.get("/topchatter/status")
def admin_topchatter_status(conn: sqlite3.Connection = Depends(db_dep)):
    """Stav výplaty TOP chatterů (kdy naposled placeno) + dnešní TOP 3 a co by brali."""
    from ..topchatter import status
    return status(conn)


@router.post("/topchatter/pay")
def admin_topchatter_pay(request: Request, conn: sqlite3.Connection = Depends(db_dep),
                         admin: sqlite3.Row = Depends(require_user)):
    """Ručně vyplatí DNEŠNÍ TOP 3 chattery hned (např. po streamu). Idempotentní – 1× denně."""
    from ..topchatter import pay_today
    res = pay_today(conn)
    if res.get("ok"):
        record_audit(conn, admin, request, "topchatter.pay", "", f"{res.get('count')} výherců")
        conn.commit()
    return res


# ---------------- Hry (piškvorky) – moderace ----------------
@router.get("/games")
def admin_games(conn: sqlite3.Connection = Depends(db_dep)):
    """Seznam probíhajících her (otevřené + rozehrané) – pro moderaci."""
    return list_games_admin(conn)


@router.get("/games/history")
def admin_games_history(conn: sqlite3.Connection = Depends(db_dep)):
    """Dohrané/zrušené hry (piškvorky + duely) – kdo s kým, kdo vyhrál, refund."""
    return games_history(conn)


@router.post("/games/{game_id}/refund")
def admin_game_refund(game_id: int, request: Request,
                      conn: sqlite3.Connection = Depends(db_dep),
                      admin: sqlite3.Row = Depends(require_user)):
    """Refund dohrané piškvorkové hry: vrátí oběma vklad a stornuje výhru vítězi."""
    res = refund_game_admin(conn, game_id)
    if res.get("ok"):
        record_audit(conn, admin, request, "game.refund", f"#{game_id}")
        conn.commit()
    return res


@router.post("/games/duels/{duel_id}/refund")
def admin_duel_refund(duel_id: int, request: Request,
                      conn: sqlite3.Connection = Depends(db_dep),
                      admin: sqlite3.Row = Depends(require_user)):
    """Refund dohraného duelu: vrátí oběma vklad a stornuje výhru vítězi."""
    res = refund_duel_admin(conn, duel_id)
    if res.get("ok"):
        record_audit(conn, admin, request, "duel.refund", f"#{duel_id}")
        conn.commit()
    return res


@router.post("/games/{game_id}/cancel")
def admin_game_cancel(game_id: int, request: Request,
                      conn: sqlite3.Connection = Depends(db_dep),
                      admin: sqlite3.Row = Depends(require_user)):
    """Ukončí hru a vrátí vklady (oběma hráčům)."""
    res = cancel_game_admin(conn, game_id)
    if res.get("ok"):
        record_audit(conn, admin, request, "game.cancel", f"#{game_id}")
        conn.commit()
    return res


# ---------------- Záloha databáze ----------------
@router.get("/backup")
def backup_db(request: Request,
              conn: sqlite3.Connection = Depends(db_dep),
              admin: sqlite3.Row = Depends(require_user)):
    """Stáhne čistou kopii celé databáze (SQLite) přes VACUUM INTO. Jen admin (sekce security).

    Po odeslání se dočasná kopie z disku smaže (ať plná DB neleží na předvídatelné cestě),
    a stažení se zaloguje do auditu (kdo a kdy – soubor obsahuje e-maily, IP, vše).
    """
    out = DATA_DIR / "webos-backup.db"
    if out.exists():
        out.unlink()
    src = sqlite3.connect(str(DB_PATH))
    src.isolation_level = None  # autocommit (VACUUM nesmí běžet v transakci)
    try:
        src.execute("VACUUM INTO ?", (str(out),))
    finally:
        src.close()
    record_audit(conn, admin, request, "backup.download", "celá DB")
    conn.commit()
    fname = f"webos-zaloha-{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.db"
    return FileResponse(str(out), filename=fname, media_type="application/octet-stream",
                        background=BackgroundTask(out.unlink, missing_ok=True))
