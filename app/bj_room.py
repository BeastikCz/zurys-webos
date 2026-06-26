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


# ---------------- split helpery (druhá ruka) ----------------
def _rank(card):
    return card[0]


def _active(s):
    """Aktivní ruka seatu → (karty, stav, sázka, číslo_ruky 1/2)."""
    if s["active_hand"] == 2:
        return json.loads(s["hand2"]), s["state2"], s["bet2"], 2
    return json.loads(s["hand"]), s["state"], s["bet"], 1


def _write_hand(conn, seat_id, which, cards, state, bet=None):
    """Zapíše karty/stav (a volitelně sázku) konkrétní ruky (1/2)."""
    col_h, col_s, col_b = ("hand2", "state2", "bet2") if which == 2 else ("hand", "state", "bet")
    if bet is None:
        conn.execute(f"UPDATE bj_seats SET {col_h}=?, {col_s}=?, acted_at=? WHERE id=?",
                     (json.dumps(cards), state, now_iso(), seat_id))
    else:
        conn.execute(f"UPDATE bj_seats SET {col_h}=?, {col_s}=?, {col_b}=?, acted_at=? WHERE id=?",
                     (json.dumps(cards), state, bet, now_iso(), seat_id))


def _advance_seat(conn, seat_id):
    """Po dohrání aktivní ruky: u splitu přepni na 2. ruku (když ještě čeká na tah)."""
    r = conn.execute("SELECT active_hand, state2 FROM bj_seats WHERE id=?", (seat_id,)).fetchone()
    if r and r["active_hand"] == 1 and r["state2"] == "acting":
        conn.execute("UPDATE bj_seats SET active_hand=2, acted_at=? WHERE id=?", (now_iso(), seat_id))


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
    if not s:
        raise ValueError("U tohoto stolu nesedíš.")
    cards, st, bet, which = _active(s)
    if st != "acting":
        raise ValueError("Teď nejsi na tahu.")
    deck = json.loads(room["deck"])
    cards.append(_draw(conn, room_id, deck))
    v = hand_value(cards)
    newst = "bust" if v > 21 else ("stood" if v >= 21 else "acting")
    _write_hand(conn, s["id"], which, cards, newst)
    if newst != "acting":
        _advance_seat(conn, s["id"])             # ruka hotová → u splitu přepni na 2.
    conn.commit()
    _maybe_resolve(conn, room_id)
    return _public(conn, _room(conn, room_id), uid)


def stand(conn, uid, room_id):
    room = _room(conn, room_id)
    if not room or room["status"] != "playing":
        raise ValueError("Teď není možné hrát.")
    s = _seat(conn, room_id, uid)
    if not s:
        raise ValueError("U tohoto stolu nesedíš.")
    cards, st, bet, which = _active(s)
    if st != "acting":
        raise ValueError("Teď nejsi na tahu.")
    _write_hand(conn, s["id"], which, cards, "stood")
    _advance_seat(conn, s["id"])
    conn.commit()
    _maybe_resolve(conn, room_id)
    return _public(conn, _room(conn, room_id), uid)


def double(conn, uid, room_id):
    """Double down: zdvojí sázku AKTIVNÍ ruky, vezme PŘESNĚ jednu kartu, pak konec tahu. Jen na první 2 karty ruky."""
    room = _room(conn, room_id)
    if not room or room["status"] != "playing":
        raise ValueError("Teď není možné hrát.")
    s = _seat(conn, room_id, uid)
    if not s:
        raise ValueError("U tohoto stolu nesedíš.")
    cards, st, bet, which = _active(s)
    if st != "acting":
        raise ValueError("Teď nejsi na tahu.")
    if len(cards) != 2:
        raise ValueError("Zdvojit (double) jde jen na první dvě karty.")
    if not try_debit(conn, uid, bet, "Blackjack stůl – zdvojení (double) 🃏"):
        raise ValueError("Nemáš dost sedláků na zdvojení.")
    deck = json.loads(room["deck"])
    cards.append(_draw(conn, room_id, deck))
    v = hand_value(cards)
    newst = "bust" if v > 21 else "stood"        # double = přesně 1 karta, pak konec tahu
    _write_hand(conn, s["id"], which, cards, newst, bet * 2)
    _advance_seat(conn, s["id"])
    conn.commit()
    _maybe_resolve(conn, room_id)
    return _public(conn, _room(conn, room_id), uid)


