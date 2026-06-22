"""Soukromé zprávy (PM).

Pravidlo: konverzaci ZAKLÁDÁ jen staff (admin/broadcaster/mod) – např. „vyhrál si skin".
User pak smí v založeném vlákně ODEPISOVAT, ale sám nikomu psát nezačne. → nula scamu
(cizí ti nepošlou fake výhru). Vlákno = 1 per ne-staff uživatel (`dm_messages.user_id`).
`from_id == user_id` → zpráva od usera (odpověď); jinak od staffa. `seen` = příjemce viděl.
"""
import sqlite3

from fastapi import APIRouter, Depends, HTTPException

from ..db import now_iso
from ..deps import db_dep, require_user, require_broadcaster
from ..models import DmIn

router = APIRouter(prefix="/dm", tags=["dm"])


def _who(conn, uid):
    r = conn.execute("SELECT username, role FROM users WHERE id = ?", (uid,)).fetchone()
    return (r["username"], r["role"]) if r else ("?", "user")


def _render(conn, rows, owner_id):
    out = []
    for r in rows:
        uname, urole = _who(conn, r["from_id"])
        out.append({"id": r["id"], "body": r["body"], "created_at": r["created_at"],
                    "from_staff": r["from_id"] != owner_id, "from_name": uname, "from_role": urole})
    return out


# ---------------- user strana ----------------
@router.get("/thread")
def my_thread(user: sqlite3.Row = Depends(require_user), conn: sqlite3.Connection = Depends(db_dep)):
    rows = conn.execute("SELECT * FROM dm_messages WHERE user_id = ? ORDER BY id", (user["id"],)).fetchall()
    conn.execute("UPDATE dm_messages SET seen = 1 WHERE user_id = ? AND from_id != user_id AND seen = 0", (user["id"],))
    conn.commit()
    return {"messages": _render(conn, rows, user["id"]), "can_reply": len(rows) > 0}


@router.get("/unread")
def unread(user: sqlite3.Row = Depends(require_user), conn: sqlite3.Connection = Depends(db_dep)):
    """Počet nepřečtených PM (pro badge poll). Levné – jen COUNT."""
    if user["role"] in ("admin", "broadcaster"):                # mod NEMÁ přístup k PM
        c = conn.execute("SELECT COUNT(*) c FROM dm_messages WHERE from_id = user_id AND seen = 0").fetchone()["c"]
    else:
        c = conn.execute("SELECT COUNT(*) c FROM dm_messages WHERE user_id = ? AND from_id != user_id AND seen = 0",
                         (user["id"],)).fetchone()["c"]
    return {"count": c}


@router.post("/reply")
def reply(data: DmIn, user: sqlite3.Row = Depends(require_user), conn: sqlite3.Connection = Depends(db_dep)):
    started = conn.execute("SELECT 1 FROM dm_messages WHERE user_id = ? AND from_id != user_id LIMIT 1",
                           (user["id"],)).fetchone()
    if not started:
        raise HTTPException(status_code=403, detail="Konverzaci může začít jen tým ZURYS – počkej, až ti někdo napíše. ✉️")
    body = (data.body or "").strip()
    if not body:
        raise HTTPException(status_code=400, detail="Prázdná zpráva.")
    conn.execute("INSERT INTO dm_messages (user_id, from_id, body, created_at, seen) VALUES (?,?,?,?,0)",
                 (user["id"], user["id"], body[:2000], now_iso()))
    conn.commit()
    return {"ok": True}


# ---------------- staff strana ----------------
@router.post("/send/{uid}")
def staff_send(uid: int, data: DmIn, staff: sqlite3.Row = Depends(require_broadcaster),
               conn: sqlite3.Connection = Depends(db_dep)):
    if not conn.execute("SELECT 1 FROM users WHERE id = ?", (uid,)).fetchone():
        raise HTTPException(status_code=404, detail="Uživatel nenalezen.")
    body = (data.body or "").strip()
    if not body:
        raise HTTPException(status_code=400, detail="Prázdná zpráva.")
    conn.execute("INSERT INTO dm_messages (user_id, from_id, body, created_at, seen) VALUES (?,?,?,?,0)",
                 (uid, staff["id"], body[:2000], now_iso()))
    conn.commit()
    return {"ok": True}


@router.get("/admin/threads")
def staff_threads(staff: sqlite3.Row = Depends(require_broadcaster), conn: sqlite3.Connection = Depends(db_dep)):
    rows = conn.execute(
        "SELECT m.user_id, u.username, u.avatar_url, u.role, MAX(m.id) AS last_id, "
        "       SUM(CASE WHEN m.from_id = m.user_id AND m.seen = 0 THEN 1 ELSE 0 END) AS unread "
        "FROM dm_messages m JOIN users u ON u.id = m.user_id "
        "GROUP BY m.user_id ORDER BY last_id DESC LIMIT 100").fetchall()
    out = []
    for r in rows:
        last = conn.execute("SELECT body, created_at, from_id FROM dm_messages WHERE id = ?", (r["last_id"],)).fetchone()
        out.append({"user_id": r["user_id"], "username": r["username"], "avatar_url": r["avatar_url"],
                    "role": r["role"], "unread": r["unread"], "last_body": last["body"],
                    "last_at": last["created_at"], "last_from_staff": last["from_id"] != r["user_id"]})
    return out


@router.get("/admin/thread/{uid}")
def staff_thread(uid: int, staff: sqlite3.Row = Depends(require_broadcaster), conn: sqlite3.Connection = Depends(db_dep)):
    rows = conn.execute("SELECT * FROM dm_messages WHERE user_id = ? ORDER BY id", (uid,)).fetchall()
    conn.execute("UPDATE dm_messages SET seen = 1 WHERE user_id = ? AND from_id = user_id AND seen = 0", (uid,))
    conn.commit()
    u = conn.execute("SELECT username, avatar_url, role FROM users WHERE id = ?", (uid,)).fetchone()
    return {
        "user": {"id": uid, "username": u["username"] if u else "?",
                 "avatar_url": u["avatar_url"] if u else None, "role": u["role"] if u else "user"},
        "messages": _render(conn, rows, uid),
    }
