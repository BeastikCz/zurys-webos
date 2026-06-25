"""Soukromý sdílený blackjack stůl (multiplayer, vs dealer) o sedláky.

Víc hráčů u JEDNOHO stolu, každý hraje svou ruku proti společnému dealerovi (house).
Soukromé: připojení jen přes kód (link), není ve veřejné Herně. Real-time přes polling –
/state auto-posouvá fáze: AFK auto-stand po 30 s + auto-vyhodnocení, jakmile všichni dohráli.

Férovost & souběh:
- Karty server-only (4-balíčkový shoe), losování CSPRNG.
- Více hráčů táhne naráz → karty se berou přes ATOMICKÝ deck_pos (UPDATE ... RETURNING),
  takže nikdy nedostanou stejnou kartu.
- Vyhodnocení kola se „claimne" atomickým UPDATE status (playing→done) → nevyplatí se 2×.
"""
import json
from datetime import datetime, timezone, timedelta

from .db import now_iso
from .deps import add_points, try_debit
from .security import secure_choice, new_code
from .blackjack import hand_value, _is_bj, _val, _RANKS, _SUITS, MIN_BET, MAX_BET

MAX_SEATS = 6
ACT_TIMEOUT_S = 30        # AFK / tah: kdo do 30 s nezahraje, automaticky se postaví
BET_SECONDS = 25          # auto-flow: odpočet sázení od PRVNÍ sázky → pak auto-rozdání
RESOLVE_SECONDS = 8       # auto-flow: odpočet po vyhodnocení → pak auto-nové kolo
CHAT_TAIL = 40


def _shoe(decks=4):
    pool = [r + s for _ in range(decks) for s in _SUITS for r in _RANKS]
    out = []
    while pool:
        c = secure_choice(pool)
        pool.remove(c)
        out.append(c)
    return out


def _draw(conn, room_id, deck):
    """Atomicky vytáhne další kartu ze shoe (bezpečné i při souběhu více hráčů)."""
    row = conn.execute("UPDATE bj_rooms SET deck_pos = deck_pos + 1 WHERE id=? RETURNING deck_pos",
                       (room_id,)).fetchone()
    pos = row["deck_pos"] - 1
    if pos >= len(deck):
        raise ValueError("Deck exhausted")
    return deck[pos]


def _room(conn, room_id):
    return conn.execute("SELECT * FROM bj_rooms WHERE id=?", (room_id,)).fetchone()


def _room_by_code(conn, code):
    return conn.execute("SELECT * FROM bj_rooms WHERE code=?", (code,)).fetchone()


def _seats(conn, room_id):
    return conn.execute("SELECT * FROM bj_seats WHERE room_id=? ORDER BY joined_at, id", (room_id,)).fetchall()


def _seat(conn, room_id, uid):
    return conn.execute("SELECT * FROM bj_seats WHERE room_id=? AND user_id=?", (room_id, uid)).fetchone()


# ---------------- akce ----------------
def create(conn, uid, username):
    code = None
    for _ in range(12):
        c = "BJ" + new_code()[:6].upper()
        if not _room_by_code(conn, c):
            code = c
            break
    if not code:
        raise ValueError("Stůl se nepodařilo založit, zkus to prosím znovu.")
    ts = now_iso()
    cur = conn.execute(
        "INSERT INTO bj_rooms (code, host_id, status, created_at, updated_at) VALUES (?,?,'betting',?,?)",
        (code, uid, ts, ts))
    rid = cur.lastrowid
    conn.execute("INSERT INTO bj_seats (room_id, user_id, joined_at, seen_at) VALUES (?,?,?,?)",
                 (rid, uid, ts, ts))
    conn.commit()
    return _public(conn, _room(conn, rid), uid)


def join(conn, uid, username, code):
    room = _room_by_code(conn, (code or "").strip().upper())
    if not room or room["status"] == "closed":
        raise ValueError("Takový stůl neexistuje – zkontroluj prosím kód.")
    if _seat(conn, room["id"], uid):
        return _public(conn, room, uid)
    if len(_seats(conn, room["id"])) >= MAX_SEATS:
        raise ValueError("Stůl je už plný.")
    ts = now_iso()
    conn.execute("INSERT INTO bj_seats (room_id, user_id, joined_at, seen_at) VALUES (?,?,?,?)",
                 (room["id"], uid, ts, ts))
    conn.commit()
    return _public(conn, _room(conn, room["id"]), uid)


