"""Automatická záloha SQLite databáze s retencí.

Při startu se v daemon vlákně:
  1. Pokud dnes ještě nebyl snapshot, vytvoří `data/backups/webos-YYYY-MM-DD.db`.
  2. Smaže snapshoty starší než RETENTION_DAYS.
  3. Spí 1 hodinu a opakuje (server může běžet jen pár minut denně).

Bez závislostí – jen stdlib. SQLite snapshot přes `VACUUM INTO` = konzistentní.
"""
import sqlite3
import threading
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

from .config import DB_PATH, DATA_DIR

BACKUP_DIR = DATA_DIR / "backups"
RETENTION_DAYS = 7
CHECK_INTERVAL_SEC = 3600  # 1 h


def _today_path() -> Path:
    return BACKUP_DIR / f"webos-{datetime.now(timezone.utc).date().isoformat()}.db"


def _snapshot() -> Path:
    """Pořídí konzistentní snapshot DB. Vrátí cestu k souboru."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    out = _today_path()
    if out.exists():
        return out
    tmp = out.with_suffix(".db.tmp")
    if tmp.exists():
        tmp.unlink()
    src = sqlite3.connect(str(DB_PATH))
    src.isolation_level = None  # VACUUM nesmí běžet v transakci
    try:
        src.execute("VACUUM INTO ?", (str(tmp),))
    finally:
        src.close()
    tmp.rename(out)  # atomické přejmenování → žádný polo-zapsaný soubor
    return out


def _prune_old(retention_days: int = RETENTION_DAYS) -> int:
    """Smaže snapshoty starší než N dnů. Vrátí počet smazaných."""
    if not BACKUP_DIR.exists():
        return 0
    cutoff = time.time() - retention_days * 86400
    removed = 0
    for p in BACKUP_DIR.glob("webos-*.db"):
        try:
            if p.stat().st_mtime < cutoff:
                p.unlink()
                removed += 1
        except OSError:
            pass
    # úklid případných tmp souborů po neúspěšném snapshotu
    for p in BACKUP_DIR.glob("*.db.tmp"):
        try:
            p.unlink()
        except OSError:
            pass
    return removed


def _run_once() -> None:
    """Jeden cyklus: snapshot (pokud dnes ještě není) + retention."""
    try:
        _snapshot()
        _prune_old()
    except Exception:
        # daemon nesmí spadnout kvůli IO chybě – jen zaloguje a pokračuje
        traceback.print_exc()


def _loop() -> None:
    while True:
        _run_once()
        time.sleep(CHECK_INTERVAL_SEC)


_thread: threading.Thread | None = None


def start_backup_daemon() -> None:
    """Spustí daemon thread – idempotentně. Volá se z main.py při startu."""
    global _thread
    if _thread and _thread.is_alive():
        return
    _thread = threading.Thread(target=_loop, name="webos-backup", daemon=True)
    _thread.start()
