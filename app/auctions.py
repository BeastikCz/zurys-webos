"""Aukce o skiny: admin vystaví předmět, diváci přihazují sedláky, nejvyšší na konci vyhrává.

Escrow: příhoz ZABLOKUJE (odečte) sedláky. Přehození → předchozímu vůdci se sedláci VRÁTÍ.
Vítěz (current_bidder při ends_at) má sedláky odečtené napořád = sink (skin doručí admin ručně).
Anti-snipe: příhoz v posledních ANTISNIPE_SEC s prodlouží konec o ANTISNIPE_SEC (ať nikdo nesnipuje).
Finalizace LAZY (na čtení/příhozu) – žádný daemon; frontend countdown polluje, takže uzavře včas.
Atomicita: podmíněný UPDATE (current_bid < můj) → vyhraje jen 1 příhoz i při souběhu (1 SQLite writer).
"""
from datetime import datetime, timezone, timedelta

from .db import now_iso

ANTISNIPE_SEC = 30          # příhoz v posledních N s prodlouží konec o N s
MAX_MINUTES = 7 * 24 * 60   # max délka aukce (7 dní)
OUTBID_REFUND_PCT = 0.5     # přehozenému se vrátí jen 50 % příhozu (zbytek propadne = sink + napětí).
                            #   Zrušení aukce vrací 100 % (není to chyba bidera). Souběh-reject vrací 100 %.


def _finalize_expired(conn) -> None:
    """Uzavře aukce po ends_at (status active → ended, vítěz = current_bidder) + notifikuje vítěze.
    Necommituje sám commit, dělá caller (volá se před čtením/příhozem). Atomické per aukce."""
    rows = conn.execute("SELECT * FROM auctions WHERE status = 'active' AND ends_at <= ?", (now_iso(),)).fetchall()
    if not rows:
        return
    from .deps import notify
    for a in rows:
        if conn.execute("UPDATE auctions SET status = 'ended', winner_id = ? WHERE id = ? AND status = 'active'",
                        (a["current_bidder_id"], a["id"])).rowcount != 1:
            continue
        if a["current_bidder_id"]:
            notify(conn, a["current_bidder_id"], "🏆", "Vyhrál jsi aukci! 🔨",
                   f"Vyhrál jsi „{a['title']}\" za {a['current_bid']} sedláků! Admin ti pošle skin. 🎉", "#/shop")


def _public(a, viewer_id=None) -> dict:
    from .db import get_conn  # jen typ; username řeší caller
    return a


def _min_next(a) -> int:
    return a["start_bid"] if (a["current_bid"] or 0) == 0 else a["current_bid"] + a["min_increment"]


def _username(conn, uid):
    if not uid:
        return None
    r = conn.execute("SELECT username FROM users WHERE id = ?", (uid,)).fetchone()
    return r["username"] if r else None


def list_public(conn) -> dict:
    """Aktivní aukce (s odpočtem, min příhozem, vůdcem) + nedávno skončené (vítězové). Lazy finalizace."""
    _finalize_expired(conn)
    conn.commit()
    now = datetime.now(timezone.utc)
    active = []
    for a in conn.execute("SELECT * FROM auctions WHERE status = 'active' ORDER BY ends_at ASC"):
        secs = max(0, int((datetime.fromisoformat(a["ends_at"]) - now).total_seconds()))
        bids = [{"username": _username(conn, b["user_id"]), "amount": b["amount"], "created_at": b["created_at"]}
                for b in conn.execute("SELECT user_id, amount, created_at FROM auction_bids "
                                      "WHERE auction_id = ? ORDER BY id DESC LIMIT 6", (a["id"],))]
        active.append({"id": a["id"], "title": a["title"], "image_url": a["image_url"] or "",
                       "current_bid": a["current_bid"], "leader": _username(conn, a["current_bidder_id"]),
                       "min_next": _min_next(a), "min_increment": a["min_increment"], "start_bid": a["start_bid"],
                       "bids_count": a["bids_count"], "seconds_left": secs, "ends_at": a["ends_at"], "recent": bids})
    ended = []
    for a in conn.execute("SELECT * FROM auctions WHERE status = 'ended' AND winner_id IS NOT NULL "
                          "ORDER BY id DESC LIMIT 6"):
        ended.append({"id": a["id"], "title": a["title"], "image_url": a["image_url"] or "",
                      "winner": _username(conn, a["winner_id"]), "final_bid": a["current_bid"]})
    return {"active": active, "ended": ended}


