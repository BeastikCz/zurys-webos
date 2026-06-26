"""Autentizace přes KICK účet (demo režim + volitelné reálné OAuth) + me/logout."""
import base64
import hashlib
import hmac
import json
import os
import secrets as _secrets
import sqlite3
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse

from ..config import (SESSION_COOKIE, SESSION_DAYS, ROLE_USER, ROLE_ADMIN,
                      ADMIN_KICK_USERNAMES, KICK_CLIENT_ID, KICK_CLIENT_SECRET,
                      KICK_REDIRECT_URI, KICK_AUTH_URL, KICK_TOKEN_URL,
                      KICK_USER_URL, KICK_SCOPE, KICK_BOT_SCOPE,
                      KICK_BROADCASTER_CHANNEL)
from ..db import now_iso, get_setting
from ..deps import (db_dep, get_current_user, require_admin, require_user, can_access,
                    to_public, client_ip, record_login, user_rank)
from ..models import KickConnectIn, FingerprintIn
from ..security import new_token
from ..ratelimit import rate_limit
from .. import kickbot

router = APIRouter(prefix="/auth", tags=["auth"])

# Reálný Kick OAuth jen na PRODUKCI (Fly nastavuje FLY_APP_NAME). Lokálně (bez FLY_APP_NAME)
# je OAUTH_ENABLED vždy False → běží DEMO login (přihlášení jen nickem, pro vývoj). Demo endpoint
# /kick/connect je navíc gated `if OAUTH_ENABLED` níž, takže na produkci (kde je FLY_APP_NAME +
# KICK_CLIENT_ID v env) zůstává zablokovaný. Prod login se NEMĚNÍ.
OAUTH_ENABLED = bool(KICK_CLIENT_ID and KICK_CLIENT_SECRET and os.environ.get("FLY_APP_NAME"))


# ---------------- Session ----------------
def _secure_cookie(request: Request) -> bool:
    """Secure cookie zapínej jen na produkci/HTTPS; lokální HTTP vývoj musí dál fungovat."""
    return bool(os.environ.get("FLY_APP_NAME")
                or request.headers.get("x-forwarded-proto") == "https"
                or request.url.scheme == "https")


def _start_session(conn: sqlite3.Connection, response: Response, user_id: int,
                   request: Request) -> None:
    token = new_token()
    created = datetime.now(timezone.utc)
    expires = created + timedelta(days=SESSION_DAYS)
    ip = client_ip(request)
    ua = (request.headers.get("user-agent") or "")[:300]
    conn.execute(
        "INSERT INTO sessions (token, user_id, ip, user_agent, last_seen, created_at, expires_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (token, user_id, ip, ua, created.isoformat(), created.isoformat(), expires.isoformat()),
    )
    conn.commit()
    response.set_cookie(SESSION_COOKIE, token, max_age=SESSION_DAYS * 86400,
                        httponly=True, secure=_secure_cookie(request),
                        samesite="lax", path="/")


def _find_or_create_kick_user(conn: sqlite3.Connection, kick_username: str,
                              display: str, kick_id: Optional[str] = None,
                              avatar: Optional[str] = None) -> sqlite3.Row:
    """Najde uživatele, nebo ho založí (role dle allowlistu).

    Páruje PRIMÁRNĚ podle stabilního `kick_id` (přežije změnu nicku na Kicku) – díky tomu
    změna nicku jen aktualizuje `kick_username` na existujícím účtu a NEZALOŽÍ druhý účet.
    Až sekundárně podle nicku (kvůli starým účtům / ghost importům bez kick_id). U shody přes
    kick_id preferuje NEzabanovaný účet, ať zabanovaný duplikát nestíní ten aktivní.
    """
    key = kick_username.strip().lstrip("@").lower()
    row = None
    if kick_id:
        row = conn.execute(
            "SELECT * FROM users WHERE kick_id = ? ORDER BY banned ASC, id ASC LIMIT 1",
            (str(kick_id),)).fetchone()
    if not row:
        row = conn.execute("SELECT * FROM users WHERE kick_username = ?", (key,)).fetchone()
    if row:
        updates, params = [], []
        if kick_id and not row["kick_id"]:
            updates.append("kick_id = ?"); params.append(str(kick_id))
        # Změna nicku na Kicku → aktualizuj kick_username i zobrazované jméno (ať nevzniká druhý
        # účet). Jen když nový nick nedrží JINÝ účet (jinak UNIQUE konflikt – to nech na adminovi).
        if key != (row["kick_username"] or "") and not conn.execute(
                "SELECT 1 FROM users WHERE kick_username = ? AND id != ?", (key, row["id"])).fetchone():
            updates.append("kick_username = ?"); params.append(key)
            updates.append("username = ?"); params.append(display.strip().lstrip("@") or key)
        if avatar and avatar != row["avatar_url"]:   # vždy obnov fotku z Kicku, když se změnila
            updates.append("avatar_url = ?"); params.append(avatar)
        if updates:
            params.append(row["id"])
            conn.execute(f"UPDATE users SET {', '.join(updates)} WHERE id = ?", params)
            conn.commit()
        return conn.execute("SELECT * FROM users WHERE id = ?", (row["id"],)).fetchone()
    role = ROLE_ADMIN if key in [a.lower() for a in ADMIN_KICK_USERNAMES] else ROLE_USER
    cur = conn.execute(
        "INSERT INTO users (kick_username, kick_id, username, avatar_url, points, role, created_at) "
        "VALUES (?, ?, ?, ?, 0, ?, ?)",
        (key, kick_id, display.strip().lstrip("@") or key, avatar, role, now_iso()),
    )
    conn.commit()
    return conn.execute("SELECT * FROM users WHERE id = ?", (cur.lastrowid,)).fetchone()


