"""Jednorázový import ručně dodaných tiketů do tomboly **AWP | Printstream (WW)**.

Stejný princip jako `navaja_import.py`: data = log jednotlivých tiketů (z chatu /
giveaway). Pro každý nick přidá daný počet tiketů. Účet, který na webu ještě není,
se založí jako **ghost** (bez Kick loginu, `kick_username = nick.lower()`) – po
přihlášení přes Kick stejným nickem si ho člověk převezme i s tikety. (V praxi tady
existují všichni, ghost je jen pojistka.)

Vlastnosti:
  • idempotentní – řídí se flagem `app_settings['awp_import_v1']`, podruhé se přeskočí
  • PŘIDÁVÁ k tomu, co v tombole už je (nemaže existující tikety)
  • plně VRATNÉ – ukládáme ID vložených raffle_entries i nově založených účtů,
    `undo(conn)` je smaže (ghosty jen pokud zůstaly prázdné a bez Kick loginu)

Pozn.: záznam „Všichni" (2×) z logu je vynechán – není to konkrétní osoba, nelze mu
přiřadit los. Sklad tomboly (`products.stock`) záměrně NEsnižujeme ani body neúčtujeme
– jde o ruční backfill (ne nákup), losuje se z `raffle_entries`, sklad je informativní.
Cap (max_per_person_pct) hlídá jen self-service nákup, tenhle backfill ho záměrně obchází.
"""
import json
import sqlite3

from .db import now_iso, get_setting, set_setting

FLAG = "awp_import_v1"

# (nick, počet tiketů) – z dodaného logu, „Všichni" (2×) vynecháno.
# Celkem 13 jmen / 56 tiketů.
TICKETS = [
    ("Itz_Ok", 10),
    ("Interaty", 8),
    ("morematysek10", 5),
    ("Numero_21", 5),
    ("ja_dejvidd", 5),
    ("nel_iii", 5),
    ("ygortekk", 5),
    ("Vic7orKing", 5),
    ("dejvikkamo", 3),
    ("akoff7", 2),
    ("filipeeklol", 1),
    ("Vojtik58", 1),
    ("VypicenaZeleninka", 1),
]


def find_awp(conn: sqlite3.Connection):
    """ID tomboly AWP | Printstream (dle názvu, ať nezávisíme na natvrdo zadaném id)."""
    row = conn.execute(
        "SELECT id FROM products WHERE type = 'raffle' "
        "AND name LIKE '%AWP%' AND name LIKE '%Printstream%' ORDER BY id LIMIT 1"
    ).fetchone()
    return row["id"] if row else None


def _get_or_create_user(conn: sqlite3.Connection, nick: str, ts: str, created: list):
    """Vrátí id uživatele dle kick_username; když není, založí ghost účet (bez Kicku)."""
    key = (nick or "").strip().lstrip("@").lower()
    if not key or len(key) > 64:
        return None
    row = conn.execute("SELECT id FROM users WHERE kick_username = ?", (key,)).fetchone()
    if row:
        return row["id"]
    display = ((nick or "").strip().lstrip("@")[:32]) or key
    cur = conn.execute(
        "INSERT INTO users (kick_username, username, points, role, created_at) "
        "VALUES (?, ?, 0, 'user', ?)",
        (key, display, ts),
    )
    created.append(cur.lastrowid)
    return cur.lastrowid


def run(conn: sqlite3.Connection) -> dict:
    """Spustí import (pokud ještě neproběhl). Idempotentní, sám commitne."""
    if get_setting(conn, FLAG, ""):
        return {"skipped": "already done"}
    pid = find_awp(conn)
    if not pid:
        return {"skipped": "no AWP raffle"}

    ts = now_iso()
    entry_ids: list = []
    created_users: list = []
    added = 0
    for nick, count in TICKETS:
        if count <= 0:
            continue
        uid = _get_or_create_user(conn, nick, ts, created_users)
        if uid is None:
            continue
        for _ in range(count):
            cur = conn.execute(
                "INSERT INTO raffle_entries (product_id, user_id, created_at) VALUES (?, ?, ?)",
                (pid, uid, ts),
            )
            entry_ids.append(cur.lastrowid)
            added += 1

    set_setting(conn, FLAG, json.dumps({
        "product_id": pid,
        "ts": ts,
        "entry_ids": entry_ids,
        "created_users": created_users,
        "tickets_added": added,
        "names": len(TICKETS),
    }))
    conn.commit()
    return {"product_id": pid, "tickets_added": added,
            "accounts_created": len(created_users), "names": len(TICKETS)}


def undo(conn: sqlite3.Connection) -> dict:
    """Vrátí import zpět: smaže vložené tikety a prázdné ghost účty. Vyčistí flag."""
    raw = get_setting(conn, FLAG, "")
    if not raw:
        return {"skipped": "nothing to undo"}
    data = json.loads(raw)
    entry_ids = data.get("entry_ids", [])
    created_users = data.get("created_users", [])

    deleted_entries = 0
    if entry_ids:
        q = ",".join("?" * len(entry_ids))
        deleted_entries = conn.execute(
            f"DELETE FROM raffle_entries WHERE id IN ({q})", entry_ids
        ).rowcount

    deleted_users = 0
    for uid in created_users:
        u = conn.execute("SELECT kick_id, points FROM users WHERE id = ?", (uid,)).fetchone()
        if not u or u["kick_id"] or (u["points"] or 0) != 0:
            continue  # mezitím se přihlásil přes Kick / má body → nemazat
        has_entry = conn.execute(
            "SELECT 1 FROM raffle_entries WHERE user_id = ? LIMIT 1", (uid,)
        ).fetchone()
        has_order = conn.execute(
            "SELECT 1 FROM orders WHERE user_id = ? LIMIT 1", (uid,)
        ).fetchone()
        if not has_entry and not has_order:
            conn.execute("DELETE FROM users WHERE id = ?", (uid,))
            deleted_users += 1

    set_setting(conn, FLAG, "")  # umožní případné opětovné spuštění
    conn.commit()
    return {"entries_deleted": deleted_entries, "ghosts_deleted": deleted_users}
