"""Denní bezpečnostní/ekonomický digest na Discord.

Jednou denně (po DIGEST_HOUR_UTC) pošle souhrn za posledních 24 h, ať nic
nepropadne sítem: nové účty, pohyb bodů, největší transakce, anticheat bloky,
bany, admin akce, aktivní dropy, stav dnešní zálohy.

Daemon vlákno jako backup.py / autodrop.py – stdlib + get_conn. Aktivní jen když
je nastavený Discord webhook (jinak no-op). Stav (kdy naposled posláno) v
app_settings (klíč `digest_last_date`), takže se neposílá víckrát za den.
"""
import shutil
import sqlite3
import threading
import time
import traceback
from datetime import datetime, timezone, timedelta

from .db import get_conn, get_setting, set_setting
from . import alerts, backup, econ_health

DIGEST_HOUR_UTC = 7          # ~09:00 CEST – ranní souhrn
CHECK_INTERVAL_SEC = 3600    # kontrola každou hodinu
INFLATION_ALERT_DEFAULT_PCT = 25   # net za 7 dní > X % oběhu → ⚠️ + ping v digestu (přepiš v app_settings: digest_infl_alert_pct)


def _cutoff_24h() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()


def _scalar(conn, sql, params=(), default=0):
    """Bezpečně vrátí první sloupec prvního řádku (0/„default" při chybě)."""
    try:
        r = conn.execute(sql, params).fetchone()
        if not r or r[0] is None:
            return default
        return r[0]
    except Exception:
        return default


def _econ_week(conn):
    """7denní ekonomické shrnutí + varování o inflaci. Vrátí (řádky, má_pingnout).

    Když web běží kratší dobu než okno (7 dní), inflace vychází ~100 % a nevypovídá –
    proto se v tom případě jen oznámí, nealertuje. Práh je v app_settings (digest_infl_alert_pct).
    """
    try:
        h = econ_health.health(conn, 7)
    except Exception:
        traceback.print_exc()
        return [], False
    young = len(h.get("series") or []) < h.get("days", 7)
    lines = [
        "📈 Ekonomika 7 dní:",
        f"   ⬆️ vytvořeno +{h['faucet_total']} · ⬇️ spáleno -{h['sink_total']} · "
        f"net {'+' if h['net_total'] >= 0 else ''}{h['net_total']}",
    ]
    fau = [c for c in h["by_category"] if c["kind"] == "faucet"][:2]
    sink = [c for c in h["by_category"] if c["kind"] == "sink"][:1]
    if fau:
        lines.append("   🟠 zdroje: " + ", ".join(f"{c['label']} +{c['net']}" for c in fau))
    if sink:
        lines.append("   🟢 sink: " + ", ".join(f"{c['label']} {c['net']}" for c in sink))
    try:
        thr = int(get_setting(conn, "digest_infl_alert_pct") or INFLATION_ALERT_DEFAULT_PCT)
    except (TypeError, ValueError):
        thr = INFLATION_ALERT_DEFAULT_PCT
    ping = False
    if young:
        lines.append(f"   ℹ️ Inflace {h['inflation_pct']} % zatím nevypovídá (web běží < 7 dní).")
    elif h["inflation_pct"] >= thr:
        lines.append(f"   ⚠️ INFLACE +{h['inflation_pct']} % / 7 dní (práh {thr} %)! Zvaž zdražení v shopu nebo méně faucetů.")
        ping = True
    else:
        lines.append(f"   ✅ Inflace +{h['inflation_pct']} % / 7 dní (práh {thr} %).")
    return lines, ping


