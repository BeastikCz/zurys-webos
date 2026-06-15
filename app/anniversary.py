"""Výročí v komunitě: divák dostane bonus + notifikaci, když jeho účet překročí milník
(1 měsíc / 3 měsíce / půl roku / 1 rok / 2 roky). Daemon vlákno (vzor order_cleanup.py).

Vyplácí jen NEDÁVNO překročené milníky (okno _GRACE_DAYS) – aby případný import starých
účtů nespustil hromadnou zpětnou výplatu. Každý milník per uživatel max 1× (tabulka
anniversary_awards). „Member since" odznak řeší frontend přímo z created_at.
"""
import threading
import time
import traceback
from datetime import datetime, timezone, timedelta

from .db import get_conn, now_iso
from .deps import add_points, notify

CHECK_INTERVAL_SEC = 6 * 3600   # 4× denně
_GRACE_DAYS = 7                 # vyplať jen milníky překročené v posledních X dnech

# (dní v komunitě, bonus sedláků, label)
MILESTONES = [
    (30,   500,   "1 měsíc"),
    (90,   1500,  "3 měsíce"),
    (180,  3500,  "půl roku"),
    (365,  10000, "1 rok"),
    (730,  25000, "2 roky"),
]


def _run_once() -> int:
    """Vyplať nově dosažené milníky. Vrátí počet výplat (kvůli testům)."""
    awarded = 0
    conn = get_conn()
    try:
        now = datetime.now(timezone.utc)
        for days, bonus, label in MILESTONES:
            hi = (now - timedelta(days=days)).isoformat()                 # už milník překročil
            lo = (now - timedelta(days=days + _GRACE_DAYS)).isoformat()   # ale nedávno (ne backfill)
            rows = conn.execute(
                "SELECT id FROM users WHERE banned = 0 AND created_at > ? AND created_at <= ? "
                "AND id NOT IN (SELECT user_id FROM anniversary_awards WHERE milestone_days = ?)",
                (lo, hi, days)).fetchall()
            for u in rows:
                add_points(conn, u["id"], bonus, f"Výročí: {label} v komunitě 🎂")
                conn.execute(
                    "INSERT INTO anniversary_awards (user_id, milestone_days, awarded_at) VALUES (?,?,?)",
                    (u["id"], days, now_iso()))
                notify(conn, u["id"], "🎂", f"Výročí — {label}!",
                       f"Jsi už {label} součástí komunity! Bereš +{bonus} sedláků jako díky. 🌾", "#/profile")
                awarded += 1
            conn.commit()
        if awarded:
            print(f"[anniversary] vyplaceno {awarded} vyroci")
    finally:
        conn.close()
    return awarded


def _loop() -> None:
    time.sleep(150)   # po startu nech naběhnout
    while True:
        try:
            _run_once()
        except Exception:
            traceback.print_exc()
        time.sleep(CHECK_INTERVAL_SEC)


_thread: threading.Thread | None = None


def start_anniversary_daemon() -> None:
    """Spustí daemon thread – idempotentně. Volá se z main.py při startu."""
    global _thread
    if _thread and _thread.is_alive():
        return
    _thread = threading.Thread(target=_loop, name="webos-anniversary", daemon=True)
    _thread.start()
