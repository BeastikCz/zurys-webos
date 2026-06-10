"""IP ban: úplné zablokování přístupu pro zabanované IP adresy.

Middleware chytí KAŽDÝ request – zabanovaná IP nedostane appku vůbec, jen blokační
stránku (HTTP 403). Bany jsou časově omezené (expirují) nebo trvalé.

Bezpečnostní pojistky: loopback (127.0.0.1 / ::1) se NIKDY neblokuje (admin lokálně),
a vlastní IP nelze zabanovat (řeší endpoint). Seznam je v paměti (rychlé, 1 worker).
"""
import html
import ipaddress
from datetime import datetime, timezone, timedelta

from fastapi.responses import HTMLResponse

from .db import now_iso

# ip -> {"reason", "created_at", "expires_at"}  (in-memory cache, žádný DB dotaz na request)
_BANS = {}


def is_loopback(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip).is_loopback
    except (ValueError, TypeError):
        return False


def valid_ip(ip: str) -> bool:
    try:
        ipaddress.ip_address(ip)
        return True
    except (ValueError, TypeError):
        return False


def load(conn) -> None:
    """Načte aktivní bany z DB do paměti (volá se při startu)."""
    _BANS.clear()
    for r in conn.execute("SELECT ip, reason, created_at, expires_at FROM ip_bans"):
        _BANS[r["ip"]] = {"reason": r["reason"], "created_at": r["created_at"],
                          "expires_at": r["expires_at"]}


def _expired(rec) -> bool:
    exp = rec.get("expires_at")
    if not exp:
        return False
    try:
        return datetime.fromisoformat(exp) < datetime.now(timezone.utc)
    except (ValueError, TypeError):
        return False


def check(ip: str):
    """Vrátí záznam banu, pokud je IP aktuálně blokovaná, jinak None."""
    if not ip or is_loopback(ip):
        return None
    rec = _BANS.get(ip)
    if not rec or _expired(rec):
        return None
    return rec


def ban(conn, ip: str, reason: str, hours: int) -> None:
    """Zabanuje IP na `hours` hodin (0 = trvale). Zapíše do DB i cache."""
    expires = None if not hours else (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()
    created = now_iso()
    conn.execute(
        "INSERT INTO ip_bans (ip, reason, created_at, expires_at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(ip) DO UPDATE SET reason=excluded.reason, created_at=excluded.created_at, "
        "expires_at=excluded.expires_at",
        (ip, (reason or "")[:200], created, expires))
    _BANS[ip] = {"reason": (reason or "")[:200], "created_at": created, "expires_at": expires}


def temp_ban(ip: str, reason: str, minutes: int) -> bool:
    """Dočasný auto-ban POUZE v paměti (NE do DB) – pro anti-DDoS. Po restartu zmizí.

    Vrátí True, pokud se ban nastavil. Loopback se nikdy nebanuje. Pokud už platí
    nějaký (i ruční) ban, nepřepisuje ho.
    """
    if not ip or is_loopback(ip):
        return False
    rec = _BANS.get(ip)
    if rec and not _expired(rec):
        return False  # už zabanováno (ruční nebo dřívější auto) – nech být
    expires = (datetime.now(timezone.utc) + timedelta(minutes=max(1, minutes))).isoformat()
    _BANS[ip] = {"reason": (reason or "")[:200], "created_at": now_iso(),
                 "expires_at": expires, "auto": True}
    return True


def unban(conn, ip: str) -> None:
    conn.execute("DELETE FROM ip_bans WHERE ip = ?", (ip,))
    _BANS.pop(ip, None)


def active_list(conn) -> list:
    """Aktivní (neexpirované) bany pro admin UI; expirované po cestě promaže."""
    out = []
    for r in conn.execute(
            "SELECT ip, reason, created_at, expires_at FROM ip_bans ORDER BY created_at DESC").fetchall():
        rec = dict(r)
        if _expired(rec):
            unban(conn, rec["ip"])
            continue
        out.append(rec)
    return out


def block_page(ip: str, rec: dict) -> HTMLResponse:
    """Full-page blokační stránka (HTTP 403). Časy se formátují v prohlížeči (lokální čas)."""
    ipe = html.escape(ip or "?")
    reason = html.escape(rec.get("reason") or "—")
    created = html.escape(rec.get("created_at") or "")
    expires = html.escape(rec.get("expires_at") or "")
    page = f"""<!DOCTYPE html>
<html lang="cs"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Přístup zablokován</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{ margin:0; min-height:100vh; display:flex; align-items:center; justify-content:center;
    background: radial-gradient(circle at 50% 30%, #15101a, #07080d 70%); color:#e8e9f0;
    font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif; text-align:center; padding:24px; }}
  .box {{ max-width: 560px; }}
  .icon {{ width:96px; height:96px; margin:0 auto 22px; border-radius:50%;
    background: radial-gradient(circle, rgba(255,70,70,.28), transparent 70%);
    display:grid; place-items:center; font-size:60px; filter: drop-shadow(0 0 18px rgba(255,60,60,.5)); }}
  h1 {{ margin:0 0 14px; font-size:34px; font-weight:900; color:#ff5c5c; letter-spacing:.01em; }}
  .sub {{ color:#aab; font-size:15.5px; line-height:1.6; margin:0 0 18px; }}
  .ip {{ font-family: ui-monospace, Consolas, monospace; background:rgba(255,157,46,.14);
    color:#ffae57; padding:2px 8px; border-radius:6px; font-weight:700; }}
  .reason {{ font-size:15px; margin:0 0 22px; }} .reason b {{ color:#fff; }}
  .times {{ color:#6b6f82; font-size:12.5px; line-height:1.7; }}
</style></head>
<body><div class="box">
  <div class="icon">🚫</div>
  <h1>IP zabanována</h1>
  <p class="sub">Tvá IP adresa <span class="ip">{ipe}</span> nemá přístup na tuto stránku.</p>
  <p class="reason"><b>Důvod:</b> {reason}</p>
  <div class="times">
    <div>Zabanováno: <span data-ts="{created}">—</span></div>
    <div>Vyprší: <span data-ts="{expires}">nikdy</span></div>
  </div>
</div>
<script>
  document.querySelectorAll('[data-ts]').forEach(function(e){{
    var v=e.getAttribute('data-ts'); if(!v) return;
    var d=new Date(v); if(!isNaN(d)) e.textContent=d.toLocaleString('cs-CZ');
  }});
</script>
</body></html>"""
    return HTMLResponse(content=page, status_code=403)
