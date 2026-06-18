"""Farmářský Battle Pass: sezónní (měsíční) postupová dráha.

Tier = kolik XP (sedláků) hráč nafarmil OD ZAČÁTKU sezóny – počítá se DIFFEM
z celoživotního earned_total proti baseline (stejná filozofie jako questy).
Každý odemčený tier si hráč VYZVEDNE (claim) a dostane sedláky; milníkové tiery
(každý 5.) dávají víc. Reset = nový měsíc (nová sezóna → nový baseline). Server
ověří odemčení i při claimu (nevěří klientovi).
"""
import json
from datetime import datetime, timezone

from .db import now_iso, local_date, local_now, LOCAL_TZ

TIER_XP = 2500     # earned_total na 1 tier
N_TIERS = 20       # délka dráhy za sezónu


def _season() -> str:
    return local_date()[:7]   # 'YYYY-MM' dle českého času


def _season_start_iso() -> str:
    n = local_now()
    return datetime(n.year, n.month, 1, tzinfo=LOCAL_TZ).astimezone(timezone.utc).isoformat()


def tier_reward(tier: int) -> int:
    """Odměna za tier (milníky každý 5. tier dávají víc)."""
    return 800 if tier % 5 == 0 else 200


def premium_reward(tier: int) -> int:
    """Prémiová odměna (jen pro suby) – 3× základ. Motivace subnout."""
    return tier_reward(tier) * 3


def _is_premium(user) -> bool:
    """Sub (nebo admin) má prémiovou řadu odemčenou."""
    try:
        return bool(user["is_sub"]) or user["role"] == "admin"
    except (KeyError, IndexError, TypeError):
        return False


def _earned(conn, uid: int) -> int:
    r = conn.execute("SELECT earned_total FROM users WHERE id = ?", (uid,)).fetchone()
    return (r["earned_total"] if r else 0) or 0


def _season_earned(conn, uid: int) -> int:
    from .deps import earn_factor
    total = 0
    for r in conn.execute(
        "SELECT change, reason FROM points_log WHERE user_id = ? AND change > 0 AND created_at >= ?",
        (uid, _season_start_iso()),
    ):
        total += int(round((r["change"] or 0) * earn_factor(r["reason"] or "")))
    return max(0, total)


def _season_baseline(conn, uid: int) -> int:
    return max(0, _earned(conn, uid) - _season_earned(conn, uid))


def _row(conn, uid: int):
    """(baseline, claimed, claimed_premium) pro aktuální sezónu; při prvním přístupu řádek založí."""
    season = _season()
    row = conn.execute("SELECT baseline, claimed, claimed_premium FROM battlepass WHERE user_id = ? AND season = ?",
                       (uid, season)).fetchone()
    calc_base = _season_baseline(conn, uid)
    if row is None:
        conn.execute(
            "INSERT INTO battlepass (user_id, season, baseline, claimed, claimed_premium, created_at) "
            "VALUES (?, ?, ?, '[]', '[]', ?) ON CONFLICT(user_id, season) DO NOTHING",
            (uid, season, calc_base, now_iso()))
        conn.commit()
        return calc_base, [], []
    baseline = row["baseline"]
    if calc_base < baseline:
        conn.execute("UPDATE battlepass SET baseline = ? WHERE user_id = ? AND season = ?",
                     (calc_base, uid, season))
        conn.commit()
        baseline = calc_base
    return baseline, json.loads(row["claimed"] or "[]"), json.loads(row["claimed_premium"] or "[]")


def status(conn, user) -> dict:
    """Stav passu pro UI: aktuální tier, postup a seznam tierů s odměnami/stavem (free + premium)."""
    base, claimed, claimed_p = _row(conn, user["id"])
    xp = max(0, _earned(conn, user["id"]) - base)
    tier = min(N_TIERS, xp // TIER_XP)
    into = (xp - tier * TIER_XP) if tier < N_TIERS else TIER_XP
    pct = round(into * 100 / TIER_XP) if tier < N_TIERS else 100
    prem = _is_premium(user)
    tiers = [{"tier": t, "reward": tier_reward(t), "premium_reward": premium_reward(t),
              "reached": t <= tier, "claimed": t in claimed, "premium_claimed": t in claimed_p,
              "milestone": t % 5 == 0} for t in range(1, N_TIERS + 1)]
    return {"season": _season(), "tier": int(tier), "max_tier": N_TIERS, "xp": int(xp),
            "tier_xp": TIER_XP, "pct": pct, "into": int(into), "tiers": tiers, "is_premium": prem,
            "claimable": sum(1 for t in tiers if t["reached"] and not t["claimed"]),
            "claimable_premium": sum(1 for t in tiers if prem and t["reached"] and not t["premium_claimed"])}


def claim(conn, user, tier: int, premium: bool = False) -> dict:
    """Vyzvedne odměnu za odemčený a dosud nevyzvednutý tier (free, nebo premium = jen sub).
    Idempotentní přes claimed / claimed_premium. Server ověří odemčení i sub status."""
    from .deps import add_points
    season = _season()
    base, claimed, claimed_p = _row(conn, user["id"])
    reached = max(0, _earned(conn, user["id"]) - base) // TIER_XP
    if tier < 1 or tier > N_TIERS:
        return {"ok": False, "error": "Neplatný tier."}
    if tier > reached:
        return {"ok": False, "error": "Tenhle tier ještě nemáš odemčený. 💪"}
    if premium:
        if not _is_premium(user):
            return {"ok": False, "error": "Prémiová řada je jen pro suby. 💜"}
        if tier in claimed_p:
            return {"ok": False, "error": "Prémiový tier už máš vyzvednutý. 🎁"}
        old_p = json.dumps(sorted(claimed_p))     # CAS: stav blobu PŘED zápisem
        claimed_p.append(tier)
        # Odměnu připíše jen když claimed_premium je pořád old_p (nikdo souběžně neclaimoval) → no double-claim.
        if conn.execute("UPDATE battlepass SET claimed_premium = ? WHERE user_id = ? AND season = ? AND claimed_premium = ?",
                        (json.dumps(sorted(claimed_p)), user["id"], season, old_p)).rowcount != 1:
            conn.commit()
            return {"ok": False, "error": "Souběh – zkus to za chvíli znovu. 🔁"}
        reward = premium_reward(tier)
        add_points(conn, user["id"], reward, f"Battle Pass PRÉMIUM tier {tier} 💜🎟️", xp=False)
    else:
        if tier in claimed:
            return {"ok": False, "error": "Tenhle tier už máš vyzvednutý. 🎁"}
        old = json.dumps(sorted(claimed))         # CAS: stav blobu PŘED zápisem
        claimed.append(tier)
        if conn.execute("UPDATE battlepass SET claimed = ? WHERE user_id = ? AND season = ? AND claimed = ?",
                        (json.dumps(sorted(claimed)), user["id"], season, old)).rowcount != 1:
            conn.commit()
            return {"ok": False, "error": "Souběh – zkus to za chvíli znovu. 🔁"}
        reward = tier_reward(tier)
        add_points(conn, user["id"], reward, f"Battle Pass tier {tier} 🎟️", xp=False)
    conn.commit()
    bal = conn.execute("SELECT points FROM users WHERE id = ?", (user["id"],)).fetchone()["points"]
    return {"ok": True, "reward": reward, "balance": bal, "tier": tier, "premium": premium}
