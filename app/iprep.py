"""Detekce VPN / proxy / Tor přes proxycheck.io (VOLITELNÉ).

Aktivuje se ENV proměnnou `PROXYCHECK_KEY` (free klíč z proxycheck.io, 1000 dotazů/den).
Bez klíče je to no-op (`is_vpn` vrací False) – nic se nerozbije.

Vlastnosti (ať to nikdy neuškodí provozu):
  - výsledky se cachují v paměti (TTL 6 h) → jedna IP = max jeden dotaz za 6 h,
  - fail-open: chyba/timeout/nedostupné API → False (nikdy kvůli tomu neblokujeme),
  - krátký timeout, volá se jen v anticheatu (citlivé akce), ne na každý request.

Používá stdlib urllib (žádná nová závislost), stejně jako zbytek appky.
"""
import json
import os
import time
import urllib.parse
import urllib.request

_KEY = os.environ.get("PROXYCHECK_KEY", "").strip()
_TTL = 6 * 3600          # cache 6 hodin
_NEG_TTL = 300           # při chybě API drž krátkou negativní cache (5 min)
_TIMEOUT = 2.5           # s – raději rychle vzdát než blokovat
_cache = {}              # ip -> (expires_monotonic, is_proxy, kind)


def enabled() -> bool:
    """Je proxycheck nakonfigurovaný (je klíč)?"""
    return bool(_KEY)


def lookup(ip: str):
    """Vrátí {'proxy': bool, 'type': str} pro IP, nebo None (vypnuto / chyba). Cachuje."""
    if not _KEY or not ip:
        return None
    now = time.monotonic()
    hit = _cache.get(ip)
    if hit and hit[0] > now:
        return {"proxy": hit[1], "type": hit[2]}
    try:
        url = (f"https://proxycheck.io/v2/{urllib.parse.quote(ip)}"
               f"?key={urllib.parse.quote(_KEY)}&vpn=1")
        req = urllib.request.Request(url, headers={"User-Agent": "webos-anticheat"})
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
            data = json.loads(r.read().decode("utf-8"))
        rec = data.get(ip, {}) if isinstance(data, dict) else {}
        is_proxy = str(rec.get("proxy", "no")).lower() == "yes"
        kind = rec.get("type", "") or ""
        _cache[ip] = (now + _TTL, is_proxy, kind)
        if len(_cache) > 10000:   # úklid expirovaných, ať paměť neroste
            for k in [k for k, v in list(_cache.items()) if v[0] <= now]:
                _cache.pop(k, None)
        return {"proxy": is_proxy, "type": kind}
    except Exception:
        _cache[ip] = (now + _NEG_TTL, False, "")   # fail-open + krátká negativní cache
        return {"proxy": False, "type": ""}


def is_vpn(ip: str) -> bool:
    """True, pokud je IP známá VPN/proxy/Tor. Fail-open na False (i bez klíče)."""
    info = lookup(ip)
    return bool(info and info.get("proxy"))
