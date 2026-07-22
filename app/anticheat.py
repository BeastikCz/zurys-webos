"""Risk-score anticheat: kombinuje signály z DB + requestu, blokuje v reálném čase, loguje.

Filozofie: web nelze udělat 100% neprůstřelně (klient je pod kontrolou útočníka),
ale lze zdražit cheating tak, aby se ekonomicky nevyplatil. Tenhle modul:

- vyhodnotí 0–100 risk score pro daného uživatele + požadavek,
- nad prahem blokuje akci (403),
- zápis do `admin_audit` (action = `anticheat.block`) — admin to vidí.

Admin role = vždy skóre 0 (nikdy nezablokujeme provozovatele).
"""
import ipaddress
import json
import os
import sqlite3
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import HTTPException, Request

from .config import ROLE_ADMIN, DATACENTER_CIDRS, TRUSTED_IPS
from .db import now_iso, get_setting
from .deps import client_ip
from . import iprep, alerts

# (block_threshold, soft_threshold) podle kontextu
THRESHOLDS = {
    "claim":    (60, 35),   # drop claim — citlivé
    "redeem":   (60, 35),
    "purchase": (50, 30),   # nákup — přísnější (přijde o body)
}

# Cooldowny pro nové účty (první 24 h)
NEW_ACCOUNT_HOURS = 24
NEW_ACCOUNT_MAX_CLAIMS = 3
NEW_ACCOUNT_MAX_REDEEM_PTS = 100

# Minimální stáří účtu (h), než smí posílat dary v Exchange. Brání funnelu přes alty:
# založ účet na čisté IP → pošli body na hlavní účet DŘÍV, než se stihne propojit otisk
# zařízení. Práh nutí počkat – a za tu dobu se alt skoro vždy propojí (chytí _shared_identity).
GIFT_MIN_AGE_HOURS = 48

# Cross-drop cooldown per zařízení (proti přepínání účtů)
FP_DROP_COOLDOWN_SEC = 30

# Form timing — claim/redeem rychleji než MIN_FORM_MS = bot
MIN_FORM_MS = 200
AUTOMATION_WINDOW_DAYS = 7
AUTOMATION_BATCH_SECONDS = 15
AUTOMATION_CHALLENGE_SCORE = 50
AUTOMATION_VERIFIED_HOURS = 24
TURNSTILE_ACTION = "farm_checkpoint"
TURNSTILE_SITE_KEY = os.environ.get("WEBOS_TURNSTILE_SITE_KEY", "").strip()
TURNSTILE_SECRET_KEY = os.environ.get("WEBOS_TURNSTILE_SECRET_KEY", "").strip()
TURNSTILE_HOSTNAME = os.environ.get("WEBOS_TURNSTILE_HOSTNAME", "zurys.live").strip()
TURNSTILE_VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
MAX_FORM_MS_DRIFT = 7 * 24 * 3600 * 1000  # >7 dní = drift / podvod s časem


# Předkompilované sítě datacenter / VPN rozsahů
_DC_NETS = []
for _c in DATACENTER_CIDRS:
    try:
        _DC_NETS.append(ipaddress.ip_network(_c))
    except ValueError:
        pass


def _is_datacenter(ip: str) -> bool:
    try:
        return any(ipaddress.ip_address(ip) in n for n in _DC_NETS)
    except (ValueError, TypeError):
        return False


def _rule_enabled(conn: sqlite3.Connection, key: str) -> bool:
    """Pravidlo je zapnuté? Default = ano (kdyby chyběl řádek v anticheat_rules)."""
    r = conn.execute("SELECT enabled FROM anticheat_rules WHERE key = ?", (key,)).fetchone()
    return bool(r["enabled"]) if r else True


def _last_signal(conn: sqlite3.Connection, user_id: int):
    """Vrátí (fp_hash, webdriver) z nejnovějšího klientského signálu uživatele."""
    r = conn.execute(
        "SELECT fp_hash, webdriver FROM client_signals WHERE user_id = ? ORDER BY id DESC LIMIT 1",
        (user_id,),
    ).fetchone()
    if not r:
        return None, False
    return r["fp_hash"], bool(r["webdriver"])


