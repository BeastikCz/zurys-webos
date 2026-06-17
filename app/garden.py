"""Zahrádka (farm-sim): sázej plodiny → rostou v reálném čase → sklidíš sedláky.

Loop: zaplať sazbu → plodina dorůstá `hours` hodin → harvest = odměna (zisk). Gated
časem + počtem záhonů (anti-inflace; sázení navíc reinvestuje sedláky zpět). Plně
na téma sedláka. Prázdný záhon = žádný řádek v `garden`.
"""
import sqlite3
from datetime import datetime, timezone, timedelta

from .db import now_iso

N_PLOTS = 4

# (key, ikona, název, sazba, hodiny růstu, odměna při sklizni). Marže ~40-90 %, gated časem.
CROPS = [
    {"key": "mrkev",    "icon": "🥕", "name": "Mrkev",       "cost": 50,   "hours": 1,  "reward": 80},
    {"key": "brambory", "icon": "🥔", "name": "Brambory",    "cost": 150,  "hours": 4,  "reward": 250},
    {"key": "dyne",     "icon": "🎃", "name": "Dýně",        "cost": 400,  "hours": 12, "reward": 700},
    {"key": "klas",     "icon": "🌾", "name": "Zlatý klas",  "cost": 1000, "hours": 24, "reward": 1800},
]
_BY_KEY = {c["key"]: c for c in CROPS}


def _crops_public() -> list:
    return [{"key": c["key"], "icon": c["icon"], "name": c["name"], "cost": c["cost"],
             "hours": c["hours"], "reward": c["reward"]} for c in CROPS]


def status(conn, user) -> dict:
    now = datetime.now(timezone.utc)
    rows = {r["plot"]: r for r in conn.execute(
        "SELECT * FROM garden WHERE user_id = ?", (user["id"],))}
    plots = []
    for p in range(N_PLOTS):
        r = rows.get(p)
        if not r:
            plots.append({"plot": p, "empty": True})
            continue
        c = _BY_KEY.get(r["crop"], {})
        ready = r["ready_at"] <= now_iso()
        secs = 0 if ready else max(0, int((datetime.fromisoformat(r["ready_at"]) - now).total_seconds()))
        plots.append({"plot": p, "empty": False, "crop": r["crop"], "icon": c.get("icon"),
                      "name": c.get("name"), "reward": c.get("reward"),
                      "ready": ready, "seconds_left": secs})
    return {"plots": plots, "crops": _crops_public(), "n_plots": N_PLOTS}


def plant(conn, user, plot: int, crop_key: str) -> dict:
    from .deps import try_debit
    if plot < 0 or plot >= N_PLOTS:
        return {"ok": False, "error": "Neplatný záhon."}
    c = _BY_KEY.get(crop_key)
    if not c:
        return {"ok": False, "error": "Neznámá plodina."}
    if conn.execute("SELECT 1 FROM garden WHERE user_id = ? AND plot = ?", (user["id"], plot)).fetchone():
        return {"ok": False, "error": "Záhon je obsazený."}
    if not try_debit(conn, user["id"], c["cost"], f"Zasazení: {c['name']} 🌱"):
        return {"ok": False, "error": f"Nemáš dost sedláků (sazba {c['cost']})."}
    now = datetime.now(timezone.utc)
    conn.execute("INSERT INTO garden (user_id, plot, crop, planted_at, ready_at) VALUES (?,?,?,?,?)",
                 (user["id"], plot, crop_key, now.isoformat(), (now + timedelta(hours=c["hours"])).isoformat()))
    conn.commit()
    bal = conn.execute("SELECT points FROM users WHERE id = ?", (user["id"],)).fetchone()["points"]
    return {"ok": True, "balance": bal}


def harvest(conn, user, plot: int) -> dict:
    from .deps import add_points
    r = conn.execute("SELECT * FROM garden WHERE user_id = ? AND plot = ?", (user["id"], plot)).fetchone()
    if not r:
        return {"ok": False, "error": "Prázdný záhon."}
    if r["ready_at"] > now_iso():
        return {"ok": False, "error": "Ještě nedorostlo. 🌱"}
    c = _BY_KEY.get(r["crop"], {})
    reward = c.get("reward", 0)
    conn.execute("DELETE FROM garden WHERE user_id = ? AND plot = ?", (user["id"], plot))
    add_points(conn, user["id"], reward, f"Sklizeň: {c.get('name', '?')} 🌾")
    conn.commit()
    bal = conn.execute("SELECT points FROM users WHERE id = ?", (user["id"],)).fetchone()["points"]
    return {"ok": True, "reward": reward, "balance": bal, "name": c.get("name")}
