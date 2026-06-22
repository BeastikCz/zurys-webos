"""Mines anti-automation: persistent throttling, behavior scan and expiring bans."""
import json
import threading
import time
import traceback
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException

from .db import get_conn, get_setting, set_setting

CHECK_INTERVAL_SEC = 60
WINDOW_MINUTES = 5
BOT_THRESHOLD = 40
FAST_GAME_SECONDS = 1.5
FAST_GAME_RATIO = 0.70
AUTO_BAN_HOURS = 24

# Hard server-side pacing. Uses persisted mines_games rows, so restart cannot reset it.
START_MIN_INTERVAL_SEC = 2.0
START_LIMIT_1M = 12
START_LIMIT_5M = 40

BAN_IDS_KEY = "mines_ban_uids"
BAN_EXPIRES_KEY = "mines_ban_expires"
AUTO_REASON_PREFIX = "Automaticky zablokováno"
BAN_REASON = (
    f"{AUTO_REASON_PREFIX} na {AUTO_BAN_HOURS} h — detekce bota/autoklikeru: "
    f"nejméně {BOT_THRESHOLD} her za {WINDOW_MINUTES} min a "
    f"{round(FAST_GAME_RATIO * 100)} % her pod {FAST_GAME_SECONDS} s."
)


def _json_set(raw: str) -> set[int]:
    try:
        return {int(v) for v in json.loads(raw)} if raw else set()
    except (ValueError, TypeError):
        return set()


def _json_dict(raw: str) -> dict[str, str]:
    try:
        data = json.loads(raw) if raw else {}
        return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except (ValueError, TypeError):
        return {}


def mines_ban_expiries(conn) -> dict[str, str]:
    return _json_dict(get_setting(conn, BAN_EXPIRES_KEY, ""))


def _save_bans(conn, ids: set[int], expiries: dict[str, str]) -> None:
    set_setting(conn, BAN_IDS_KEY, json.dumps(sorted(ids)))
    set_setting(conn, BAN_EXPIRES_KEY, json.dumps(expiries, sort_keys=True))


def active_mines_ban_ids(conn) -> set[int]:
    """Return active bans and lazily remove expired temporary bans."""
    ids = _json_set(get_setting(conn, BAN_IDS_KEY, ""))
    expiries = mines_ban_expiries(conn)
    now = datetime.now(timezone.utc).isoformat()
    expired = {uid for uid in ids if expiries.get(str(uid)) and expiries[str(uid)] <= now}
    if expired:
        ids -= expired
        for uid in expired:
            expiries.pop(str(uid), None)
            row = conn.execute("SELECT ban_reason FROM users WHERE id=?", (uid,)).fetchone()
            reason = row["ban_reason"] if row else None
            if reason and (reason.startswith(AUTO_REASON_PREFIX) or reason.startswith("Ban 24 h")):
                conn.execute("UPDATE users SET ban_reason=NULL WHERE id=?", (uid,))
        _save_bans(conn, ids, expiries)
        conn.commit()
    return ids


def is_mines_banned(conn, uid: int) -> bool:
    return uid in active_mines_ban_ids(conn)


def ban_mines_user(conn, uid: int, reason: str, expires_at: str | None = None) -> None:
    ids = active_mines_ban_ids(conn)
    expiries = mines_ban_expiries(conn)
    ids.add(uid)
    if expires_at:
        expiries[str(uid)] = expires_at
    else:
        expiries.pop(str(uid), None)  # manual admin ban = permanent
    _save_bans(conn, ids, expiries)
    conn.execute("UPDATE users SET ban_reason=? WHERE id=?", (reason, uid))


def unban_mines_user(conn, uid: int) -> None:
    ids = active_mines_ban_ids(conn)
    expiries = mines_ban_expiries(conn)
    ids.discard(uid)
    expiries.pop(str(uid), None)
    _save_bans(conn, ids, expiries)


def check_start_allowed(conn, uid: int, now: datetime | None = None) -> None:
    """Persistent per-user pace limits checked before a new bet is charged."""
    now = now or datetime.now(timezone.utc)
    cutoff_5m = (now - timedelta(minutes=5)).isoformat()
    rows = conn.execute(
        "SELECT created_at FROM mines_games WHERE user_id=? AND created_at>=? ORDER BY created_at DESC",
        (uid, cutoff_5m),
    ).fetchall()
    if not rows:
        return

    newest = datetime.fromisoformat(rows[0]["created_at"])
    since_last = (now - newest).total_seconds()
    if since_last < START_MIN_INTERVAL_SEC:
        retry = max(1, int(START_MIN_INTERVAL_SEC - since_last + 0.999))
        raise HTTPException(status_code=429, detail=f"Mines zpomalení: další hra za {retry} s.")

    cutoff_1m = (now - timedelta(minutes=1)).isoformat()
    games_1m = sum(r["created_at"] >= cutoff_1m for r in rows)
    if games_1m >= START_LIMIT_1M:
        raise HTTPException(status_code=429, detail="Mines ochrana: moc her za minutu. Počkej 60 s.")
    if len(rows) >= START_LIMIT_5M:
        raise HTTPException(status_code=429, detail="Mines ochrana: moc her za 5 minut. Dej si pauzu.")


def _scan(conn) -> None:
    """Ban only when both volume and machine-speed signals agree."""
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(minutes=WINDOW_MINUTES)).isoformat()
    rows = conn.execute(
        "SELECT user_id, created_at, ended_at FROM mines_games WHERE created_at>=? ORDER BY user_id, created_at",
        (cutoff,),
    ).fetchall()
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["user_id"]].append(row)

    banned_ids = active_mines_ban_ids(conn)
    new_bans = []
    for uid, games in grouped.items():
        if uid in banned_ids or len(games) < BOT_THRESHOLD:
            continue
        completed = [g for g in games if g["ended_at"]]
        if not completed:
            continue
        fast = sum(
            (datetime.fromisoformat(g["ended_at"]) - datetime.fromisoformat(g["created_at"])).total_seconds()
            <= FAST_GAME_SECONDS
            for g in completed
        )
        fast_ratio = fast / len(games)
        if fast_ratio < FAST_GAME_RATIO:
            continue

        expires = (now + timedelta(hours=AUTO_BAN_HOURS)).isoformat()
        ban_mines_user(conn, uid, BAN_REASON, expires)
        banned_ids.add(uid)
        new_bans.append((uid, len(games), fast_ratio))
        print(f"[mines-anticheat] 24h ban uid={uid} ({len(games)} games/5m, {fast_ratio:.0%} fast)")

    if new_bans:
        conn.commit()


def _loop() -> None:
    while True:
        try:
            conn = get_conn()
            try:
                _scan(conn)
            finally:
                conn.close()
        except Exception:
            traceback.print_exc()
        time.sleep(CHECK_INTERVAL_SEC)


_thread = None


def start_mines_anticheat_daemon() -> None:
    global _thread
    if _thread and _thread.is_alive():
        return
    _thread = threading.Thread(target=_loop, name="webos-mines-anticheat", daemon=True)
    _thread.start()