def _account_age_hours(user) -> float:
    try:
        created = datetime.fromisoformat(user["created_at"])
        return (datetime.now(timezone.utc) - created).total_seconds() / 3600
    except (ValueError, TypeError):
        return 9999.0


def is_new_account(user, hours: int = NEW_ACCOUNT_HOURS) -> bool:
    return _account_age_hours(user) < hours


def fp_drop_cooldown_remaining(conn, fp_hash: str,
                                ttl_sec: int = FP_DROP_COOLDOWN_SEC) -> int:
    """Kolik sekund zbývá do uplynutí cross-drop cooldownu pro toto zařízení. 0 = volno."""
    if not fp_hash:
        return 0
    last = conn.execute(
        "SELECT created_at FROM drop_claims WHERE fp_hash = ? ORDER BY id DESC LIMIT 1",
        (fp_hash,),
    ).fetchone()
    if not last:
        return 0
    try:
        elapsed = (datetime.now(timezone.utc) - datetime.fromisoformat(last["created_at"])).total_seconds()
    except (ValueError, TypeError):
        return 0
    remaining = ttl_sec - elapsed
    return max(0, int(remaining)) if remaining > 0 else 0


def new_account_drop_count(conn, user_id: int, hours: int = NEW_ACCOUNT_HOURS) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    return conn.execute(
        "SELECT COUNT(*) AS c FROM drop_claims WHERE user_id = ? AND created_at >= ?",
        (user_id, cutoff),
    ).fetchone()["c"]


def new_account_redeem_pts(conn, user_id: int, hours: int = NEW_ACCOUNT_HOURS) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    r = conn.execute(
        "SELECT COALESCE(SUM(change), 0) AS s FROM points_log "
        "WHERE user_id = ? AND change > 0 AND reason LIKE 'Redeem kód%' AND created_at >= ?",
        (user_id, cutoff),
    ).fetchone()
    return r["s"] or 0


