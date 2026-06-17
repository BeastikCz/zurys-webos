"""Farmářský Battle Pass: sezónní (měsíční) postupová dráha.

Tier = kolik XP (sedláků) hráč nafarmil OD ZAČÁTKU sezóny – počítá se DIFFEM
z celoživotního earned_total proti baseline (stejná filozofie jako questy).
Každý odemčený tier si hráč VYZVEDNE (claim) a dostane sedláky; milníkové tiery
(každý 5.) dávají víc. Reset = nový měsíc (nová sezóna → nový baseline). Server
ověří odemčení i při claimu (nevěří klientovi).
"""
import json

from .db import now_iso, local_date

TIER_XP = 2500     # earned_total na 1 tier
N_TIERS = 20       # délka dráhy za sezónu


def _season() -> str:
    return local_date()[:7]   # 'YYYY-MM' dle českého času


def tier_reward(tier: int) -> int:
    """Odměna za tier (milníky každý 5. tier dávají víc)."""
    return 800 if tier % 5 == 0 else 200


def _earned(conn, uid: int) -> int:
    r = conn.execute("SELECT earned_total FROM users WHERE id = ?", (uid,)).fetchone()
    return (r["earned_total"] if r else 0) or 0


def _row(conn, uid: int):
    """(baseline, claimed_list) pro aktuální sezónu; při prvním přístupu řádek založí."""
    season = _season()
    row = conn.execute("SELECT baseline, claimed FROM battlepass WHERE user_id = ? AND season = ?",
                       (uid, season)).fetchone()
    if row is None:
        base = _earned(conn, uid)
        conn.execute(
            "INSERT INTO battlepass (user_id, season, baseline, claimed, created_at) "
            "VALUES (?, ?, ?, '[]', ?) ON CONFLICT(user_id, season) DO NOTHING",
            (uid, season, base, now_iso()))
        conn.commit()
        return base, []
    return row["baseline"], json.loads(row["claimed"] or "[]")


def status(conn, user) -> dict:
    """Stav passu pro UI: aktuální tier, postup a seznam tierů s odměnami/stavem."""
    base, claimed = _row(conn, user["id"])
    xp = max(0, _earned(conn, user["id"]) - base)
    tier = min(N_TIERS, xp // TIER_XP)
    into = (xp - tier * TIER_XP) if tier < N_TIERS else TIER_XP
    pct = round(into * 100 / TIER_XP) if tier < N_TIERS else 100
    tiers = [{"tier": t, "reward": tier_reward(t), "reached": t <= tier,
              "claimed": t in claimed, "milestone": t % 5 == 0} for t in range(1, N_TIERS + 1)]
    return {"season": _season(), "tier": int(tier), "max_tier": N_TIERS, "xp": int(xp),
            "tier_xp": TIER_XP, "pct": pct, "into": int(into), "tiers": tiers,
            "claimable": sum(1 for t in tiers if t["reached"] and not t["claimed"])}


def claim(conn, user, tier: int) -> dict:
    """Vyzvedne odměnu za odemčený a dosud nevyzvednutý tier. Idempotentní přes claimed."""
    from .deps import add_points
    season = _season()
    base, claimed = _row(conn, user["id"])
    reached = max(0, _earned(conn, user["id"]) - base) // TIER_XP
    if tier < 1 or tier > N_TIERS:
        return {"ok": False, "error": "Neplatný tier."}
    if tier > reached:
        return {"ok": False, "error": "Tenhle tier ještě nemáš odemčený. 💪"}
    if tier in claimed:
        return {"ok": False, "error": "Tenhle tier už máš vyzvednutý. 🎁"}
    claimed.append(tier)
    conn.execute("UPDATE battlepass SET claimed = ? WHERE user_id = ? AND season = ?",
                 (json.dumps(sorted(claimed)), user["id"], season))
    reward = tier_reward(tier)
    add_points(conn, user["id"], reward, f"Battle Pass tier {tier} 🎟️")
    conn.commit()
    bal = conn.execute("SELECT points FROM users WHERE id = ?", (user["id"],)).fetchone()["points"]
    return {"ok": True, "reward": reward, "balance": bal, "tier": tier}
