"""Kick chat bot (SedlakBOT): ukládání OAuth tokenu, refresh, odesílání zpráv.

Dva režimy:
  - REÁLNÝ: existuje uložený OAuth token bota (is_demo=0) → volá Kick API
    POST /public/v1/chat a posílá do kanálu `broadcaster_channel` (zurys1337).
  - DEMO: bot je „připojen" jako demo (is_demo=1) nebo token chybí → zprávy se
    jen zalogují do bot_messages a zobrazí v simulovaném chatu. Testovatelné lokálně.

Posílání NEvyžaduje veřejný hosting (je to odchozí HTTPS). Příjem webhooků ano.
"""
import json
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta

from .config import (KICK_CLIENT_ID, KICK_CLIENT_SECRET, KICK_TOKEN_URL,
                     KICK_CHAT_URL, KICK_CHANNELS_URL, KICK_BROADCASTER_CHANNEL,
                     KICK_BOT_USERNAME, KICK_EVENTS_SUB_URL, KICK_USER_URL,
                     KICK_MODERATION_URL)

# Eventy, ke kterým se přihlásíme (body za sub/resub/gift/follow/chat)
WEBHOOK_EVENTS = [
    {"name": "channel.subscription.new", "version": 1},
    {"name": "channel.subscription.renewal", "version": 1},
    {"name": "channel.subscription.gifts", "version": 1},
    {"name": "channel.followed", "version": 1},
    {"name": "chat.message.sent", "version": 1},
]
from .db import now_iso, get_setting, set_setting

SETTING_AUTO_POST = "bot_auto_post_drops"   # "1"/"0" – posílat drop kódy do chatu
HTTP_TIMEOUT = 12


# ---------------- Uložený bot token ----------------
def get_bot(conn: sqlite3.Connection):
    return conn.execute("SELECT * FROM bot_tokens WHERE id = 1").fetchone()


def is_connected(conn: sqlite3.Connection) -> bool:
    return get_bot(conn) is not None


def auto_post_enabled(conn: sqlite3.Connection) -> bool:
    return get_setting(conn, SETTING_AUTO_POST, "1") == "1"


def set_auto_post(conn: sqlite3.Connection, enabled: bool) -> None:
    set_setting(conn, SETTING_AUTO_POST, "1" if enabled else "0")


