"""Sdílené FastAPI závislosti: připojení k DB, aktuální uživatel, role, pomocníci."""
import sqlite3
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import Depends, HTTPException, Request

from .config import SESSION_COOKIE, ROLE_ADMIN, ROLE_BROADCASTER, STAFF_ROLES, ADMIN_SECTIONS, TRUSTED_IPS, KNOWN_ADMIN_IPS
from .db import get_conn, now_iso
from . import alerts

# Jak často (max) přepisovat „naposledy viděn" u session. get_current_user běží na VĚTŠINĚ
# requestů → zápis+commit při KAŽDÉM by zbytečně serializoval zápisy do SQLite (jeden
# zapisovatel), hlavně při náporu z pollingu (heartbeaty, živé predikce). Throttle = míň
# zápisů na hot-path, žádná funkční změna pro diváka.
SESSION_TOUCH_SEC = 120


def db_dep():
    """Yield připojení k DB a po requestu ho zavři."""
    conn = get_conn()
    try:
        yield conn
    finally:
        conn.close()


def client_ip(request: Request) -> str:
    """Reálná IP klienta.

    BEZPEČNOST: za Cloudflare (před Fly) nese reálnou IP `CF-Connecting-IP` (CF ji nastaví
    a klientskou hodnotu přepíše). Za Fly bez CF je důvěryhodná `Fly-Client-IP`. NAOPAK
    první záznam `X-Forwarded-For` si pošle sám klient → tím by šly obejít IP bany /
    anticheat, proto se na něj nespoléháme. Lokálně (bez proxy) bereme přímé spojení.
    Pozn.: až poběží CF, je vhodné omezit Fly origin jen na Cloudflare IP rozsahy, ať
    nejde `CF-Connecting-IP` podvrhnout přímým requestem na *.fly.dev.
    """
    cf = request.headers.get("cf-connecting-ip")
    if cf:
        return cf.strip()
    fly = request.headers.get("fly-client-ip")
    if fly:
        return fly.strip()
    if request.client and request.client.host:
        return request.client.host
    # poslední záchrana mimo Fly: poslední (proxy-přidaný) záznam XFF
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[-1].strip()
    return "?"


def record_login(conn: sqlite3.Connection, user_id: int, request: Request, method: str) -> None:
    """Zapíše událost přihlášení (pro bezpečnostní log / anticheat)."""
    ip = client_ip(request)
    ua = (request.headers.get("user-agent") or "")[:300]
    u = conn.execute("SELECT username, role FROM users WHERE id = ?", (user_id,)).fetchone()
    if u and u["role"] in STAFF_ROLES:
        known_ip = conn.execute(
            "SELECT 1 FROM login_events WHERE user_id = ? AND ip = ? LIMIT 1", (user_id, ip)
        ).fetchone()
        known_ua = conn.execute(
            "SELECT 1 FROM login_events WHERE user_id = ? AND user_agent = ? LIMIT 1", (user_id, ua)
        ).fetchone()
        if (not known_ip or not known_ua) and ip not in TRUSTED_IPS:
            alerts.send(
                "Admin/staff login z nove IP nebo zarizeni",
                detail=f"{u['username']} ({u['role']})\nmethod={method}\nip={ip}\nua={ua[:180]}",
                key=f"staff-login:{user_id}:{ip}:{hash(ua)}",
                cooldown=3600,
                ping=True,
            )
    conn.execute(
        "INSERT INTO login_events (user_id, ip, user_agent, method, created_at) VALUES (?, ?, ?, ?, ?)",
        (user_id, ip, ua, method, now_iso()),
    )


# Discord alert na KAŽDOU citlivou admin akci je VYPNUTÝ (spamoval Discord).
# Audit se pořád zapisuje do DB (admin_audit → admin tab „Bezpečnost"), jen se
# neposílá na Discord. Pro zapnutí zpět přepni na True.
ALERT_ON_ADMIN_ACTIONS = False

# Alert „admin akce z NEznámé IP" – VYPNUTÝ. Spamoval, protože adminovi rotuje
# IPv6 (každá akce přišla z „nové" IP). Audit do DB jede dál. Zpět = True.
ALERT_ON_ADMIN_NEW_IP = False


