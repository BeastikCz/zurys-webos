"""Lokální katalog CS2 skinů (jméno + obrázek) pro snadné přidávání do shopu.

Zdroj: ByMykel/CSGO-API – statický JSON všech ~2100 skinů, obrázky na Steam CDN.
Stáhne se JEDNOU (lazy, při prvním hledání) a drží v paměti → vyhledávání je
okamžité, BEZ Steam rate-limitu / CORS / „nenalezeno". Fallback: když fetch selže,
vrátí prázdno a zkusí příště (necachuje chybu).
"""
import json
import urllib.request
from typing import List, Optional

_URL = "https://raw.githubusercontent.com/ByMykel/CSGO-API/main/public/api/en/skins.json"
_UA = "Mozilla/5.0 ZURYS-shop/1.0"
_SKINS: Optional[List[dict]] = None     # [{name, name_lower, image}] | None když nenačteno


def _load() -> List[dict]:
    global _SKINS
    if _SKINS is not None:
        return _SKINS
    try:
        req = urllib.request.Request(_URL, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
    except Exception:
        return []          # NEcacheovat → zkusí se příště
    out = []
    seen = set()
    for it in (data or []):
        name = (it.get("name") or "").strip()
        image = (it.get("image") or "").strip()
        if name and image and name.lower() not in seen:
            seen.add(name.lower())
            out.append({"name": name, "name_lower": name.lower(), "image": image})
    if out:
        _SKINS = out
    return out


def search(query: str, limit: int = 24) -> List[dict]:
    """Skiny, jejichž název obsahuje query (case-insensitive). startswith má přednost."""
    q = " ".join((query or "").lower().split())
    if len(q) < 2:
        return []
    skins = _load()
    starts, contains = [], []
    for s in skins:
        nl = s["name_lower"]
        if nl.startswith(q):
            starts.append(s)
        elif q in nl:
            contains.append(s)
    res = (starts + contains)[:limit]
    return [{"name": s["name"], "image": s["image"]} for s in res]


def ready() -> bool:
    return bool(_SKINS)


def count() -> int:
    return len(_SKINS) if _SKINS else 0
