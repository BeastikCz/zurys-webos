"""StreamElements donaty → overlay alerty (poll v daemon threadu, žádný webhook).

Aktivní jen se secrets SE_JWT + SE_CHANNEL_ID (Fly secrets) – jinak úplný no-op,
vzor alerts.py. Poll 1× za POLL_SEC, overlay si nové řádky bere z /api/recent-events
(klíče don_latest_id + donates, vlastní kurzor mimo points_log id-space).

Proti spamu historických donatů drží poller kurzor `se_tips_last_ts` v app_settings:
první běh (bez kurzoru) si jen zapamatuje nejnovější createdAt a NIC nevkládá,
další běhy vkládají jen novější tipy. donations.se_id UNIQUE kryje zbytek.
Síť běží mimo request handlery i mimo DB write-lock (viz no-blocking-io pravidlo).
"""
import json
import os
import threading
import time
import traceback
import urllib.request

from .db import get_conn, get_setting, set_setting, now_iso

POLL_SEC = 20
_JWT = os.environ.get("SE_JWT", "").strip()
_CHANNEL = os.environ.get("SE_CHANNEL_ID", "").strip()


def enabled() -> bool:
    return bool(_JWT and _CHANNEL)


def _fetch_tips() -> list:
    """Posledních pár tipů z SE API (nejnovější první). Výjimky řeší caller."""
    url = f"https://api.streamelements.com/kappa/v2/tips/{_CHANNEL}?limit=20&sort=-createdAt"
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {_JWT}",
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (compatible; ZURYS-Shop/1.0; +https://zurys.live)",
    })
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data.get("docs") or []


def parse_tip(doc) -> dict | None:
    """SE tip doc → {se_id, ts, name, amount, currency, message}; None = nepoužitelný.

    Defenzivně: pole bere z doc['donation'] i z top-levelu (SE shape kolísá)."""
    if not isinstance(doc, dict):
        return None
    d = doc.get("donation") or {}
    try:
        amount = float(d.get("amount", doc.get("amount")))
    except (TypeError, ValueError):
        return None
    if amount <= 0:
        return None
    user = d.get("user") or {}
    return {"se_id": str(doc.get("_id") or doc.get("id") or ""),
            "ts": str(doc.get("createdAt") or ""),
            "name": str(user.get("username") or doc.get("username") or "Anonym")[:80],
            "amount": amount,
            "currency": str(d.get("currency") or doc.get("currency") or "CZK")[:8],
            "message": str(d.get("message") or "")[:300]}


def store_tips(conn, docs: list) -> int:
    """Uloží NOVÉ tipy (createdAt > kurzor) do donations, posune kurzor. Vrací počet nových.

    První běh (prázdný kurzor) jen nastaví baseline – historie se nehlásí ani neukládá."""
    last = get_setting(conn, "se_tips_last_ts", "")
    newest = last
    new = 0
    for doc in docs:
        t = parse_tip(doc)
        if not t or not t["se_id"]:
            continue
        if t["ts"] > newest:
            newest = t["ts"]
        if not last or (t["ts"] and t["ts"] <= last):
            continue   # baseline běh / starý tip
        new += conn.execute(
            "INSERT OR IGNORE INTO donations (se_id, name, amount, currency, message, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (t["se_id"], t["name"], t["amount"], t["currency"], t["message"], now_iso())).rowcount
    if newest != last:
        set_setting(conn, "se_tips_last_ts", newest)
    conn.commit()
    return new


def _loop() -> None:
    while True:
        try:
            docs = _fetch_tips()          # síť PŘED otevřením DB (mimo write-lock)
            if docs:
                conn = get_conn()
                try:
                    store_tips(conn, docs)
                finally:
                    conn.close()
        except Exception:
            traceback.print_exc()
        time.sleep(POLL_SEC)


_thread = None


def start_se_tips_daemon() -> None:
    """Spustí poll daemon – idempotentně; úplný no-op bez SE_JWT/SE_CHANNEL_ID."""
    global _thread
    if not enabled():
        return
    if _thread and _thread.is_alive():
        return
    _thread = threading.Thread(target=_loop, name="webos-se-tips", daemon=True)
    _thread.start()
