import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from starlette.requests import Request

from app import webpush
from app.models import PushSubIn
from app.routers import auth, kickhook


_KEYS = {"p256dh": "A" * 87, "auth": "A" * 22}


def _request(cookie: bytes) -> Request:
    return Request({"type": "http", "method": "GET", "path": "/api/auth/kick/callback",
                    "headers": [(b"cookie", cookie)], "query_string": b""})


def test_bot_oauth_callback_requires_authorized_session(monkeypatch):
    monkeypatch.setattr(auth, "OAUTH_ENABLED", True)
    with pytest.raises(HTTPException, match="oprávnění") as exc:
        auth.kick_callback(_request(b"kick_state=s; kick_pkce=v; kick_flow=bot"),
                           code="code", state="s", conn=None, user=None)
    assert exc.value.status_code == 403


def test_push_only_accepts_known_hosts_and_never_follows_redirects():
    with pytest.raises(ValidationError):
        PushSubIn(endpoint="https://8.8.8.8/push", keys=_KEYS)
    assert PushSubIn(endpoint="https://fcm.googleapis.com/push", keys=_KEYS).endpoint
    assert webpush._push_session().max_redirects == 0


def test_webhook_rejects_oversized_body(client):
    response = client.post("/api/kick/webhook", content=b"x" * (kickhook.MAX_WEBHOOK_BODY + 1))
    assert response.status_code == 413