def record_audit(conn: sqlite3.Connection, admin: sqlite3.Row, request: Request,
                 action: str, target: str = "", details: str = "") -> None:
    """Zapíše záznam do audit logu admin akcí (kdo, kdy, co). Volá se před commitem."""
    conn.execute(
        "INSERT INTO admin_audit (admin_id, admin_name, action, target, details, ip, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (admin["id"], admin["username"], action, target[:200], details[:400],
         client_ip(request), now_iso()),
    )
    important = {"user.role", "raffle.draw", "ip.ban", "rule.update", "ddos.autoban",
                 "maintenance.on", "maintenance.off", "drop.create", "code.generate",
                 "code.delete", "product.delete", "user.meta"}
    should_alert = action in important
    if action == "user.points":
        try:
            amount = int((details or "0").split(" PTS")[0].replace("+", "").strip())
            should_alert = abs(amount) >= 10000
        except Exception:
            should_alert = True
    if ALERT_ON_ADMIN_ACTIONS and should_alert:
        alerts.send(
            "Citliva admin akce",
            detail=f"{admin['username']} -> {action}\ntarget={target[:180]}\ndetail={details[:300]}\nip={client_ip(request)}",
            key=f"audit:{action}:{target[:80]}",
            cooldown=60,
            ping=action in {"user.role", "ip.ban", "rule.update"},
        )
    # Bezpečnostní signál: citlivá admin akce z NEznámé IP (mimo KNOWN_ADMIN_IPS
    # i historii přihlášení). VYPNUTÝ – spamoval kvůli rotující IPv6 admina.
    # Pro zapnutí zpět přepni ALERT_ON_ADMIN_NEW_IP na True.
    if ALERT_ON_ADMIN_NEW_IP and should_alert:
        ip = client_ip(request)
        if ip not in KNOWN_ADMIN_IPS and not conn.execute(
                "SELECT 1 FROM login_events WHERE user_id = ? AND ip = ? LIMIT 1",
                (admin["id"], ip)).fetchone():
            alerts.send(
                "⚠️ Admin akce z NEZNAME IP",
                detail=f"{admin['username']} -> {action}\ntarget={target[:160]}\nip={ip}\n"
                       f"(IP neni v historii prihlaseni ani v KNOWN_ADMIN_IPS – mozny unos uctu!)",
                key=f"admin-newip:{admin['id']}:{ip}",
                cooldown=600,
                ping=True,
            )


def self_excluded_until(row):
    """ISO konec aktivního sebevyloučení ze sázek, "permanent", nebo None (neaktivní/vypršelo)."""
    try:
        v = row["gamble_block_until"]
    except (KeyError, IndexError, TypeError):
        return None
    if not v:
        return None
    if v == "permanent":
        return "permanent"
    return v if v > now_iso() else None


def require_can_gamble(row) -> None:
    """Tipsport-style sebevyloučení: vyhodí 403, když má user aktivní zámek. Volat na začátku
    KAŽDÉHO sázkového endpointu (duely, piškvorky, blackjack, predikce)."""
    until = self_excluded_until(row)
    if not until:
        return
    if until == "permanent":
        raise HTTPException(status_code=403,
                            detail="Máš nastavené TRVALÉ sebevyloučení ze sázek 🔒 Pro zrušení napiš adminovi.")
    raise HTTPException(status_code=403,
                        detail=f"Máš aktivní sebevyloučení ze sázek do {until[:16].replace('T', ' ')} 🔒 Do té doby nejde sázet.")


def check_wager_limit(conn: sqlite3.Connection, user, amount: int) -> None:
    """Responsible gaming – denní limit sázek (Tipsport-style). Volat PŘED debetem sázky na
    KAŽDÉM herním endpointu (mines/predikce/duely/piškvorky/blackjack). Resetuje dnešní součet
    na nový den (a aplikuje ODLOŽENÉ navýšení limitu), zkontroluje strop a započte tuto sázku.
    Vyhodí 403, když by sázka překročila denní limit. 0/NULL limit = bez omezení."""
    from .db import local_date
    uid = user["id"]
    today = local_date()
    row = conn.execute(
        "SELECT wager_limit, wager_limit_pending, wagered_today, wager_day FROM users WHERE id = ?",
        (uid,)).fetchone()
    if row is None:
        return
    limit = row["wager_limit"]
    wagered = row["wagered_today"] or 0
    if row["wager_day"] != today:                      # nový den → reset + aplikuj odložené navýšení
        if row["wager_limit_pending"] is not None:
            limit = row["wager_limit_pending"]
        wagered = 0
        conn.execute("UPDATE users SET wager_day = ?, wagered_today = 0, wager_limit = ?, "
                     "wager_limit_pending = NULL WHERE id = ?", (today, limit, uid))
    if limit and limit > 0 and wagered + amount > limit:
        left = max(0, limit - wagered)
        raise HTTPException(status_code=403,
                            detail=f"Denní limit sázek {limit} sedláků – dnes zbývá {left}. 🛑 "
                                   f"Pro dnešek dost, vrať se zítra.")
    conn.execute("UPDATE users SET wagered_today = COALESCE(wagered_today, 0) + ? WHERE id = ?",
                 (amount, uid))


