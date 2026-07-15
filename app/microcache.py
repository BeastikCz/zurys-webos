"""Mini TTL cache pro GLOBÁLNÍ read-only endpointy (odpověď stejná pro všechny).

Při live pollují stovky klientů tytéž endpointy (/stream/status, /auctions, …)
každé ~3 s a server počítá pořád stejnou odpověď. Tenhle dekorátor ji na pár
sekund podrží v paměti → z hot-path zmizí ~80 % SQL práce, staleness ≤ TTL.

Použití: @ttl_cache(3) MEZI @router.get(...) a def handler(...). Dependencies
(conn, user) se injektují dál — cache je ignoruje v klíči (auth se tedy pořád
vyhodnocuje per request, cachuje se JEN výpočet odpovědi). Klíčuje se podle
ostatních argumentů (např. limit=10 vs 24 má vlastní záznam).

POZOR: jen pro endpointy, jejichž odpověď NEZÁVISÍ na uživateli. 1 worker →
žádná invalidace napříč procesy není potřeba.
"""
import sqlite3
import time
from functools import wraps


def _cacheable(v) -> bool:
    """Do klíče nepatří per-request objekty (DB spojení, users řádek z auth)."""
    return not isinstance(v, (sqlite3.Connection, sqlite3.Row))


def ttl_cache(seconds: float):
    def deco(fn):
        store = {}   # key -> (monotonic_ts, hodnota)

        @wraps(fn)
        def wrap(*args, **kw):
            key = (tuple(a for a in args if _cacheable(a)),
                   tuple(sorted((k, v) for k, v in kw.items() if _cacheable(v))))
            now = time.monotonic()
            hit = store.get(key)
            if hit and now - hit[0] < seconds:
                return hit[1]
            val = fn(*args, **kw)
            store[key] = (now, val)
            if len(store) > 64:   # pojistka proti růstu při divokých kombinacích parametrů
                store.clear()
            return val
        return wrap
    return deco
