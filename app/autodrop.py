"""Auto-drop scheduler: spouští dropy samy v intervalu (volitelně jen když je stream LIVE).

Vzor jako backup.py – daemon vlákno, stdlib + get_conn. Nastavení v app_settings
(klíče autodrop_*). NESTACKUJE (když už nějaký drop běží, počká, až se rozebere)
a dodrží interval mezi dropy. Po vytvoření drop oznámí bot v chatu (post_drop).
"""
import random
import threading
import time
import traceback
from datetime import datetime, timezone

from .db import get_conn, now_iso, get_setting, set_setting
from .security import new_code
from . import kickbot, live, kickevents

CHECK_INTERVAL_SEC = 60      # jak často daemon kontroluje podmínky

# Rozsahy „od–do": web pokaždé vylosuje náhodnou hodnotu v intervalu, ať diváci
# nemůžou drop načasovat (anti-timing). Když je *_max == základ, chová se to fixně.
DEFAULTS = {
    "autodrop_enabled": 0,
    "autodrop_interval_min": 30,      # interval OD (min)
    "autodrop_interval_max": 30,      # interval DO (min)
    "autodrop_points": 100,           # body OD
    "autodrop_points_max": 100,       # body DO
    "autodrop_winners": 3,            # výherců OD
    "autodrop_winners_max": 3,        # výherců DO
    "autodrop_only_live": 1,
}
_BOUNDS = {
    "autodrop_enabled": (0, 1),
    "autodrop_interval_min": (1, 1440),
    "autodrop_interval_max": (1, 1440),
    "autodrop_points": (1, 1_000_000),
    "autodrop_points_max": (1, 1_000_000),
    "autodrop_winners": (1, 1000),
    "autodrop_winners_max": (1, 1000),
    "autodrop_only_live": (0, 1),
}


def get_config(conn) -> dict:
    out = {}
    for k, dv in DEFAULTS.items():
        v = get_setting(conn, k)
        try:
            out[k] = int(v) if v is not None and str(v).strip() != "" else dv
        except (TypeError, ValueError):
            out[k] = dv
    # „do" nesmí být menší než „od" (jinak je rozsah fixní = „od")
    out["autodrop_interval_max"] = max(out["autodrop_interval_max"], out["autodrop_interval_min"])
    out["autodrop_points_max"] = max(out["autodrop_points_max"], out["autodrop_points"])
    out["autodrop_winners_max"] = max(out["autodrop_winners_max"], out["autodrop_winners"])
    out["last_at"] = get_setting(conn, "autodrop_last_at") or ""
    try:
        out["next_interval"] = int(get_setting(conn, "autodrop_next_interval") or 0)
    except (TypeError, ValueError):
        out["next_interval"] = 0
    return out


def set_config(conn, values: dict) -> dict:
    for k, v in (values or {}).items():
        if k in DEFAULTS and v is not None:
            lo, hi = _BOUNDS[k]
            set_setting(conn, k, str(max(lo, min(hi, int(v)))))
    conn.commit()
    return get_config(conn)


def _minutes_since(iso_ts: str) -> float:
    if not iso_ts:
        return 1e9
    try:
        t = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - t).total_seconds() / 60.0
    except Exception:
        return 1e9


def _roll(lo: int, hi: int) -> int:
    """Náhodné celé číslo v <lo, hi>. Když hi<=lo, vrátí lo (fixní hodnota)."""
    return lo if hi <= lo else random.randint(lo, hi)


def _maybe_drop(conn) -> None:
    cfg = get_config(conn)
    if not cfg["autodrop_enabled"]:
        return
    if conn.execute("SELECT 1 FROM drops WHERE active = 1 LIMIT 1").fetchone():
        return                                                       # nestackuj – počkej, až se drop rozebere
    # Cílový interval tohoto kola se losuje JEDNOU a uloží, ať se nemění při každé
    # 60s kontrole (jinak by drop padal skoro vždy hned na spodní hranici rozsahu).
    target = cfg["next_interval"]
    if not (cfg["autodrop_interval_min"] <= target <= cfg["autodrop_interval_max"]):
        target = _roll(cfg["autodrop_interval_min"], cfg["autodrop_interval_max"])
        set_setting(conn, "autodrop_next_interval", str(target))
        conn.commit()
    if _minutes_since(cfg["last_at"]) < target:
        return                                                       # ještě neuplynul (náhodný) interval
    if cfg["autodrop_only_live"] and not live.is_live(conn):
        return                                                       # jen když je stream live
    points = _roll(cfg["autodrop_points"], cfg["autodrop_points_max"])
    winners = _roll(cfg["autodrop_winners"], cfg["autodrop_winners_max"])
    code = "DROP-" + new_code()
    conn.execute(
        "INSERT INTO drops (code, points, max_winners, active, created_at) VALUES (?, ?, ?, 1, ?)",
        (code, points, winners, now_iso()),
    )
    set_setting(conn, "autodrop_last_at", now_iso())
    set_setting(conn, "autodrop_next_interval",                      # nový náhodný interval pro PŘÍŠTÍ drop
                str(_roll(cfg["autodrop_interval_min"], cfg["autodrop_interval_max"])))
    conn.commit()
    try:
        kickbot.post_drop(conn, code, points, winners)
    except Exception:
        traceback.print_exc()


def _loop() -> None:
    while True:
        try:
            conn = get_conn()
            try:
                _maybe_drop(conn)
                kickevents.expire_subs(conn)       # sundá vypršelé suby (Kick nemá „expired" event)
            finally:
                conn.close()
        except Exception:
            traceback.print_exc()
        time.sleep(CHECK_INTERVAL_SEC)


_thread = None


def start_autodrop_daemon() -> None:
    """Spustí daemon thread – idempotentně. Volá se z main.py při startu."""
    global _thread
    if _thread and _thread.is_alive():
        return
    _thread = threading.Thread(target=_loop, name="webos-autodrop", daemon=True)
    _thread.start()
