"""Zpracování Kick webhook eventů → přičítání sedláků (sub/resub/gift/follow/chat).

Bezpečnost: každý webhook se ověřuje RSA-SHA256 podpisem (Kickův veřejný klíč),
takže body může spustit JEN reálný Kick event, ne podvodník.

Body za eventy se konfigurují v admin Ekonomice (eco_sub_pts / eco_resub_pts /
eco_giftsub_pts / eco_follow_pts). Chat využívá stávající ekonomiku (award_chat,
cooldown + kombinovaný násobič sub×VIP).
"""
import base64
import json
import time
import urllib.request
from datetime import datetime, timedelta, timezone

import rsa

from .db import now_iso
from .deps import add_points, notify
from . import economy, kickcommands, services, subgoal, webpush

_KEY_URL = "https://api.kick.com/public/v1/public-key"
_pub = None
_pub_at = 0.0


def _public_key():
    """Stáhne (a cachuje 1 h) Kickův veřejný klíč pro ověření podpisu."""
    global _pub, _pub_at
    now = time.monotonic()
    if _pub and now - _pub_at < 3600:
        return _pub
    try:
        with urllib.request.urlopen(_KEY_URL, timeout=8) as r:
            raw = r.read().decode("utf-8")
        pem = None
        try:
            data = json.loads(raw)
            pem = ((data.get("data") or {}).get("public_key")
                   or data.get("public_key") or data.get("publicKey"))
        except (ValueError, AttributeError):
            pem = None
        if not pem and "BEGIN PUBLIC KEY" in raw:
            pem = raw
        if not pem:
            return None
        _pub = rsa.PublicKey.load_pkcs1_openssl_pem(pem.encode("utf-8"))
        _pub_at = now
        return _pub
    except Exception as e:  # pragma: no cover
        print("[kickevents] public key fetch failed:", e)
        _pub_at = now  # cache failure for 1 h to avoid spamming Kick API
        return None


# Replay guard: dedup (webhook_seen) drží message_id 3 dny, replay tedy projde až PO prune.
# Stačí proto odmítat podpisy starší než 24 h (< 3 dny) – díra zavřená, a přitom je okno
# tolerantní ke Kick retryům (nesou PŮVODNÍ podepsaný timestamp) i delšímu výpadku appky.
# POZOR: 5min okno by při >5min downtime zahodilo retryované eventy (Kick by nás mohl odhlásit).
_WEBHOOK_SKEW_SEC = 86400


def verify(message_id: str, timestamp: str, body_bytes: bytes, signature_b64: str) -> bool:
    """Ověří podpis webhooku. Podepisuje se `message_id.timestamp.raw_body`."""
    pub = _public_key()
    if not pub or not signature_b64:
        return False
    try:
        signed = (str(message_id) + "." + str(timestamp) + ".").encode("utf-8") + (body_bytes or b"")
        rsa.verify(signed, base64.b64decode(signature_b64), pub)  # raises VerificationError když nesedí
    except Exception:
        return False
    # Čerstvost podepsaného timestampu (viz _WEBHOOK_SKEW_SEC výš). Odmítnutí LOGUJ –
    # jinak by systémová chyba (např. jiný formát timestampu) tiše zabila všechny eventy.
    try:
        event_time = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
        age = abs((datetime.now(timezone.utc) - event_time).total_seconds())
        if age > _WEBHOOK_SKEW_SEC:
            print(f"[kickevents] webhook odmítnut: timestamp starý {age:.0f}s (limit {_WEBHOOK_SKEW_SEC}s): {timestamp!r}")
            return False
    except Exception as e:
        print(f"[kickevents] webhook odmítnut: neparsovatelný timestamp {timestamp!r}: {e}")
        return False
    return True


