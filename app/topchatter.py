"""Top Chatteři: žebříček nejaktivnějších v chatu (počet GENUINE zpráv = řádků
'Aktivita v chatu' v points_logu, takže anti-spam jako u zbytku). Denní reset
(žebříček za dnešek). 1× denně se TOP 3 PŘEDCHOZÍHO dne vyplatí bonus + bot to
oznámí. Spouští se z achievements daemonu (maybe_payout je denně-gated).
"""
from datetime import datetime, timezone, timedelta

from .db import now_iso, get_setting, set_setting, local_date, local_day_start_iso, local_now
from .config import BOT_USERNAMES

PAYOUT = [3000, 2000, 1000]   # odměna pro 1./2./3. nejaktivnějšího chattera (za den)


def _is_bot(username, kick_username) -> bool:
    return ((username or "").strip().lower() in BOT_USERNAMES
            or (kick_username or "").strip().lower() in BOT_USERNAMES)


def _since(period: str) -> str:
    if period == "week":
        return (local_now() - timedelta(days=7)).astimezone(timezone.utc).isoformat()
    return local_day_start_iso(0)            # začátek dnešního ČESKÉHO dne (v UTC)


def top_chatters(conn, period: str = "day", limit: int = 10) -> list:
    """Nejaktivnější chatteři za období (počet odměněných zpráv). Boti vyřazeni."""
    buf = limit + len(BOT_USERNAMES) + 5      # rezerva, ať po vyřazení botů zbyde dost
    rows = conn.execute(
        "SELECT u.username AS username, u.avatar_url AS avatar_url, u.kick_username AS kick_username, COUNT(*) AS msgs "
        "FROM points_log p JOIN users u ON u.id = p.user_id "
        "WHERE p.reason = 'Aktivita v chatu' AND p.created_at >= ? "
        "GROUP BY p.user_id ORDER BY msgs DESC, u.username ASC LIMIT ?",
        (_since(period), buf)).fetchall()
    out = [{"username": r["username"], "avatar_url": r["avatar_url"] or "", "msgs": r["msgs"]}
           for r in rows if not _is_bot(r["username"], r["kick_username"])]
    return out[:limit]


def maybe_payout(conn) -> int:
    """1× denně odmění TOP 3 chattery PŘEDCHOZÍHO dne + bot shoutout. Na první
    spuštění tichá inicializace (žádná retroaktivní výplata). Vrátí počet výherců."""
    start_iso = local_day_start_iso(-1)      # začátek VČEREJŠÍHO českého dne (v UTC)
    end_iso = local_day_start_iso(0)          # začátek dnešního českého dne (v UTC)
    yday = local_date(-1)                      # datum včerejška (ČR) jako klíč

    last = get_setting(conn, "topchat_paid_day")
    if last is None or last == "":
        set_setting(conn, "topchat_paid_day", yday)   # silent init – neplatit zpětně
        conn.commit()
        return 0
    if last >= yday:
        return 0                                       # už vyplaceno (nebo novější)

    rows = conn.execute(
        "SELECT p.user_id AS uid, u.username AS username, u.kick_username AS kick_username, COUNT(*) AS msgs "
        "FROM points_log p JOIN users u ON u.id = p.user_id "
        "WHERE p.reason = 'Aktivita v chatu' AND p.created_at >= ? AND p.created_at < ? "
        "GROUP BY p.user_id ORDER BY msgs DESC, u.username ASC LIMIT ?",
        (start_iso, end_iso, 3 + len(BOT_USERNAMES) + 3)).fetchall()
    rows = [r for r in rows if not _is_bot(r["username"], r["kick_username"])][:3]   # boti nevyhrávají
    winners = []
    for i, r in enumerate(rows):
        reward = PAYOUT[i] if i < len(PAYOUT) else 0
        if reward <= 0:
            continue
        conn.execute("UPDATE users SET points = points + ? WHERE id = ?", (reward, r["uid"]))
        conn.execute("INSERT INTO points_log (user_id, change, reason, created_at) VALUES (?,?,?,?)",
                     (r["uid"], reward, f"Top Chatter dne ({i + 1}. místo) 🗣️", now_iso()))
        winners.append((r["username"], reward))
    set_setting(conn, "topchat_paid_day", yday)
    conn.commit()
    if winners:
        try:
            from . import kickbot
            medal = ["🥇", "🥈", "🥉"]
            parts = " · ".join(f"{medal[i]} {w[0]} (+{w[1]})" for i, w in enumerate(winners))
            kickbot.send_message(conn, f"🗣️ TOP CHATTEŘI VČEREJŠKA: {parts} – děkujeme za skvěle rozjetý chat! 💬🌾",
                                 kind="system")
        except Exception:
            import traceback
            traceback.print_exc()
    return len(winners)


