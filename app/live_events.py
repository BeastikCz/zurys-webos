"""Live události: při STARTU streamu (přechod offline→live) zapne dočasný Happy Hour
násobič sedláků za sledování/chat a oznámí to v chatu. Když stream skončí, je klid.

Daemon vlákno (vzor autodrop.py), pollne `live.is_live`. Stav „byl live" se drží v
app_settings (`live_was_live`), aby se akce spustila JEN při přechodu, ne opakovaně.
Konfigurace v app_settings (vše přepnutelné, ať si to provozovatel řídí sám):
  livehappy_enabled  "1"/"0"   – zapnuto?
  livehappy_mult     "1.5"     – násobič během Happy Hour (nad sub/VIP)
  livehappy_minutes  "5"       – jak dlouho po startu streamu

`happy_mult(conn)` čte economy.award_earned a vrací aktuální násobič (1.0 = neaktivní).
"""
import threading
import time
import traceback
from datetime import datetime, timezone, timedelta

from .db import get_conn, get_setting, set_setting
from . import live, kickbot

CHECK_INTERVAL_SEC = 45


def _enabled(conn) -> bool:
    return (get_setting(conn, "livehappy_enabled", "1") or "1") == "1"


def _mult(conn) -> float:
    try:
        return max(1.0, float(get_setting(conn, "livehappy_mult", "1.5") or 1.5))
    except (TypeError, ValueError):
        return 1.5


def _minutes(conn) -> int:
    try:
        return max(1, int(get_setting(conn, "livehappy_minutes", "5") or 5))
    except (TypeError, ValueError):
        return 5


def happy_mult(conn) -> float:
    """Aktuální Happy Hour násobič (1.0 = neaktivní). Volá economy.award_earned.
    Aktivní = `happy_until` je v budoucnu – nastaví ho auto-start streamu NEBO ruční spuštění.
    (Záměrně NEgatuje na livehappy_enabled, ať jde HH spustit ručně i s vyplým auto-režimem.)"""
    try:
        until = get_setting(conn, "happy_until", "") or ""
        if not until:
            return 1.0
        t = datetime.fromisoformat(until)
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) < t:
            return _mult(conn)
    except Exception:
        pass
    return 1.0


def _announce_async(mins: int, mult: float) -> None:
    """Pošle oznámení do Kick chatu v BACKGROUND vlákně – synchronní Kick HTTP v request
    threadu by zablokoval jediný worker (single-writer SQLite) = riziko výpadku."""
    def _send():
        try:
            c = get_conn()
            try:
                kickbot.send_message(
                    c, f"🔥 HAPPY HOUR! Příštích {mins} min jsou sedláci ×{mult:g} za sledování i chat – "
                       f"sleduj a piš na zurys.live! 🌾⚡", kind="live")
            finally:
                c.close()
        except Exception:
            traceback.print_exc()
    threading.Thread(target=_send, name="webos-hh-announce", daemon=True).start()


def start_now(conn) -> dict:
    """RUČNÍ spuštění Happy Hour TEĎ (bez ohledu na stream): nastaví happy_until + oznámí v chatu."""
    mins, mult = _minutes(conn), _mult(conn)
    until = (datetime.now(timezone.utc) + timedelta(minutes=mins)).isoformat()
    set_setting(conn, "happy_until", until)
    set_setting(conn, "live_was_live", "1")   # ať to auto-daemon nezdvojí, když je zrovna live
    conn.commit()
    _announce_async(mins, mult)
    return {"active_until": until, "minutes": mins, "mult": mult}


def stop_now(conn) -> dict:
    """Okamžitě ukončí běžící Happy Hour (vymaže happy_until). Auto-režim (enabled) nemění."""
    set_setting(conn, "happy_until", "")
    conn.commit()
    return {"active_until": ""}


def get_config(conn) -> dict:
    """Nastavení Happy Hour pro admin UI."""
    return {
        "livehappy_enabled": 1 if _enabled(conn) else 0,
        "livehappy_mult": _mult(conn),
        "livehappy_minutes": _minutes(conn),
        "active_until": get_setting(conn, "happy_until", "") or "",
    }


def set_config(conn, values: dict) -> dict:
    """Uloží nastavení (posílají se jen měněná pole). Vrátí aktuální config."""
    v = values or {}
    if v.get("livehappy_enabled") is not None:
        set_setting(conn, "livehappy_enabled", "1" if int(v["livehappy_enabled"]) else "0")
    if v.get("livehappy_mult") is not None:
        m = max(1.0, min(10.0, float(v["livehappy_mult"])))
        set_setting(conn, "livehappy_mult", f"{m:g}")
    if v.get("livehappy_minutes") is not None:
        set_setting(conn, "livehappy_minutes", str(max(1, min(720, int(v["livehappy_minutes"])))))
    conn.commit()
    return get_config(conn)


def _reset_subgoal_on_stream_end(conn) -> None:
    """Konec streamu → vynuluj komunitní SUB cíl (ať další stream začíná s čistou lištou).
    Přepínatelné settingem subgoal_reset_on_stream_end (default ON). Nikdy nesmí shodit _check."""
    if (get_setting(conn, "subgoal_reset_on_stream_end", "1") or "1") != "1":
        return
    try:
        from . import subgoal
        subgoal.reset(conn)
    except Exception:
        traceback.print_exc()


def _check(conn) -> None:
    # Přechody live↔offline trackujeme VŽDY (nezávisle na livehappy toggle) – aby reset SUB cíle
    # na konci streamu jel i s vyplým auto-Happy-Hour. Gated je jen samotná HH akce.
    is_live = live.is_live(conn)
    was = (get_setting(conn, "live_was_live", "0") or "0") == "1"
    if is_live and not was:
        # přechod offline → LIVE
        set_setting(conn, "live_was_live", "1")
        announce = None
        if _enabled(conn):                       # Happy Hour na startu streamu (jen když auto-režim zapnutý)
            mins, mult = _minutes(conn), _mult(conn)
            set_setting(conn, "happy_until",
                        (datetime.now(timezone.utc) + timedelta(minutes=mins)).isoformat())
            announce = (f"🔴 Jsme LIVE! Příštích {mins} min jsou sedláci ×{mult:g} "
                        f"za sledování i chat – sleduj a piš na zurys.live 🌾⚡")
        conn.commit()
        if announce:
            try:
                kickbot.send_message(conn, announce, kind="live")
            except Exception:
                traceback.print_exc()
    elif not is_live and was:
        # přechod LIVE → offline (konec streamu)
        set_setting(conn, "live_was_live", "0")
        _reset_subgoal_on_stream_end(conn)
        conn.commit()


def _loop() -> None:
    while True:
        try:
            conn = get_conn()
            try:
                _check(conn)
            finally:
                conn.close()
        except Exception:
            traceback.print_exc()
        time.sleep(CHECK_INTERVAL_SEC)


_thread = None


def start_live_events_daemon() -> None:
    """Spustí daemon thread – idempotentně. Volá se z main.py při startu."""
    global _thread
    if _thread and _thread.is_alive():
        return
    _thread = threading.Thread(target=_loop, name="webos-live-events", daemon=True)
    _thread.start()
