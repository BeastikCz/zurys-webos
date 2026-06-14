"""Automatická údržba: úklid objednávek + nudge na zapomenuté pending věci.

Daemon vlákno, každých CHECK_INTERVAL_SEC:
  1. Smaže VYŘÍZENÉ objednávky starší než RETENTION_DAYS – hodnota už byla divákovi
     dodána, drží se jen nedávná historie (proti bobtnání tabulky `orders`).
  2. Tichý alert (max 1×/den), když ČEKAJÍCÍCH objednávek je 300+ (zapomenuté vyřízení).
  3. Tichý alert (max 1×/den), když nějaká ŽÁDOST O DAR čeká na schválení > 24 h.

Maže jen `orders` (body v points_log zůstávají). Jeden krátký zápis za pár hodin =
žádná zátěž na single-writer SQLite.
"""
import threading
import time
import traceback
from datetime import datetime, timezone, timedelta

from .config import ORDER_FULFILLED, ORDER_PENDING
from .db import get_conn
from . import alerts

RETENTION_DAYS = 30             # vyřízené objednávky starší než tohle se mažou
CHECK_INTERVAL_SEC = 6 * 3600   # kontrola každých 6 h
PENDING_ALERT_AT = 300          # tolik+ čekajících objednávek → tichý alert (zapomenuté vyřízení)
GIFT_AGE_ALERT_H = 24           # žádost o dar čekající déle než tohle → tichý alert


def _run_once() -> int:
    """Jeden cyklus: smaž staré vyřízené + nudge na zapomenuté pending věci.
    Vrátí počet smazaných objednávek (kvůli testům)."""
    conn = get_conn()
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)).isoformat()
        n = conn.execute("DELETE FROM orders WHERE status = ? AND created_at < ?",
                         (ORDER_FULFILLED, cutoff)).rowcount
        conn.commit()
        if n:
            print(f"[order-cleanup] smazano {n} vyrizenych objednavek starsich {RETENTION_DAYS} dni")
        pending = conn.execute("SELECT COUNT(*) AS c FROM orders WHERE status = ?",
                              (ORDER_PENDING,)).fetchone()["c"]
        gift_cutoff = (datetime.now(timezone.utc) - timedelta(hours=GIFT_AGE_ALERT_H)).isoformat()
        old_gifts = conn.execute(
            "SELECT COUNT(*) AS c FROM gift_requests WHERE status = 'pending' AND created_at < ?",
            (gift_cutoff,)).fetchone()["c"]
    finally:
        conn.close()
    if pending >= PENDING_ALERT_AT:
        alerts.send("Hodne cekajicich objednavek",
                    detail=f"{pending} objednavek ceka na vyrizeni – mrkni do adminu -> Objednavky.",
                    key="orders-pending-high", cooldown=24 * 3600, ping=False)
    if old_gifts:
        alerts.send("Cekajici zadosti o dar",
                    detail=f"{old_gifts} zadosti o dar ceka na schvaleni dele nez {GIFT_AGE_ALERT_H} h "
                           f"(Admin -> Bezpecnost -> Audit & dary).",
                    key="gift-requests-aging", cooldown=24 * 3600, ping=False)
    return n


def _loop() -> None:
    time.sleep(120)  # po startu nech naběhnout DB/migrace
    while True:
        try:
            _run_once()
        except Exception:
            traceback.print_exc()   # daemon nesmí spadnout kvůli IO/DB chybě
        time.sleep(CHECK_INTERVAL_SEC)


_thread: threading.Thread | None = None


def start_order_cleanup_daemon() -> None:
    """Spustí daemon thread – idempotentně. Volá se z main.py při startu."""
    global _thread
    if _thread and _thread.is_alive():
        return
    _thread = threading.Thread(target=_loop, name="webos-order-cleanup", daemon=True)
    _thread.start()
