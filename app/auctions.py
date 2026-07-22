"""Aukce o skiny: admin vystaví předmět, diváci přihazují sedláky, nejvyšší na konci vyhrává.

Escrow: příhoz ZABLOKUJE (odečte) sedláky. Přehození → předchozímu vůdci se vrátí 100 %.
Sink = jednorázový vstupní poplatek při PRVNÍM příhozu do aukce (ENTRY_FEE_PCT z něj, strop
ENTRY_FEE_CAP) — další příhozy do téže aukce jsou bez poplatku, bid war nekrvácí.
Vítěz (current_bidder při ends_at) má sedláky odečtené. U skinu webu jsou sink; u komunitního skinu
zůstanou v escrow do potvrzení převzetí, potom dostane prodávající 95 % a 5 % se spálí.
Anti-snipe: příhoz v posledních ANTISNIPE_SEC s prodlouží konec o ANTISNIPE_SEC (ať nikdo nesnipuje).
Finalizace LAZY (na čtení/příhozu) – žádný daemon; frontend countdown polluje, takže uzavře včas.
Atomicita: podmíněný UPDATE (current_bid < můj) → vyhraje jen 1 příhoz i při souběhu (1 SQLite writer).
"""
import re
from datetime import datetime, timezone, timedelta

from .db import now_iso


def _safe_image_url(url: str) -> str:
    """image_url jde do CSS background-image:url('...') → zahoď CSS-breakout znaky a vynuť bezpečné schéma.
    Admin-only pole, ale defense-in-depth proti CSS/HTML injekci (Steam/CDN URL tyhle znaky nemají)."""
    img = re.sub(r"""['"()<>\\`\s]""", "", (url or "").strip())[:500]
    return img if re.match(r"^(https?://|/)", img) else ""

ANTISNIPE_SEC = 30          # příhoz v posledních N s prodlouží konec o N s
MAX_MINUTES = 7 * 24 * 60   # max délka aukce (7 dní)
ENTRY_FEE_PCT = 0.10        # jednorázový vstupní poplatek = % z PRVNÍHO příhozu do aukce (sink)…
ENTRY_FEE_CAP = 5000        # …se stropem: velké aukce platí flat 5k, malé úměrně míň. Přehození vrací 100 %.
                            #   Zrušení aukce vrací escrow i poplatky (není to chyba bidera). Souběh-reject vrací vše.
MARKET_FEE_PCT = 5          # komunitní komisní nabídka: prodávající dostane 95 %, zbytek je sink


def wear_from_float(value: float) -> str:
    """Oficiální CS intervaly; dolní mez patří do nové kategorie."""
    if value < 0.07:
        return "FN"
    if value < 0.15:
        return "MW"
    if value < 0.38:
        return "FT"
    if value < 0.45:
        return "WW"
    return "BS"


def _start_market_delivery(conn, auction) -> bool:
    """Po prodeji nechá cenu v escrow a otevře předání mezi prodávajícím a vítězem."""
    if not auction["seller_user_id"] or not auction["winner_id"]:
        return False
    if conn.execute(
        "UPDATE auctions SET delivery_status='awaiting_delivery',sold_at=? "
        "WHERE id=? AND delivery_status=''",
        (now_iso(), auction["id"]),
    ).rowcount != 1:
        return False
    from .deps import notify
    winner = _username(conn, auction["winner_id"]) or "neznámý uživatel"
    seller = _username(conn, auction["seller_user_id"]) or "prodávající"
    notify(conn, auction["seller_user_id"], "📦", "Skin se prodal – čeká na odeslání",
           f"„{auction['title']}“ koupil {winner}. Cena zůstává v escrow, dokud kupující nepotvrdí převzetí.", "#/shop")
    notify(conn, auction["winner_id"], "🛡️", "Nákup je chráněný escrow",
           f"„{auction['title']}“ ti pošle {seller}. Potvrď převzetí až po kontrole skinu.", "#/shop")
    return True


