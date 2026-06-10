"""Risk-score anticheat: kombinuje signály z DB + requestu, blokuje v reálném čase, loguje.

Filozofie: web nelze udělat 100% neprůstřelně (klient je pod kontrolou útočníka),
ale lze zdražit cheating tak, aby se ekonomicky nevyplatil. Tenhle modul:

- vyhodnotí 0–100 risk score pro daného uživatele + požadavek,
- nad prahem blokuje akci (403),
- zápis do `admin_audit` (action = `anticheat.block`) — admin to vidí.

Admin role = vždy skóre 0 (nikdy nezablokujeme provozovatele).
"""
import ipaddress
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import HTTPException, Request

from .config import ROLE_ADMIN, DATACENTER_CIDRS, TRUSTED_IPS
from .db import now_iso
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

    # Důvěryhodná IP (NAT operátora / síť streamera apod.) → anticheat ji ignoruje:
    # skóre 0, nikdy neblokuje, žádný alert. fp_hash necháme dopočítat, ať dál
    # funguje cross-drop cooldown i logy. Spravováno v config.TRUSTED_IPS.
    if ip in TRUSTED_IPS:
        fp_hash, webdriver = _last_signal(conn, user["id"]) if user else (None, False)
        return {"score": 0, "reasons": [], "block": False, "soft": False,
                "ip": ip, "fp_hash": fp_hash, "webdriver": webdriver}

    # Admin nikdy blokován
    if user and user["role"] == ROLE_ADMIN:
        return {"score": 0, "reasons": [], "block": False, "soft": False,
                "ip": ip, "fp_hash": None, "webdriver": False}

    fp_hash, webdriver = _last_signal(conn, user["id"])
    reasons = []
    score = 0

    # Kritické: zařízení zabanované → 100 (instant block)
    if fp_hash:
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
                score += 70
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

    # Sdílená IP s jinými účty
    if _rule_enabled(conn, "multi_account"):
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

    # Hodně různých IP u jednoho účtu
    ip_count = conn.execute(
        "SELECT COUNT(DISTINCT ip) AS c FROM login_events "
        "WHERE user_id = ? AND ip IS NOT NULL AND ip <> ''",
        (user["id"],),
    ).fetchone()["c"]
    if ip_count >= 5:
        score += 25; reasons.append(f"účet z {ip_count} IP")

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
    alerts.send(
        "Anticheat zablokoval akci",
        detail=f"{context} #{user['id']} {user['username']}\nskore={score}\n{', '.join(reasons[:5])}\nip={client_ip(request)}",
        key=f"ac-block:{context}:{user['id']}",
        cooldown=180,
        ping=True,
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