def evaluate_risk(conn: sqlite3.Connection, user, request: Request,
                  context: str = "claim",
                  t0_ms: Optional[int] = None) -> dict:
    """Spočítá risk score 0–100 a důvody. Pro admina vždy 0.

    Vrací: {score, reasons, block, soft, ip, fp_hash, webdriver}
    """
    ip = client_ip(request)

    # Důvěryhodná IP: suppress ONLY multi-account signal (it's noisy on shared IPs).
    # Still evaluate device, automation, new-account, and timing signals.
    skip_multi_account = ip in TRUSTED_IPS

    # Admin nikdy blokován
    if user and user["role"] == ROLE_ADMIN:
        return {"score": 0, "reasons": [], "block": False, "soft": False,
                "ip": ip, "fp_hash": None, "webdriver": False}

    fp_hash, webdriver = _last_signal(conn, user["id"])
    reasons = []
    score = 0

    # Kritické: zařízení zabanované → 100 (instant block).
    # VYPNUTO defaultně (fp_ban_enforce != "1") – hrubý otisk dává falešné bany cizím lidem
    # se stejným modelem/prohlížečem. Zapne se až bude přesnější fingerprint. Revert: setting "1".
    if fp_hash and get_setting(conn, "fp_ban_enforce", "0") == "1":
        if conn.execute("SELECT 1 FROM fingerprint_bans WHERE fp_hash = ?",
                        (fp_hash,)).fetchone():
            return {"score": 100, "reasons": ["zařízení zabanováno"],
                    "block": True, "soft": False, "ip": ip,
                    "fp_hash": fp_hash, "webdriver": webdriver}

    # Form timing (rychleji než MIN_FORM_MS = bot)
    if t0_ms and t0_ms > 0:
        try:
            now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
            delta = now_ms - int(t0_ms)
            if 0 < delta < MIN_FORM_MS:
                score += 40    # sníženo ze 70: paste/autofill legit userů → samo neblokuje (40<60), nutný 2. signál
                reasons.append(f"form za {delta} ms (bot)")
            elif delta < -60_000 or delta > MAX_FORM_MS_DRIFT:
                score += 30
                reasons.append("podezřelý čas na klientovi")
        except (ValueError, TypeError):
            pass

    # Nový účet
    age = _account_age_hours(user)
    if age < 1:
        score += 30; reasons.append("nový účet <1 h")
    elif age < 24:
        score += 15; reasons.append("nový účet <24 h")

    # Sdílená IP s jinými účty (skip on trusted IPs – too noisy for NAT/office networks)
    if not skip_multi_account and _rule_enabled(conn, "multi_account"):
        shared = conn.execute(
            "SELECT COUNT(DISTINCT user_id) AS c FROM login_events "
            "WHERE ip = ? AND user_id <> ? AND ip IS NOT NULL AND ip <> ''",
            (ip, user["id"]),
        ).fetchone()["c"]
        if shared >= 3:
            score += 50; reasons.append(f"sdílená IP ({shared} jiných účtů)")
        elif shared >= 1:
            score += 20; reasons.append(f"sdílená IP ({shared} jiných účtů)")

    # VPN / datacenter / proxy
    if _rule_enabled(conn, "vpn_proxy"):
        if _is_datacenter(ip):
            score += 60; reasons.append("VPN / datacenter IP")
        elif iprep.is_vpn(ip):   # proxycheck.io (jen když je nastaven PROXYCHECK_KEY)
            score += 60; reasons.append("VPN / proxy (proxycheck)")

    # Rapid-fire nákupy/akce
    if _rule_enabled(conn, "rapid_fire"):
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        rapid = conn.execute(
            "SELECT COUNT(*) AS c FROM orders WHERE user_id = ? AND created_at >= ?",
            (user["id"], cutoff),
        ).fetchone()["c"]
        if rapid >= 10:
            score += 50; reasons.append(f"{rapid} nákupů/5 min")
        elif rapid >= 5:
            score += 20; reasons.append(f"{rapid} nákupů/5 min")

    # Hodně různých IP u jednoho účtu V KRÁTKÉM OKNĚ (24 h). Dřív se počítaly IP za CELOU historii
    # → dynamická/mobilní IP (O2 rotuje po dnech) se nasčítala a dělala false positive na legit
    # userech. 5 různých IP za 24 h = VPN-hopping / přepínání altů; poctivý user má 1–2 IP/den.
    ip_cut = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    ip_count = conn.execute(
        "SELECT COUNT(DISTINCT ip) AS c FROM login_events "
        "WHERE user_id = ? AND ip IS NOT NULL AND ip <> '' AND created_at >= ?",
        (user["id"], ip_cut),
    ).fetchone()["c"]
    if ip_count >= 5:
        score += 25; reasons.append(f"účet z {ip_count} IP/24h")
    elif ip_count >= 3:
        score += 10; reasons.append(f"účet z {ip_count} IP/24h")

    score = min(score, 100)
    block_t, soft_t = THRESHOLDS.get(context, (60, 35))
    return {
        "score": score,
        "reasons": reasons,
        "block": score >= block_t,
        "soft": soft_t <= score < block_t,
        "ip": ip,
        "fp_hash": fp_hash,
        "webdriver": webdriver,
    }


def record_block(conn, user, request: Request, score: int, reasons: list, context: str) -> None:
    """Zápis do admin_audit (actor = '(anticheat)'). Volá se PŘED commitem."""
    conn.execute(
        "INSERT INTO admin_audit (admin_id, admin_name, action, target, details, ip, created_at) "
        "VALUES (NULL, '(anticheat)', 'anticheat.block', ?, ?, ?, ?)",
        (
            f"{context} #{user['id']} {user['username']}",
            (f"skóre {score}: " + ", ".join(reasons[:5]))[:400],
            client_ip(request),
            now_iso(),
        ),
    )
    ip = client_ip(request)
    alerts.send(
        "Anticheat zablokoval akci",
        detail=f"{context} #{user['id']} {user['username']}\nskore={score}\n{', '.join(reasons[:5])}\nip={ip}",
        key=f"ac-block:{context}:{ip}",      # dedup per IP (ne user_id) → alt rotace = jeden alert/IP, ne záplava
        cooldown=900,                         # 15 min (bylo 180s) – tlumí opakované pokusy z téže IP
        ping=False,                           # bez @everyone (rutinní auto-blok, anticheat to řeší sám)
    )


