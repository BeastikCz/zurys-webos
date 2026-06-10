"""Leaderboard, redeem kódů a profil (objednávky + historie bodů)."""
import re
import sqlite3
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from ..anticheat import (check_or_block, is_new_account, new_account_redeem_pts,
                          NEW_ACCOUNT_MAX_REDEEM_PTS, GIFT_MIN_AGE_HOURS)
from ..config import ORDER_PENDING, UNLIMITED_STOCK, ROLE_ADMIN
from ..db import now_iso
from ..deps import db_dep, require_user, add_points, try_debit, record_audit, client_ip, user_rank, tier_for_rank
from ..models import RedeemIn, TradeUrlIn, GiftIn, QuestClaimIn
from ..services import product_public, role_allows
from ..ratelimit import rate_limit
from ..security import secure_weighted_choice
from .. import economy, live, partners

router = APIRouter(tags=["misc"])


@router.get("/news")
def list_news(conn: sqlite3.Connection = Depends(db_dep)):
    """Veřejný changelog – publikované novinky, nejnovější první."""
    rows = conn.execute(
        "SELECT id, title, body, tag, created_at FROM patch_notes "
        "WHERE published = 1 ORDER BY created_at DESC, id DESC LIMIT 60"
    ).fetchall()
    return {"notes": [dict(r) for r in rows]}


@router.get("/stream/status")
def stream_status(conn: sqlite3.Connection = Depends(db_dep)):
    """Veřejný stav streamu pro „živou tečku" v hlavičce (zelená = online, červená = offline).
    Respektuje admin override (on/off/auto) a 45s cache v live modulu."""
    return {"live": live.is_live(conn), "channel": live.broadcaster_slug(conn)}


@router.get("/leaderboard")
def leaderboard(limit: int = Query(50, ge=1, le=200),
                conn: sqlite3.Connection = Depends(db_dep)):
    rows = conn.execute(
        "SELECT id, username, avatar_url, points, role, is_sub, is_vip, is_og FROM users "
        "ORDER BY points DESC, username ASC LIMIT ?",
        (limit,),
    ).fetchall()
    return [
        {
            "rank": i + 1,
            "username": r["username"],
            "avatar_url": r["avatar_url"],
            "points": r["points"],
            "role": r["role"],
            "is_sub": bool(r["is_sub"]),
            "is_vip": bool(r["is_vip"]),
            "is_og": bool(r["is_og"]),
        }
        for i, r in enumerate(rows)
    ]