def bid(conn, user, auction_id: int, amount: int) -> dict:
    """Přihoď na aukci (escrow). Atomicky se staň nejvyšším; přehozenému se sedláci vrátí. Anti-snipe."""
    from .deps import try_debit, add_points, notify
    _finalize_expired(conn)
    a = conn.execute("SELECT * FROM auctions WHERE id = ?", (auction_id,)).fetchone()
    if not a:
        return {"ok": False, "error": "Aukce nenalezena."}
    if a["status"] != "active" or a["ends_at"] <= now_iso():
        conn.commit()
        return {"ok": False, "error": "Aukce už skončila."}
    if a["current_bidder_id"] == user["id"]:
        return {"ok": False, "error": "Už vedeš tuhle aukci. 😎"}
    min_next = _min_next(a)
    if amount < min_next:
        return {"ok": False, "error": f"Minimální příhoz je {min_next} sedláků."}
    # escrow: zablokuj (odečti) sedláky příhozce
    if not try_debit(conn, user["id"], amount, f"Aukce #{auction_id} – příhoz (blokace)"):
        return {"ok": False, "error": f"Nemáš dost sedláků ({amount})."}
    prev_bidder, prev_amount = a["current_bidder_id"], a["current_bid"]
    # anti-snipe: zbývá < N s → prodluž konec
    new_ends = a["ends_at"]
    if (datetime.fromisoformat(a["ends_at"]) - datetime.now(timezone.utc)).total_seconds() < ANTISNIPE_SEC:
        new_ends = (datetime.now(timezone.utc) + timedelta(seconds=ANTISNIPE_SEC)).isoformat()
    # ATOMICKY se staň nejvyšším – jen pokud je můj příhoz pořád > current (anti-souběh)
    won = conn.execute(
        "UPDATE auctions SET current_bid = ?, current_bidder_id = ?, bids_count = bids_count + 1, ends_at = ? "
        "WHERE id = ? AND status = 'active' AND current_bid < ?",
        (amount, user["id"], new_ends, auction_id, amount)).rowcount == 1
    if not won:
        add_points(conn, user["id"], amount, f"Aukce #{auction_id} – vrácení (předběhnut)", xp=False)
        conn.commit()
        return {"ok": False, "error": "Někdo přihodil dřív – zkus víc. 🔨"}
    if prev_bidder:                                   # přehozenému se vrátí jen OUTBID_REFUND_PCT (zbytek propadne = sink)
        refund = int(round(prev_amount * OUTBID_REFUND_PCT))
        lost = prev_amount - refund
        add_points(conn, prev_bidder, refund, f"Aukce #{auction_id} – vrácení {int(OUTBID_REFUND_PCT * 100)} % (přehozen)", xp=False)
        notify(conn, prev_bidder, "🔨", "Přehodili tě v aukci!",
               f"Někdo přihodil víc na „{a['title']}\". Vráceno {refund} sedláků (50 %), {lost} propadlo. Přihoď znova? 💰", "#/shop")
    conn.execute("INSERT INTO auction_bids (auction_id, user_id, amount, created_at) VALUES (?,?,?,?)",
                 (auction_id, user["id"], amount, now_iso()))
    conn.commit()
    bal = conn.execute("SELECT points FROM users WHERE id = ?", (user["id"],)).fetchone()["points"]
    return {"ok": True, "balance": bal, "current_bid": amount, "ends_at": new_ends,
            "extended": new_ends != a["ends_at"]}


# ---- Admin ----
def create(conn, title: str, image_url: str, start_bid: int, min_increment: int, minutes: int) -> dict:
    title = (title or "").strip()
    if not title:
        return {"ok": False, "error": "Zadej název předmětu."}
    minutes = max(1, min(MAX_MINUTES, int(minutes)))
    start_bid = max(1, int(start_bid))
    min_increment = max(1, int(min_increment))
    ends = (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()
    cur = conn.execute(
        "INSERT INTO auctions (title, image_url, start_bid, min_increment, current_bid, status, ends_at, created_at) "
        "VALUES (?, ?, ?, ?, 0, 'active', ?, ?)",
        (title[:120], (image_url or "").strip()[:500], start_bid, min_increment, ends, now_iso()))
    conn.commit()
    return {"ok": True, "id": cur.lastrowid, "ends_at": ends}


def cancel(conn, auction_id: int) -> dict:
    """Zruší aktivní aukci → vrátí aktuálnímu vůdci jeho blokaci (escrow). Žádný vítěz."""
    from .deps import add_points, notify
    a = conn.execute("SELECT * FROM auctions WHERE id = ?", (auction_id,)).fetchone()
    if not a:
        return {"ok": False, "error": "Aukce nenalezena."}
    if a["status"] != "active":
        return {"ok": False, "error": "Aukce není aktivní."}
    if conn.execute("UPDATE auctions SET status = 'cancelled' WHERE id = ? AND status = 'active'",
                    (auction_id,)).rowcount != 1:
        conn.commit()
        return {"ok": False, "error": "Aukce mezitím skončila."}
    if a["current_bidder_id"]:
        add_points(conn, a["current_bidder_id"], a["current_bid"], f"Aukce #{auction_id} – zrušeno (vráceno)", xp=False)
        notify(conn, a["current_bidder_id"], "🔨", "Aukce zrušena",
               f"Aukce „{a['title']}\" byla zrušena. Sedláci ({a['current_bid']}) vráceny. 💰", "#/shop")
    conn.commit()
    return {"ok": True, "refunded": a["current_bid"] if a["current_bidder_id"] else 0}


def admin_list(conn) -> list:
    """Všechny aukce pro admina (vč. vůdce/vítěze + jeho kick nicku pro doručení skinu)."""
    _finalize_expired(conn)
    conn.commit()
    out = []
    for a in conn.execute("SELECT * FROM auctions ORDER BY id DESC LIMIT 50"):
        win = a["winner_id"] or a["current_bidder_id"]
        wrow = conn.execute("SELECT username, kick_username FROM users WHERE id = ?", (win,)).fetchone() if win else None
        out.append({"id": a["id"], "title": a["title"], "image_url": a["image_url"] or "",
                    "status": a["status"], "current_bid": a["current_bid"], "bids_count": a["bids_count"],
                    "ends_at": a["ends_at"],
                    "who": (wrow["username"] if wrow else None),
                    "who_kick": (wrow["kick_username"] if wrow else None)})
    return out