def _today_str() -> str:
    return local_date()                      # den podle českého času


def status(conn) -> dict:
    """Stav výplaty TOP chatterů: kdy naposled placeno + dnešní TOP 3 (a co by brali)."""
    paid = get_setting(conn, "topchat_paid_day") or ""
    today = _today_str()
    top = top_chatters(conn, "day", 3)
    preview = [{"username": t["username"], "msgs": t["msgs"],
                "reward": PAYOUT[i] if i < len(PAYOUT) else 0} for i, t in enumerate(top)]
    return {"paid_day": paid, "today": today, "already_paid_today": paid >= today,
            "today_top3": preview, "payout": PAYOUT}


def pay_today(conn) -> dict:
    """Ručně vyplatí DNEŠNÍ TOP 3 hned (např. po streamu). Idempotentní – 1× za den.
    Nastaví topchat_paid_day=dnes, takže noční auto-výplata už nedvojplatí."""
    today = _today_str()
    if (get_setting(conn, "topchat_paid_day") or "") >= today:
        return {"ok": False, "error": "Dnešní TOP chatteři už byli vyplaceni."}
    rows = conn.execute(
        "SELECT p.user_id AS uid, u.username AS username, u.kick_username AS kick_username, COUNT(*) AS msgs "
        "FROM points_log p JOIN users u ON u.id = p.user_id "
        "WHERE p.reason = 'Aktivita v chatu' AND p.created_at >= ? "
        "GROUP BY p.user_id ORDER BY msgs DESC, u.username ASC LIMIT ?",
        (_since("day"), 3 + len(BOT_USERNAMES) + 3)).fetchall()
    rows = [r for r in rows if not _is_bot(r["username"], r["kick_username"])][:3]
    winners = []
    for i, r in enumerate(rows):
        reward = PAYOUT[i] if i < len(PAYOUT) else 0
        if reward <= 0:
            continue
        conn.execute("UPDATE users SET points = points + ? WHERE id = ?", (reward, r["uid"]))
        conn.execute("INSERT INTO points_log (user_id, change, reason, created_at) VALUES (?,?,?,?)",
                     (r["uid"], reward, f"Top Chatter dne ({i + 1}. místo) 🗣️", now_iso()))
        winners.append((r["username"], reward))
    set_setting(conn, "topchat_paid_day", today)        # zabrání nočnímu dvojplacení
    conn.commit()
    if winners:
        try:
            from . import kickbot
            medal = ["🥇", "🥈", "🥉"]
            parts = " · ".join(f"{medal[i]} {w[0]} (+{w[1]})" for i, w in enumerate(winners))
            kickbot.send_message(conn, f"🗣️ TOP CHATTEŘI DNE: {parts} – děkujeme za skvěle rozjetý chat! 💬🌾", kind="system")
        except Exception:
            import traceback
            traceback.print_exc()
    return {"ok": True, "winners": [{"username": w[0], "reward": w[1]} for w in winners], "count": len(winners)}