@router.get("/profile/public")
def public_profile(nick: str = Query("", max_length=64),
                   viewer: sqlite3.Row = Depends(require_user),
                   conn: sqlite3.Connection = Depends(db_dep)):
    """Veřejný profil uživatele se statistikami (pro každého přihlášeného). Bez IP/e-mailu."""
    key = (nick or "").strip().lstrip("@").lower()
    if not key:
        raise HTTPException(status_code=400, detail="Zadej uživatele.")
    u = conn.execute(
        "SELECT * FROM users WHERE LOWER(username) = ? OR LOWER(kick_username) = ? "
        "ORDER BY (kick_username IS NOT NULL) DESC LIMIT 1", (key, key),
    ).fetchone()
    if not u:
        raise HTTPException(status_code=404, detail="Uživatel nenalezen.")
    uid = u["id"]
    rank = user_rank(conn, u["points"], u["username"])
    league_key, league_mult = tier_for_rank(rank)
    earned = conn.execute("SELECT COALESCE(SUM(change),0) c FROM points_log WHERE user_id=? AND change>0", (uid,)).fetchone()["c"]
    spent = conn.execute("SELECT COALESCE(SUM(-change),0) c FROM points_log WHERE user_id=? AND change<0", (uid,)).fetchone()["c"]
    biggest = conn.execute("SELECT COALESCE(MAX(change),0) c FROM points_log WHERE user_id=? AND change>0", (uid,)).fetchone()["c"]
    g = conn.execute(
        "SELECT COUNT(*) AS played, "
        "COALESCE(SUM(CASE WHEN (winner=1 AND p1_id=?) OR (winner=2 AND p2_id=?) THEN 1 ELSE 0 END),0) AS won "
        "FROM duels WHERE status='finished' AND winner IN (1,2) AND (p1_id=? OR p2_id=?)",
        (uid, uid, uid, uid),
    ).fetchone()
    played, won = g["played"] or 0, g["won"] or 0
    raffle_wins = conn.execute("SELECT COUNT(*) AS c FROM raffle_winners WHERE user_id=?", (uid,)).fetchone()["c"]
    from ..achievements import BADGES
    earned_b = {r["badge_key"]: r["tier"] for r in conn.execute(
        "SELECT badge_key, tier FROM user_badges WHERE user_id=?", (uid,))}
    badges = [{"key": b["key"], "emoji": b["emoji"], "name": b["name"], "desc": b["desc"],
               "max_tier": len(b["tiers"]), "tier": earned_b.get(b["key"], 0),
               "earned": earned_b.get(b["key"], 0) > 0} for b in BADGES]
    # Vitrína: vyhrané (tomboly) + vlastněné odměny S OBRÁZKEM (dedup dle produktu, max 12).
    # Tomboly mají přednost (trofeje) a nesou flag won=True; pak vlastněné z objednávek.
    showcase = []
    seen = set()
    for r in conn.execute(
        "SELECT p.id AS pid, p.name, p.image_url, p.rarity, p.type, w.created_at "
        "FROM raffle_winners w JOIN products p ON p.id = w.product_id "
        "WHERE w.user_id = ? ORDER BY w.created_at DESC, w.id DESC", (uid,)):
        if r["pid"] in seen or not (r["image_url"] or "").strip():
            continue
        seen.add(r["pid"])
        showcase.append({"name": r["name"], "image_url": r["image_url"], "rarity": r["rarity"],
                         "type": r["type"], "created_at": r["created_at"], "won": True})
    for r in conn.execute(
        "SELECT p.id AS pid, COALESCE(p.name, o.product_name) AS name, p.image_url, p.rarity, p.type, o.created_at "
        "FROM orders o JOIN products p ON p.id = o.product_id "
        "WHERE o.user_id = ? AND p.image_url IS NOT NULL AND p.image_url != '' "
        "ORDER BY o.created_at DESC, o.id DESC", (uid,)):
        if r["pid"] in seen:
            continue
        seen.add(r["pid"])
        showcase.append({"name": r["name"], "image_url": r["image_url"], "rarity": r["rarity"],
                         "type": r["type"], "created_at": r["created_at"], "won": False})
    showcase = showcase[:12]
    return {
        "username": u["username"], "avatar_url": u["avatar_url"] or "", "role": u["role"],
        "created_at": u["created_at"], "points": u["points"],
        "rank": rank, "league": league_key, "league_mult": league_mult,
        "earned_total": earned, "spent_total": spent, "biggest_win": biggest,
        "games_played": played, "games_won": won,
        "win_rate": round(won / played, 3) if played else 0,
        "raffle_wins": raffle_wins, "badges": badges, "showcase": showcase,
        "is_sub": bool(u["is_sub"]), "is_vip": bool(u["is_vip"]), "is_og": bool(u["is_og"]),
        "daily_streak": (u["daily_streak"] if "daily_streak" in u.keys() else 0) or 0,
    }


@router.get("/community-goal")
def community_goal_status(conn: sqlite3.Connection = Depends(db_dep)):
    """Stav komunitního chat cíle (veřejné – pro lištu na webu)."""
    from ..community_goal import status
    return status(conn)


@router.get("/top-chatters")
def top_chatters_ep(period: str = Query("day", max_length=8),
                    conn: sqlite3.Connection = Depends(db_dep)):
    """Žebříček nejaktivnějších v chatu (veřejné). period = day | week."""
    from ..topchatter import top_chatters
    return top_chatters(conn, "week" if period == "week" else "day", 10)