# ---------------- Stav / info ----------------
@router.get("/kick/status")
def kick_status():
    """Frontend podle toho zvolí reálný OAuth vs. demo modal."""
    return {
        "mode": "oauth" if OAUTH_ENABLED else "demo",
        "demo_admin": (ADMIN_KICK_USERNAMES[0] if ADMIN_KICK_USERNAMES else "admin"),
    }


# ---------------- DEMO připojení (bez OAuth) ----------------
@router.post("/kick/connect")
def kick_connect(data: KickConnectIn, request: Request, response: Response,
                 conn: sqlite3.Connection = Depends(db_dep)):
    """Demo režim: propojení Kick účtu jen přes uživatelské jméno."""
    rate_limit(f"connect:{client_ip(request)}", 12, 60)  # max 12 / min z jedné IP
    if OAUTH_ENABLED:
        raise HTTPException(status_code=400,
                            detail="Je zapnuté reálné Kick OAuth – použij tlačítko Připojit přes Kick.")
    handle = data.username.strip().lstrip("@")
    if len(handle) < 2:
        raise HTTPException(status_code=400, detail="Zadej platný Kick nick.")
    row = _find_or_create_kick_user(conn, handle, handle)
    record_login(conn, row["id"], request, "kick-demo")
    _start_session(conn, response, row["id"], request)
    return {"user": to_public(row, include_email=True), "mode": "demo"}


# ---------------- REÁLNÉ Kick OAuth (zapne se přes kick.json) ----------------
def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


_CB_PATH = urllib.parse.urlsplit(KICK_REDIRECT_URI).path or "/api/auth/kick/callback"


def _callback_uri(request: Request) -> str:
    """redirect_uri podle aktuální domény → login funguje na zurys.live i itzokk.com.

    Obě domény musí být registrované v Kick dashboardu. Fallback: config KICK_REDIRECT_URI.
    Stejný redirect_uri se použije v authorize i při výměně kódu (Kick to vyžaduje shodné).
    """
    host = request.headers.get("host")
    if not host:
        return KICK_REDIRECT_URI
    proto = request.headers.get("x-forwarded-proto") or "https"
    return f"{proto}://{host}{_CB_PATH}"


@router.get("/kick/login")
def kick_login(request: Request):
    """Přesměruje na Kick autorizaci (PKCE). V demu pošle na connect stránku."""
    if not OAUTH_ENABLED:
        return RedirectResponse("/#/connect")
    verifier = _b64url(_secrets.token_bytes(40))
    challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
    state = _secrets.token_urlsafe(16)
    params = urllib.parse.urlencode({
        "client_id": KICK_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": _callback_uri(request),
        "scope": KICK_SCOPE,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
    })
    resp = RedirectResponse(f"{KICK_AUTH_URL}?{params}")
    secure = _secure_cookie(request)
    resp.set_cookie("kick_pkce", verifier, httponly=True, secure=secure, max_age=600, samesite="lax", path="/")
    resp.set_cookie("kick_state", state, httponly=True, secure=secure, max_age=600, samesite="lax", path="/")
    return resp


