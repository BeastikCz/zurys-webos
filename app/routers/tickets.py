"""Samostatné support tickety – oddělené od soukromých zpráv."""
import sqlite3
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request

from .. import alerts
from ..config import UPLOAD_DIR
from ..db import now_iso
from ..deps import add_points, db_dep, level_info, notify, record_audit, require_admin, require_user
from ..models import DmIn, ImageUploadIn, TicketCreateIn, TicketRefundIn
from ..ratelimit import rate_limit

router = APIRouter(prefix="/tickets", tags=["tickets"])

CATEGORIES = {
    "account": {"login", "profile", "other"},
    "orders": {"order", "raffle", "refund"},
    "web": {"bug", "performance", "game"},
    "other": {"question", "idea", "other"},
}
STATUSES = {"open", "in_progress", "resolved", "closed"}
AUTOCLOSE_DAYS = 7   # resolved ticket bez další aktivity se sám uzavře


def _autoclose(conn):
    """Líný sweep místo daemonu – běží při načtení seznamu ticketů (ne na polling hot-path)."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=AUTOCLOSE_DAYS)).isoformat()
    rows = conn.execute(
        "SELECT id FROM support_tickets WHERE status='resolved' AND updated_at < ?", (cutoff,)
    ).fetchall()
    cur = conn.execute(
        "UPDATE support_tickets SET status='closed', updated_at=? WHERE status='resolved' AND updated_at < ?",
        (now_iso(), cutoff),
    )
    if cur.rowcount:
        conn.executemany(
            "INSERT INTO support_ticket_events (ticket_id,actor_name,event,detail,created_at) "
            "VALUES (?,'Systém','status','Vyřešený → Uzavřený (automaticky)',?)",
            [(r["id"], now_iso()) for r in rows],
        )
        conn.commit()


def _ticket(conn, ticket_id):
    return conn.execute(
        "SELECT t.*, u.username, u.avatar_url, u.role FROM support_tickets t "
        "JOIN users u ON u.id = t.user_id WHERE t.id = ?", (ticket_id,)
    ).fetchone()


def _messages(conn, ticket_id, owner_id):
    rows = conn.execute(
        "SELECT m.*, u.username AS from_name, u.role AS from_role "
        "FROM support_ticket_messages m LEFT JOIN users u ON u.id = m.from_id "
        "WHERE m.ticket_id = ? ORDER BY m.id", (ticket_id,)
    ).fetchall()
    return [{"id": r["id"], "body": r["body"], "created_at": r["created_at"],
             "from_staff": r["from_id"] != owner_id, "from_name": r["from_name"] or "?",
             "from_role": r["from_role"] or "user", "image": r["image"]} for r in rows]


def _event(conn, ticket_id, actor_id, event, detail):
    actor = conn.execute("SELECT username FROM users WHERE id = ?", (actor_id,)).fetchone() if actor_id else None
    conn.execute(
        "INSERT INTO support_ticket_events (ticket_id,actor_id,actor_name,event,detail,created_at) "
        "VALUES (?,?,?,?,?,?)",
        (ticket_id, actor_id, actor["username"] if actor else "Systém", event, detail[:300], now_iso()),
    )


def _events(conn, ticket_id):
    return [dict(r) for r in conn.execute(
        "SELECT event,actor_name,detail,created_at FROM support_ticket_events "
        "WHERE ticket_id=? ORDER BY id", (ticket_id,),
    ).fetchall()]


def _summary(r):
    return {k: r[k] for k in ("id", "user_id", "username", "avatar_url", "role", "category",
                               "subcategory", "subject", "status", "created_at", "updated_at",
                               "last_body", "unread") if k in r.keys()}


def _add_message(conn, ticket, from_id, body, image=None):
    if ticket["status"] == "closed":
        raise HTTPException(status_code=409, detail="Ticket je uzavřený.")
    text = (body or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Prázdná zpráva.")
    now = now_iso()
    conn.execute(
        "INSERT INTO support_ticket_messages (ticket_id, from_id, body, created_at, seen, image) VALUES (?,?,?,?,0,?)",
        (ticket["id"], from_id, text[:2000], now, image),
    )
    status = "in_progress" if from_id != ticket["user_id"] and ticket["status"] == "open" else ticket["status"]
    conn.execute("UPDATE support_tickets SET status = ?, updated_at = ? WHERE id = ?", (status, now, ticket["id"]))
    if status != ticket["status"]:
        _event(conn, ticket["id"], from_id, "status", "Otevřený → Řeší se")
    if from_id != ticket["user_id"]:  # odpověď podpory → zvoneček uživateli
        notify(conn, ticket["user_id"], "🎫", f"Podpora odpověděla na ticket #{ticket['id']}",
               text[:120], f"#/podpora/{ticket['id']}")
    conn.commit()
    if from_id == ticket["user_id"]:  # píše uživatel → pípni adminovi na Discord
        alerts.send(f"💬 Ticket #{ticket['id']} – nová zpráva od {ticket['username']}",
                    detail=text[:300] + f"\nhttps://zurys.live/#/podpora/{ticket['id']}",
                    key=f"ticket:msg:{ticket['id']}", cooldown=300)


@router.get("/unread")
def unread(user: sqlite3.Row = Depends(require_user), conn: sqlite3.Connection = Depends(db_dep)):
    if user["role"] == "admin":
        count = conn.execute(
            "SELECT COUNT(*) c FROM support_ticket_messages m JOIN support_tickets t ON t.id = m.ticket_id "
            "WHERE m.from_id = t.user_id AND m.seen = 0"
        ).fetchone()["c"]
    else:
        count = conn.execute(
            "SELECT COUNT(*) c FROM support_ticket_messages m JOIN support_tickets t ON t.id = m.ticket_id "
            "WHERE t.user_id = ? AND m.from_id != t.user_id AND m.seen = 0", (user["id"],)
        ).fetchone()["c"]
    return {"count": count}


@router.get("/mine")
def mine(user: sqlite3.Row = Depends(require_user), conn: sqlite3.Connection = Depends(db_dep)):
    _autoclose(conn)
    rows = conn.execute(
        "SELECT t.*, u.username, u.avatar_url, u.role, "
        "COALESCE((SELECT body FROM support_ticket_messages WHERE ticket_id=t.id ORDER BY id DESC LIMIT 1),'') last_body, "
        "(SELECT COUNT(*) FROM support_ticket_messages m WHERE m.ticket_id=t.id AND m.from_id!=t.user_id AND m.seen=0) unread "
        "FROM support_tickets t JOIN users u ON u.id=t.user_id WHERE t.user_id=? ORDER BY t.updated_at DESC",
        (user["id"],),
    ).fetchall()
    return [_summary(r) for r in rows]


@router.post("")
def create(data: TicketCreateIn, user: sqlite3.Row = Depends(require_user),
           conn: sqlite3.Connection = Depends(db_dep)):
    rate_limit(f"ticket:new:{user['id']}", 3, 3600)
    subject, body = (data.subject or "").strip(), (data.body or "").strip()
    allowed = CATEGORIES.get(data.category)
    if not allowed or data.subcategory not in allowed:
        raise HTTPException(status_code=400, detail="Neplatná kategorie ticketu.")
    if not subject or not body:
        raise HTTPException(status_code=400, detail="Vyplň předmět i popis.")
    open_count = conn.execute(
        "SELECT COUNT(*) c FROM support_tickets WHERE user_id=? AND status!='closed'", (user["id"],)
    ).fetchone()["c"]
    if open_count >= 5:
        raise HTTPException(status_code=409, detail="Nejdřív dořeš některý z otevřených ticketů.")
    now = now_iso()
    cur = conn.execute(
        "INSERT INTO support_tickets (user_id,category,subcategory,subject,status,created_at,updated_at) "
        "VALUES (?,?,?,?, 'open', ?,?)",
        (user["id"], data.category, data.subcategory, subject[:100], now, now),
    )
    conn.execute(
        "INSERT INTO support_ticket_messages (ticket_id,from_id,body,created_at,seen) VALUES (?,?,?,?,0)",
        (cur.lastrowid, user["id"], body[:2000], now),
    )
    _event(conn, cur.lastrowid, user["id"], "created", "Ticket vytvořen")
    conn.commit()
    alerts.send(f"🎫 Nový ticket #{cur.lastrowid} od {user['username']}: {subject[:80]}",
                detail=f"{data.category}/{data.subcategory}\n{body[:300]}\nhttps://zurys.live/#/podpora/{cur.lastrowid}",
                key=f"ticket:new:{cur.lastrowid}", cooldown=0)
    return {"ok": True, "id": cur.lastrowid}


@router.get("/admin/all")
def admin_all(staff: sqlite3.Row = Depends(require_admin), conn: sqlite3.Connection = Depends(db_dep)):
    _autoclose(conn)
    rows = conn.execute(
        "SELECT t.*, u.username, u.avatar_url, u.role, "
        "COALESCE((SELECT body FROM support_ticket_messages WHERE ticket_id=t.id ORDER BY id DESC LIMIT 1),'') last_body, "
        "(SELECT COUNT(*) FROM support_ticket_messages m WHERE m.ticket_id=t.id AND m.from_id=t.user_id AND m.seen=0) unread "
        "FROM support_tickets t JOIN users u ON u.id=t.user_id "
        "ORDER BY CASE t.status WHEN 'open' THEN 0 WHEN 'in_progress' THEN 1 WHEN 'resolved' THEN 2 ELSE 3 END, "
        "t.updated_at DESC LIMIT 200"
    ).fetchall()
    return [_summary(r) for r in rows]


@router.get("/admin/{ticket_id}")
def admin_thread(ticket_id: int, staff: sqlite3.Row = Depends(require_admin),
                 conn: sqlite3.Connection = Depends(db_dep)):
    ticket = _ticket(conn, ticket_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket nenalezen.")
    messages = _messages(conn, ticket_id, ticket["user_id"])
    conn.execute(
        "UPDATE support_ticket_messages SET seen=1 WHERE ticket_id=? AND from_id=? AND seen=0",
        (ticket_id, ticket["user_id"]),
    )
    conn.commit()
    # Kontext uživatele, ať admin nemusí do admin panelu (refund/sub stížnosti)
    u = conn.execute(
        "SELECT id, username, kick_username, points, earned_total, banned, created_at "
        "FROM users WHERE id = ?", (ticket["user_id"],)
    ).fetchone()
    user_ctx = None
    if u:
        orders = conn.execute(
            "SELECT id, product_name, points_spent, status, created_at FROM orders "
            "WHERE user_id = ? ORDER BY id DESC LIMIT 3", (u["id"],)
        ).fetchall()
        ticket_count = conn.execute(
            "SELECT COUNT(*) c FROM support_tickets WHERE user_id = ?", (u["id"],)
        ).fetchone()["c"]
        user_ctx = {"id": u["id"], "username": u["username"], "kick_username": u["kick_username"],
                    "points": u["points"], "level": level_info(u["earned_total"])["level"],
                    "banned": u["banned"], "created_at": u["created_at"],
                    "ticket_count": ticket_count, "orders": [dict(o) for o in orders]}
    return {"ticket": dict(ticket), "messages": messages, "events": _events(conn, ticket_id),
            "user_ctx": user_ctx}


@router.post("/admin/{ticket_id}/reply")
def admin_reply(ticket_id: int, data: DmIn, staff: sqlite3.Row = Depends(require_admin),
                conn: sqlite3.Connection = Depends(db_dep)):
    ticket = _ticket(conn, ticket_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket nenalezen.")
    _add_message(conn, ticket, staff["id"], data.body)
    return {"ok": True}


@router.post("/admin/{ticket_id}/status/{status}")
def admin_status(ticket_id: int, status: str, staff: sqlite3.Row = Depends(require_admin),
                 conn: sqlite3.Connection = Depends(db_dep)):
    if status not in STATUSES:
        raise HTTPException(status_code=400, detail="Neplatný stav.")
    ticket = _ticket(conn, ticket_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket nenalezen.")
    conn.execute("UPDATE support_tickets SET status=?, updated_at=? WHERE id=?", (status, now_iso(), ticket_id))
    if status != ticket["status"]:
        labels = {"open": "Otevřený", "in_progress": "Řeší se", "resolved": "Vyřešený", "closed": "Uzavřený"}
        _event(conn, ticket_id, staff["id"], "status", f"{labels[ticket['status']]} → {labels[status]}")
    if status in ("resolved", "closed"):   # ruční změna stavu → zvoneček uživateli
        label = {"resolved": "je vyřešený ✅", "closed": "byl uzavřen"}[status]
        notify(conn, ticket["user_id"], "🎫", f"Ticket #{ticket_id} {label}",
               ticket["subject"][:120], f"#/podpora/{ticket_id}")
    conn.commit()
    return {"ok": True, "status": status}


@router.get("/{ticket_id}")
def thread(ticket_id: int, user: sqlite3.Row = Depends(require_user),
           conn: sqlite3.Connection = Depends(db_dep)):
    ticket = _ticket(conn, ticket_id)
    if not ticket or ticket["user_id"] != user["id"]:
        raise HTTPException(status_code=404, detail="Ticket nenalezen.")
    messages = _messages(conn, ticket_id, ticket["user_id"])
    conn.execute(
        "UPDATE support_ticket_messages SET seen=1 WHERE ticket_id=? AND from_id!=? AND seen=0",
        (ticket_id, user["id"]),
    )
    conn.commit()
    return {"ticket": dict(ticket), "messages": messages, "events": _events(conn, ticket_id)}


@router.post("/{ticket_id}/resolve")
def resolve_own(ticket_id: int, user: sqlite3.Row = Depends(require_user),
                conn: sqlite3.Connection = Depends(db_dep)):
    """Uživatel sám potvrdí, že je jeho problém vyřešený."""
    ticket = _ticket(conn, ticket_id)
    if not ticket or ticket["user_id"] != user["id"]:
        raise HTTPException(status_code=404, detail="Ticket nenalezen.")
    if ticket["status"] in ("resolved", "closed"):
        raise HTTPException(status_code=409, detail="Ticket už je vyřešený.")
    conn.execute("UPDATE support_tickets SET status='resolved', updated_at=? WHERE id=?",
                 (now_iso(), ticket_id))
    _event(conn, ticket_id, user["id"], "status", "Označeno uživatelem jako vyřešené")
    conn.commit()
    return {"ok": True, "status": "resolved"}


@router.post("/admin/{ticket_id}/refund")
def admin_refund(ticket_id: int, data: TicketRefundIn, request: Request,
                 staff: sqlite3.Row = Depends(require_admin),
                 conn: sqlite3.Connection = Depends(db_dep)):
    """Atomicky připíše refund a zapíše ho do historie ticketu i globálního auditu."""
    ticket = _ticket(conn, ticket_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket nenalezen.")
    add_points(conn, ticket["user_id"], data.amount, f"Refund: ticket #{ticket_id}", xp=False)
    _event(conn, ticket_id, staff["id"], "refund", f"Připsáno {data.amount} sedláků")
    record_audit(conn, staff, request, "user.points", f"#{ticket['user_id']} {ticket['username']}",
                 f"+{data.amount} PTS – Refund: ticket #{ticket_id}")
    conn.commit()
    balance = conn.execute("SELECT points FROM users WHERE id=?", (ticket["user_id"],)).fetchone()["points"]
    return {"ok": True, "amount": data.amount, "balance": balance}


@router.post("/{ticket_id}/reply")
def reply(ticket_id: int, data: DmIn, user: sqlite3.Row = Depends(require_user),
          conn: sqlite3.Connection = Depends(db_dep)):
    rate_limit(f"ticket:reply:{user['id']}", 5, 60)
    ticket = _ticket(conn, ticket_id)
    if not ticket or ticket["user_id"] != user["id"]:
        raise HTTPException(status_code=404, detail="Ticket nenalezen.")
    _add_message(conn, ticket, user["id"], data.body)
    return {"ok": True}


@router.post("/{ticket_id}/attach")
def attach(ticket_id: int, data: ImageUploadIn, user: sqlite3.Row = Depends(require_user),
           conn: sqlite3.Connection = Depends(db_dep)):
    """Příloha (screenshot) k ticketu – vlastník nebo admin. Validace formátu/velikosti
    je sdílená s uploady odměn (jen PNG/JPG/WEBP/GIF, max 6 MB, přípona dle MIME)."""
    import secrets
    import shutil
    from .admin import _decode_image_dataurl
    rate_limit(f"ticket:attach:{user['id']}", 5, 300)
    ticket = _ticket(conn, ticket_id)
    if not ticket or (ticket["user_id"] != user["id"] and user["role"] != "admin"):
        raise HTTPException(status_code=404, detail="Ticket nenalezen.")
    raw, ext = _decode_image_dataurl(data.data)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    stat = shutil.disk_usage(UPLOAD_DIR)
    if stat.free < 100 * 1024 * 1024:
        raise HTTPException(status_code=507, detail="Disk je plný – nemůžeme přidat přílohu.")
    name = f"ticket_{secrets.token_hex(8)}.{ext}"
    (UPLOAD_DIR / name).write_bytes(raw)
    _add_message(conn, ticket, user["id"], "📎 obrázek", image=f"/uploads/{name}")
    return {"ok": True, "url": f"/uploads/{name}"}