def check_or_block(conn, user, request: Request, context: str = "claim",
                   t0_ms: Optional[int] = None,
                   block_msg: str = "Akce zablokována ochranou proti zneužití.") -> dict:
    """evaluate_risk + případná blokace (HTTP 403) + zápis do auditu.

    Vrací risk dict pro pozdější použití (fp_hash, ip…). Nezablokuje admina.
    """
    risk = evaluate_risk(conn, user, request, context, t0_ms)
    if risk["block"]:
        record_block(conn, user, request, risk["score"], risk["reasons"], context)
        conn.commit()  # blok musí být uložen i když dál vyhodíme HTTPException
        raise HTTPException(
            status_code=403,
            detail=f"{block_msg} (skóre {risk['score']}: {', '.join(risk['reasons'][:3])})",
        )
    return risk


def _parse_utc(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def record_automation_event(conn: sqlite3.Connection, user_id: int, source: str,
                            item_key: str, ready_at: str, acted_at: Optional[str] = None) -> None:
    """Store a successful mature garden/farm reward in the caller's transaction."""
    acted_at = acted_at or now_iso()
    try:
        reaction_ms = max(
            0,
            round((_parse_utc(acted_at) - _parse_utc(ready_at)).total_seconds() * 1000),
        )
    except (TypeError, ValueError):
        return
    conn.execute(
        "INSERT INTO automation_events (user_id,source,item_key,ready_at,acted_at,reaction_ms) "
        "VALUES (?,?,?,?,?,?)",
        (user_id, source, item_key, ready_at, acted_at, reaction_ms),
    )


def _related_accounts(conn: sqlite3.Connection, user_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT DISTINCT u.id,u.username FROM login_events a "
        "JOIN login_events b ON b.ip=a.ip AND b.user_id<>a.user_id "
        "JOIN users u ON u.id=b.user_id "
        "WHERE a.user_id=? AND a.ip IS NOT NULL AND a.ip!='' "
        "ORDER BY lower(u.username)",
        (user_id,),
    )
    return [{"id": row["id"], "username": row["username"], "via": ["ip"]} for row in rows]


def automation_report(conn: sqlite3.Connection, days: int = AUTOMATION_WINDOW_DAYS,
                      limit: int = 50, now: Optional[datetime] = None,
                      user_id: Optional[int] = None) -> dict:
    """Build an evidence-only automation report. It never blocks or changes a user."""
    now = now or datetime.now(timezone.utc)
    now = now.replace(tzinfo=timezone.utc) if now.tzinfo is None else now.astimezone(timezone.utc)
    window_days = max(1, min(days, 30))
    cutoff = (now - timedelta(days=window_days)).isoformat()
    user_filter = " AND e.user_id=?" if user_id is not None else ""
    params = (cutoff, user_id) if user_id is not None else (cutoff,)
    rows = conn.execute(
        "SELECT e.*,u.username,u.role,u.points,u.banned FROM automation_events e "
        f"JOIN users u ON u.id=e.user_id WHERE e.acted_at>=?{user_filter} "
        "ORDER BY e.user_id,e.source,e.acted_at,e.id",
        params,
    ).fetchall()

    grouped: dict[tuple[int, str], list] = {}
    users: dict[int, dict] = {}
    for row in rows:
        grouped.setdefault((row["user_id"], row["source"]), []).append(row)
        users[row["user_id"]] = {
            "id": row["user_id"],
            "username": row["username"],
            "role": row["role"],
            "points": row["points"],
            "banned": row["banned"],
        }

    user_batches: dict[int, list[dict]] = {user_id: [] for user_id in users}
    for (user_id, source), events in grouped.items():
        batch = None
        for event in events:
            acted = _parse_utc(event["acted_at"])
            if batch is None or (acted - batch["last_dt"]).total_seconds() > AUTOMATION_BATCH_SECONDS:
                batch = {
                    "source": source,
                    "acted_at": event["acted_at"],
                    "acted_dt": acted,
                    "last_dt": acted,
                    "reaction_ms": event["reaction_ms"],
                    "units": 1,
                    "items": {event["item_key"]} if event["item_key"] else set(),
                }
                user_batches[user_id].append(batch)
            else:
                batch["last_dt"] = acted
                batch["reaction_ms"] = min(batch["reaction_ms"], event["reaction_ms"])
                batch["units"] += 1
                if event["item_key"]:
                    batch["items"].add(event["item_key"])

    result = []
    for user_id, batches in user_batches.items():
        batches.sort(key=lambda batch: batch["acted_dt"])
        fast = sum(batch["reaction_ms"] <= 5_000 for batch in batches)
        fast15 = sum(batch["reaction_ms"] <= 15_000 for batch in batches)
        score, reasons = 0, []
        ratio = fast / len(batches) if batches else 0
        if fast >= 6 and ratio >= 0.60:
            score += 45
            reasons.append({
                "code": "fast_reactions",
                "weight": 45,
                "label": f"{fast}/{len(batches)} reakcí do 5 s",
            })
        elif fast >= 3 and ratio >= 0.50:
            score += 25
            reasons.append({
                "code": "fast_reactions",
                "weight": 25,
                "label": f"{fast}/{len(batches)} reakcí do 5 s",
            })

        max_gap_h = None
        span_h = 0.0
        if len(batches) >= 2:
            gaps = [
                (batches[index]["acted_dt"] - batches[index - 1]["acted_dt"]).total_seconds() / 3600
                for index in range(1, len(batches))
            ]
            max_gap_h = max(gaps)
            span_h = (batches[-1]["acted_dt"] - batches[0]["acted_dt"]).total_seconds() / 3600
        if len(batches) >= 10 and span_h >= 24 and max_gap_h is not None:
            if max_gap_h < 4:
                score += 35
                reasons.append({
                    "code": "no_sleep",
                    "weight": 35,
                    "label": f"bez pauzy delší než {max_gap_h:.1f} h",
                })
            elif max_gap_h < 6:
                score += 20
                reasons.append({
                    "code": "no_sleep",
                    "weight": 20,
                    "label": f"nejdelší pauza jen {max_gap_h:.1f} h",
                })

        score = min(score, 100)
        recent = [{
            "source": batch["source"],
            "acted_at": batch["acted_at"],
            "reaction_ms": batch["reaction_ms"],
            "units": batch["units"],
            "items": sorted(batch["items"]),
        } for batch in reversed(batches[-6:])]
        result.append({
            "user": users[user_id],
            "score": score,
            "level": "high" if score >= 50 else "watch" if score >= 25 else "ok",
            "reasons": reasons,
            "batches": len(batches),
            "events": sum(batch["units"] for batch in batches),
            "fast_5s": fast,
            "fast_15s": fast15,
            "max_gap_hours": round(max_gap_h, 2) if max_gap_h is not None else None,
            "last_action": batches[-1]["acted_at"] if batches else None,
            "recent": recent,
        })

    for item in result:
        item["related_accounts"] = _related_accounts(conn, item["user"]["id"])
        related = item["related_accounts"]
        if item["score"] > 0 and len(related) >= 3:
            item["score"] = min(100, item["score"] + 20)
            item["reasons"].append({
                "code": "linked_accounts",
                "weight": 20,
                "label": f"stejnou IP používá {len(related) + 1} účtů",
            })
            item["level"] = "high" if item["score"] >= AUTOMATION_CHALLENGE_SCORE else "watch"
    result.sort(key=lambda item: (item["score"], item["last_action"] or ""), reverse=True)
    result = result[:max(1, min(limit, 100))]
    since = conn.execute("SELECT MIN(acted_at) AS value FROM automation_events").fetchone()["value"]
    return {
        "mode": "audit-only",
        "window_days": window_days,
        "since": since,
        "events": len(rows),
        "accounts": len(users),
        "flagged": sum(item["score"] >= 25 for item in result),
        "users": result,
    }


def automation_risk(conn: sqlite3.Connection, user_id: int,
                    now: Optional[datetime] = None) -> dict:
    """Score one account cheaply enough for a reward-path checkpoint.

    Shared IP is supporting evidence only: it never creates a score by itself.
    """
    report = automation_report(conn, limit=1, now=now, user_id=user_id)
    if not report["users"]:
        return {"score": 0, "level": "ok", "reasons": [], "related_accounts": []}
    return report["users"][0]


def turnstile_enabled() -> bool:
    return bool(TURNSTILE_SITE_KEY and TURNSTILE_SECRET_KEY)


def automation_checkpoint(conn: sqlite3.Connection, user,
                          now: Optional[datetime] = None) -> dict:
    """Return checkpoint state without changing user or economy data."""
    now = now or datetime.now(timezone.utc)
    try:
        role = user["role"]
    except (KeyError, IndexError):
        role = None
    if role == ROLE_ADMIN or not turnstile_enabled():
        return {"required": False, "configured": turnstile_enabled(), "score": 0}
    risk = automation_risk(conn, user["id"], now)
    if risk["score"] < AUTOMATION_CHALLENGE_SCORE:
        return {"required": False, "configured": True, "score": risk["score"]}
    verified = conn.execute(
        "SELECT verified_until FROM automation_verifications WHERE user_id=?",
        (user["id"],),
    ).fetchone()
    if verified and verified["verified_until"] > now.isoformat():
        return {"required": False, "configured": True, "score": risk["score"],
                "verified_until": verified["verified_until"]}
    return {
        "required": True,
        "configured": True,
        "score": risk["score"],
        "site_key": TURNSTILE_SITE_KEY,
        "action": TURNSTILE_ACTION,
    }


def require_automation_checkpoint(conn: sqlite3.Connection, user) -> dict:
    checkpoint = automation_checkpoint(conn, user)
    if checkpoint["required"]:
        raise HTTPException(status_code=428, detail={
            "code": "automation_checkpoint",
            "message": "Nejdřív potvrď, že farmu ovládáš ty.",
            **checkpoint,
        })
    return checkpoint


def _turnstile_siteverify(token: str, remote_ip: str) -> dict:
    payload = urllib.parse.urlencode({
        "secret": TURNSTILE_SECRET_KEY,
        "response": token,
        "remoteip": remote_ip,
    }).encode("utf-8")
    request = urllib.request.Request(
        TURNSTILE_VERIFY_URL,
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception:
        return {"success": False, "error-codes": ["internal-error"]}


def complete_automation_checkpoint(conn: sqlite3.Connection, user, token: str,
                                   remote_ip: str) -> dict:
    state = automation_checkpoint(conn, user)
    if not state["required"]:
        return {"ok": True, "required": False, "verified_until": state.get("verified_until")}
    result = _turnstile_siteverify(token, remote_ip)
    valid = (
        result.get("success") is True
        and result.get("action") == TURNSTILE_ACTION
        and (not TURNSTILE_HOSTNAME or result.get("hostname") == TURNSTILE_HOSTNAME)
    )
    if not valid:
        raise HTTPException(status_code=400, detail="Kontrola se nepodařila. Zkus ji prosím znovu.")
    now = datetime.now(timezone.utc)
    until = (now + timedelta(hours=AUTOMATION_VERIFIED_HOURS)).isoformat()
    conn.execute(
        "INSERT INTO automation_verifications (user_id,verified_until,score,created_at) VALUES (?,?,?,?) "
        "ON CONFLICT(user_id) DO UPDATE SET verified_until=excluded.verified_until, "
        "score=excluded.score,created_at=excluded.created_at",
        (user["id"], until, state["score"], now.isoformat()),
    )
    conn.commit()
    return {"ok": True, "required": False, "verified_until": until}
