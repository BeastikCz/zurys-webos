"""Discord alerty na chyby/události (best-effort, na pozadí).

Aktivní jen když je nastavený secret DISCORD_ALERT_WEBHOOK – jinak je vše no-op,
takže to nikdy nerozbije běh appky. Dedup (cooldown per klíč) brání zaplavení
stejnou chybou. Odesílá se v daemon threadu, takže to neblokuje request.
"""
import json
import os
import threading
import time
import urllib.request

_WEBHOOK = os.environ.get("DISCORD_ALERT_WEBHOOK", "").strip()
_last: dict = {}
_lock = threading.Lock()


def enabled() -> bool:
    return bool(_WEBHOOK)


def _post(content: str) -> None:
    try:
        data = json.dumps({"content": content[:1900]}).encode("utf-8")
        req = urllib.request.Request(
            _WEBHOOK, data=data, headers={
                "Content-Type": "application/json",
                # Bez vlastního User-Agent posílá urllib "Python-urllib/..", což Cloudflare
                # před Discordem blokuje (403 / error 1010) → alerty by tiše nedorazily.
                "User-Agent": "Mozilla/5.0 (compatible; ZURYS-Shop/1.0; +https://zurys.live)",
            })
        urllib.request.urlopen(req, timeout=8).read()
    except Exception as e:  # alert nikdy nesmí shodit appku – ale chybu aspoň zaloguj
        print("[alerts] webhook POST selhal:", type(e).__name__, str(e)[:150])


def _post_file(file_path: str, caption: str) -> None:
    """Pošle soubor jako přílohu na Discord webhook (multipart/form-data). Best-effort."""
    try:
        with open(file_path, "rb") as f:
            content = f.read()
        fname = os.path.basename(file_path)
        boundary = "----WebOSBackupBoundary7MA4YWxkTrZu0gW"
        pre = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="content"\r\n\r\n'
            f"{(caption or '')[:1900]}\r\n"
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{fname}"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n"
        ).encode("utf-8")
        data = pre + content + f"\r\n--{boundary}--\r\n".encode("utf-8")
        req = urllib.request.Request(_WEBHOOK, data=data, headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "User-Agent": "Mozilla/5.0 (compatible; ZURYS-Shop/1.0; +https://zurys.live)",
        })
        urllib.request.urlopen(req, timeout=30).read()
    except Exception as e:
        print("[alerts] file upload selhal:", type(e).__name__, str(e)[:150])


def send_file(file_path, caption: str = "") -> None:
    """Pošle soubor (např. zálohu DB) na Discord webhook na pozadí. No-op bez webhooku."""
    if not _WEBHOOK:
        return
    threading.Thread(target=_post_file, args=(str(file_path), caption), daemon=True).start()


def send(title: str, detail: str = "", key: str = None, cooldown: int = 180,
         ping: bool = False) -> None:
    """Pošle zprávu na Discord. `key`+`cooldown` (s) brání spamu stejné chyby.

    ping=True přidá `@everyone`, takže to v kanálu reálně pípne (notifikace).
    Použít jen na fakt důležité věci (500 chyby, útok) – ne na běžné info."""
    if not _WEBHOOK:
        return
    k = key or title
    now = time.monotonic()
    with _lock:
        if k in _last and now - _last[k] < cooldown:
            return
        _last[k] = now
    body = ("@everyone " if ping else "") + "**" + (title or "Alert")[:240] + "**"
    if detail:
        body += "\n```" + detail[:1500] + "```"
    threading.Thread(target=_post, args=(body,), daemon=True).start()
