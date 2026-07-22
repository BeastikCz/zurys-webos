"""Lehká detekce náporu (anti-DDoS) – počítá requesty per reálná IP v klouzavém okně.

Co umí:
  - **Top-IP přehled** pro admina (kdo tě teď nejvíc „tlačí").
  - volitelný **OPATRNÝ auto-dočasný ban** IP, která výrazně přestřelí běžný provoz.

Bezpečnostní pojistky (proti vyhození legitimních diváků):
  - počítají se JEN reálné klientské IP (z hlavičky `Fly-Client-IP`); interní Fly proxy
    ani loopback se sem nikdy nedostanou → proxy se NIKDY nezabanuje,
  - práh je vysoko NAD běžným provozem (legit divák ~pár desítek req/min),
  - auto-ban je jen DOČASNÝ a KRÁTKÝ (sám expiruje) a dá se vypnout,
  - žádné DB zápisy na hot-path – i při náporu zůstává levné (vše v paměti, 1 worker).

Po restartu se stav (i auto-bany) vynuluje – což je při náporu jen dobře.
"""
import threading
import time
from collections import defaultdict, deque

# ---- Konfigurace (konzervativní defaulty; klidně si uprav) ----
WINDOW_SEC = 300          # okno pro Top-IP přehled = posledních 5 min
RATE_WINDOW_SEC = 60      # okno pro výpočet rychlosti (req/min)
AUTOBAN_PER_MIN = 500     # po optimalizaci běžný klient zůstává hluboko pod 500/min; farmy mohou sdílet IP, ale nesmí shodit web
AUTOBAN_MINUTES = 60      # hodinový ban pro skript, který ignoruje soft 429 limity

_lock = threading.Lock()
_hits = defaultdict(deque)     # ip -> deque[monotonic timestamp] za posledních WINDOW_SEC
_recent_autobans = deque(maxlen=100)  # poslední auto-bany (pro admin panel)
_autoban_enabled = True
_last_sweep = 0.0


def set_autoban(enabled: bool) -> None:
    global _autoban_enabled
    _autoban_enabled = bool(enabled)


def autoban_enabled() -> bool:
    return _autoban_enabled


def _prune(dq, cutoff) -> None:
    while dq and dq[0] < cutoff:
        dq.popleft()


def observe(ip: str) -> int:
    """Zaznamená 1 request z reálné IP. Vrátí aktuální rychlost té IP (req za poslední minutu).

    Vždy jen POČÍTÁ (i když je auto-ban vypnutý) – rozhodnutí o banu dělá volající
    podle `autoban_enabled()` + `AUTOBAN_PER_MIN`. Volá se POUZE s reálnou klientskou
    IP (Fly-Client-IP) – ne s proxy/loopback.
    """
    if not ip:
        return 0
    now = time.monotonic()
    win_cut = now - WINDOW_SEC
    with _lock:
        dq = _hits[ip]
        dq.append(now)
        _prune(dq, win_cut)

        # občasný úklid neaktivních IP (ať paměť neroste)
        global _last_sweep
        if now - _last_sweep > 30:
            _last_sweep = now
            for k in [k for k, v in list(_hits.items()) if not v or v[-1] < win_cut]:
                _hits.pop(k, None)

        return sum(1 for t in dq if t >= now - RATE_WINDOW_SEC)


def note_autoban(ip: str, per_min: int, created_iso: str) -> None:
    """Zaznamená provedený auto-ban do paměti (pro admin přehled)."""
    with _lock:
        _recent_autobans.appendleft({"ip": ip, "per_min": per_min, "at": created_iso})


def top(n: int = 15) -> list:
    """Top IP podle počtu requestů za posledních WINDOW_SEC (+ rychlost req/min)."""
    now = time.monotonic()
    win_cut = now - WINDOW_SEC
    rate_cut = now - RATE_WINDOW_SEC
    out = []
    with _lock:
        for ip, dq in _hits.items():
            cnt = sum(1 for t in dq if t >= win_cut)
            if cnt <= 0:
                continue
            per_min = sum(1 for t in dq if t >= rate_cut)
            out.append({"ip": ip, "count": cnt, "per_min": per_min})
    out.sort(key=lambda x: x["count"], reverse=True)
    return out[:n]


def recent_autobans() -> list:
    with _lock:
        return list(_recent_autobans)


def stats() -> dict:
    """Souhrn pro panel: kolik IP sledujeme, celkem requestů v okně."""
    now = time.monotonic()
    win_cut = now - WINDOW_SEC
    with _lock:
        ips = 0
        total = 0
        for dq in _hits.values():
            c = sum(1 for t in dq if t >= win_cut)
            if c > 0:
                ips += 1
                total += c
    return {"tracked_ips": ips, "total_requests": total,
            "window_min": WINDOW_SEC // 60, "threshold_per_min": AUTOBAN_PER_MIN,
            "ban_minutes": AUTOBAN_MINUTES, "autoban_enabled": _autoban_enabled}