def state(conn, uid, room_id):
    room = _room(conn, room_id)
    if not room:
        raise ValueError("Stůl neexistuje.")
    if not _seat(conn, room_id, uid):
        raise ValueError("U tohoto stolu nesedíš.")
    return _public(conn, room, uid)


def my_room(conn, uid):
    r = conn.execute(
        "SELECT r.id FROM bj_rooms r JOIN bj_seats s ON s.room_id=r.id "
        "WHERE s.user_id=? AND r.status!='closed' ORDER BY r.id DESC LIMIT 1", (uid,)).fetchone()
    return {"room_id": r["id"] if r else None}


def place_bet(conn, uid, room_id, amount):
    room = _room(conn, room_id)
    if not room:
        raise ValueError("Stůl neexistuje.")
    if room["status"] != "betting":
        raise ValueError("Teď není možné sázet – kolo už běží.")
    s = _seat(conn, room_id, uid)
    if not s:
        raise ValueError("U tohoto stolu nesedíš.")
    if s["state"] == "ready":
        raise ValueError("Sázku už máš zadanou. Počkej prosím na rozdání.")
    amount = int(amount)
    if amount < MIN_BET or amount > MAX_BET:
        raise ValueError(f"Sázka musí být {MIN_BET}–{MAX_BET} sedláků.")
    if not try_debit(conn, uid, amount, "Blackjack stůl – sázka 🃏"):
        raise ValueError("Nemáš dostatek sedláků.")
    conn.execute("UPDATE bj_seats SET bet=?, state='ready' WHERE id=?", (amount, s["id"]))
    if not room["phase_until"]:                  # první sázka u stolu spustí odpočet do auto-rozdání
        until = (datetime.now(timezone.utc) + timedelta(seconds=BET_SECONDS)).isoformat()
        conn.execute("UPDATE bj_rooms SET phase_until=? WHERE id=? AND phase_until IS NULL", (until, room_id))
    conn.commit()
    return _public(conn, _room(conn, room_id), uid)


def _do_deal(conn, room_id):
    """Rozdá kolo (BEZ auth – sdílí host i auto-flow). Atomicky claimne betting→playing, aby dvě
    souběžná rozdání (host klik + auto na pollu) neudělala karty 2×."""
    room = _room(conn, room_id)
    if not room or room["status"] != "betting":
        return False
    ready = [s for s in _seats(conn, room_id) if s["state"] == "ready" and s["bet"] > 0]
    if not ready:
        return False
    if conn.execute("UPDATE bj_rooms SET status='playing' WHERE id=? AND status='betting'", (room_id,)).rowcount == 0:
        return False                              # někdo jiný už claimnul rozdání (souběh)
    deck = _shoe()
    pos = 0
    dealer = [deck[pos], deck[pos + 1]]
    pos += 2
    ts = now_iso()
    for s in ready:
        hand = [deck[pos], deck[pos + 1]]
        pos += 2
        st = "stood" if hand_value(hand) == 21 else "acting"      # natural BJ → auto-stand
        conn.execute("UPDATE bj_seats SET hand=?, state=?, acted_at=? WHERE id=?",
                     (json.dumps(hand), st, ts, s["id"]))
    conn.execute(
        "UPDATE bj_rooms SET dealer=?, deck=?, deck_pos=?, round_no=round_no+1, phase_until=NULL, updated_at=? WHERE id=?",
        (json.dumps(dealer), json.dumps(deck), pos, ts, room_id))
    conn.commit()
    _maybe_resolve(conn, room_id)            # všichni měli BJ → rovnou vyhodnoť
    return True