def split(conn, uid, room_id):
    """Rozdělí pár na dvě ruky (extra sázka = původní). Každá ruka dostane 1 novou kartu. Split es = 1 karta
    na ruku, pak stojí. Bez re-splitu (max 2 ruky). 21 po splitu = běžná výhra (NE natural blackjack 3:2)."""
    room = _room(conn, room_id)
    if not room or room["status"] != "playing":
        raise ValueError("Teď není možné hrát.")
    s = _seat(conn, room_id, uid)
    if not s or s["state"] != "acting" or s["active_hand"] != 1:
        raise ValueError("Teď nejsi na tahu.")
    if s["state2"] is not None:
        raise ValueError("Rozdělit (split) jde jen jednou.")
    hand = json.loads(s["hand"])
    if len(hand) != 2 or _rank(hand[0]) != _rank(hand[1]):
        raise ValueError("Rozdělit jde jen dvě stejné karty.")
    bet = s["bet"]
    if not try_debit(conn, uid, bet, "Blackjack stůl – rozdělení (split) 🃏"):
        raise ValueError("Nemáš dost sedláků na split.")
    deck = json.loads(room["deck"])
    h1 = [hand[0], _draw(conn, room_id, deck)]
    h2 = [hand[1], _draw(conn, room_id, deck)]
    aces = _rank(hand[0]) == "A"                 # split es: jen 1 karta každá, pak stojí
    st1 = "stood" if (aces or hand_value(h1) >= 21) else "acting"
    st2 = "stood" if (aces or hand_value(h2) >= 21) else "acting"
    active = 1 if st1 == "acting" else (2 if st2 == "acting" else 1)
    conn.execute(
        "UPDATE bj_seats SET hand=?, state=?, hand2=?, state2=?, bet2=?, active_hand=?, acted_at=? WHERE id=?",
        (json.dumps(h1), st1, json.dumps(h2), st2, bet, active, now_iso(), s["id"]))
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
    conn.execute("UPDATE bj_seats SET bet=0, hand='[]', state='idle', result=NULL, payout=0, acted_at=NULL, "
                 "hand2='[]', bet2=0, state2=NULL, result2=NULL, payout2=0, active_hand=1 WHERE room_id=?",
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
    # AFK: postav VŠECHNY hrající ruky seatu (i obě u splitu) po vypršení limitu
    conn.execute(
        "UPDATE bj_seats SET "
        "state = CASE WHEN state='acting' THEN 'stood' ELSE state END, "
        "state2 = CASE WHEN state2='acting' THEN 'stood' ELSE state2 END "
        "WHERE room_id=? AND (state='acting' OR state2='acting') AND (acted_at IS NULL OR acted_at < ?)",
        (room_id, cutoff))
    conn.commit()
    if conn.execute("SELECT 1 FROM bj_seats WHERE room_id=? AND (state='acting' OR state2='acting') LIMIT 1",
                    (room_id,)).fetchone():
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
    # všechny dohrané ruky (u splitu obě): (karty, sázka, číslo_ruky, seat)
    played = []
    for s in _seats(conn, room_id):
        hands = [(json.loads(s["hand"]), s["state"], s["bet"], 1)]
        if s["state2"] is not None:
            hands.append((json.loads(s["hand2"]), s["state2"], s["bet2"], 2))
        for cards, st, bet, which in hands:
            if st in ("stood", "bust") and cards:
                played.append((cards, bet, which, s))
    # dealer dobírá jen pokud aspoň jedna ruka nepřebrala
    if any(hand_value(c) <= 21 for (c, bet, which, s) in played):
        while hand_value(dealer) < 17:
            dealer.append(_draw(conn, room_id, deck))
    dv = hand_value(dealer)
    dbj = _is_bj(dealer)
    for cards, bet, which, s in played:
        pv = hand_value(cards)
        is_split = s["state2"] is not None
        if pv > 21:
            res, pay = "bust", 0
        elif (not is_split) and _is_bj(cards):     # natural blackjack 3:2 JEN bez splitu
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
        if which == 2:
            conn.execute("UPDATE bj_seats SET state2='resolved', result2=?, payout2=? WHERE id=?", (res, pay, s["id"]))
        else:
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
        split = s["state2"] is not None
        hand2 = json.loads(s["hand2"]) if split else []
        u = conn.execute("SELECT username, avatar_url FROM users WHERE id=?", (s["user_id"],)).fetchone()
        acting_now = s["state"] == "acting" or s["state2"] == "acting"
        turn_until = None
        if acting_now and s["acted_at"]:
            try:
                turn_until = (datetime.fromisoformat(s["acted_at"]) + timedelta(seconds=ACT_TIMEOUT_S)).isoformat()
            except ValueError:
                turn_until = None
        seats_out.append({
            "user_id": s["user_id"], "username": u["username"] if u else "?",
            "avatar_url": (u["avatar_url"] if u else "") or "",
            "bet": s["bet"], "hand": hand, "value": hand_value(hand) if hand else 0,
            "state": s["state"], "result": s["result"], "payout": s["payout"],
            "split": split, "active_hand": s["active_hand"],
            "hand2": hand2, "value2": hand_value(hand2) if hand2 else 0,
            "bet2": s["bet2"], "state2": s["state2"], "result2": s["result2"], "payout2": s["payout2"],
            "is_you": s["user_id"] == viewer_uid, "is_host": s["user_id"] == room["host_id"],
            "turn_until": turn_until,
        })
    you = next((x for x in seats_out if x["is_you"]), None)
    # aktivní ruka „tebe" (u splitu může být 2.) → can_act/double/split počítáme z ní
    you_state = you_cards = None
    if you:
        you_state, you_cards = (you["state2"], you["hand2"]) if you["active_hand"] == 2 else (you["state"], you["hand"])
    you_can_act = status == "playing" and you is not None and you_state == "acting"
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
        "can_act": you_can_act,
        "can_double": you_can_act and len(you_cards) == 2,
        "can_split": you_can_act and you["active_hand"] == 1 and not you["split"]
                     and len(you["hand"]) == 2 and _rank(you["hand"][0]) == _rank(you["hand"][1]),
        "can_next": status == "done" and room["host_id"] == viewer_uid,
        "phase_until": room["phase_until"], "server_now": now_iso(),
        "chat": chat, "min_bet": MIN_BET, "max_bet": MAX_BET,
    }
