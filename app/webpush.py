"""Web Push – notifikace do mobilu / oznamovacího centra (i když je appka zavřená).

VAPID klíče: ENV (Fly secrets) WEBPUSH_VAPID_PRIVATE/_PUBLIC/_SUBJECT mají přednost;
fallback lokální webpush.json (gitignored, stejný princip jako kick.json). Bez klíčů =
vypnuto (no-op), aby šlo běžet i lokálně bez configu.

Posílá se BEST-EFFORT z background threadu (garden_notify daemon) – chyba nikdy nesmí
shodit appku. Mrtvou subscription (HTTP 404/410) signalizuje DeadSubscription, ať ji
volající smaže z DB (push_subs).
"""
import json
import os

from .config import BASE_DIR

_PRIVATE_PEM = None      # PEM string EC P-256 privátního klíče
_PUBLIC = None           # app-server-key (base64url uncompressed point) – pro frontend
_SUBJECT = "mailto:admin@zurys.live"
_VAPID = None            # py_vapid Vapid01 instance (podepisuje JWT)


def _load() -> None:
    global _PRIVATE_PEM, _PUBLIC, _SUBJECT, _VAPID
    cfg = BASE_DIR / "webpush.json"          # 1) lokální soubor (dev)
    if cfg.exists():
        try:
            d = json.loads(cfg.read_text(encoding="utf-8"))
            _PUBLIC = d.get("public") or _PUBLIC
            _PRIVATE_PEM = d.get("private") or _PRIVATE_PEM
            _SUBJECT = d.get("subject") or _SUBJECT
        except Exception as e:  # pragma: no cover
            print("[webpush] webpush.json se nepodařilo načíst:", e)
    _PUBLIC = os.environ.get("WEBPUSH_VAPID_PUBLIC", _PUBLIC)          # 2) ENV override (prod)
    _PRIVATE_PEM = os.environ.get("WEBPUSH_VAPID_PRIVATE", _PRIVATE_PEM)
    _SUBJECT = os.environ.get("WEBPUSH_VAPID_SUBJECT", _SUBJECT)
    # Fly secret bývá jednořádkový → privátní klíč smí přijít jako base64(PEM); rozkóduj.
    if _PRIVATE_PEM and not _PRIVATE_PEM.lstrip().startswith("-----BEGIN"):
        try:
            import base64
            _PRIVATE_PEM = base64.b64decode(_PRIVATE_PEM).decode("utf-8")
        except Exception:
            pass
    if _PRIVATE_PEM:
        try:
            from py_vapid import Vapid01
            _VAPID = Vapid01.from_pem(_PRIVATE_PEM.encode("utf-8"))
        except Exception as e:  # pragma: no cover
            print("[webpush] VAPID privátní klíč nelze načíst:", e)
            _VAPID = None


_load()


def enabled() -> bool:
    return bool(_VAPID and _PUBLIC)


def public_key() -> str:
    """App-server-key pro frontend (PushManager.subscribe applicationServerKey)."""
    return _PUBLIC or ""


class DeadSubscription(Exception):
    """Subscription je mrtvá (404/410) → volající ji má smazat z push_subs."""


def send(subscription_info: dict, title: str, body: str = "", url: str = "/", icon: str = "") -> bool:
    """Pošle 1 web push. True = OK. Mrtvá sub → DeadSubscription. Jiná chyba → False (best-effort)."""
    if not enabled():
        return False
    from pywebpush import webpush, WebPushException
    payload = json.dumps({"title": title, "body": body, "url": url or "/", "icon": icon or "/sedlak-cut.png"})
    try:
        webpush(subscription_info=subscription_info, data=payload,
                vapid_private_key=_VAPID, vapid_claims={"sub": _SUBJECT}, ttl=600)
        return True
    except WebPushException as e:
        code = getattr(getattr(e, "response", None), "status_code", None)
        if code in (404, 410):
            raise DeadSubscription() from e
        print("[webpush] send selhal:", code, str(e)[:160])
        return False
    except Exception as e:  # pragma: no cover – push nikdy nesmí shodit caller
        print("[webpush] send chyba:", type(e).__name__, str(e)[:160])
        return False
