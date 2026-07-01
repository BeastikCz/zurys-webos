"""Měsíční „Wrapped" push: 1. den v měsíci pošle všem push subscriberům
„🌾 Tvoje čísla za <měsíc>" → deep link #/moje-cisla (profil + sdílitelná karta).
Karta je screenshot-friendly → lidi ji sdílí = reklama zdarma.

Daemon vzor jako garden_notify. Flag `wrapped_push_RRRR-MM` v app_settings
zaručuje odeslání 1× za měsíc i přes restarty; flag se commitne PŘED odesíláním
(crash uprostřed = radši pár lidí bez pushky než dvojitý spam všem).
Bez VAPID klíčů (webpush.enabled()) je celý daemon no-op.
"""
import threading
import time
import traceback

from .db import get_conn, get_setting, set_setting, local_now
from . import webpush

CHECK_INTERVAL_SEC = 3600
MONTHS = ["leden", "únor", "březen", "duben", "květen", "červen",
          "červenec", "srpen", "září", "říjen", "listopad", "prosinec"]


def _tick(conn) -> None:
    n = local_now()
    if n.day != 1:
        return
    flag = f"wrapped_push_{n.year:04d}-{n.month:02d}"
    if get_setting(conn, flag, "") == "1":
        return
    prev_month = MONTHS[(n.month - 2) % 12]
    set_setting(conn, flag, "1")
    conn.commit()                     # flag PŘED sítí (viz docstring) + uvolní write-lock
    dead: list = []
    for s in conn.execute("SELECT id, endpoint, p256dh, auth FROM push_subs").fetchall():
        info = {"endpoint": s["endpoint"], "keys": {"p256dh": s["p256dh"], "auth": s["auth"]}}
        try:
            webpush.send(info, f"🌾 Tvoje čísla za {prev_month}!",
                         "Level, sklizně, největší výhra… mrkni a flexni.",
                         "#/moje-cisla", "/sedlak-cut.png")
        except webpush.DeadSubscription:
            dead.append(s["id"])
        except Exception:
            pass
    if dead:
        for d in dead:
            conn.execute("DELETE FROM push_subs WHERE id = ?", (d,))
        conn.commit()


def _loop() -> None:
    while True:
        try:
            if webpush.enabled():
                conn = get_conn()
                try:
                    _tick(conn)
                finally:
                    conn.close()
        except Exception:
            traceback.print_exc()
        time.sleep(CHECK_INTERVAL_SEC)


_thread = None


def start_wrapped_push_daemon() -> None:
    """Spustí daemon thread – idempotentně. Volá se z main.py při startu."""
    global _thread
    if _thread and _thread.is_alive():
        return
    _thread = threading.Thread(target=_loop, name="webos-wrapped-push", daemon=True)
    _thread.start()