def to_public(row: sqlite3.Row, include_email: bool = False) -> dict:
    """Veřejná podoba uživatele (bez hesla)."""
    data = {
        "id": row["id"],
        "username": row["username"],
        "kick_username": row["kick_username"],
        "points": row["points"],
        "role": row["role"],
        "avatar_url": row["avatar_url"],
        "banned": bool(row["banned"]),
        "created_at": row["created_at"],
        "steam_trade_url": (row["steam_trade_url"] if "steam_trade_url" in row.keys() else None),
        "is_sub": bool(row["is_sub"]) if "is_sub" in row.keys() else False,
        "is_vip": bool(row["is_vip"]) if "is_vip" in row.keys() else False,
        "is_og": bool(row["is_og"]) if "is_og" in row.keys() else False,
    }
    _et = row["earned_total"] if "earned_total" in row.keys() else 0
    _li = level_info(_et)
    data["earned_total"] = _et
    data["level"] = _li["level"]
    data["level_pct"] = _li["pct"]
    data["level_into"] = _li["into"]
    data["level_span"] = _li["span"]
    try:
        from . import cosmetics
        data["cos"] = cosmetics.resolve(row)
    except Exception:
        data["cos"] = {"name": "", "frame": "", "banner": ""}
    data["gamble_block_until"] = self_excluded_until(row)   # Tipsport-style sebevyloučení (None = může sázet)
    if include_email:
        data["email"] = row["email"]
        data["ban_reason"] = row["ban_reason"]
    return data


def get_current_user(request: Request,
                     conn: sqlite3.Connection = Depends(db_dep)) -> Optional[sqlite3.Row]:
    """Vrátí přihlášeného uživatele podle session cookie, nebo None (host)."""
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    sess = conn.execute(
        "SELECT * FROM sessions WHERE token = ?", (token,)
    ).fetchone()
    if not sess:
        return None
    if sess["expires_at"] < now_iso():
        conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
        conn.commit()
        return None
    # průběžně aktualizuj „naposledy viděn" + IP relace, ale jen občas (ne při KAŽDÉM requestu) –
    # jinak je to zápis+commit na hot-path a při náporu (polling z mnoha tabů) to zbytečně
    # serializuje zápisy do SQLite. Stačí ~1× za SESSION_TOUCH_SEC na session.
    last_seen = sess["last_seen"] if "last_seen" in sess.keys() else None
    touch_threshold = (datetime.now(timezone.utc) - timedelta(seconds=SESSION_TOUCH_SEC)).isoformat()
    if not last_seen or last_seen < touch_threshold:
        conn.execute(
            "UPDATE sessions SET last_seen = ?, ip = COALESCE(ip, ?), user_agent = COALESCE(user_agent, ?) WHERE token = ?",
            (now_iso(), client_ip(request), (request.headers.get("user-agent") or "")[:300], token),
        )
        conn.commit()
    return conn.execute(
        "SELECT * FROM users WHERE id = ?", (sess["user_id"],)
    ).fetchone()


def require_user(user: Optional[sqlite3.Row] = Depends(get_current_user)) -> sqlite3.Row:
    """Vyžaduje přihlášení (a blokuje zabanované účty)."""
    if user is None:
        raise HTTPException(status_code=401, detail="Pro tuto akci se musíš přihlásit.")
    if user["banned"] and user["role"] != ROLE_ADMIN:
        raise HTTPException(status_code=403,
                            detail="Tvůj účet byl zablokován (anticheat). Kontaktuj streamera.")
    return user


def require_admin(user: sqlite3.Row = Depends(require_user)) -> sqlite3.Row:
    """Vyžaduje roli admin."""
    if user["role"] != ROLE_ADMIN:
        raise HTTPException(status_code=403, detail="Nemáš oprávnění správce.")
    return user