def disconnect(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM bot_tokens WHERE id = 1")


def save_real_token(conn: sqlite3.Connection, bot_username: str, access_token: str,
                    refresh_token: str, expires_in: int, scope: str,
                    broadcaster_channel: str, broadcaster_user_id: str = "") -> None:
    expires_at = (datetime.now(timezone.utc) + timedelta(seconds=max(60, expires_in - 30))).isoformat()
    conn.execute(
        "INSERT INTO bot_tokens (id, bot_username, access_token, refresh_token, expires_at, "
        "scope, broadcaster_channel, broadcaster_user_id, is_demo, updated_at) "
        "VALUES (1, ?, ?, ?, ?, ?, ?, ?, 0, ?) "
        "ON CONFLICT(id) DO UPDATE SET bot_username=excluded.bot_username, "
        "access_token=excluded.access_token, refresh_token=excluded.refresh_token, "
        "expires_at=excluded.expires_at, scope=excluded.scope, "
        "broadcaster_channel=excluded.broadcaster_channel, "
        "broadcaster_user_id=excluded.broadcaster_user_id, is_demo=0, updated_at=excluded.updated_at",
        (bot_username, access_token, refresh_token, expires_at, scope,
         broadcaster_channel, broadcaster_user_id, now_iso()),
    )


def connect_demo(conn: sqlite3.Connection, bot_username: str = None,
                 channel: str = None) -> None:
    """Demo připojení – bez reálného OAuth. Pro lokální vyzkoušení konzole + auto-postu."""
    conn.execute(
        "INSERT INTO bot_tokens (id, bot_username, broadcaster_channel, is_demo, updated_at) "
        "VALUES (1, ?, ?, 1, ?) "
        "ON CONFLICT(id) DO UPDATE SET bot_username=excluded.bot_username, "
        "broadcaster_channel=excluded.broadcaster_channel, is_demo=1, "
        "access_token=NULL, refresh_token=NULL, updated_at=excluded.updated_at",
        (bot_username or KICK_BOT_USERNAME, channel or KICK_BROADCASTER_CHANNEL, now_iso()),
    )


def status(conn: sqlite3.Connection) -> dict:
    """Stav bota pro admin UI."""
    row = get_bot(conn)
    if not row:
        return {"connected": False, "mode": "none",
                "bot_username": KICK_BOT_USERNAME,
                "channel": KICK_BROADCASTER_CHANNEL,
                "auto_post": auto_post_enabled(conn)}
    return {
        "connected": True,
        "mode": "demo" if row["is_demo"] else "real",
        "bot_username": row["bot_username"] or KICK_BOT_USERNAME,
        "channel": row["broadcaster_channel"] or KICK_BROADCASTER_CHANNEL,
        "auto_post": auto_post_enabled(conn),
        "updated_at": row["updated_at"],
    }


# ---------------- Token refresh ----------------
def _refresh_token(conn: sqlite3.Connection, row) -> str:
    """Obnoví access token přes refresh_token. Vrátí platný access token."""
    body = urllib.parse.urlencode({
        "grant_type": "refresh_token",
        "refresh_token": row["refresh_token"],
        "client_id": KICK_CLIENT_ID,
        "client_secret": KICK_CLIENT_SECRET,
    }).encode()
    req = urllib.request.Request(KICK_TOKEN_URL, data=body,
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
        tok = json.loads(r.read().decode())
    save_real_token(conn, row["bot_username"], tok["access_token"],
                    tok.get("refresh_token", row["refresh_token"]),
                    int(tok.get("expires_in", 3600)), row["scope"] or "",
                    row["broadcaster_channel"], row["broadcaster_user_id"] or "")
    conn.commit()
    return tok["access_token"]


def _valid_access_token(conn: sqlite3.Connection, row) -> str:
    """Vrátí platný access token (obnoví, pokud vypršel)."""
    exp = row["expires_at"]
    if exp and exp < now_iso() and row["refresh_token"]:
        return _refresh_token(conn, row)
    return row["access_token"]


_app_tok = {"token": None, "exp": None}   # cache App Access Tokenu (client_credentials)


def app_access_token() -> str:
    """App Access Token (grant client_credentials) pro veřejné GET endpointy – např. čtení
    stavu streamu (GET /channels). Nepotřebuje připojeného bota ani user scope `channel:read`.
    Cachuje se do (expirace − 60 s)."""
    now = datetime.now(timezone.utc)
    if _app_tok["token"] and _app_tok["exp"] and now < _app_tok["exp"]:
        return _app_tok["token"]
    body = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "client_id": KICK_CLIENT_ID,
        "client_secret": KICK_CLIENT_SECRET,
    }).encode()
    req = urllib.request.Request(KICK_TOKEN_URL, data=body,
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=8) as r:
        tok = json.loads(r.read().decode())
    _app_tok["token"] = tok["access_token"]
    _app_tok["exp"] = now + timedelta(seconds=max(60, int(tok.get("expires_in", 3600)) - 60))
    return _app_tok["token"]


def _http_err(e) -> str:
    """Z HTTPError vytáhne i TĚLO odpovědi – Kick tam píše, PROČ to selhalo."""
    if isinstance(e, urllib.error.HTTPError):
        try:
            body = e.read().decode("utf-8", "replace")[:300]
        except Exception:
            body = ""
        return f"HTTP {e.code}: {body or e.reason}"
    return str(e)[:300]


