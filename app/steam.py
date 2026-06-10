"""Vyhledání obrázku CS2 skinu na Steam Community Marketu (server-side).

Proč na serveru: prohlížečový `fetch` na Steam padá na CORS (Steam neposílá
Access-Control-Allow-Origin) + Steam rate-limituje. Tady to jede přes stdlib
urllib (žádná nová závislost), s in-memory cache a fail-open chováním.

Přesnost: Steam search vrací podle RELEVANCE, ne přesně. `count=1` by trefil i jiný
skin (např. „Oxide Blaze" místo „Blaze", nebo StatTrak™ verzi). Proto bereme víc
výsledků a matchujeme PŘESNĚ `hash_name` (case-insensitive, bez ™). Když není přesná
shoda → vrátíme None (radši nic než špatný obrázek), streamer doplní URL ručně.
"""
import json
import urllib.parse
import urllib.request
from typing import Optional

APPID_CS2 = 730
_CDN = "https://community.cloudflare.steamstatic.com/economy/image/"
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) ZURYS-shop/1.0"
_CACHE: dict = {}          # norm(název) -> dict (cachujeme JEN nálezy)
_CACHE_MAX = 1000


def _norm(s: str) -> str:
    """Porovnávací klíč: lowercase, bez ™, sjednocené mezery."""
    return " ".join((s or "").lower().replace("™", "").split())


def _search(query: str, count: int) -> Optional[dict]:
    """Surový dotaz na Steam market search. None = chyba/timeout/soft-limit."""
    qs = urllib.parse.urlencode({"query": query, "appid": APPID_CS2, "count": count, "norender": 1})
    url = "https://steamcommunity.com/market/search/render/?" + qs
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except Exception:
        return None


def lookup_skin(name: str) -> Optional[dict]:
    """Najde PŘESNOU shodu skinu → {name, hash_name, image_url, price} nebo None."""
    name = (name or "").strip()
    if len(name) < 2:
        return None
    key = _norm(name)
    if key in _CACHE:
        return _CACHE[key]

    data = _search(name, 20)
    if data is None:
        return None                      # chyba/soft-limit → NEcachovat, zkusit příště
    out = None
    for it in (data.get("results") or []):
        if _norm(it.get("hash_name", "")) == key:
            icon = (it.get("asset_description") or {}).get("icon_url")
            if icon:
                out = {
                    "name": it.get("hash_name") or name,
                    "hash_name": it.get("hash_name") or name,
                    "image_url": _CDN + icon + "/360fx360f",
                    "price": it.get("sell_price_text") or "",
                }
            break
    if out:                              # cachujeme JEN pozitivní (negativ může být dočasný soft-limit)
        if len(_CACHE) >= _CACHE_MAX:
            _CACHE.clear()
        _CACHE[key] = out
    return out