def deal(conn, uid, room_id):
    room = _room(conn, room_id)
    if not room:
        raise ValueError("Stůl neexistuje.")
    if room["host_id"] != uid:
        raise ValueError("Rozdat karty může jen host stolu.")
    if room["status"] != "betting":
        raise ValueError("Teď není možné rozdávat.")
    if not _do_deal(conn, room_id):
        raise ValueError("Zatím nikdo nevsadil.")
    return _public(conn, _room(conn, room_id), uid)


def hit(conn, uid, room_id):
    room = _room(conn, room_id)
    if not room or room["status"] != "playing":
        raise ValueError("Teď není možné hrát.")
    s = _seat(conn, room_id, uid)
    if not s or s["state"] != "acting":
        raise ValueError("Teď nejsi na tahu.")
    deck = json.loads(room["deck"])
    hand = json.loads(s["hand"])
    hand.append(_draw(conn, room_id, deck))
    v = hand_value(hand)
    st = "bust" if v > 21 else ("stood" if v >= 21 else "acting")
    conn.execute("UPDATE bj_seats SET hand=?, state=?, acted_at=? WHERE id=?",
                 (json.dumps(hand), st, now_iso(), s["id"]))
    conn.commit()
    _maybe_resolve(conn, room_id)
    return _public(conn, _room(conn, room_id), uid)


def stand(conn, uid, room_id):
    room = _room(conn, room_id)
    if not room or room["status"] != "playing":
        raise ValueError("Teď není možné hrát.")
    s = _seat(conn, room_id, uid)
    if not s or s["state"] != "acting":
        raise ValueError("Teď nejsi na tahu.")
    conn.execute("UPDATE bj_seats SET state='stood', acted_at=? WHERE id=?", (now_iso(), s["id"]))
    conn.commit()
    _maybe_resolve(conn, room_id)
    return _public(conn, _room(conn, room_id), uid)


def double(conn, uid, room_id):
    """Double down: zdvojí sázku, vezme PŘESNĚ jednu kartu, pak automaticky stojí. Jen na první 2 karty."""
    room = _room(conn, room_id)
    if not room or room["status"] != "playing":
        raise ValueError("Teď není možné hrát.")
    s = _seat(conn, room_id, uid)
    if not s or s["state"] != "acting":
        raise ValueError("Teď nejsi na tahu.")
    hand = json.loads(s["hand"])
    if len(hand) != 2:
        raise ValueError("Zdvojit (double) jde jen na první dvě karty.")
    bet = s["bet"]
    if not try_debit(conn, uid, bet, "Blackjack stůl – zdvojení (double) 🃏"):
        raise ValueError("Nemáš dost sedláků na zdvojení.")
    deck = json.loads(room["deck"])
    hand.append(_draw(conn, room_id, deck))
    v = hand_value(hand)
    st = "bust" if v > 21 else "stood"           # double = přesně 1 karta, pak konec tahu
    conn.execute("UPDATE bj_seats SET hand=?, bet=?, state=?, acted_at=? WHERE id=?",
                 (json.dumps(hand), bet * 2, st, now_iso(), s["id"]))
    conn.commit()
    _maybe_resolve(conn, room_id)
    return _public(conn, _room(conn, room_id), uid)


def _do_next(conn, room_id):
    """Nové kolo (BEZ auth – host i auto-flow): done→betting + reset seatů. Atomický claim proti dvojímu resetu."""
    room = _room(conn, room_id)
    if not room:
        return False
    if room["status"] == "done":
        if conn.execute("UPDATE bj_rooms SET status='betting' WHERE id=? AND status='done'", (room_id,)).rowcount == 0:
            return False                          # někdo jiný už spustil nové kolo (souběh)
    elif room["status"] != "betting":
        return False
    conn.execute("UPDATE bj_seats SET bet=0, hand='[]', state='idle', result=NULL, payout=0, acted_at=NULL WHERE room_id=?",
                 (room_id,))
    conn.execute("UPDATE bj_rooms SET dealer='[]', deck='[]', deck_pos=0, phase_until=NULL, updated_at=? WHERE id=?",
                 (now_iso(), room_id))
    conn.commit()
    return True