def diagnose(conn: sqlite3.Connection) -> dict:
    """Otestuje token bota proti Kick API (GET /users) – BEZ posílání zpráv.

    Ukáže uložené scopes, expiraci a přesný důvod případného 401 (z těla odpovědi).
    """
    row = get_bot(conn)
    if not row:
        return {"ok": False, "stage": "no_bot", "detail": "Bot není připojen."}
    if row["is_demo"] or not row["access_token"]:
        return {"ok": False, "stage": "demo", "detail": "Bot je v demo režimu (chybí reálný token)."}
    info = {
        "scope": row["scope"] or "(prázdné)",
        "has_chat_scope": "chat:write" in (row["scope"] or ""),
        "has_events_scope": "events:subscribe" in (row["scope"] or ""),
        "expires_at": row["expires_at"],
        "expired": bool(row["expires_at"] and row["expires_at"] < now_iso()),
        "broadcaster_user_id": row["broadcaster_user_id"] or "(nezjištěno)",
    }
    try:
        token = _valid_access_token(conn, row)
    except Exception as e:
        info.update({"ok": False, "stage": "refresh", "detail": _http_err(e)})
        return info
    req = urllib.request.Request(KICK_USER_URL, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
            body = r.read().decode("utf-8", "replace")[:300]
        info.update({"ok": True, "stage": "users", "status": 200, "detail": body})
    except Exception as e:
        info.update({"ok": False, "stage": "users", "detail": _http_err(e)})
    return info


def resolve_broadcaster_id(conn: sqlite3.Connection, row, access_token: str) -> str:
    """Zjistí broadcaster_user_id (a uloží ho).

    Bot je majitel kanálu (zurys1337) → broadcaster_user_id = jeho vlastní user_id
    z GET /users (to volání funguje). Fallback: /channels?slug=. Dřív se volalo jen
    /channels, což házelo 401 a maskovalo se to jako chyba chatu.
    """
    if row["broadcaster_user_id"]:
        return row["broadcaster_user_id"]
    bid = ""
    # 1) vlastní user_id z /users (bot = majitel kanálu zurys1337)
    try:
        req = urllib.request.Request(KICK_USER_URL, headers={"Authorization": f"Bearer {access_token}"})
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
            d = json.loads(r.read().decode())
        u = (d.get("data") or [{}])[0] if isinstance(d, dict) else {}
        bid = str(u.get("user_id") or u.get("id") or "")
    except Exception:
        bid = ""
    # 2) fallback: /channels?slug=
    if not bid:
        try:
            slug = row["broadcaster_channel"] or KICK_BROADCASTER_CHANNEL
            url = f"{KICK_CHANNELS_URL}?slug={urllib.parse.quote(slug)}"
            req = urllib.request.Request(url, headers={"Authorization": f"Bearer {access_token}"})
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
                data = json.loads(r.read().decode())
            ch = (data.get("data") or [data])[0] if isinstance(data, dict) else {}
            bid = str(ch.get("broadcaster_user_id") or ch.get("user_id") or ch.get("id") or "")
        except Exception:
            bid = ""
    if bid:
        conn.execute("UPDATE bot_tokens SET broadcaster_user_id = ? WHERE id = 1", (bid,))
        conn.commit()
    return bid


# ---------------- Log zpráv ----------------
def log_message(conn: sqlite3.Connection, channel: str, author: str, content: str,
                kind: str, sent_real: int, error: str = None) -> int:
    cur = conn.execute(
        "INSERT INTO bot_messages (channel, author, content, kind, sent_real, error, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (channel, author, content, kind, sent_real, error, now_iso()),
    )
    return cur.lastrowid


def recent_messages(conn: sqlite3.Connection, limit: int = 40) -> list:
    rows = conn.execute(
        "SELECT id, channel, author, content, kind, sent_real, error, created_at "
        "FROM bot_messages ORDER BY id DESC LIMIT ?", (limit,),
    ).fetchall()
    return [dict(r) for r in reversed(rows)]   # nejstarší → nejnovější (pro chat)


# ---------------- Odeslání zprávy ----------------
def send_message(conn: sqlite3.Connection, content: str, kind: str = "manual") -> dict:
    """Pošle zprávu botem. Reálně (Kick API) nebo demo (jen log). Necommituje samo? – ano, commituje."""
    content = (content or "").strip()
    if not content:
        return {"sent": False, "error": "Prázdná zpráva."}
    content = content[:480]
    row = get_bot(conn)
    if not row:
        return {"sent": False, "error": "Bot není připojen."}

    channel = row["broadcaster_channel"] or KICK_BROADCASTER_CHANNEL
    author = row["bot_username"] or KICK_BOT_USERNAME

    # DEMO režim – jen zaloguj
    if row["is_demo"] or not row["access_token"]:
        mid = log_message(conn, channel, author, content, kind, 0)
        conn.commit()
        return {"sent": True, "real": False, "id": mid,
                "note": "Demo režim – zpráva zalogována, neodeslána na Kick."}

    # REÁLNÝ režim – Kick API
    try:
        token = _valid_access_token(conn, row)
        broadcaster_id = resolve_broadcaster_id(conn, row, token)
        payload = json.dumps({
            "broadcaster_user_id": int(broadcaster_id) if broadcaster_id.isdigit() else broadcaster_id,
            "content": content,
            "type": "user",   # posílá jako účet bota (SedlakBOT) do kanálu
        }).encode()
        req = urllib.request.Request(
            KICK_CHAT_URL, data=payload, method="POST",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
            r.read()
        mid = log_message(conn, channel, author, content, kind, 1)
        conn.commit()
        return {"sent": True, "real": True, "id": mid}
    except Exception as e:
        err = _http_err(e)
        mid = log_message(conn, channel, author, content, kind, 0, error=err)
        conn.commit()
        return {"sent": False, "real": True, "id": mid, "error": err}


# ---------------- Moderace chatu (ban / unban) ----------------
def _moderation(conn: sqlite3.Connection, target_kick_id, method: str, extra: dict = None) -> dict:
    """POST = ban/timeout, DELETE = unban na Kick moderačním API. Vrací {ok, error?}.

    Bez sítě skončí na guardrailech (není bot / demo / chybí scope / špatné ID)."""
    row = get_bot(conn)
    if not row:
        return {"ok": False, "error": "Bot není připojen."}
    if row["is_demo"] or not row["access_token"]:
        return {"ok": False, "error": "Bot je v demo režimu – reálný Kick ban nejde poslat."}
    if "moderation:ban" not in (row["scope"] or ""):
        return {"ok": False, "error": "Token bota nemá scope moderation:ban – znovu připoj bota (Admin → 🤖 Kick bot)."}
    tid = str(target_kick_id or "").strip()
    if not tid.isdigit():
        return {"ok": False, "error": "Neplatné Kick ID uživatele."}
    try:
        token = _valid_access_token(conn, row)
        bid = str(resolve_broadcaster_id(conn, row, token) or "")
        if not bid.isdigit():
            return {"ok": False, "error": "Nepodařilo se zjistit ID kanálu (broadcaster_user_id)."}
        payload = {"broadcaster_user_id": int(bid), "user_id": int(tid)}
        payload.update(extra or {})
        req = urllib.request.Request(
            KICK_MODERATION_URL, data=json.dumps(payload).encode(), method=method,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
            r.read()
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": _http_err(e)}


def moderate_ban(conn: sqlite3.Connection, target_kick_id, reason: str = "",
                 duration_min: int = None) -> dict:
    """Ban v Kick chatu: bez duration permanentní, s duration timeout (1–10080 min)."""
    extra = {}
    if duration_min:
        extra["duration"] = max(1, min(10080, int(duration_min)))
    if reason:
        extra["reason"] = str(reason)[:100]
    return _moderation(conn, target_kick_id, "POST", extra)


def moderate_unban(conn: sqlite3.Connection, target_kick_id) -> dict:
    """Zruší ban/timeout v Kick chatu."""
    return _moderation(conn, target_kick_id, "DELETE")


def drop_announcement(code: str, points: int, max_winners: int) -> str:
    """Text oznámení dropu do chatu (styl ZurysBota)."""
    return (f"🎁 DROP TIME! Prvních {max_winners} hráčů zadá kód {code} na "
            f"zurys.live a získá {points} sedláků! GO 👑")


def post_drop(conn: sqlite3.Connection, code: str, points: int, max_winners: int) -> dict:
    """Pošle oznámení dropu (pokud je auto-post zapnutý a bot připojený)."""
    if not auto_post_enabled(conn) or not is_connected(conn):
        return {"sent": False, "skipped": True}
    return send_message(conn, drop_announcement(code, points, max_winners), kind="drop")


def subscribe_events(conn: sqlite3.Connection) -> dict:
    """Přihlásí se k odběru Kick eventů (sub/resub/gift/follow/chat) → náš webhook.

    Použije token bota (musí mít scope `events:subscribe`). Webhook URL se nastavuje
    v Kick app settings (developer dashboard). Vrátí výsledek pro admin UI.
    """
    row = get_bot(conn)
    if not row or row["is_demo"] or not row["access_token"]:
        return {"ok": False, "error": "Bot není připojen reálně (chybí token). Připoj bota přes Kick."}
    try:
        token = _valid_access_token(conn, row)
    except Exception as e:
        return {"ok": False, "error": "Token bota nejde obnovit: " + str(e)[:200]}
    payload = json.dumps({"events": WEBHOOK_EVENTS, "method": "webhook"}).encode()
    req = urllib.request.Request(
        KICK_EVENTS_SUB_URL, data=payload, method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
            body = r.read().decode()
        return {"ok": True, "subscribed": len(WEBHOOK_EVENTS), "response": body[:600]}
    except urllib.error.HTTPError as e:
        try:
            err = e.read().decode()[:600]
        except Exception:
            err = ""
        return {"ok": False, "status": e.code, "error": err or str(e)}
    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}


def list_subscriptions(conn: sqlite3.Connection) -> dict:
    """Diagnostika: vrátí AKTIVNÍ odběry eventů u Kicku (GET) – ať vidíme, jestli jsou přihlášené i suby."""
    row = get_bot(conn)
    if not row or row["is_demo"] or not row["access_token"]:
        return {"ok": False, "error": "Bot není připojen reálně (chybí token)."}
    try:
        token = _valid_access_token(conn, row)
    except Exception as e:
        return {"ok": False, "error": "Token bota nejde obnovit: " + str(e)[:200]}
    req = urllib.request.Request(
        KICK_EVENTS_SUB_URL, method="GET",
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
            body = r.read().decode()
        try:
            data = json.loads(body)
        except Exception:
            data = body[:800]
        # vytáhni jen názvy eventů pro přehlednost
        names = []
        try:
            for s in (data.get("data") if isinstance(data, dict) else data) or []:
                nm = s.get("event") or s.get("name") or (s.get("subscription") or {}).get("event")
                if nm:
                    names.append(nm)
        except Exception:
            pass
        return {"ok": True, "event_names": names, "raw": data}
    except urllib.error.HTTPError as e:
        try:
            err = e.read().decode()[:600]
        except Exception:
            err = ""
        return {"ok": False, "status": e.code, "error": err or str(e)}
    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}
