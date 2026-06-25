"""Zahrádka – notifikace: in-app ping „úroda dozrála" + „chrobáci v zahrádce".

Daemon vlákno (vzor jako autodrop.py). Periodicky projede záhony a pošle notify()
majiteli, když plodina dozrála nebo když se objevili chrobáci (aktivní okno).
Bit `notified` (1=zralé, 2=chrobáci) brání spamu – každá událost 1× na záhon.
Po sklizni se řádek smaže → znovuzasazení má notified=0 (oznámí se zas).
"""
import threading
import time
import traceback
from datetime import datetime, timezone

from .db import get_conn, now_iso
from .deps import notify
from . import garden, webpush

CHECK_INTERVAL_SEC = 60      # jak často daemon projede zahrádky


def _push_user(conn, user_id: int, title: str, body: str, url: str) -> None:
    """Best-effort web push na všechna zařízení uživatele (vedle in-app notify). Mrtvé subs (404/410) maže."""
    if not webpush.enabled():
        return
    for s in conn.execute("SELECT id, endpoint, p256dh, auth FROM push_subs WHERE user_id = ?", (user_id,)).fetchall():
        info = {"endpoint": s["endpoint"], "keys": {"p256dh": s["p256dh"], "auth": s["auth"]}}
        try:
            webpush.send(info, title, body, url, "/sedlak-cut.png")
        except webpush.DeadSubscription:
            conn.execute("DELETE FROM push_subs WHERE id = ?", (s["id"],))
        except Exception:
            pass


def _scan(conn) -> None:
    now = datetime.now(timezone.utc)
    # 1) ÚRODA DOZRÁLA – záhon zralý a ještě neoznámen (bit 1)
    for r in conn.execute(
        "SELECT user_id, plot, crop FROM garden WHERE ready_at <= ? AND (notified & 1) = 0",
        (now_iso(),)).fetchall():
        c = garden._BY_KEY.get(r["crop"], {})
        notify(conn, r["user_id"], "🌾", "Úroda dozrála!",
               f"{c.get('icon', '')} {c.get('name', 'Plodina')} je připravená ke sklizni.", "#/zahrada")
        _push_user(conn, r["user_id"], "Úroda dozrála! 🌾",
                   f"{c.get('name', 'Plodina')} je připravená ke sklizni.", "#/zahrada")
        conn.execute("UPDATE garden SET notified = notified | 1 WHERE user_id = ? AND plot = ?",
                     (r["user_id"], r["plot"]))
    # 2) CHROBÁCI – aktivní okno a ještě neoznámeni (bit 2); incoming/eaten/none přeskoč
    for r in conn.execute(
        "SELECT user_id, plot, crop, pest, pest_at FROM garden "
        "WHERE pest_at IS NOT NULL AND pest = 0 AND (notified & 2) = 0").fetchall():
        state, _left = garden._pest_state(r, now)
        if state != "active":
            continue
        c = garden._BY_KEY.get(r["crop"], {})
        notify(conn, r["user_id"], "🐛", "Chrobáci v zahrádce!",
               f"Zachraň {c.get('name', 'plodinu')} než ti sežerou půlku úrody. 🚜", "#/zahrada")
        _push_user(conn, r["user_id"], "Chrobáci v zahrádce! 🐛",
                   f"Zachraň {c.get('name', 'plodinu')} než ti sežerou půlku úrody.", "#/zahrada")
        conn.execute("UPDATE garden SET notified = notified | 2 WHERE user_id = ? AND plot = ?",
                     (r["user_id"], r["plot"]))
    conn.commit()


def _loop() -> None:
    while True:
        try:
            conn = get_conn()
            try:
                _scan(conn)
            finally:
                conn.close()
        except Exception:
            traceback.print_exc()
        time.sleep(CHECK_INTERVAL_SEC)


_thread = None


def start_garden_notify_daemon() -> None:
    """Spustí daemon thread – idempotentně. Volá se z main.py při startu."""
    global _thread
    if _thread and _thread.is_alive():
        return
    _thread = threading.Thread(target=_loop, name="webos-garden-notify", daemon=True)
    _thread.start()