def _award_kick_user(conn, kick_username, points, reason, set_sub=None, sub_expires_at=None, log_event=False, crew_bonus=False):
    """Najde uživatele podle Kick nicku (nebo založí ghost účet) a přičte body.

    Ghost účet = aby se body uložily, i když ten člověk ještě není na webu;
    při přihlášení přes Kick si je převezme (claim dle kick_username).
    """
    key = (kick_username or "").strip().lstrip("@").lower()
    if not key:
        return None
    row = conn.execute("SELECT id FROM users WHERE kick_username = ?", (key,)).fetchone()
    if row:
        uid = row["id"]
    else:
        cur = conn.execute(
            "INSERT INTO users (kick_username, username, points, role, created_at) "
            "VALUES (?, ?, 0, 'user', ?)",
            (key, (kick_username or key).strip().lstrip("@")[:32], now_iso()),
        )
        uid = cur.lastrowid
    if points:
        if crew_bonus:                      # crew level → bonus na sedláky ze subu/resubu/giftu (žene subbing = $ pro streamera)
            try:
                from . import crews
                points = int(round(int(points) * crews.earn_bonus(conn, uid, "sub")))
            except Exception:
                pass
        add_points(conn, uid, int(points), reason)
    elif log_event:
        # Záznam do historie i bez bodů (příjemce gift subu dostává 0 bodů, ale chceme vidět,
        # kdy a jak suba získal). change=0 se schová z osobní historie bodů (filtr v misc.py).
        conn.execute(
            "INSERT INTO points_log (user_id, change, reason, created_at) VALUES (?, 0, ?, ?)",
            (uid, reason, now_iso()),
        )
    if set_sub is not None:
        conn.execute("UPDATE users SET is_sub = ? WHERE id = ?", (1 if set_sub else 0, uid))
        if set_sub:
            exp_iso = _norm_iso(sub_expires_at)
            if not exp_iso:
                # Pojistka: když Kick datum nepošle (nebo je nečitelné), dosadíme +32 dní.
                # Měsíční sub = ~30 dní + pár dní rezerva → odznak se nikdy nezasekne napořád,
                # renewal ho stejně hned přepíše přesným Kick datem.
                exp_iso = (datetime.now(timezone.utc) + timedelta(days=32)).isoformat()
                print(f"[kickevents] sub bez expires_at -> fallback +32d pro {key}")
            conn.execute("UPDATE users SET sub_expires_at = ? WHERE id = ?", (exp_iso, uid))
            # role na 'sub' JEN když je teď 'user' (nikdy nedemotovat vip/mod/broadcaster/admin)
            conn.execute("UPDATE users SET role = 'sub' WHERE id = ? AND role = 'user'", (uid,))
    return uid


def _norm_iso(ts):
    """Kickovo expires_at → ISO UTC (kompatibilní s now_iso pro spolehlivé string-porovnání)."""
    if not ts:
        return None
    try:
        t = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return t.astimezone(timezone.utc).isoformat()
    except Exception:
        return None


def expire_subs(conn) -> int:
    """Sundá SUB flag (a roli 'sub') těm, kterým sub vypršel. Legacy/migrace (bez expirace) zůstává.
    Navíc pošle comeback signál (in-app notif + best-effort push), ať se lapsed sub vrátí (re-engagement).
    Běží v daemonu (autodrop) → síťový push tu neblokuje request handler."""
    now = now_iso()
    # kdo právě teď vyprší (před UPDATE) → comeback nudge; každý dostane notif jen 1× (příští cyklus už is_sub=0)
    lapsed = [r["id"] for r in conn.execute(
        "SELECT id FROM users WHERE is_sub = 1 AND sub_expires_at IS NOT NULL AND sub_expires_at < ?",
        (now,)).fetchall()]
    cur = conn.execute(
        "UPDATE users SET is_sub = 0 WHERE is_sub = 1 AND sub_expires_at IS NOT NULL AND sub_expires_at < ?",
        (now,),
    )
    # vypršelým vrať roli 'sub' → 'user' (týká se jen těch, co měli expiraci; staff/vip nemají roli 'sub')
    conn.execute(
        "UPDATE users SET role = 'user' WHERE role = 'sub' AND is_sub = 0 "
        "AND sub_expires_at IS NOT NULL AND sub_expires_at < ?",
        (now,),
    )
    for uid in lapsed:
        notify(conn, uid, "💜", "Sub vypršel 💜",
               "Obnov sub na Kicku a vrať se farmit sedláky! 🌾", "/")
    conn.commit()
    for uid in lapsed:                      # best-effort web push (po commitu; push_to_user nikdy nehodí)
        webpush.push_to_user(conn, uid, "Sub vypršel 💜", "Statek na tebe čeká, obnov sub a vrať se! 🌾", "/")
    return cur.rowcount