@router.get("/kick/bot/login")
def kick_bot_login(request: Request, user: sqlite3.Row = Depends(require_user)):
    """Spustí OAuth pro připojení BOT účtu (SedlakBOT) se scope chat:write.

    Používá stejný redirect_uri jako přihlášení diváka – rozliší se cookie kick_flow=bot.
    Smí admin i broadcaster (sekce „bot"). Musí být v prohlížeči přihlášený na Kicku
    jako účet, který bota poveze (např. zurys1337 / SedlakBOT).
    """
    if not can_access(user["role"], "bot"):
        raise HTTPException(status_code=403, detail="Na připojení bota nemáš oprávnění (jen broadcaster / admin).")
    if not OAUTH_ENABLED:
        raise HTTPException(status_code=400,
                            detail="Kick OAuth není nakonfigurováno (chybí kick.json).")
    verifier = _b64url(_secrets.token_bytes(40))
    challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
    state = _secrets.token_urlsafe(16)
    params = urllib.parse.urlencode({
        "client_id": KICK_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": _callback_uri(request),
        "scope": KICK_BOT_SCOPE,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
    })
    resp = RedirectResponse(f"{KICK_AUTH_URL}?{params}")
    secure = _secure_cookie(request)
    resp.set_cookie("kick_pkce", verifier, httponly=True, secure=secure, max_age=600, samesite="lax", path="/")
    resp.set_cookie("kick_state", state, httponly=True, secure=secure, max_age=600, samesite="lax", path="/")
    resp.set_cookie("kick_flow", "bot", httponly=True, secure=secure, max_age=600, samesite="lax", path="/")
    return resp


@router.get("/kick/callback")
def kick_callback(request: Request, code: str = "", state: str = "",
                  conn: sqlite3.Connection = Depends(db_dep)):
    """Zpracuje návrat z Kicku: vymění kód za token, načte profil, přihlásí."""
    if not OAUTH_ENABLED:
        raise HTTPException(status_code=400, detail="Kick OAuth není nakonfigurováno.")
    if not code or not hmac.compare_digest(state, request.cookies.get("kick_state", "")):
        raise HTTPException(status_code=400, detail="Neplatný OAuth stav.")
    verifier = request.cookies.get("kick_pkce") or ""

    # 1) výměna kódu za access token
    token_body = urllib.parse.urlencode({
        "grant_type": "authorization_code",
        "client_id": KICK_CLIENT_ID,
        "client_secret": KICK_CLIENT_SECRET,
        "redirect_uri": _callback_uri(request),
        "code_verifier": verifier,
        "code": code,
    }).encode()
    is_bot_flow = request.cookies.get("kick_flow") == "bot"
    try:
        req = urllib.request.Request(KICK_TOKEN_URL, data=token_body,
                                     headers={"Content-Type": "application/x-www-form-urlencoded"})
        with urllib.request.urlopen(req, timeout=12) as r:
            tok = json.loads(r.read().decode())
        access_token = tok["access_token"]
        # 2) profil uživatele (= bot, pokud bot flow)
        ureq = urllib.request.Request(KICK_USER_URL, headers={"Authorization": f"Bearer {access_token}"})
        with urllib.request.urlopen(ureq, timeout=12) as r:
            udata = json.loads(r.read().decode())
        # Kick vrací {"data":[{...}]} – vezmeme první
        u = (udata.get("data") or [udata])[0]
        kick_id = str(u.get("user_id") or u.get("id") or "")
        kick_username = u.get("name") or u.get("username") or f"kick_{kick_id}"
        avatar = u.get("profile_picture") or u.get("profile_pic")
    except Exception as e:
        raise HTTPException(status_code=502,
                            detail=f"Kick {'připojení bota' if is_bot_flow else 'přihlášení'} selhalo: {e}")

    # --- BOT flow: ulož token bota, NEpřihlašuj jako uživatele ---
    if is_bot_flow:
        kickbot.save_real_token(
            conn, bot_username=kick_username,
            access_token=access_token,
            refresh_token=tok.get("refresh_token", ""),
            expires_in=int(tok.get("expires_in", 3600)),
            scope=tok.get("scope", KICK_BOT_SCOPE),
            broadcaster_channel=KICK_BROADCASTER_CHANNEL,
        )
        conn.commit()
        try:  # nepovinné – předem zjisti ID kanálu (ať první zpráva nečeká)
            kickbot.resolve_broadcaster_id(conn, kickbot.get_bot(conn), access_token)
        except Exception:
            pass
        resp = RedirectResponse("/#/admin?bot=connected")
        resp.delete_cookie("kick_pkce", path="/")
        resp.delete_cookie("kick_state", path="/")
        resp.delete_cookie("kick_flow", path="/")
        return resp

    # --- Normální login diváka ---
    row = _find_or_create_kick_user(conn, kick_username, kick_username, kick_id, avatar)
    record_login(conn, row["id"], request, "kick-oauth")
    resp = RedirectResponse("/#/shop")
    _start_session(conn, resp, row["id"], request)
    resp.delete_cookie("kick_pkce", path="/")
    resp.delete_cookie("kick_state", path="/")
    return resp


