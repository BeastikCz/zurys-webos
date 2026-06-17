"""Login kalendář: měsíční mřížka aktivních dní. Den se OZNAČÍ při denním claimu
(žádné druhé tlačítko – navazuje na existující daily streak). Milníky (X aktivních
dní v měsíci) dají bonus navíc. Reset = nový měsíc.
"""
import json
import calendar as _cal

from .db import local_date

MILESTONES = [(5, 200), (10, 500), (15, 1000), (20, 2000)]   # (aktivních dní, odměna)


def _month() -> str:
    return local_date()[:7]


def _today_day() -> int:
    return int(local_date()[8:10])


def _row(conn, uid: int):
    m = _month()
    r = conn.execute("SELECT days, claimed_ms FROM login_calendar WHERE user_id = ? AND month = ?",
                     (uid, m)).fetchone()
    if r is None:
        conn.execute("INSERT INTO login_calendar (user_id, month) VALUES (?, ?) "
                     "ON CONFLICT(user_id, month) DO NOTHING", (uid, m))
        conn.commit()
        return [], []
    return json.loads(r["days"] or "[]"), json.loads(r["claimed_ms"] or "[]")


def mark(conn, uid: int) -> None:
    """Označ dnešní den jako aktivní (volá se z denního claimu). Necommituje – commit caller."""
    days, _ = _row(conn, uid)
    d = _today_day()
    if d not in days:
        days.append(d)
        conn.execute("UPDATE login_calendar SET days = ? WHERE user_id = ? AND month = ?",
                     (json.dumps(sorted(days)), uid, _month()))


def status(conn, user) -> dict:
    days, claimed = _row(conn, user["id"])
    total = len(days)
    y, mo = (int(x) for x in _month().split("-"))
    ms = [{"days": d, "reward": rew, "reached": total >= d, "claimed": d in claimed}
          for d, rew in MILESTONES]
    return {"month": _month(), "days_in_month": _cal.monthrange(y, mo)[1], "today": _today_day(),
            "active": days, "total": total, "milestones": ms,
            "claimable": sum(1 for x in ms if x["reached"] and not x["claimed"])}


def claim(conn, user, milestone: int) -> dict:
    from .deps import add_points
    days, claimed = _row(conn, user["id"])
    total = len(days)
    mdef = next((x for x in MILESTONES if x[0] == milestone), None)
    if not mdef:
        return {"ok": False, "error": "Neplatný milník."}
    need, reward = mdef
    if total < need:
        return {"ok": False, "error": f"Potřebuješ {need} aktivních dní (máš {total})."}
    if milestone in claimed:
        return {"ok": False, "error": "Tenhle milník už máš vyzvednutý. 🎁"}
    claimed.append(milestone)
    conn.execute("UPDATE login_calendar SET claimed_ms = ? WHERE user_id = ? AND month = ?",
                 (json.dumps(sorted(claimed)), user["id"], _month()))
    add_points(conn, user["id"], reward, f"Login kalendář – {need} dní 🗓️")
    conn.commit()
    bal = conn.execute("SELECT points FROM users WHERE id = ?", (user["id"],)).fetchone()["points"]
    return {"ok": True, "reward": reward, "balance": bal, "milestone": milestone}