@router.get("/quests")
def quests_list(user: sqlite3.Row = Depends(require_user),
                conn: sqlite3.Connection = Depends(db_dep)):
    """Denní/týdenní úkoly aktuálního uživatele (postup + stav vyzvednutí)."""
    from ..quests import get_quests, QUESTS_ENABLED
    if not QUESTS_ENABLED:          # úkoly dočasně mimo provoz → prázdno (frontend kartu schová)
        return []
    return get_quests(conn, user["id"])


@router.post("/quests/claim")
def quests_claim(data: QuestClaimIn, user: sqlite3.Row = Depends(require_user),
                 conn: sqlite3.Connection = Depends(db_dep)):
    """Vyzvedne odměnu za splněný úkol (server ověří splnění, nevěří klientovi)."""
    from ..quests import claim_quest, QUESTS_ENABLED
    if not QUESTS_ENABLED:
        raise HTTPException(status_code=400, detail="Úkoly jsou dočasně mimo provoz. 🛠️")
    try:
        return claim_quest(conn, user["id"], data.key)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/partner-links")
def partner_links_list(user: sqlite3.Row = Depends(require_user),
                       conn: sqlite3.Connection = Depends(db_dep)):
    """Zapnuté partnerské odkazy + stav (claimable/claimed dle režimu + flash okno)."""
    return partners.status_for_user(conn, user["id"])