def next_round(conn, uid, room_id):
    room = _room(conn, room_id)
    if not room:
        raise ValueError("Stůl neexistuje.")
    if room["host_id"] != uid:
        raise ValueError("Nové kolo může spustit jen host stolu.")
    if room["status"] not in ("done", "betting"):
        raise ValueError("Kolo ještě běží.")
    _do_next(conn, room_id)
    return _public(conn, _room(conn, room_id), uid)


def leave(conn, uid, room_id):
    room = _room(conn, room_id)
    if not room:
        return {"left": True}
    s = _seat(conn, room_id, uid)
    if s:
        if room["status"] == "betting" and s["bet"] > 0:
            add_points(conn, uid, s["bet"], "Blackjack stůl – odchod (vrácení sázky)")
        conn.execute("DELETE FROM bj_seats WHERE id=?", (s["id"],))
        conn.commit()
    remaining = _seats(conn, room_id)
    if not remaining:
        conn.execute("UPDATE bj_rooms SET status='closed', updated_at=? WHERE id=?", (now_iso(), room_id))
    elif room["host_id"] == uid:
        conn.execute("UPDATE bj_rooms SET host_id=? WHERE id=?", (remaining[0]["user_id"], room_id))
    conn.commit()
    return {"left": True}


def chat_send(conn, uid, username, room_id, msg):
    if not _seat(conn, room_id, uid):
        raise ValueError("U tohoto stolu nesedíš.")
    msg = (msg or "").strip()[:200]
    if msg:
        conn.execute("INSERT INTO bj_chat (room_id, user_id, username, msg, created_at) VALUES (?,?,?,?,?)",
                     (room_id, uid, username, msg, now_iso()))
        conn.commit()
    return {"ok": True}


# ---------------- vyhodnocení ----------------
def _maybe_resolve(conn, room_id):
    room = _room(conn, room_id)
    if not room or room["status"] != "playing":
        return
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=ACT_TIMEOUT_S)).isoformat()
    conn.execute("UPDATE bj_seats SET state='stood' WHERE room_id=? AND state='acting' AND (acted_at IS NULL OR acted_at < ?)",
                 (room_id, cutoff))
    conn.commit()
    if conn.execute("SELECT 1 FROM bj_seats WHERE room_id=? AND state='acting' LIMIT 1", (room_id,)).fetchone():
        return
    if conn.execute("UPDATE bj_rooms SET status='done' WHERE id=? AND status='playing'", (room_id,)).rowcount == 0:
        return                                # někdo už claimnul vyhodnocení (souběh)
    conn.commit()
    _resolve(conn, room_id)
    until = (datetime.now(timezone.utc) + timedelta(seconds=RESOLVE_SECONDS)).isoformat()
    conn.execute("UPDATE bj_rooms SET phase_until=? WHERE id=?", (until, room_id))   # odpočet → auto-nové kolo
    conn.commit()