def handle_event(conn, event_type: str, payload: dict) -> dict:
    """Zpracuje jeden Kick event a přičte sedláky dle nastavení. Necommituje (caller)."""
    eco = economy.get_eco(conn)
    payload = payload or {}

    if event_type in ("channel.subscription.new", "channel.subscription.renewal"):
        uname = (payload.get("subscriber") or {}).get("username")
        is_new = event_type.endswith("new")
        mult = services.sub_points_mult(conn)   # happy-hour 2× na subs (jinak 1×)
        pts = (eco["eco_sub_pts"] if is_new else eco["eco_resub_pts"]) * mult
        exp = payload.get("expires_at")
        label = ("Kick sub 🟣" if is_new else "Kick resub 🔁") + (" (happy 2×)" if mult > 1 else "")
        _award_kick_user(conn, uname, pts, label, set_sub=True, sub_expires_at=exp, log_event=True, crew_bonus=True)
        subgoal.tick(conn, 1)                    # komunitní sub cíl: +1
        return {"ok": True, "type": event_type, "user": uname, "pts": pts, "mult": mult}

    if event_type == "channel.subscription.gifts":
        gifter = (payload.get("gifter") or {}).get("username")   # None = anonym
        giftees = payload.get("giftees") or []
        n = len(giftees)
        if n == 0:
            # Kick občas pošle gifts event BEZ giftees (2.7.2026 2× „×0": ušlé body, sub statusy
            # i subgoal tick — ručně kompenzováno). Zaloguj celý payload + Discord alert, ať
            # u další takové rány vidíme reálný shape a umíme doparsovat i tuhle variantu.
            raw = json.dumps(payload, ensure_ascii=False)[:1400]
            print("[kick-webhook] gifts event BEZ giftees! payload:", raw)
            from . import alerts
            alerts.send("Kick gifts webhook bez giftees 🎁⚠️ (ušlé body/subgoal — zkontroluj)",
                        raw, key="gifts-no-giftees", cooldown=60)
        mult = services.sub_points_mult(conn)   # happy-hour 2× na gift subs (jinak 1×)
        total = eco["eco_giftsub_pts"] * n * mult
        gifter_uid = None
        if gifter:
            # award (i s 0 body založí/najde účet a zaloguje) → máme uid pro sub cíl
            gifter_uid = _award_kick_user(
                conn, gifter, total,
                f"Kick gift sub 🎁 ×{n}" + (" (happy 2×)" if mult > 1 else ""),
                log_event=(total == 0), crew_bonus=True)
        if gifter_uid:
            # komunitní SUB cíl: zapiš giftera (PŘED tick → je v outpayu, i když cíl naplní jeho gift)
            subgoal.record_gifter(conn, gifter_uid, n, in_hh=(mult > 1))
        gexp = payload.get("expires_at")
        for g in giftees:                                        # příjemci se stávají suby
            gu = (g or {}).get("username")
            if gu:
                _award_kick_user(conn, gu, 0, "Kick gift sub (příjemce)", set_sub=True, sub_expires_at=gexp, log_event=True)
        subgoal.tick(conn, n)                    # komunitní sub cíl: +n (počet darovaných subů)
        return {"ok": True, "type": event_type, "gifter": gifter, "count": n, "pts": total}

    if event_type == "channel.followed":
        uname = (payload.get("follower") or {}).get("username")
        key = (uname or "").strip().lstrip("@").lower()
        already = conn.execute(
            "SELECT 1 FROM points_log l JOIN users u ON u.id = l.user_id "
            "WHERE u.kick_username = ? AND l.reason = 'Kick follow ➕' LIMIT 1", (key,),
        ).fetchone()
        if not already:
            _award_kick_user(conn, uname, eco["eco_follow_pts"], "Kick follow ➕")
        return {"ok": True, "type": event_type, "user": uname, "awarded": not bool(already)}

    if event_type == "chat.message.sent":
        uname = (payload.get("sender") or {}).get("username")
        content = payload.get("content") or ""
        res = economy.award_chat_by_kick(conn, uname)   # cooldown + násobič řeší ekonomika
        reply = kickcommands.handle(conn, uname, content)   # !sedláci/!leaderboard/… → text (pošle webhook)
        if not reply:                                   # příkaz má přednost, ping ho nepřebíjí
            reply = _ghost_claim_ping(conn, uname)
        return {"ok": True, "type": event_type, "user": uname, "chat": res, "reply": reply}

    return {"ok": False, "ignored": event_type}


# --- Ghost kampaň: „máš u nás sedláky, vyzvedni si je" -------------------------------
GHOST_PING_MIN_PTS = 1000    # ping jen ghostům, u kterých je co vyzvednout (endowment efekt)
GHOST_PING_GAP_S = 600       # globální rozestup mezi pingy – bot nesmí spamovat chat
_ghost_ping_last = 0.0


def _ghost_claim_ping(conn, kick_username):
    """Ghost účet (nikdy nepřihlášený, kick_id NULL) s nasbíranými sedláky právě napsal do
    chatu → bot ho 1× ZA ŽIVOT pozve, ať se přihlásí a body si vyzvedne. Vrací text pro bota
    nebo None. ponytail: globální 10min rozestup + per-user flag; žádná fronta, žádný daemon."""
    global _ghost_ping_last
    key = (kick_username or "").strip().lstrip("@").lower()
    if not key:
        return None
    now = time.time()
    if now - _ghost_ping_last < GHOST_PING_GAP_S:
        return None
    row = conn.execute(
        "SELECT id, username, points FROM users WHERE kick_username = ? AND kick_id IS NULL "
        "AND banned = 0 AND ghost_pinged_at IS NULL AND points >= ?",
        (key, GHOST_PING_MIN_PTS)).fetchone()
    if not row:
        return None
    conn.execute("UPDATE users SET ghost_pinged_at = ? WHERE id = ?", (now_iso(), row["id"]))
    _ghost_ping_last = now
    pts = f"{row['points']:,}".replace(",", " ")
    return (f"@{row['username']} na zurys.live na tebe čeká {pts} sedláků z giftů a aktivity 🌾 "
            f"Přihlas se přes Kick a jsou tvoje!")
