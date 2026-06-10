"""Partner Flash Bonus scheduler — v náhodném intervalu (jen když je stream LIVE)
otevře 'flash kolo': partnerské odkazy v režimu 'flash' jdou na pár minut znovu
vyzvednout a bot to oznámí v chatu. Vzor: autodrop.py. Nestackuje (jedno okno v čase).

Nastavení v app_settings (klíče pflash_*). Default „chill": ~45–90 min, okno 20 min.
"""
import random
import threading
import time
import traceback
from datetime import datetime, timezone, timedelta

from .db import get_conn, now_iso, get_setting, set_setting
from . import kickbot, live

CHECK_INTERVAL_SEC = 60
SITE = "zurys.live"

DEFAULTS = {
    "pflash_enabled": 0,
    "pflash_interval_min": 45,     # interval OD (min)
    "pflash_interval_max": 90,     # interval DO (min)
    "pflash_window_min": 20,       # jak dlouho je okno otevřené (min)
    "pflash_only_live": 1,
}
_BOUNDS = {
    "pflash_enabled": (0, 1),
    "pflash_interval_min": (1, 1440),
    "pflash_interval_max": (1, 1440),
    "pflash_window_min": (1, 240),
    "pflash_only_live": (0, 1),
}


def get_config(conn) -> dict:
    out = {}
    for k, dv in DEFAULTS.items():
        v = get_setting(conn, k)
        try:
            out[k] = int(v) if v is not None and str(v).strip() != "" else dv
        except (TypeError, ValueError):
            out[k] = dv
    out["pflash_interval_max"] = max(out["pflash_interval_max"], out["pflash_interval_min"])
    out["last_at"] = get_setting(conn, "pflash_last_at") or ""
    try:
        out["next_interval"] = int(get_setting(conn, "pflash_next_interval") or 0)
    except (TypeError, ValueError):
        out["next_interval"] = 0
    return out


def set_config(conn, values: dict) -> dict:
    for k, v in (values or {}).items():
        if k in DEFAULTS and v is not None:
            lo, hi = _BOUNDS[k]
            set_setting(conn, k, str(max(lo, min(hi, int(v)))))
    conn.commit()
    return status(conn)


def _roll(lo: int, hi: int) -> int:
    return lo if hi <= lo else random.randint(lo, hi)


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


def _active_round(conn):
    return conn.execute(
        "SELECT id, expires_at FROM partner_rounds WHERE expires_at > ? ORDER BY id DESC LIMIT 1",
        (now_iso(),)).fetchone()


def _flash_links(conn):
    return conn.execute(
        "SELECT label, reward FROM partner_links WHERE enabled=1 AND COALESCE(mode,'once')='flash' "
        "ORDER BY sort_order, id").fetchall()


def _announce(conn, window_min: int) -> None:
    try:
        links = _flash_links(conn)
        if not links:
            return
        top = max(int(r["reward"] or 0) for r in links)
        msg = (f"⚡ FLASH BONUS! Partneři na webu se právě obnovili — skoč na "
               f"{SITE} → 🎁 Bonusy, klikni a hrabni si až +{top} sedláků! Jen {window_min} min! 🌾")
        kickbot.send_message(conn, msg, kind="system")
    except Exception:
        traceback.print_exc()


def open_round(conn, *, force: bool = False) -> dict:
    """Otevře flash kolo. force=True přeskočí kontrolu zapnuto/live (ruční spuštění z adminu)."""
    cfg = get_config(conn)
    if _active_round(conn):
        return {"ok": False, "error": "Flash kolo už běží."}
    if not _flash_links(conn):
        return {"ok": False, "error": "Žádný odkaz v režimu Flash (přepni u odkazu na ⚡ Flash)."}
    if not force:
        if not cfg["pflash_enabled"]:
            return {"ok": False, "error": "Flash je vypnutý."}
        if cfg["pflash_only_live"] and not live.is_live(conn):
            return {"ok": False, "error": "Stream není live."}
    win = cfg["pflash_window_min"]
    expires = (datetime.now(timezone.utc) + timedelta(minutes=win)).isoformat()
    conn.execute("INSERT INTO partner_rounds (opened_at, expires_at) VALUES (?,?)", (now_iso(), expires))
    set_setting(conn, "pflash_last_at", now_iso())
    set_setting(conn, "pflash_next_interval",
                str(_roll(cfg["pflash_interval_min"], cfg["pflash_interval_max"])))
    conn.commit()
    _announce(conn, win)
    return {"ok": True, "window_min": win, "expires_at": expires}


def _maybe_open(conn) -> None:
    cfg = get_config(conn)
    if not cfg["pflash_enabled"] or _active_round(conn) or not _flash_links(conn):
        return
    target = cfg["next_interval"]
    if not (cfg["pflash_interval_min"] <= target <= cfg["pflash_interval_max"]):
        target = _roll(cfg["pflash_interval_min"], cfg["pflash_interval_max"])
        set_setting(conn, "pflash_next_interval", str(target))
        conn.commit()
    if _minutes_since(cfg["last_at"]) < target:
        return
    if cfg["pflash_only_live"] and not live.is_live(conn):
        return
    open_round(conn, force=True)        # podmínky splněné → otevři


def status(conn) -> dict:
    """Stav pro admin: config + jestli zrovna běží kolo + počet flash odkazů."""
    cfg = get_config(conn)
    rnd = _active_round(conn)
    cfg["active"] = bool(rnd)
    cfg["active_until"] = rnd["expires_at"] if rnd else None
    cfg["flash_links"] = len(_flash_links(conn))
    return cfg


def _loop() -> None:
    while True:
        try:
            conn = get_conn()
            try:
                _maybe_open(conn)
            finally:
                conn.close()
        except Exception:
            traceback.print_exc()
        time.sleep(CHECK_INTERVAL_SEC)


_thread = None


def start_partners_flash_daemon() -> None:
    """Spustí daemon thread – idempotentně. Volá se z main.py při startu."""
    global _thread
    if _thread and _thread.is_alive():
        return
    _thread = threading.Thread(target=_loop, name="webos-partners-flash", daemon=True)
    _thread.start()