def _resolve(conn, room_id):
    room = _room(conn, room_id)
    deck = json.loads(room["deck"])
    dealer = json.loads(room["dealer"])
    seats = [s for s in _seats(conn, room_id) if s["state"] in ("stood", "bust")]
    if any(hand_value(json.loads(s["hand"])) <= 21 for s in seats):
        while hand_value(dealer) < 17:
            dealer.append(_draw(conn, room_id, deck))
    dv = hand_value(dealer)
    dbj = _is_bj(dealer)
    for s in seats:
        hand = json.loads(s["hand"])
        pv = hand_value(hand)
        bet = s["bet"]
        if pv > 21:
            res, pay = "bust", 0
        elif _is_bj(hand):
            res, pay = ("push", bet) if dbj else ("blackjack", bet + (bet * 3) // 2)
        elif dbj:
            res, pay = "lose", 0
        elif dv > 21 or pv > dv:
            res, pay = "win", bet * 2
        elif pv < dv:
            res, pay = "lose", 0
        else:
            res, pay = "push", bet
        if pay > 0:
            add_points(conn, s["user_id"], pay, f"Blackjack stůl – {res} 🃏")
        conn.execute("UPDATE bj_seats SET state='resolved', result=?, payout=? WHERE id=?", (res, pay, s["id"]))
    conn.execute("UPDATE bj_rooms SET dealer=?, updated_at=? WHERE id=?", (json.dumps(dealer), now_iso(), room_id))
    conn.commit()


def _auto_advance(conn, room_id):
    """Motor fází (běží na KAŽDÉM pollu /state): betting→auto-rozdání po odpočtu, playing→auto-vyhodnocení
    (AFK), done→auto-nové kolo po odpočtu. Vše atomicky claimnuté → bezpečné při souběhu pollů více hráčů."""
    room = _room(conn, room_id)
    if not room:
        return
    st = room["status"]
    if st == "playing":
        _maybe_resolve(conn, room_id)
        return
    now = datetime.now(timezone.utc).isoformat()
    pu = room["phase_until"]
    if not pu or now < pu:
        return
    if st == "betting":
        if not _do_deal(conn, room_id):          # odpočet vypršel, ale nikdo nevsadil → zruš odpočet (čeká dál)
            conn.execute("UPDATE bj_rooms SET phase_until=NULL WHERE id=? AND status='betting'", (room_id,))
            conn.commit()
    elif st == "done":
        _do_next(conn, room_id)


# ---------------- veřejný stav (polling) ----------------
def _public(conn, room, viewer_uid):
    room_id = room["id"]
    _auto_advance(conn, room_id)             # polling = motor postupu fází (auto-deal / resolve / next)
    room = _room(conn, room_id)
    conn.execute("UPDATE bj_seats SET seen_at=? WHERE room_id=? AND user_id=?", (now_iso(), room_id, viewer_uid))
    conn.commit()
    status = room["status"]
    dealer = json.loads(room["dealer"])
    reveal = status == "done"
    seats_out = []
    for s in _seats(conn, room_id):
        hand = json.loads(s["hand"])
        u = conn.execute("SELECT username, avatar_url FROM users WHERE id=?", (s["user_id"],)).fetchone()
        turn_until = None
        if s["state"] == "acting" and s["acted_at"]:
            try:
                turn_until = (datetime.fromisoformat(s["acted_at"]) + timedelta(seconds=ACT_TIMEOUT_S)).isoformat()
            except ValueError:
                turn_until = None
        seats_out.append({
            "user_id": s["user_id"], "username": u["username"] if u else "?",
            "avatar_url": (u["avatar_url"] if u else "") or "",
            "bet": s["bet"], "hand": hand, "value": hand_value(hand) if hand else 0,
            "state": s["state"], "result": s["result"], "payout": s["payout"],
            "is_you": s["user_id"] == viewer_uid, "is_host": s["user_id"] == room["host_id"],
            "turn_until": turn_until,
        })
    you = next((x for x in seats_out if x["is_you"]), None)
    chat = [dict(r) for r in conn.execute(
        "SELECT username, msg, created_at FROM bj_chat WHERE room_id=? ORDER BY id DESC LIMIT ?",
        (room_id, CHAT_TAIL)).fetchall()][::-1]
    if status == "betting" or not dealer:
        dealer_show, dealer_val = [], 0
    elif reveal:
        dealer_show, dealer_val = dealer, hand_value(dealer)
    else:
        dealer_show, dealer_val = [dealer[0], "??"], _val(dealer[0][0])
    return {
        "room_id": room_id, "code": room["code"], "status": status, "round_no": room["round_no"],
        "is_host": room["host_id"] == viewer_uid, "max_seats": MAX_SEATS,
        "dealer": dealer_show, "dealer_value": dealer_val,
        "dealer_hidden": (status == "playing"),
        "seats": seats_out, "you": you,
        "can_bet": status == "betting" and you is not None and you["state"] == "idle",
        "can_deal": status == "betting" and room["host_id"] == viewer_uid and any(s["state"] == "ready" for s in seats_out),
        "can_act": status == "playing" and you is not None and you["state"] == "acting",
        "can_double": status == "playing" and you is not None and you["state"] == "acting" and len(you["hand"]) == 2,
        "can_next": status == "done" and room["host_id"] == viewer_uid,
        "phase_until": room["phase_until"], "server_now": now_iso(),
        "chat": chat, "min_bet": MIN_BET, "max_bet": MAX_BET,
    }