def require_staff(user: sqlite3.Row = Depends(require_user)) -> sqlite3.Row:
    """Vyžaduje staff roli (admin / broadcaster / moderátor)."""
    if user["role"] not in STAFF_ROLES:
        raise HTTPException(status_code=403, detail="Nemáš přístup do administrace.")
    return user


def require_broadcaster(user: sqlite3.Row = Depends(require_user)) -> sqlite3.Row:
    """Broadcaster nebo admin (NE moderátor). Pro citlivější provozní akce: ban, import."""
    if user["role"] not in (ROLE_BROADCASTER, ROLE_ADMIN):
        raise HTTPException(status_code=403, detail="Tahle akce je jen pro broadcastera nebo admina.")
    return user


def can_access(role: str, section: str) -> bool:
    """Smí daná role na danou admin sekci? (admin vždy ano)"""
    return role == ROLE_ADMIN or role in ADMIN_SECTIONS.get(section, ())


# Mapování cesty požadavku → sekce admin panelu (nejspecifičtější prefixy dřív)
_SECTION_BY_PREFIX = [
    ("/api/admin/stats", "stats"),
    ("/api/admin/economy", "economy"),
    ("/api/admin/products", "products"),
    ("/api/admin/users", "users"),
    ("/api/admin/import", "users"),
    ("/api/admin/export/orders", "orders"),
    ("/api/admin/export/audit", "security"),
    ("/api/admin/order", "orders"),          # pokryje /orders i /order-products
    ("/api/admin/raffle", "raffles"),
    ("/api/admin/codes", "codes"),
    ("/api/admin/gift-requests", "gifts"),
    ("/api/admin/security", "security"),
    ("/api/admin/backup", "security"),
    ("/api/admin/drops", "drops"),
    ("/api/admin/games", "games"),
    ("/api/admin/bot", "bot"),
    ("/api/admin/news", "news"),
]


def admin_guard(request: Request, user: sqlite3.Row = Depends(require_user)) -> sqlite3.Row:
    """Jediná stráž administrace: musí být staff a mít právo na sekci dle cesty.

    Vynucuje se na úrovni routeru → platí pro KAŽDÝ admin endpoint, nejde obejít přímým API.
    """
    if user["role"] not in STAFF_ROLES:
        raise HTTPException(status_code=403, detail="Nemáš přístup do administrace.")
    if user["role"] == ROLE_ADMIN:
        return user
    section = next((sec for pre, sec in _SECTION_BY_PREFIX if request.url.path.startswith(pre)), None)
    if section is None or not can_access(user["role"], section):
        raise HTTPException(status_code=403, detail="Na tuhle sekci nemáš oprávnění (jen admin).")
    return user


XP_DIV = 900   # earned_total → level: level = 1 + floor(sqrt(earned_total / XP_DIV)). 900 (zvýšeno z 300, 3× pomalejší levelování – aby se nelevelovalo moc rychle; lvl 100 ≈ 8,8 mil XP)


def level_info(earned_total) -> dict:
    """Level + progress v levelu z celkově nafarmeného (earned_total). Level nikdy neklesá."""
    import math
    e = max(0, int(earned_total or 0))
    level = 1 + int(math.floor((e / XP_DIV) ** 0.5))
    cur_at = XP_DIV * (level - 1) ** 2
    next_at = XP_DIV * level ** 2
    span = next_at - cur_at
    into = e - cur_at
    pct = int(round(into * 100 / span)) if span > 0 else 0
    return {"level": level, "into": into, "span": span, "next_at": next_at, "pct": max(0, min(100, pct))}


# Kolik z kladného přírůstku jde do earned_total (lifetime XP → level / Battle Pass):
#  • gambling/vratky/storna = 0 % (level se nedá vygamblit)
#  • placené/gift suby = 50 % (přispěvatel má NÁSKOK, ale lvl 100 = ~5 880 subů → koupit nejde)
#  • poctivé FARMENÍ (sledování, chat, denní, kolo, úkoly, sklizeň, drops, partneři…) = 100 %
# points (zůstatek) se mění vždy plně; tohle filtruje JEN lifetime XP. Forward-only (staré
# earned_total se nemění). Soft denylist (nezachycený reason počítá plně – nerozbije farmení).
_NO_EARN_KW = ("mines", "blackjack", "piškvor", "duel", "hra #", "predikce", "výhra",
               "vrácen", "vráceno", "remíza", "vypršel", "refund", "storno", "zrušen",
               "odchod (vrácení")
