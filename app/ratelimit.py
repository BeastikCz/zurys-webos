"""Jednoduchý in-memory rate-limiter (klouzavé okno).

Pozn.: drží se v paměti procesu – pro produkci s víc workery použij Redis.
"""
import time
from collections import defaultdict

from fastapi import HTTPException

_hits = defaultdict(list)


def rate_limit(key: str, max_n: int, per_seconds: float) -> None:
    """Povolí max_n požadavků za per_seconds pro daný klíč, jinak HTTP 429."""
    now = time.monotonic()
    cutoff = now - per_seconds
    q = _hits[key]
    while q and q[0] < cutoff:
        q.pop(0)
    if len(q) >= max_n:
        raise HTTPException(status_code=429, detail="Moc rychle za sebou – zkus to za chvíli. ⏳")
    q.append(now)
    # občasný úklid prázdných klíčů, ať paměť neroste
    if len(_hits) > 5000:
        for k in [k for k, v in list(_hits.items()) if not v]:
            _hits.pop(k, None)