@router.post("/partner-links/{link_id}/claim")
def partner_links_claim(link_id: int, user: sqlite3.Row = Depends(require_user),
                        conn: sqlite3.Connection = Depends(db_dep)):
    """Vyzvedne jednorázovou odměnu za proklik partnerského odkazu (1× za uživatele)."""
    try:
        return partners.claim(conn, user["id"], link_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


def _try_claim_drop(conn, user, request, raw_code):
    """Sjednocení: když je zadaný „kód" AKTIVNÍ drop, nárokuje ho (závod o kód).

    Vrátí redeem-style odpověď, nebo None když to drop není. Banner anti-bot (honeypot/
    dwell) se nepoužije (na to je drop banner) – redeem už dělá rate-limit + anticheat.
    """
    code = (raw_code or "").strip().lstrip("#").upper()
    d = conn.execute(
        "SELECT * FROM drops WHERE active = 1 AND UPPER(code) = ? ORDER BY id DESC LIMIT 1", (code,),
    ).fetchone()
    if not d:
        return None
    ip = client_ip(request)
    sig = conn.execute(
        "SELECT fp_hash FROM client_signals WHERE user_id = ? ORDER BY id DESC LIMIT 1", (user["id"],),
    ).fetchone()
    fp = (sig["fp_hash"] if sig else "") or ""
    # (Limit „1 chyt na IP/zařízení na drop" VYPNUT na přání – i přes pole „Uplatnit kód".)
    ts = now_iso()
    cur = conn.execute(
        "INSERT INTO drop_claims (drop_id, user_id, position, ip, fp_hash, created_at) "
        "SELECT :did, :uid, (SELECT COUNT(*) FROM drop_claims WHERE drop_id = :did) + 1, :ip, :fp, :ts "
        "WHERE (SELECT COUNT(*) FROM drop_claims WHERE drop_id = :did) < :maxw "
        "AND NOT EXISTS (SELECT 1 FROM drop_claims WHERE drop_id = :did AND user_id = :uid)",
        {"did": d["id"], "uid": user["id"], "ip": ip, "fp": fp, "ts": ts, "maxw": d["max_winners"]},
    )
    if cur.rowcount == 0:
        mine = conn.execute(
            "SELECT position FROM drop_claims WHERE drop_id = ? AND user_id = ?", (d["id"], user["id"]),
        ).fetchone()
        conn.commit()
        if mine:
            raise HTTPException(status_code=400, detail=f"Tenhle drop už jsi chytil (pozice {mine['position']}).")
        raise HTTPException(status_code=400, detail="Drop už je rozebraný! 😢 Příště rychleji. ⚡")
    position = conn.execute(
        "SELECT position FROM drop_claims WHERE drop_id = ? AND user_id = ?", (d["id"], user["id"]),
    ).fetchone()["position"]
    add_points(conn, user["id"], d["points"], f"Drop #{d['id']} – {position}. místo (přes kód)")
    if position >= d["max_winners"]:
        conn.execute("UPDATE drops SET active = 0, ended_at = ? WHERE id = ?", (ts, d["id"]))
    conn.commit()
    fresh = conn.execute("SELECT points FROM users WHERE id = ?", (user["id"],)).fetchone()
    return {"ok": True, "balance": fresh["points"],
            "message": f"🎁 Drop chycen! {position}. místo · +{d['points']} sedláků 🌾"}


@router.post("/redeem")
def redeem(data: RedeemIn, request: Request,
           user: sqlite3.Row = Depends(require_user),
           conn: sqlite3.Connection = Depends(db_dep)):
    rate_limit(f"redeem:{user['id']}", 6, 60)  # max 6 pokusů / min

    # --- ANTI-BOT: risk score blokuje vysoký combined risk ---
    check_or_block(conn, user, request, context="redeem", t0_ms=data.t0,
                   block_msg="Redeem zablokován ochranou proti zneužití.")

    code = data.code.strip()
    # AKTIVNÍ drop má přednost (živý závod o kód) – zabere i když existuje stejnojmenný
    # starý/expirovaný redeem kód (jinak by ho ten expirovaný kód „zastínil").
    drop_res = _try_claim_drop(conn, user, request, code)
    if drop_res:
        return drop_res
    row = conn.execute(
        "SELECT * FROM redeem_codes WHERE UPPER(code) = UPPER(?)", (code,)
    ).fetchone()
    if not row:
        raise HTTPException(status_code=400, detail="Neplatný kód.")
    if row["expires_at"] and row["expires_at"] < now_iso():
        raise HTTPException(status_code=400, detail="Platnost kódu vypršela.")
    if row["uses_count"] >= row["max_uses"]:
        raise HTTPException(status_code=400, detail="Kód už byl vyčerpán.")
    already = conn.execute(
        "SELECT 1 FROM redeem_uses WHERE code_id = ? AND user_id = ?",
        (row["id"], user["id"]),
    ).fetchone()
    if already:
        raise HTTPException(status_code=400, detail="Tento kód jsi už použil.")

    # --- ANTI-BOT: nové účty mají cap na získané body z kódů (první 24 h) ---
    if is_new_account(user) and row["points_value"] > 0:
        already_pts = new_account_redeem_pts(conn, user["id"])
        if already_pts + row["points_value"] > NEW_ACCOUNT_MAX_REDEEM_PTS:
            raise HTTPException(
                status_code=429,
                detail=f"Nové účty (<24 h) mohou získat max {NEW_ACCOUNT_MAX_REDEEM_PTS} sedláků "
                       f"z kódů. Zatím {already_pts}.",
            )

    points_added = 0
    product_payload = None
    parts = []

    if row["points_value"] and row["points_value"] != 0:
        points_added = row["points_value"]
        add_points(conn, user["id"], points_added, f"Redeem kód {row['code']}")
        parts.append(f"+{points_added} b")

    if row["product_id"]:
        product = conn.execute(
            "SELECT * FROM products WHERE id = ?", (row["product_id"],)
        ).fetchone()
        if product:
            if not role_allows(user, product):
                raise HTTPException(status_code=403,
                                    detail="Tuto odměnu nemůžeš uplatnit (jen pro sub/VIP).")
            conn.execute(
                "INSERT INTO orders (user_id, product_id, product_name, points_spent, status, created_at) "
                "VALUES (?, ?, ?, 0, ?, ?)",
                (user["id"], product["id"], product["name"], ORDER_PENDING, now_iso()),
            )
            if product["stock"] != UNLIMITED_STOCK and product["stock"] > 0:
                conn.execute("UPDATE products SET stock = stock - 1 WHERE id = ?", (product["id"],))
            product_payload = product_public(product)
            parts.append(f"odměna „{product['name']}“")

    conn.execute(
        "UPDATE redeem_codes SET uses_count = uses_count + 1 WHERE id = ?", (row["id"],)
    )
    conn.execute(
        "INSERT INTO redeem_uses (code_id, user_id, created_at) VALUES (?, ?, ?)",
        (row["id"], user["id"], now_iso()),
    )
    conn.commit()

    fresh = conn.execute("SELECT points FROM users WHERE id = ?", (user["id"],)).fetchone()
    return {
        "ok": True,
        "points_added": points_added,
        "product": product_payload,
        "balance": fresh["points"],
        "message": "Kód uplatněn: " + (", ".join(parts) if parts else "hotovo") + ".",
    }


# ---------------- Pasivní výdělek: sledování + přehled ----------------
@router.post("/activity/heartbeat")
def activity_heartbeat(request: Request, user: sqlite3.Row = Depends(require_user),
                       conn: sqlite3.Connection = Depends(db_dep)):
    """Tep ze živé záložky – body za sledování (PTS/min × násobič). Anti-spam přes cooldown."""
    rate_limit(f"hb:{user['id']}", 4, 50)  # max 4 heartbeaty / ~50 s (klient posílá ~1/min)
    # boti/headless/VPN se do pasivního výdělku nepočítají
    risk = check_or_block(conn, user, request, context="claim",
                          block_msg="Body za sledování blokovány (anti-bot).")
    res = economy.award_watch(conn, user)
    res["balance"] = conn.execute("SELECT points FROM users WHERE id = ?", (user["id"],)).fetchone()["points"]
    res["summary"] = economy.activity_summary(conn, user)
    return res


@router.get("/activity/summary")
def activity_summary(user: sqlite3.Row = Depends(require_user),
                     conn: sqlite3.Connection = Depends(db_dep)):
    """Dnešní pasivní výdělek + nastavení (pro UI banner)."""
    return economy.activity_summary(conn, user)


@router.get("/profile/orders")
def my_orders(user: sqlite3.Row = Depends(require_user),
              conn: sqlite3.Connection = Depends(db_dep)):
    rows = conn.execute(
        "SELECT o.id, o.points_spent, o.status, o.created_at, COALESCE(p.name, o.product_name) AS product_name, "
        "       p.type AS product_type FROM orders o "
        "LEFT JOIN products p ON p.id = o.product_id "
        "WHERE o.user_id = ? ORDER BY o.created_at DESC, o.id DESC",
        (user["id"],),
    ).fetchall()
    return [
        {
            "id": r["id"],
            "product_name": r["product_name"] or "(smazaná odměna)",
            "product_type": r["product_type"],
            "points_spent": r["points_spent"],
            "status": r["status"],
            "created_at": r["created_at"],
        }
        for r in rows
    ]


@router.get("/profile/points-log")
def my_points_log(user: sqlite3.Row = Depends(require_user),
                  conn: sqlite3.Connection = Depends(db_dep)):
    rows = conn.execute(
        "SELECT change, reason, created_at FROM points_log WHERE user_id = ? AND change != 0 "
        "ORDER BY created_at DESC, id DESC LIMIT 100",
        (user["id"],),
    ).fetchall()
    return [
        {"change": r["change"], "reason": r["reason"], "created_at": r["created_at"]}
        for r in rows
    ]


# ---------------- Steam trade link (na ruční výplatu skinů) ----------------
# Tvar: https://steamcommunity.com/tradeoffer/new/?partner=<číslo>&token=<token>
_TRADE_RE = re.compile(
    r"^https://steamcommunity\.com/tradeoffer/new/\?partner=\d+&token=[A-Za-z0-9_-]{6,}$"
)


@router.post("/profile/trade-url")
def set_trade_url(data: TradeUrlIn,
                  user: sqlite3.Row = Depends(require_user),
                  conn: sqlite3.Connection = Depends(db_dep)):
    """Uloží Steam trade odkaz přihlášeného diváka (prázdné = smazat)."""
    url = (data.url or "").strip()
    if url and not _TRADE_RE.match(url):
        raise HTTPException(
            status_code=400,
            detail="Neplatný Steam trade link. Zkopíruj ho celý ze Steamu – má tvar "
                   "https://steamcommunity.com/tradeoffer/new/?partner=…&token=…",
        )
    conn.execute("UPDATE users SET steam_trade_url = ? WHERE id = ?",
                 (url or None, user["id"]))
    conn.commit()
    return {"steam_trade_url": url or None}


# ---------------- Exchange: poslání sedláků kamarádovi ----------------
# Darování sedláků – přepínač. False = MIMO PROVOZ (neukáže se a neprojde). Zpět nastav na True.
GIFT_ENABLED = False


def _shared_identity(conn: sqlite3.Connection, uid1: int, uid2: int) -> bool:
    """Sdílí dva účty IP nebo otisk zařízení? (anti-alt – brání farmení přes vlastní účty)."""
    def ips(uid):
        return {r["ip"] for r in conn.execute(
            "SELECT DISTINCT ip FROM login_events WHERE user_id=? AND ip IS NOT NULL AND ip!=''", (uid,))}

    def fps(uid):
        return {r["fp_hash"] for r in conn.execute(
            "SELECT DISTINCT fp_hash FROM client_signals WHERE user_id=? AND fp_hash IS NOT NULL", (uid,))}
    return bool(ips(uid1) & ips(uid2)) or bool(fps(uid1) & fps(uid2))


@router.post("/exchange/gift")
def gift_points(data: GiftIn, request: Request,
                user: sqlite3.Row = Depends(require_user),
                conn: sqlite3.Connection = Depends(db_dep)):
    """Pošle sedláky jinému divákovi. Nevratné. Anti-alt: ne na stejnou IP/zařízení."""
    if not GIFT_ENABLED:
        raise HTTPException(status_code=403, detail="Darování sedláků je dočasně mimo provoz. 🔧")
    rate_limit(f"gift:{user['id']}", 5, 60)
    key = (data.username or "").strip().lstrip("@").lower()
    if not key:
        raise HTTPException(status_code=400, detail="Zadej příjemce.")
    rcp = conn.execute(
        "SELECT * FROM users WHERE LOWER(kick_username) = ? OR LOWER(username) = ? "
        "ORDER BY (kick_username IS NOT NULL) DESC LIMIT 1", (key, key),
    ).fetchone()
    if not rcp:
        raise HTTPException(status_code=400, detail=f"Uživatel {data.username} neexistuje.")
    if rcp["id"] == user["id"]:
        raise HTTPException(status_code=400, detail="Sobě poslat nemůžeš. 😄")
    if rcp["banned"]:
        raise HTTPException(status_code=400, detail="Tomuhle účtu nelze poslat (zabanován).")
    # anti-funnel: nový účet nesmí hned posílat dary. Klasický trik je založit alt na čisté
    # IP/zařízení a poslat body na hlavní účet DŘÍV, než se stihne propojit otisk – proto
    # darování pustíme až po pár dnech od založení (admin výjimka). Doplňuje _shared_identity níž.
    if user["role"] != ROLE_ADMIN and is_new_account(user, hours=GIFT_MIN_AGE_HOURS):
        raise HTTPException(
            status_code=403,
            detail=f"Darovat můžeš až {GIFT_MIN_AGE_HOURS} h po založení účtu "
                   f"(ochrana proti farmění bodů přes nové účty).")
    # anti-farma: nelze posílat účtu ze stejné IP/zařízení (admin výjimka)
    if user["role"] != ROLE_ADMIN and rcp["role"] != ROLE_ADMIN and _shared_identity(conn, user["id"], rcp["id"]):
        raise HTTPException(status_code=403,
                            detail="Nelze poslat účtu ze stejné sítě/zařízení (ochrana proti farmení).")
    # atomický odečet – nejde do mínusu ani při souběhu
    if not try_debit(conn, user["id"], data.amount, f"Dar pro {rcp['username']} 🎁"):
        raise HTTPException(status_code=400, detail=f"Nemáš dost sedláků (máš {user['points']}).")
    add_points(conn, rcp["id"], data.amount, f"Dar od {user['username']} 🎁")
    conn.commit()
    fresh = conn.execute("SELECT points FROM users WHERE id = ?", (user["id"],)).fetchone()["points"]
    return {"ok": True, "balance": fresh, "recipient": rcp["username"], "amount": data.amount,
            "message": f"🎁 Posláno {data.amount} sedláků uživateli {rcp['username']}!"}


# ---------------- Denní bonus – 7denní streak ----------------
DAILY_LADDER = [10, 20, 30, 40, 50, 75, 200]  # PTS za den 1..7
DAILY_COOLDOWN_H = 20    # jak často lze vyzvednout
DAILY_RESET_H = 48       # po výpadku se cyklus resetuje


def _daily_state(user: sqlite3.Row, now: datetime):
    """(streak, can_claim, next_in_s) s ohledem na reset cyklu."""
    last = user["last_daily"]; streak = user["daily_streak"] or 0
    can, next_in = True, 0
    if last:
        elapsed = (now - datetime.fromisoformat(last)).total_seconds()
        if elapsed < DAILY_COOLDOWN_H * 3600:
            can, next_in = False, int(DAILY_COOLDOWN_H * 3600 - elapsed)
        elif elapsed > DAILY_RESET_H * 3600:
            streak = 0  # vynechal den → reset týdenního cyklu
    return streak, can, next_in


@router.get("/daily/status")
def daily_status(user: sqlite3.Row = Depends(require_user),
                 conn: sqlite3.Connection = Depends(db_dep)):
    now = datetime.now(timezone.utc)
    streak, can, next_in = _daily_state(user, now)
    done = streak % 7  # vybráno v aktuálním týdnu
    rank = user_rank(conn, user["points"], user["username"])
    return {
        "ladder": DAILY_LADDER,
        "mult": tier_for_rank(rank)[1],
        "rank": rank,
        "done_count": done,
        "day": done + 1,           # DEN x/7
        "can_claim": can,
        "reward_now": DAILY_LADDER[done],
        "next_in_seconds": next_in,
        "streak_total": user["daily_streak"] or 0,
    }


@router.post("/daily/claim")
def daily_claim(user: sqlite3.Row = Depends(require_user),
                conn: sqlite3.Connection = Depends(db_dep)):
    now = datetime.now(timezone.utc)
    streak, can, next_in = _daily_state(user, now)
    if not can:
        hrs = round(next_in / 3600, 1)
        raise HTTPException(status_code=400, detail=f"Denní bonus už máš. Vrať se za {hrs} h. ⏳")
    idx = streak % 7
    mult = tier_for_rank(user_rank(conn, user["points"], user["username"]))[1]
    reward = DAILY_LADDER[idx] * mult
    add_points(conn, user["id"], reward, f"Denní streak – den {idx + 1} (×{mult} liga)")
    conn.execute("UPDATE users SET last_daily = ?, daily_streak = ? WHERE id = ?",
                 (now.isoformat(), streak + 1, user["id"]))
    conn.commit()
    fresh = conn.execute("SELECT points FROM users WHERE id = ?", (user["id"],)).fetchone()
    return {
        "ok": True, "reward": reward, "mult": mult, "day": idx + 1, "streak": streak + 1,
        "balance": fresh["points"], "message": f"🔥 Den {idx + 1}/7 — získáváš +{reward} sedláků (×{mult} liga)!",
    }


# ---------------- Kolo štěstí (denní spin) ----------------
# Políčka kola: (sedláci, váha). Pořadí v seznamu = pořadí na kole (záměrně střídá
# velké/malé). Váhy = relativní šance; součet 100 → rovnou %. Malé časté, jackpot vzácný.
WHEEL_SEGMENTS = [
    (50, 24), (1500, 3), (100, 18), (350, 9),
    (25, 28), (3000, 1), (200, 12), (750, 5),
]
WHEEL_COOLDOWN_H = 20    # 1 zatočení / ~den (stejně jako denní bonus)
_WHEEL_JACKPOT = max(a for a, _ in WHEEL_SEGMENTS)


def _wheel_state(user: sqlite3.Row, now: datetime):
    """(can_spin, next_in_s) – 1× za WHEEL_COOLDOWN_H hodin."""
    last = user["last_wheel"]
    if last:
        elapsed = (now - datetime.fromisoformat(last)).total_seconds()
        if elapsed < WHEEL_COOLDOWN_H * 3600:
            return False, int(WHEEL_COOLDOWN_H * 3600 - elapsed)
    return True, 0


@router.get("/wheel/status")
def wheel_status(user: sqlite3.Row = Depends(require_user)):
    can, next_in = _wheel_state(user, datetime.now(timezone.utc))
    return {
        "segments": [a for a, _ in WHEEL_SEGMENTS],   # částky v pořadí na kole
        "jackpot": _WHEEL_JACKPOT,
        "can_spin": can,
        "next_in_seconds": next_in,
        "cooldown_h": WHEEL_COOLDOWN_H,
    }


@router.post("/wheel/spin")
def wheel_spin(user: sqlite3.Row = Depends(require_user),
               conn: sqlite3.Connection = Depends(db_dep)):
    now = datetime.now(timezone.utc)
    # Atomicky „zaberu" dnešní spin – brání dvojímu zatočení i při souběhu requestů
    # (stejný princip jako atomický claim u sledování streamu).
    threshold = (now - timedelta(hours=WHEEL_COOLDOWN_H)).isoformat()
    cur = conn.execute(
        "UPDATE users SET last_wheel = ? WHERE id = ? AND (last_wheel IS NULL OR last_wheel < ?)",
        (now.isoformat(), user["id"], threshold),
    )
    if cur.rowcount == 0:                       # už dnes točil (nebo souběžný request předběhl)
        conn.commit()
        _, next_in = _wheel_state(user, now)
        hrs = round((next_in or WHEEL_COOLDOWN_H * 3600) / 3600, 1)
        raise HTTPException(status_code=400, detail=f"Dnes už jsi točil 🎡 Vrať se za {hrs} h. ⏳")
    # Výsledek vybírá SERVER (klient ho jen dotočí) → z prohlížeče se nedá ošvindlit.
    idx = secure_weighted_choice(range(len(WHEEL_SEGMENTS)), [w for _, w in WHEEL_SEGMENTS])
    amount = WHEEL_SEGMENTS[idx][0]
    jackpot = amount == _WHEEL_JACKPOT
    add_points(conn, user["id"], amount, "Kolo štěstí 🎡" + (" – JACKPOT! 🎰" if jackpot else ""))
    conn.commit()
    fresh = conn.execute("SELECT points FROM users WHERE id = ?", (user["id"],)).fetchone()
    return {
        "ok": True,
        "index": idx,
        "amount": amount,
        "jackpot": jackpot,
        "balance": fresh["points"],
        "message": (f"🎰 JACKPOT! +{amount} sedláků!" if jackpot
                    else f"🎡 Padlo ti +{amount} sedláků!"),
    }
