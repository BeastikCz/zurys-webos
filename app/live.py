"""Zjišťuje, jestli běží stream (kick.com/<channel>). Body za sledování jdou jen když je live.

Režim se řídí nastavením `stream_live_override` (app_settings):
  - "on"   → vždy považuj za live (body za sledování běží pořád, jako dřív),
  - "off"  → vždy offline (body za sledování se nepřičítají),
  - "auto" → automatická detekce přes Kick API (default).

Auto-detekce: GET /public/v1/channels?slug=<channel> s OAuth tokenem bota → přečte
`stream.is_live`. Výsledek se cachuje (~45 s), aby se Kick nebombardoval při heartbeatech.
Fail-safe: při chybě drží poslední známý stav; když nikdy nedetekoval, vrátí False
(radši nedávat body, než je sypat při offline).
"""
import json
import threading
import time
import urllib.parse
import urllib.request

from .config import KICK_CHANNELS_URL, KICK_BROADCASTER_CHANNEL
from .db import get_setting
from . import kickbot

_TTL = 45
HTTP_TIMEOUT = 8
_cache = {"at": 0.0, "live": False, "ok": False}   # ok = už proběhla úspěšná detekce


_refresh_lock = threading.Lock()


def _detect(conn) -> bool:
    """Zavolá Kick API a vrátí, jestli stream běží. Používá App Access Token (client_credentials),
    takže nepotřebuje připojeného bota ani user scope `channel:read`."""
    token = kickbot.app_access_token()
    row = kickbot.get_bot(conn)
    slug = (row["broadcaster_channel"] if row and row["broadcaster_channel"] else "") or KICK_BROADCASTER_CHANNEL
    url = f"{KICK_CHANNELS_URL}?slug={urllib.parse.quote(slug)}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
        data = json.loads(r.read().decode())
    ch = (data.get("data") or [data])[0] if isinstance(data, dict) else {}
    stream = ch.get("stream")
    if isinstance(stream, dict):
        return bool(stream.get("is_live"))
    # fallback klíče (kdyby Kick změnil tvar)
    if isinstance(ch.get("is_live"), bool):
        return ch["is_live"]
    lv = ch.get("livestream")
    if lv is not None:
        return bool(lv)
    return False


def is_live(conn) -> bool:
    """Hlavní dotaz: má se teď přičítat za sledování? Respektuje override + cache."""
    mode = (get_setting(conn, "stream_live_override", "auto") or "auto").lower()
    if mode == "on":
        return True
    if mode == "off":
        return False
    now = time.monotonic()
    if _cache["ok"] and now - _cache["at"] < _TTL:
        return _cache["live"]
    if not _refresh_lock.acquire(blocking=False):
        # ponytail: stale state is better than blocking every request on the same Kick call.
        return _cache["live"]
    try:
        live = _detect(conn)
        _cache.update(at=now, live=live, ok=True)
        return live
    except Exception:
        _cache["at"] = now            # nezahlcovat – zkus zas až za TTL
        return _cache["live"] if _cache["ok"] else False
    finally:
        _refresh_lock.release()


def get_mode(conn) -> str:
    return (get_setting(conn, "stream_live_override", "auto") or "auto").lower()


def status(conn) -> dict:
    """Stav pro admin panel: režim, aktuální live, jestli jde detekovat (připojený reálný bot)."""
    row = kickbot.get_bot(conn)
    detectable = bool(row and not row["is_demo"] and row["access_token"])
    return {"mode": get_mode(conn), "live": is_live(conn), "detectable": detectable}


def broadcaster_slug(conn) -> str:
    """Slug streamerova kanálu (pro odkaz na živý stream z hlavičky)."""
    try:
        row = kickbot.get_bot(conn)
        slug = row["broadcaster_channel"] if row and row["broadcaster_channel"] else None
    except Exception:
        slug = None
    return slug or KICK_BROADCASTER_CHANNEL