def compose(conn) -> str:
    """Sestaví text digestu za posledních 24 h. Čistá funkce – nic neposílá."""
    cut = _cutoff_24h()
    new_users = _scalar(conn, "SELECT COUNT(*) FROM users WHERE created_at >= ?", (cut,))
    minted = _scalar(conn, "SELECT COALESCE(SUM(change),0) FROM points_log WHERE change > 0 AND created_at >= ?", (cut,))
    burned = _scalar(conn, "SELECT COALESCE(SUM(-change),0) FROM points_log WHERE change < 0 AND created_at >= ?", (cut,))
    circ = _scalar(conn, "SELECT COALESCE(SUM(points),0) FROM users", ())
    ac_blocks = _scalar(conn, "SELECT COUNT(*) FROM admin_audit WHERE action='anticheat.block' AND created_at >= ?", (cut,))
    bans = _scalar(conn, "SELECT COUNT(*) FROM admin_audit WHERE action IN ('ip.ban','ddos.autoban') AND created_at >= ?", (cut,))
    admin_acts = _scalar(conn, "SELECT COUNT(*) FROM admin_audit WHERE admin_id IS NOT NULL AND created_at >= ?", (cut,))
    active_drops = _scalar(conn, "SELECT COUNT(*) FROM drops WHERE active = 1", ())

    # největší transakce (24 h)
    top_tx = []
    try:
        for row in conn.execute(
            "SELECT p.change AS change, p.reason AS reason, COALESCE(u.username,'?') AS uname "
            "FROM points_log p LEFT JOIN users u ON u.id = p.user_id "
            "WHERE p.created_at >= ? ORDER BY ABS(p.change) DESC LIMIT 3", (cut,)):
            sign = "+" if row["change"] >= 0 else ""
            top_tx.append(f"   {sign}{row['change']} → {row['uname']} ({(row['reason'] or '')[:38]})")
    except Exception:
        pass

    # stav dnešní zálohy
    try:
        backup_ok = backup._today_path().exists()
        backup_str = "✅ OK" if backup_ok else "❌ CHYBÍ!"
    except Exception:
        backup_str = "?"

    net = minted - burned
    lines = [
        "📊 Souhrn za posledních 24 h:",
        f"👤 Nové účty:        {new_users}",
        f"💰 Body v oběhu:     {circ}  (čistá změna {'+' if net >= 0 else ''}{net})",
        f"   ⬆️ vytvořeno +{minted} · ⬇️ spáleno -{burned}",
        f"🚨 Anticheat bloky:  {ac_blocks}",
        f"🚫 Bany (IP/auto):   {bans}",
        f"🛠️ Admin akcí:       {admin_acts}",
        f"🎁 Aktivní dropy:    {active_drops}",
        f"💾 Záloha dnes:      {backup_str}",
    ]
    econ_lines, _ = _econ_week(conn)
    lines.extend(econ_lines)
    # 🕵️ Sdílená zařízení = možné alty (stejný otisk fp_hash u ≥3 účtů) – k ruční kontrole.
    alt_groups = []
    try:
        for row in conn.execute(
            "SELECT COUNT(DISTINCT cs.user_id) AS accs, GROUP_CONCAT(DISTINCT u.username) AS names "
            "FROM client_signals cs JOIN users u ON u.id = cs.user_id "
            "WHERE cs.fp_hash IS NOT NULL AND cs.fp_hash != '' "
            "GROUP BY cs.fp_hash HAVING accs >= 3 ORDER BY accs DESC LIMIT 5"):
            alt_groups.append(f"   ⚠️ {row['accs']} účtů z 1 zařízení: {(row['names'] or '')[:110]}")
    except Exception:
        pass

    if top_tx:
        lines.append("🏆 Největší transakce:")
        lines.extend(top_tx)
    if alt_groups:
        lines.append("🕵️ Sdílená zařízení (možné alty):")
        lines.extend(alt_groups)
    return "\n".join(lines)


def _maybe_send(conn) -> None:
    if not alerts.enabled():
        return                                   # bez webhooku nemá kam – no-op
    now = datetime.now(timezone.utc)
    today = now.date().isoformat()
    if get_setting(conn, "digest_last_date") == today:
        return                                   # dnešní digest už odešel
    if now.hour < DIGEST_HOUR_UTC:
        return                                   # ještě před ranní hodinou
    _, infl_ping = _econ_week(conn)
    alerts.send("📊 Denní ZURYS digest", detail=compose(conn),
                key="daily-digest", cooldown=0, ping=infl_ping)
    send_offsite_backup()        # off-site DB záloha na Discord (mimo Fly disk)
    set_setting(conn, "digest_last_date", today)
    conn.commit()


def send_offsite_backup() -> bool:
    """Pošle dnešní snapshot DB jako přílohu na Discord (off-site pojistka proti ztrátě
    dat – lokální zálohy leží na témže Fly disku jako živá DB). Vrátí, zda se odeslalo."""
    if not alerts.enabled():
        return False
    try:
        p = backup._today_path()
        if not p.exists():
            backup._snapshot()
            p = backup._today_path()
        if p.exists():
            kb = p.stat().st_size // 1024
            today = datetime.now(timezone.utc).date().isoformat()
            # ponytail: scrub credentials before Discord upload; on-disk backup stays complete
            tmp = p.with_suffix(".discord.tmp")
            try:
                shutil.copy2(p, tmp)
                c = sqlite3.connect(str(tmp))
                c.isolation_level = None
                c.execute("DELETE FROM sessions")
                c.execute("DELETE FROM bot_tokens")
                c.execute("VACUUM")
                c.close()
                alerts.send_file(tmp, caption=f"💾 Off-site záloha DB · {today} · {kb} kB")
            finally:
                if tmp.exists():
                    tmp.unlink()
            return True
    except Exception:
        traceback.print_exc()
    return False


def _loop() -> None:
    while True:
        try:
            conn = get_conn()
            try:
                _maybe_send(conn)
            finally:
                conn.close()
        except Exception:
            traceback.print_exc()
        time.sleep(CHECK_INTERVAL_SEC)


_thread = None


def start_digest_daemon() -> None:
    """Spustí daemon thread – idempotentně. Volá se z main.py při startu."""
    global _thread
    if _thread and _thread.is_alive():
        return
    _thread = threading.Thread(target=_loop, name="webos-digest", daemon=True)
    _thread.start()