# ---------------- Společné ----------------
@router.post("/logout")
def logout(request: Request, response: Response,
           conn: sqlite3.Connection = Depends(db_dep)):
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
        conn.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
def me(user: Optional[sqlite3.Row] = Depends(get_current_user),
       conn: sqlite3.Connection = Depends(db_dep)):
    if user is None:
        return {"user": None}
    data = to_public(user, include_email=True)
    data["rank"] = user_rank(conn, user["points"], user["username"])   # pozice = liga (titul + daily násobič)
    data["pending_rankup"] = (user["pending_rankup"] if "pending_rankup" in user.keys() else "") or ""
    data["pending_overtake"] = (user["pending_overtake"] if "pending_overtake" in user.keys() else "") or ""
    try:                                                       # počet nepřečtených PM (badge)
        if user["role"] in ("admin", "broadcaster"):           # PM jen broadcaster+admin (mod NE)
            data["dm_unread"] = conn.execute(
                "SELECT COUNT(*) c FROM dm_messages WHERE from_id = user_id AND seen = 0").fetchone()["c"]
        else:                                                  # user (vč. moda): nepřečtené zprávy od staffa
            data["dm_unread"] = conn.execute(
                "SELECT COUNT(*) c FROM dm_messages WHERE user_id = ? AND from_id != user_id AND seen = 0",
                (user["id"],)).fetchone()["c"]
    except Exception:
        data["dm_unread"] = 0
    try:                                                       # počet nepřečtených notifikací (zvoneček)
        data["notif_unread"] = conn.execute(
            "SELECT COUNT(*) AS c FROM notifications WHERE user_id = ? AND read = 0",
            (user["id"],)).fetchone()["c"]
    except Exception:
        data["notif_unread"] = 0
    return {"user": data}


@router.post("/seen-rankup")
def seen_rankup(user: Optional[sqlite3.Row] = Depends(get_current_user),
                conn: sqlite3.Connection = Depends(db_dep)):
    """Po zobrazení konfet smaže frontu rank-up oslavy (ať se neukáže znovu)."""
    if user is None:
        return {"ok": False}
    conn.execute("UPDATE users SET pending_rankup = NULL WHERE id = ?", (user["id"],))
    conn.commit()
    return {"ok": True}


@router.post("/seen-overtake")
def seen_overtake(user: Optional[sqlite3.Row] = Depends(get_current_user),
                  conn: sqlite3.Connection = Depends(db_dep)):
    """Po zobrazení hlášky „přeskočil tě" smaže frontu (ať se neukáže znovu)."""
    if user is None:
        return {"ok": False}
    conn.execute("UPDATE users SET pending_overtake = NULL WHERE id = ?", (user["id"],))
    conn.commit()
    return {"ok": True}


@router.post("/fingerprint")
def fingerprint(data: FingerprintIn, request: Request,
                user: Optional[sqlite3.Row] = Depends(get_current_user),
                conn: sqlite3.Connection = Depends(db_dep)):
    """Klientský anticheat signál (webdriver/headless). Tichý no-op pro hosty."""
    if user is None:
        return {"ok": False}
    fp = (data.fp or "")[:64] or None
    conn.execute(
        "INSERT INTO client_signals (user_id, webdriver, fp_hash, ua, created_at) VALUES (?, ?, ?, ?, ?)",
        (user["id"], 1 if data.webdriver else 0, fp,
         (request.headers.get("user-agent") or "")[:300], now_iso()),
    )
    # ban podle zařízení – pokud je otisk na blacklistu, zabanuj i tento (alt) účet.
    # VYPNUTO defaultně (fp_ban_enforce != "1"): hrubý otisk (model+prohlížeč+jazyk) sdílí
    # i cizí lidi → falešné bany. Zapne se až bude přesnější fingerprint. Revert: setting "1".
    banned = False
    if fp and user["role"] != ROLE_ADMIN and get_setting(conn, "fp_ban_enforce", "0") == "1" and \
            conn.execute("SELECT 1 FROM fingerprint_bans WHERE fp_hash = ?", (fp,)).fetchone():
        conn.execute("UPDATE users SET banned = 1, ban_reason = ? WHERE id = ?",
                     ("Zabanované zařízení (fingerprint)", user["id"]))
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (user["id"],))
        banned = True
    conn.commit()
    return {"ok": True, "banned": banned}