_SUB_EARN_KW = ("kick sub", "kick resub", "gift sub")   # placené/gift suby → jen část XP
SUB_EARN_FACTOR = 0.5                                    # sub dá 50 % hodnoty jako XP (náskok, ne koupený level)


def earn_factor(reason: str) -> float:
    """Podíl kladného přírůstku do earned_total (XP). 0.0 = gambling/vratky, 0.5 = placené/gift
    suby (náskok přispěvatelů), 1.0 = poctivé farmení. Pořadí: nejdřív denylist (vratka subu = 0)."""
    r = (reason or "").lower()
    if any(k in r for k in _NO_EARN_KW):
        return 0.0
    if any(k in r for k in _SUB_EARN_KW):
        return SUB_EARN_FACTOR
    return 1.0


def counts_as_earned(reason: str) -> bool:
    """True = přírůstek dává aspoň část XP (False jen pro gambling/vratky). Zpětná kompatibilita."""
    return earn_factor(reason) > 0


def add_points(conn: sqlite3.Connection, user_id: int, change: int, reason: str, *, xp: bool = True) -> None:
    """Změní body uživatele a zapíše záznam do points_log. Kladný přírůstek navíc naskládá do
    earned_total (lifetime XP – nikdy neklesá): farmení 100 %, suby 50 %, gambling/vratky 0 %.
    xp=False → přírůstek se do earned_total NEpočítá vůbec (admin granty: body ano, level NE)."""
    earn = int(round(max(0, change) * earn_factor(reason))) if xp else 0
    conn.execute("UPDATE users SET points = points + ?, earned_total = earned_total + ? WHERE id = ?",
                 (change, earn, user_id))
    conn.execute(
        "INSERT INTO points_log (user_id, change, reason, created_at) VALUES (?, ?, ?, ?)",
        (user_id, change, reason, now_iso()),
    )


def notify(conn: sqlite3.Connection, user_id: int, icon: str, title: str,
           body: str = "", link: str = "") -> None:
    """Vloží in-app notifikaci uživateli (zvoneček v hlavičce). Necommituje – commituje caller.

    Drží max 50 nejnovějších na uživatele, ať tabulka neroste donekonečna."""
    conn.execute(
        "INSERT INTO notifications (user_id, icon, title, body, link, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (user_id, icon, title[:120], (body or "")[:300], (link or "")[:80], now_iso()),
    )
    conn.execute(
        "DELETE FROM notifications WHERE user_id = ? AND id NOT IN "
        "(SELECT id FROM notifications WHERE user_id = ? ORDER BY id DESC LIMIT 50)",
        (user_id, user_id),
    )


def user_rank(conn: sqlite3.Connection, points: int, username: str) -> int:
    """Pozice uživatele v žebříčku (1 = nejvíc sedláků). Stejné řazení jako leaderboard
    (points DESC, username ASC), takže rank == pozice na leaderboardu."""
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM users WHERE points > ? OR (points = ? AND username < ?)",
        (points or 0, points or 0, username or ""),
    ).fetchone()
    return (row["c"] if row else 0) + 1


# Tituly (ligy) podle POZICE na leaderboardu → (max_rank, klíč, násobič denního streaku).
# Mimo TOP 100 = bez titulu a bez bonusu (×1).
TIER_BY_RANK = ((3, "unreal", 10), (10, "elite", 5), (30, "gold", 3), (50, "silver", 2), (100, "bronze", 2))


def tier_for_rank(rank: int):
    """(klíč_ligy, násobič) podle pozice. rank > 100 → ('', 1)."""
    for max_rank, key, mult in TIER_BY_RANK:
        if rank <= max_rank:
            return key, mult
    return "", 1


def try_debit(conn: sqlite3.Connection, user_id: int, amount: int, reason: str) -> bool:
    """Atomicky odečte body JEN když jich má uživatel dost. Vrátí True/False.

    Odečet je v jednom UPDATE s podmínkou `points >= amount`, takže ani při souběhu
    dvou requestů nejde zůstatek do mínusu ani se neodečte dvakrát. Necommituje (volá caller).
    """
    if amount <= 0:
        return True
    cur = conn.execute(
        "UPDATE users SET points = points - ? WHERE id = ? AND points >= ?",
        (amount, user_id, amount),
    )
    if cur.rowcount == 0:
        return False
    conn.execute(
        "INSERT INTO points_log (user_id, change, reason, created_at) VALUES (?, ?, ?, ?)",
        (user_id, -amount, reason, now_iso()),
    )
    return True
