"""Dropy: závod o kód z chatu. Nejrychlejší, kdo zadá kód, berou body."""
import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Request

from ..anticheat import (check_or_block, fp_drop_cooldown_remaining, is_new_account,
                          new_account_drop_count, NEW_ACCOUNT_MAX_CLAIMS,
                          FP_DROP_COOLDOWN_SEC)
from ..db import now_iso
from ..deps import db_dep, require_user, add_points, client_ip
from ..models import DropClaimIn
from ..ratelimit import rate_limit

DROP_MIN_DWELL_MS = 600  # claim dřív než 0,6 s po zobrazení banneru = nejspíš bot

router = APIRouter(prefix="/drops", tags=["drops"])


def _active_drop(conn: sqlite3.Connection):
    return conn.execute(
        "SELECT * FROM drops WHERE active = 1 ORDER BY id DESC LIMIT 1"
    ).fetchone()


@router.get("/active")
def active_drop(conn: sqlite3.Connection = Depends(db_dep)):
    """Info o aktuálním live dropu (BEZ kódu – ten je jen v chatu!)."""
    d = _active_drop(conn)
    if not d:
        return {"active": False}
    claims = conn.execute(
        "SELECT c.position, u.username FROM drop_claims c JOIN users u ON u.id = c.user_id "
        "WHERE c.drop_id = ? ORDER BY c.position", (d["id"],),
    ).fetchall()
    return {
        "active": True,
        "id": d["id"],
        "points": d["points"],
        "max_winners": d["max_winners"],
        "taken": len(claims),
        "spots_left": max(0, d["max_winners"] - len(claims)),
        "winners": [{"position": c["position"], "username": c["username"]} for c in claims],
    }


@router.post("/claim")
def claim_drop(data: DropClaimIn, request: Request, user: sqlite3.Row = Depends(require_user),
               conn: sqlite3.Connection = Depends(db_dep)):
    """Zadání kódu z chatu. Kdo dřív přijde, ten dřív bere – do naplnění míst. + anti-bot."""
    rate_limit(f"drop:{user['id']}", 8, 10)  # max 8 pokusů / 10 s

    # --- ANTI-BOT vrstva 1: explicitní jasné kontroly s konkrétními hláškami ---
    if (data.hp or "").strip():                         # honeypot vyplněn = bot
        raise HTTPException(status_code=400, detail="Neplatný požadavek.")
    sig = conn.execute(
        "SELECT webdriver, fp_hash FROM client_signals WHERE user_id = ? ORDER BY id DESC LIMIT 1",
        (user["id"],)).fetchone()
    if not sig or not sig["fp_hash"]:                   # žádný otisk = přímé API / bot
        raise HTTPException(status_code=400, detail="Ověř se v prohlížeči (načti stránku) a zkus znovu.")
    if sig["webdriver"]:                                # automatizovaný prohlížeč
        raise HTTPException(status_code=403, detail="Automatizovaný prohlížeč není povolen.")
    if (data.dwell or 0) < DROP_MIN_DWELL_MS:           # claim moc rychle po zobrazení
        raise HTTPException(status_code=400, detail="Moc rychle ⚡ – počkej na zobrazení dropu.")

    # --- ANTI-BOT vrstva 2: risk score (kombinace IP/účet/VPN/form timing/rapid) ---
    check_or_block(conn, user, request, context="claim", t0_ms=data.t0,
                   block_msg="Drop zablokován ochranou proti botům.")

    # --- ANTI-BOT vrstva 3: per-zařízení limity (proti přepínání účtů) ---
    fp = sig["fp_hash"]
    rate_limit(f"drop:fp:{fp}", 5, 30)                  # 5 claim/30 s na zařízení
    wait = fp_drop_cooldown_remaining(conn, fp)
    if wait:
        raise HTTPException(status_code=429,
                            detail=f"Z tohoto zařízení už drop padl – počkej {wait} s. ⏳")

    # --- ANTI-BOT vrstva 4: cooldown nových účtů ---
    if is_new_account(user):
        used = new_account_drop_count(conn, user["id"])
        if used >= NEW_ACCOUNT_MAX_CLAIMS:
            raise HTTPException(
                status_code=429,
                detail=f"Nové účty (<24 h) mají limit {NEW_ACCOUNT_MAX_CLAIMS} dropů. Zatím {used}.",
            )

    ip = client_ip(request)
    code = data.code.strip().lstrip("#").upper()
    d = conn.execute(
        "SELECT * FROM drops WHERE active = 1 AND UPPER(code) = ? ORDER BY id DESC LIMIT 1",
        (code,),
    ).fetchone()
    if not d:
        raise HTTPException(status_code=400, detail="Žádný takový live drop. Zkontroluj kód z chatu. ⚡")

    # (Limit „1 chyt na IP/zařízení na drop" VYPNUT na přání – drop si může chytit kdokoliv,
    #  i víc účtů ze stejné IP/zařízení. Zůstává 1 chyt na UŽIVATELE na drop + anti-bot vrstvy.)

    ts = now_iso()
    # ATOMICKY: vloží claim jen když je volné místo A ještě jsi nehrál.
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
        raise HTTPException(status_code=400, detail="Už je rozebráno! 😢 Příště buď rychlejší. ⚡")

    position = conn.execute(
        "SELECT position FROM drop_claims WHERE drop_id = ? AND user_id = ?", (d["id"], user["id"]),
    ).fetchone()["position"]
    add_points(conn, user["id"], d["points"], f"Drop #{d['id']} – {position}. místo")
    if position >= d["max_winners"]:
        conn.execute("UPDATE drops SET active = 0, ended_at = ? WHERE id = ?", (ts, d["id"]))
    conn.commit()

    fresh = conn.execute("SELECT points FROM users WHERE id = ?", (user["id"],)).fetchone()
    return {
        "ok": True,
        "position": position,
        "points": d["points"],
        "balance": fresh["points"],
        "message": f"🏆 {position}. místo! Získáváš {d['points']} b.",
    }
