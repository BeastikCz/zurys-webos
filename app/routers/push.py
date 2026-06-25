"""Web Push: VAPID public key + subscribe / unsubscribe.

Notifikace do mobilu (oznamovací centrum) – hlavně „chrobáci v zahrádce". Subscription
ukládáme per uživatel+endpoint (1 zařízení = 1 řádka); re-subscribe přepíše.
"""
import sqlite3

from fastapi import APIRouter, Depends, Request

from ..db import now_iso
from ..deps import db_dep, require_user
from ..models import PushSubIn
from .. import webpush

router = APIRouter(prefix="/push", tags=["push"])


@router.get("/vapid-public")
def vapid_public():
    """Veřejný VAPID klíč pro PushManager.subscribe + jestli je push vůbec zapnutý (klíče nastavené)."""
    return {"key": webpush.public_key(), "enabled": webpush.enabled()}


@router.post("/subscribe")
def subscribe(data: PushSubIn, request: Request,
              user: sqlite3.Row = Depends(require_user),
              conn: sqlite3.Connection = Depends(db_dep)):
    """Uloží/aktualizuje push subscription přihlášeného uživatele (UPSERT na endpoint)."""
    ua = (request.headers.get("user-agent") or "")[:300]
    conn.execute(
        "INSERT INTO push_subs (user_id, endpoint, p256dh, auth, ua, created_at) VALUES (?,?,?,?,?,?) "
        "ON CONFLICT(endpoint) DO UPDATE SET user_id=excluded.user_id, p256dh=excluded.p256dh, "
        "auth=excluded.auth, ua=excluded.ua",
        (user["id"], data.endpoint, data.keys.p256dh, data.keys.auth, ua, now_iso()))
    conn.commit()
    return {"ok": True}


@router.post("/unsubscribe")
def unsubscribe(data: PushSubIn, user: sqlite3.Row = Depends(require_user),
                conn: sqlite3.Connection = Depends(db_dep)):
    """Smaže subscription (uživatel vypnul upozornění / odhlásil zařízení)."""
    conn.execute("DELETE FROM push_subs WHERE endpoint = ? AND user_id = ?", (data.endpoint, user["id"]))
    conn.commit()
    return {"ok": True}