def _release_seller(conn, auction, by_admin: bool = False) -> tuple[int, int] | None:
    """Atomicky uvolní escrow prodávajícímu právě jednou."""
    seller_id = auction["seller_user_id"]
    price = int(auction["current_bid"] or 0)
    if not seller_id or not auction["winner_id"] or price <= 0 or auction["seller_paid_at"]:
        return None
    fee = max(1, (price * MARKET_FEE_PCT + 99) // 100)
    payout = max(0, price - fee)
    completed_at = now_iso()
    if conn.execute(
        "UPDATE auctions SET seller_payout=?,market_fee=?,seller_paid_at=?,"
        "delivery_status='completed',delivery_completed_at=? "
        "WHERE id=? AND seller_paid_at IS NULL AND delivery_status<>'refunded'",
        (payout, fee, completed_at, completed_at, auction["id"]),
    ).rowcount != 1:
        return None
    from .deps import add_points, notify
    if payout:
        add_points(conn, seller_id, payout,
                   f"Komunitní trh #{auction['id']} – výnos po {MARKET_FEE_PCT}% poplatku", xp=False)
    winner_name = _username(conn, auction["winner_id"]) or "kupující"
    notify(conn, seller_id, "🌾", "Obchod dokončen",
           (f"Admin uvolnil escrow za „{auction['title']}“." if by_admin else
            f"{winner_name} potvrdil převzetí „{auction['title']}“.")
           + f" Dostáváš {payout}; poplatek trhu je {fee}.", "#/shop")
    notify(conn, auction["winner_id"], "✅", "Obchod dokončen",
           (f"Admin dokončil obchod „{auction['title']}“." if by_admin else
            f"Převzetí „{auction['title']}“ bylo potvrzeno. Díky za bezpečný obchod."), "#/shop")
    return payout, fee


def _finalize_expired(conn) -> None:
    """Uzavře aukce po ends_at (status active → ended, vítěz = current_bidder) + notifikuje vítěze.
    Necommituje sám commit, dělá caller (volá se před čtením/příhozem). Atomické per aukce."""
    now = now_iso()
    rows = conn.execute("SELECT id FROM auctions WHERE status = 'active' AND ends_at <= ?", (now,)).fetchall()
    if not rows:
        return
    from .deps import notify
    for a in rows:
        # vítěz = current_bidder ATOMICKY ze živého sloupce (ne stale snapshot); gate i na ends_at,
        # ať těsný anti-snipe příhoz (posune ends_at do budoucna) tohle uzavření PROHRAJE a aukce běží dál.
        if conn.execute("UPDATE auctions SET status = 'ended', winner_id = current_bidder_id "
                        "WHERE id = ? AND status = 'active' AND ends_at <= ?", (a["id"], now)).rowcount != 1:
            continue
        fa = conn.execute("SELECT * FROM auctions WHERE id = ?", (a["id"],)).fetchone()
        if fa["winner_id"]:
            if fa["seller_user_id"]:
                _start_market_delivery(conn, fa)
            else:
                notify(conn, fa["winner_id"], "🏆", "Vyhrál jsi aukci! 🔨",
                       f"Vyhrál jsi „{fa['title']}\" za {fa['current_bid']} sedláků! Admin ti pošle skin. 🎉", "#/shop")
            if fa["chat_announce"]:
                _announce_async(f"🏆 AUKCE DOKLEPNUTÁ! „{fa['title']}\" bere {_username(conn, fa['winner_id'])} "
                                f"za {fa['current_bid']} sedláků. 🔨🌾")


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


def _is_sub(user) -> bool:
    try:
        return bool(user["is_sub"]) or user["role"] in ("admin", "broadcaster", "mod")
    except (KeyError, IndexError, TypeError):
        return False


def _announce_async(text: str) -> None:
    """Hláška do Kick chatu v BACKGROUND threadu (Kick API je synchronní HTTP → nesmí blokovat
    request handler / 1 SQLite writer). Vlastní conn. Stejný pattern jako subgoal._announce_async."""
    import threading

    def _bg():
        try:
            from .db import get_conn
            from . import kickbot
            c = get_conn()
            try:
                kickbot.send_message(c, text, kind="system")
            finally:
                c.close()
        except Exception:
            pass
    threading.Thread(target=_bg, daemon=True).start()


def list_public(conn) -> dict:
    """Aktivní aukce (s odpočtem, min příhozem, vůdcem) + nedávno skončené (vítězové). Lazy finalizace."""
    _finalize_expired(conn)
    conn.commit()
    now = datetime.now(timezone.utc)
    completed_sales = {r["seller_user_id"]: r["c"] for r in conn.execute(
        "SELECT seller_user_id,COUNT(*) c FROM auctions WHERE delivery_status='completed' "
        "AND seller_user_id IS NOT NULL GROUP BY seller_user_id"
    )}
    active = []
    for a in conn.execute("SELECT * FROM auctions WHERE status = 'active' ORDER BY ends_at ASC"):
        secs = max(0, int((datetime.fromisoformat(a["ends_at"]) - now).total_seconds()))
        # „going once" hype: 1× když zbývá < ANTISNIPE_SEC s (atomicky přes flag → poll ho neopakuje)
        if 0 < secs <= ANTISNIPE_SEC and a["chat_announce"] and not a["going_once_sent"] and a["current_bidder_id"]:
            if conn.execute("UPDATE auctions SET going_once_sent = 1 WHERE id = ? AND going_once_sent = 0",
                            (a["id"],)).rowcount == 1:
                conn.commit()
                _announce_async(f"⏳ POSLEDNÍ VTEŘINY na „{a['title']}\"! Vede {_username(conn, a['current_bidder_id'])} "
                                f"za {a['current_bid']}. Kdo přebije?! 🔨🔥")
        # historie příhozů se veřejně neposílá (soukromí dražitelů) – admin ji vidí v admin_list
        active.append({"id": a["id"], "title": a["title"], "image_url": a["image_url"] or "",
                       "current_bid": a["current_bid"], "leader": _username(conn, a["current_bidder_id"]),
                       "min_next": _min_next(a), "min_increment": a["min_increment"], "start_bid": a["start_bid"],
                       "bids_count": a["bids_count"], "seconds_left": secs, "ends_at": a["ends_at"],
                       "buy_now": a["buy_now"] or 0, "sub_only": bool(a["sub_only"]),
                       "seller": _username(conn, a["seller_user_id"]), "sale_type": a["sale_type"],
                       "description": a["market_description"] or "", "wear": a["wear"] or "",
                       "float_value": a["float_value"],
                       "seller_completed_sales": completed_sales.get(a["seller_user_id"], 0)})
    ended = []
    for a in conn.execute("SELECT * FROM auctions WHERE status = 'ended' AND winner_id IS NOT NULL "
                          "ORDER BY id DESC LIMIT 6"):
        ended.append({"id": a["id"], "title": a["title"], "image_url": a["image_url"] or "",
                      "winner": _username(conn, a["winner_id"]), "final_bid": a["current_bid"],
                      "seller": _username(conn, a["seller_user_id"]), "sale_type": a["sale_type"]})
    return {"active": active, "ended": ended, "top_bidders": top_bidders(conn)}


def top_bidders(conn, limit: int = 5) -> list:
    """Žebříček dražitelů: kdo vyhrál nejvíc aukcí (a utratil nejvíc) – status + rivalita."""
    rows = conn.execute(
        "SELECT a.winner_id, COUNT(*) wins, COALESCE(SUM(a.current_bid),0) spent FROM auctions a "
        "WHERE a.status = 'ended' AND a.winner_id IS NOT NULL AND a.sale_type = 'auction' "
        "GROUP BY a.winner_id ORDER BY wins DESC, spent DESC LIMIT ?", (limit,)).fetchall()
    return [{"username": _username(conn, r["winner_id"]), "wins": r["wins"], "spent": r["spent"]} for r in rows]


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
    if a["sale_type"] == "fixed":
        return {"ok": False, "error": "Tohle je nabídka za pevnou cenu – použij Koupit."}
    if a["seller_user_id"] == user["id"]:
        return {"ok": False, "error": "Vlastní skin koupit ani dražit nemůžeš. 😄"}
    if a["sub_only"] and not _is_sub(user):
        return {"ok": False, "error": "Tahle aukce je jen pro suby. 💜"}
    if a["current_bidder_id"] == user["id"]:
        return {"ok": False, "error": "Už vedeš tuhle aukci. 😎"}
    min_next = _min_next(a)
    if amount < min_next:
        return {"ok": False, "error": f"Minimální příhoz je {min_next} sedláků."}
    if a["buy_now"] and amount >= a["buy_now"]:        # příhoz nesmí dorůst/přerůst kup-teď cenu → drž current_bid < buy_now
        return {"ok": False, "error": f"Tolik už ne — radši klikni 💎 Kup teď za {a['buy_now']} sedláků."}
    # vstupní poplatek jen při PRVNÍM příhozu do téhle aukce (další příhozy zdarma → bid war nekrvácí)
    first_bid = conn.execute("SELECT 1 FROM auction_bids WHERE auction_id = ? AND user_id = ? LIMIT 1",
                             (auction_id, user["id"])).fetchone() is None
    fee = min(ENTRY_FEE_CAP, int(round(amount * ENTRY_FEE_PCT))) if first_bid else 0
    # escrow: zablokuj (odečti) sedláky příhozce (+ případný poplatek v jedné transakci)
    if not try_debit(conn, user["id"], amount + fee,
                     f"Aukce #{auction_id} – příhoz (blokace)" + (f" + vstupní poplatek {fee}" if fee else "")):
        return {"ok": False, "error": f"Nemáš dost sedláků ({amount + fee}" + (f" vč. vstupního poplatku {fee}" if fee else "") + ")."}
    prev_bidder, prev_amount = a["current_bidder_id"], a["current_bid"]
    # anti-snipe: zbývá < N s → prodluž konec (a povol nové „going once" – reset flag)
    new_ends, extended_flag = a["ends_at"], 0
    if (datetime.fromisoformat(a["ends_at"]) - datetime.now(timezone.utc)).total_seconds() < ANTISNIPE_SEC:
        new_ends = (datetime.now(timezone.utc) + timedelta(seconds=ANTISNIPE_SEC)).isoformat()
        extended_flag = 1
    # ATOMICKY se staň nejvyšším – jen pokud je můj příhoz pořád > current A nejsem už vůdce já
    # (poslední podmínka brání souběžnému self-outbidu dvou mých příhozů → ztráta vlastního escrow)
    won = conn.execute(
        "UPDATE auctions SET current_bid = ?, current_bidder_id = ?, bids_count = bids_count + 1, ends_at = ?, "
        "going_once_sent = CASE WHEN ? = 1 THEN 0 ELSE going_once_sent END "
        "WHERE id = ? AND status = 'active' AND current_bid < ? AND (current_bidder_id IS NULL OR current_bidder_id <> ?)",
        (amount, user["id"], new_ends, extended_flag, auction_id, amount, user["id"])).rowcount == 1
    if not won:
        add_points(conn, user["id"], amount + fee, f"Aukce #{auction_id} – vrácení (předběhnut)", xp=False)
        conn.commit()
        return {"ok": False, "error": "Někdo přihodil dřív – zkus víc. 🔨"}
    if prev_bidder:                                   # přehozenému se vrátí 100 % (sink je vstupní poplatek, ne srážka)
        add_points(conn, prev_bidder, prev_amount, f"Aukce #{auction_id} – vrácení 100 % (přehozen)", xp=False)
        notify(conn, prev_bidder, "🔨", "Přehodili tě v aukci!",
               f"Někdo přihodil víc na „{a['title']}\". Vráceno {prev_amount} sedláků (100 %). Přihoď znova? 💰", "#/shop")
    conn.execute("INSERT INTO auction_bids (auction_id, user_id, amount, fee, created_at) VALUES (?,?,?,?,?)",
                 (auction_id, user["id"], amount, fee, now_iso()))
    conn.commit()
    if a["chat_announce"]:                            # hype do Kick chatu (background thread)
        _announce_async(f"🔨 {user['username']} přihodil {amount} na „{a['title']}\"! Kdo dá víc? 💰")
    bal = conn.execute("SELECT points FROM users WHERE id = ?", (user["id"],)).fetchone()["points"]
    return {"ok": True, "balance": bal, "current_bid": amount, "ends_at": new_ends,
            "extended": new_ends != a["ends_at"], "fee": fee}


def buy_now(conn, user, auction_id: int) -> dict:
    """Kup teď: zaplať buy_now cenu → okamžitá výhra + konec aukce. Předchozímu vůdci se vrátí 100 %."""
    from .deps import try_debit, add_points, notify
    _finalize_expired(conn)
    a = conn.execute("SELECT * FROM auctions WHERE id = ?", (auction_id,)).fetchone()
    if not a:
        return {"ok": False, "error": "Aukce nenalezena."}
    if a["status"] != "active" or a["ends_at"] <= now_iso():
        conn.commit()
        return {"ok": False, "error": "Aukce už skončila."}
    if a["seller_user_id"] == user["id"]:
        return {"ok": False, "error": "Vlastní skin koupit ani dražit nemůžeš. 😄"}
    if not a["buy_now"] or a["buy_now"] <= 0:
        return {"ok": False, "error": "Tahle aukce nemá kup-teď cenu."}
    if a["sub_only"] and not _is_sub(user):
        return {"ok": False, "error": "Tahle aukce je jen pro suby. 💜"}
    price = a["buy_now"]
    if not try_debit(conn, user["id"], price, f"Aukce #{auction_id} – kup teď 💎"):
        return {"ok": False, "error": f"Nemáš dost sedláků ({price})."}
    # try_debit zahájil write-txn (drží lock) → re-SELECT vidí REÁLNÉHO aktuálního vůdce/cenu (ne stale snapshot `a`).
    cur = conn.execute("SELECT status, current_bidder_id, current_bid FROM auctions WHERE id = ?", (auction_id,)).fetchone()
    if cur["status"] != "active" or (cur["current_bid"] or 0) >= price:
        # mezitím skončila NEBO příhoz dorovnal/přesáhl kup-teď cenu → vykoupení nedává smysl, vrať vše
        add_points(conn, user["id"], price, f"Aukce #{auction_id} – vrácení (kup teď selhal)", xp=False)
        conn.commit()
        return {"ok": False, "error": "Aukce už nejde vykoupit (skončila, nebo příhoz dosáhl kup-teď ceny)."}
    real_bidder, real_bid = cur["current_bidder_id"], cur["current_bid"]
    if conn.execute("UPDATE auctions SET status = 'ended', winner_id = ?, current_bid = ?, current_bidder_id = ? "
                    "WHERE id = ? AND status = 'active'",
                    (user["id"], price, user["id"], auction_id)).rowcount != 1:
        add_points(conn, user["id"], price, f"Aukce #{auction_id} – vrácení (kup teď selhal)", xp=False)
        conn.commit()
        return {"ok": False, "error": "Aukce právě skončila jinak."}
    if real_bidder == user["id"]:                     # kupující byl vůdce → vrať mu jeho escrow (neplatí 2×)
        add_points(conn, user["id"], real_bid, f"Aukce #{auction_id} – vrácení escrow (kup teď)", xp=False)
    elif real_bidder:                                 # jiný vůdce → vrať mu 100 % (vykoupen, ne přehozen)
        add_points(conn, real_bidder, real_bid, f"Aukce #{auction_id} – vrácení (vykoupeno)", xp=False)
        notify(conn, real_bidder, "🔨", "Aukce vykoupena",
               f"Někdo koupil „{a['title']}\" za kup-teď cenu. Sedláci ({real_bid}) vráceny. 💰", "#/shop")
    conn.execute("INSERT INTO auction_bids (auction_id, user_id, amount, created_at) VALUES (?,?,?,?)",
                 (auction_id, user["id"], price, now_iso()))
    sold = conn.execute("SELECT * FROM auctions WHERE id = ?", (auction_id,)).fetchone()
    market_escrow = _start_market_delivery(conn, sold)
    conn.commit()
    if a["chat_announce"]:
        if a["sale_type"] == "fixed":
            _announce_async(f"💎 {user['username']} KOUPIL na Trhu „{a['title']}\" za {price} sedláků! 🌾")
        else:
            _announce_async(f"💎 {user['username']} VYKOUPIL „{a['title']}\" za {price} (kup teď)! Aukce končí. 🏆🔨")
    bal = conn.execute("SELECT points FROM users WHERE id = ?", (user["id"],)).fetchone()["points"]
    return {"ok": True, "balance": bal, "price": price, "title": a["title"],
            "market_escrow": market_escrow}


def mark_delivered(conn, user, auction_id: int) -> dict:
    """Prodávající označí skin jako odeslaný; peníze zůstávají v escrow."""
    from .deps import notify
    a = conn.execute("SELECT * FROM auctions WHERE id=?", (auction_id,)).fetchone()
    if not a or not a["seller_user_id"] or a["status"] != "ended" or not a["winner_id"]:
        return {"ok": False, "error": "Komunitní obchod neexistuje."}
    if a["seller_user_id"] != user["id"]:
        return {"ok": False, "error": "Odeslání může potvrdit jen prodávající."}
    if a["delivery_status"] != "awaiting_delivery":
        return {"ok": False, "error": "Obchod už není ve stavu čekání na odeslání."}
    sent_at = now_iso()
    if conn.execute(
        "UPDATE auctions SET delivery_status='delivered',delivery_sent_at=? "
        "WHERE id=? AND delivery_status='awaiting_delivery'",
        (sent_at, auction_id),
    ).rowcount != 1:
        conn.rollback()
        return {"ok": False, "error": "Stav obchodu se mezitím změnil."}
    notify(conn, a["winner_id"], "📦", "Prodávající označil skin jako odeslaný",
           f"Zkontroluj „{a['title']}“ ve Steamu. Převzetí potvrď až potom.", "#/shop")
    conn.commit()
    return {"ok": True, "delivery_status": "delivered", "delivery_sent_at": sent_at}


def confirm_delivery(conn, user, auction_id: int) -> dict:
    """Kupující potvrdí převzetí a tím uvolní 95 % ceny prodávajícímu."""
    a = conn.execute("SELECT * FROM auctions WHERE id=?", (auction_id,)).fetchone()
    if not a or not a["seller_user_id"] or a["status"] != "ended" or not a["winner_id"]:
        return {"ok": False, "error": "Komunitní obchod neexistuje."}
    if a["winner_id"] != user["id"]:
        return {"ok": False, "error": "Převzetí může potvrdit jen kupující."}
    if a["delivery_status"] != "delivered":
        return {"ok": False, "error": "Prodávající zatím neoznačil skin jako odeslaný."}
    settled = _release_seller(conn, a)
    if settled is None:
        conn.rollback()
        return {"ok": False, "error": "Escrow už bylo vyřízeno."}
    conn.commit()
    return {"ok": True, "delivery_status": "completed",
            "seller_payout": settled[0], "market_fee": settled[1]}


def dispute_delivery(conn, user, auction_id: int, reason: str) -> dict:
    """Kupující nebo prodávající zastaví předání pro ruční rozhodnutí admina."""
    from .deps import notify
    a = conn.execute("SELECT * FROM auctions WHERE id=?", (auction_id,)).fetchone()
    if not a or not a["seller_user_id"] or not a["winner_id"] or a["status"] != "ended":
        return {"ok": False, "error": "Komunitní obchod neexistuje."}
    if user["id"] not in (a["seller_user_id"], a["winner_id"]):
        return {"ok": False, "error": "Spor může otevřít jen účastník obchodu."}
    if a["delivery_status"] not in ("awaiting_delivery", "delivered"):
        return {"ok": False, "error": "Tento obchod už nejde nahlásit."}
    reason = (reason or "").strip()[:500]
    if not reason:
        return {"ok": False, "error": "Napiš stručný důvod sporu."}
    if conn.execute(
        "UPDATE auctions SET delivery_status='disputed',dispute_reason=?,dispute_by_id=? "
        "WHERE id=? AND delivery_status IN ('awaiting_delivery','delivered')",
        (reason, user["id"], auction_id),
    ).rowcount != 1:
        conn.rollback()
        return {"ok": False, "error": "Stav obchodu se mezitím změnil."}
    other_id = a["winner_id"] if user["id"] == a["seller_user_id"] else a["seller_user_id"]
    notify(conn, other_id, "⚠️", "U obchodu byl otevřen spor",
           f"Obchod „{a['title']}“ teď zkontroluje admin. Escrow zůstává zamčené.", "#/shop")
    conn.commit()
    return {"ok": True, "delivery_status": "disputed"}


def resolve_delivery(conn, auction_id: int, action: str) -> dict:
    """Admin uvolní escrow prodávajícímu, nebo refunduje kupujícímu cenu i jeho vstupní fee."""
    from .deps import add_points, notify
    a = conn.execute("SELECT * FROM auctions WHERE id=?", (auction_id,)).fetchone()
    if not a or not a["seller_user_id"] or a["status"] != "ended":
        return {"ok": False, "error": "Komunitní obchod neexistuje."}
    if not a["winner_id"]:
        return {"ok": False, "error": "Aukce skončila bez kupujícího – není co refundovat ani vyplácet."}
    if a["delivery_status"] in ("completed", "refunded") or a["seller_paid_at"]:
        return {"ok": False, "error": "Escrow už bylo vyřízeno."}
    if action == "release":
        settled = _release_seller(conn, a, by_admin=True)
        if settled is None:
            conn.rollback()
            return {"ok": False, "error": "Escrow se nepodařilo uvolnit."}
        conn.commit()
        return {"ok": True, "delivery_status": "completed",
                "seller_payout": settled[0], "market_fee": settled[1]}
    if action != "refund":
        return {"ok": False, "error": "Neplatné rozhodnutí."}
    completed_at = now_iso()
    if conn.execute(
        "UPDATE auctions SET delivery_status='refunded',delivery_completed_at=? "
        "WHERE id=? AND seller_paid_at IS NULL AND delivery_status NOT IN ('completed','refunded')",
        (completed_at, auction_id),
    ).rowcount != 1:
        conn.rollback()
        return {"ok": False, "error": "Escrow už bylo vyřízeno."}
    fee = conn.execute(
        "SELECT COALESCE(SUM(fee),0) f FROM auction_bids WHERE auction_id=? AND user_id=?",
        (auction_id, a["winner_id"]),
    ).fetchone()["f"]
    refunded = int(a["current_bid"] or 0) + int(fee or 0)
    add_points(conn, a["winner_id"], refunded, f"Komunitní trh #{auction_id} – refund escrow", xp=False)
    notify(conn, a["winner_id"], "↩️", "Escrow vráceno",
           f"Za „{a['title']}“ ti admin vrátil {refunded} sedláků.", "#/shop")
    notify(conn, a["seller_user_id"], "↩️", "Obchod refundován",
           f"Admin refundoval obchod „{a['title']}“. Výplata nebyla provedena.", "#/shop")
    conn.commit()
    return {"ok": True, "delivery_status": "refunded", "refunded": refunded}


# ---- Admin ----
def create(conn, title: str, image_url: str, start_bid: int, min_increment: int, minutes: int,
           buy_now: int = 0, sub_only: int = 0, chat_announce: int = 1,
           seller_username: str = "", sale_type: str = "auction", commit: bool = True,
           market_description: str = "", wear: str = "", float_value: float | None = None) -> dict:
    title = (title or "").strip()
    if not title:
        return {"ok": False, "error": "Zadej název předmětu."}
    minutes = max(1, min(MAX_MINUTES, int(minutes)))
    start_bid = max(1, int(start_bid))
    min_increment = max(1, int(min_increment))
    buy_now = max(0, int(buy_now or 0))
    sale_type = sale_type if sale_type in ("fixed", "auction") else "auction"
    if float_value is not None:
        float_value = max(0.0, min(1.0, float(float_value)))
        wear = wear_from_float(float_value)
    wear = wear if wear in ("FN", "MW", "FT", "WW", "BS") else ""
    if sale_type == "fixed":
        buy_now = start_bid
    seller_id = None
    seller_username = (seller_username or "").strip().lstrip("@")
    if seller_username:
        seller = conn.execute(
            "SELECT id, username, banned FROM users WHERE LOWER(username)=LOWER(?) OR LOWER(kick_username)=LOWER(?) "
            "ORDER BY (kick_username IS NOT NULL) DESC LIMIT 1", (seller_username, seller_username),
        ).fetchone()
        if not seller or seller["banned"]:
            return {"ok": False, "error": "Prodávající nebyl nalezen nebo má zablokovaný účet."}
        seller_id = seller["id"]
    ends = (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()
    cur = conn.execute(
        "INSERT INTO auctions (title, image_url, start_bid, min_increment, current_bid, status, ends_at, "
        "buy_now, sub_only, chat_announce, seller_user_id, sale_type, market_description, wear, float_value, created_at) "
        "VALUES (?, ?, ?, ?, 0, 'active', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (title[:120], _safe_image_url(image_url), start_bid, min_increment, ends,
         buy_now, 1 if sub_only else 0, 1 if chat_announce else 0, seller_id, sale_type,
         (market_description or "").strip()[:500], wear, float_value, now_iso()))
    if commit:
        conn.commit()
    return {"ok": True, "id": cur.lastrowid, "ends_at": ends,
            "seller": _username(conn, seller_id), "sale_type": sale_type}


def update(conn, auction_id: int, f: dict) -> dict:
    """Úprava BĚŽÍCÍ aukce (jen poslaná pole). start_bid jde měnit jen dokud nikdo nepřihodil;
    buy_now musí být 0 (vypnuto) nebo vyšší než aktuální příhoz; minutes = nový konec od teď."""
    a = conn.execute("SELECT * FROM auctions WHERE id = ?", (auction_id,)).fetchone()
    if not a:
        return {"ok": False, "error": "Aukce nenalezena."}
    if a["status"] != "active":
        return {"ok": False, "error": "Upravit jde jen běžící aukce."}
    sets, vals = [], []
    if f.get("title") is not None:
        t = f["title"].strip()
        if not t:
            return {"ok": False, "error": "Název nesmí být prázdný."}
        sets.append("title = ?"); vals.append(t[:120])
    if f.get("image_url") is not None:
        sets.append("image_url = ?"); vals.append(_safe_image_url(f["image_url"]))
    if f.get("start_bid") is not None:
        if a["bids_count"]:
            return {"ok": False, "error": "Vyvolávací cenu nejde měnit – už se přihazovalo."}
        sets.append("start_bid = ?"); vals.append(max(1, int(f["start_bid"])))
    if f.get("min_increment") is not None:
        sets.append("min_increment = ?"); vals.append(max(1, int(f["min_increment"])))
    if f.get("buy_now") is not None:
        bn = max(0, int(f["buy_now"]))
        if bn and bn <= (a["current_bid"] or 0):
            return {"ok": False, "error": f"Kup-teď musí být vyšší než aktuální příhoz ({a['current_bid']})."}
        sets.append("buy_now = ?"); vals.append(bn)
    if f.get("minutes") is not None:
        m = max(1, min(MAX_MINUTES, int(f["minutes"])))
        sets.append("ends_at = ?"); vals.append((datetime.now(timezone.utc) + timedelta(minutes=m)).isoformat())
    if f.get("sub_only") is not None:
        sets.append("sub_only = ?"); vals.append(1 if f["sub_only"] else 0)
    if f.get("chat_announce") is not None:
        sets.append("chat_announce = ?"); vals.append(1 if f["chat_announce"] else 0)
    if not sets:
        return {"ok": False, "error": "Nic k úpravě."}
    conn.execute(f"UPDATE auctions SET {', '.join(sets)} WHERE id = ? AND status = 'active'",
                 (*vals, auction_id))
    conn.commit()
    return {"ok": True}


def delete(conn, auction_id: int) -> dict:
    """Smaže UKONČENOU/ZRUŠENOU aukci z historie. Aktivní se musí nejdřív zrušit (kvůli escrow)."""
    a = conn.execute("SELECT status,seller_user_id,delivery_status FROM auctions WHERE id = ?", (auction_id,)).fetchone()
    if not a:
        return {"ok": False, "error": "Aukce nenalezena."}
    if a["status"] == "active":
        return {"ok": False, "error": "Běžící aukci nejdřív zruš (vrátí escrow), pak smaž."}
    if a["seller_user_id"] and a["delivery_status"] not in ("completed", "refunded", ""):
        return {"ok": False, "error": "Obchod s nevyřízeným escrow nejde smazat."}
    conn.execute("DELETE FROM auction_bids WHERE auction_id = ?", (auction_id,))
    conn.execute("DELETE FROM auctions WHERE id = ?", (auction_id,))
    conn.commit()
    return {"ok": True}


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
    # re-SELECT po gate UPDATE (drží write-lock) → REÁLNÝ aktuální vůdce, ne stale snapshot z úvodu funkce
    cur = conn.execute("SELECT current_bidder_id, current_bid, title FROM auctions WHERE id = ?", (auction_id,)).fetchone()
    bidder, bid_amt = cur["current_bidder_id"], cur["current_bid"]
    if bidder:
        add_points(conn, bidder, bid_amt, f"Aukce #{auction_id} – zrušeno (vráceno)", xp=False)
        notify(conn, bidder, "🔨", "Aukce zrušena",
               f"Aukce „{cur['title']}\" byla zrušena. Sedláci ({bid_amt}) vráceny. 💰", "#/shop")
    # vrať i vstupní poplatky všem dražitelům (zrušení není jejich chyba; staré aukce mají fee=0 → no-op)
    for r in conn.execute("SELECT user_id, SUM(fee) f FROM auction_bids WHERE auction_id = ? "
                          "GROUP BY user_id HAVING f > 0", (auction_id,)):
        add_points(conn, r["user_id"], r["f"], f"Aukce #{auction_id} – zrušeno (vrácen vstupní poplatek)", xp=False)
    conn.commit()
    return {"ok": True, "refunded": bid_amt if bidder else 0}


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
                    "ends_at": a["ends_at"], "buy_now": a["buy_now"] or 0, "sub_only": bool(a["sub_only"]),
                    "start_bid": a["start_bid"], "min_increment": a["min_increment"],
                    "chat_announce": bool(a["chat_announce"]),
                    "seller": _username(conn, a["seller_user_id"]),
                    "sale_type": a["sale_type"],
                    "description": a["market_description"] or "", "wear": a["wear"] or "",
                    "float_value": a["float_value"], "sold_at": a["sold_at"],
                    "delivery_status": a["delivery_status"] or "",
                    "delivery_sent_at": a["delivery_sent_at"],
                    "delivery_completed_at": a["delivery_completed_at"],
                    "dispute_reason": a["dispute_reason"] or "",
                    "seller_payout": a["seller_payout"], "market_fee": a["market_fee"],
                    "who": (wrow["username"] if wrow else None),
                    "who_kick": (wrow["kick_username"] if wrow else None)})
    return out
