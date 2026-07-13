"""Partnerské/sponzorské odkazy v Bonusech.

Dva režimy u každého odkazu:
  * 'once'  – klasika: vyzvedne se 1× za uživatele NAVŽDY (UNIQUE v partner_link_claims).
  * 'flash' – random obnova: jde vyzvednout JEN když běží 'flash kolo' (otevírá scheduler
              partners_flash, jen když je stream live), a to 1× za KOLO (partner_flash_claims).

Ověřuje se klik na NAŠE tlačítko (proti farmení), ne reálná návštěva cíle.
"""
from .db import now_iso
from .economy import award_soft_faucet


def active_round(conn):
    """Aktuálně OTEVŘENÉ flash kolo (now < expires_at), nebo None."""
    return conn.execute(
        "SELECT id, opened_at, expires_at FROM partner_rounds WHERE expires_at > ? "
        "ORDER BY id DESC LIMIT 1", (now_iso(),)).fetchone()


def status_for_user(conn, user_id: int) -> dict:
    """Zapnuté odkazy + stav pro daného uživatele (claimable/claimed dle režimu)."""
    rnd = active_round(conn)
    rid = rnd["id"] if rnd else 0
    rows = conn.execute(
        "SELECT id, label, url, reward, icon, COALESCE(mode,'once') AS mode "
        "FROM partner_links WHERE enabled=1 ORDER BY sort_order ASC, id ASC").fetchall()
    out = []
    for r in rows:
        flash = (r["mode"] == "flash")
        if flash:
            if rnd:
                claimed = conn.execute(
                    "SELECT 1 FROM partner_flash_claims WHERE user_id=? AND link_id=? AND round_id=?",
                    (user_id, r["id"], rid)).fetchone() is not None
                claimable = not claimed
            else:
                claimed, claimable = False, False        # flash neběží → nejde vzít
        else:
            claimed = conn.execute(
                "SELECT 1 FROM partner_link_claims WHERE user_id=? AND link_id=?",
                (user_id, r["id"])).fetchone() is not None
            claimable = not claimed
        out.append({"id": r["id"], "label": r["label"], "url": r["url"], "reward": r["reward"],
                    "icon": r["icon"] or "🤝", "mode": r["mode"], "flash": flash,
                    "claimed": claimed, "claimable": claimable})
    return {"links": out, "flash_active": bool(rnd),
            "flash_ends_at": rnd["expires_at"] if rnd else None}


def claim(conn, user_id: int, link_id: int) -> dict:
    """Vyzvedne odměnu za odkaz dle režimu. Atomicky – nejde dvakrát (ani při souběhu)."""
    link = conn.execute(
        "SELECT id, label, reward, enabled, COALESCE(mode,'once') AS mode "
        "FROM partner_links WHERE id=?", (link_id,)).fetchone()
    if not link or not link["enabled"]:
        raise ValueError("Tenhle odkaz teď není dostupný.")
    reward = max(0, int(link["reward"] or 0))
    if link["mode"] == "flash":
        rnd = active_round(conn)
        if not rnd:
            raise ValueError("⚡ Flash bonus teď neběží — počkej na oznámení v chatu!")
        cur = conn.execute(
            "INSERT OR IGNORE INTO partner_flash_claims (user_id, link_id, round_id, created_at) "
            "VALUES (?,?,?,?)", (user_id, link_id, rnd["id"], now_iso()))
        if cur.rowcount == 0:
            conn.commit()
            raise ValueError("Z tohoto flash kola už máš vybráno. ✓ Počkej na další!")
        reason, msg = f"Flash partner: {link['label']} ⚡", f"⚡ FLASH! +{reward} sedláků 🌾"
    else:
        cur = conn.execute(
            "INSERT OR IGNORE INTO partner_link_claims (user_id, link_id, created_at) VALUES (?,?,?)",
            (user_id, link_id, now_iso()))
        if cur.rowcount == 0:
            conn.commit()
            raise ValueError("Tuhle odměnu už máš vyzvednutou. ✓")
        reason, msg = f"Partner: {link['label']} 🤝", f"🤝 Díky! +{reward} sedláků 🌾"
    if reward > 0:
        award = award_soft_faucet(conn, user_id, reward, reason)
        reward = award["amount"]
        if award["guarded"]:
            msg += " ⚖️ Ekonomická pojistka je aktivní."
    conn.commit()
    bal = conn.execute("SELECT points FROM users WHERE id=?", (user_id,)).fetchone()["points"]
    return {"ok": True, "reward": reward, "balance": bal, "message": msg}
