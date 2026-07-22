import sqlite3
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException

from app import anticheat
from app.anticheat import (automation_checkpoint, automation_report,
                           complete_automation_checkpoint, record_automation_event)
from app.config import SESSION_COOKIE
from app.routers.misc import FARMING_ACTIONS_PER_MIN, _farming_rate_limit


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE users (
            id INTEGER PRIMARY KEY,
            username TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'user',
            points INTEGER NOT NULL DEFAULT 0,
            banned INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE automation_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            source TEXT NOT NULL,
            item_key TEXT,
            ready_at TEXT NOT NULL,
            acted_at TEXT NOT NULL,
            reaction_ms INTEGER NOT NULL
        );
        CREATE TABLE login_events (user_id INTEGER, ip TEXT);
        CREATE TABLE client_signals (user_id INTEGER, fp_hash TEXT);
        CREATE TABLE automation_verifications (
            user_id INTEGER PRIMARY KEY,
            verified_until TEXT NOT NULL,
            score INTEGER NOT NULL,
            created_at TEXT NOT NULL
        );
        """
    )
    conn.executemany(
        "INSERT INTO users (id,username) VALUES (?,?)",
        [(1, "ScriptPattern"), (2, "HumanPattern"), (3, "LinkedAlt")],
    )
    return conn


def test_automation_report_groups_bulk_and_explains_score():
    conn = _conn()
    now = datetime(2026, 7, 21, 12, tzinfo=timezone.utc)

    # Twelve precise batches across 33 hours, each with four units from one bulk click.
    for batch in range(12):
        acted = now - timedelta(hours=33 - batch * 3)
        ready = acted - timedelta(seconds=1)
        for _unit in range(4):
            record_automation_event(conn, 1, "garden", "mrkev", ready.isoformat(), acted.isoformat())

    # Human-scale reactions with a normal overnight pause.
    for hours, delay_minutes in ((48, 7), (45, 2), (30, 11), (25, 4), (8, 3), (1, 9)):
        acted = now - timedelta(hours=hours)
        ready = acted - timedelta(minutes=delay_minutes)
        record_automation_event(conn, 2, "farm", "chicken", ready.isoformat(), acted.isoformat())

    conn.executemany("INSERT INTO login_events (user_id,ip) VALUES (?,?)", [(1, "1.2.3.4"), (3, "1.2.3.4")])
    conn.executemany("INSERT INTO client_signals (user_id,fp_hash) VALUES (?,?)", [(1, "weak-fp"), (2, "weak-fp")])
    report = automation_report(conn, now=now)
    bot = next(item for item in report["users"] if item["user"]["id"] == 1)
    human = next(item for item in report["users"] if item["user"]["id"] == 2)

    assert bot["events"] == 48
    assert bot["batches"] == 12
    assert bot["score"] == 80
    assert bot["level"] == "high"
    assert {reason["code"] for reason in bot["reasons"]} == {"fast_reactions", "no_sleep"}
    assert bot["related_accounts"] == [{"id": 3, "username": "LinkedAlt", "via": ["ip"]}]
    assert human["score"] == 0
    assert human["level"] == "ok"
    assert human["related_accounts"] == []
    assert report["flagged"] == 1


def test_checkpoint_uses_ip_only_as_support_and_verifies_for_24h(monkeypatch):
    conn = _conn()
    now = datetime.now(timezone.utc)
    for batch in range(12):
        acted = now - timedelta(hours=33 - batch * 3)
        record_automation_event(conn, 1, "garden", "mrkev",
                                (acted - timedelta(seconds=1)).isoformat(), acted.isoformat())
    conn.executemany("INSERT INTO users (id,username) VALUES (?,?)",
                     [(4, "Linked4"), (5, "Linked5")])
    conn.executemany("INSERT INTO login_events (user_id,ip) VALUES (?,?)",
                     [(1, "1.2.3.4"), (3, "1.2.3.4"), (4, "1.2.3.4"), (5, "1.2.3.4")])
    user = conn.execute("SELECT * FROM users WHERE id=1").fetchone()
    monkeypatch.setattr(anticheat, "TURNSTILE_SITE_KEY", "site-key")
    monkeypatch.setattr(anticheat, "TURNSTILE_SECRET_KEY", "secret-key")
    monkeypatch.setattr(anticheat, "TURNSTILE_HOSTNAME", "zurys.live")

    assert anticheat.automation_risk(conn, 3, now)["score"] == 0
    state = automation_checkpoint(conn, user, now)
    assert state["required"] is True
    assert state["score"] == 100

    monkeypatch.setattr(anticheat, "_turnstile_siteverify", lambda *_: {
        "success": True, "action": "wrong", "hostname": "zurys.live"
    })
    with pytest.raises(HTTPException) as invalid:
        complete_automation_checkpoint(conn, user, "bad-token", "1.2.3.4")
    assert invalid.value.status_code == 400

    monkeypatch.setattr(anticheat, "_turnstile_siteverify", lambda *_: {
        "success": True, "action": anticheat.TURNSTILE_ACTION, "hostname": "zurys.live"
    })
    verified = complete_automation_checkpoint(conn, user, "good-token", "1.2.3.4")
    assert verified["ok"] is True
    assert automation_checkpoint(conn, user)["required"] is False


def test_farming_actions_share_one_user_rate_limit():
    user = {"id": secrets.randbelow(1_000_000_000) + 10_000}
    for _ in range(FARMING_ACTIONS_PER_MIN):
        _farming_rate_limit(user)
    with pytest.raises(HTTPException) as blocked:
        _farming_rate_limit(user)
    assert blocked.value.status_code == 429


def _login(role):
    from app.db import get_conn, now_iso

    conn = get_conn()
    suffix = secrets.token_hex(4)
    cur = conn.execute(
        "INSERT INTO users (kick_username,username,role,created_at) VALUES (?,?,?,?)",
        (f"automation_{role}_{suffix}", f"automation_{role}_{suffix}", role, now_iso()),
    )
    token = secrets.token_hex(24)
    conn.execute(
        "INSERT INTO sessions (token,user_id,created_at,expires_at) VALUES (?,?,?,?)",
        (token, cur.lastrowid, now_iso(), (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()),
    )
    conn.commit()
    conn.close()
    return token


def test_automation_dashboard_is_admin_only_and_wired(client):
    user_token = _login("user")
    admin_token = _login("admin")
    path = "/api/admin/security/automation"

    assert client.get(path, headers={"Cookie": f"{SESSION_COOKIE}={user_token}"}).status_code == 403
    response = client.get(path, headers={"Cookie": f"{SESSION_COOKIE}={admin_token}"})
    assert response.status_code == 200
    assert response.json()["mode"] == "audit-only"

    frontend = (Path(__file__).parents[1] / "web" / "app.js").read_text(encoding="utf-8")
    assert 'api("/admin/security/automation")' in frontend
    assert '${automationReportHTML(automation)}' in frontend
    panel = frontend.split("function automationReportHTML", 1)[1].split("function ruleRowHTML", 1)[0]
    assert "otisk" not in panel


def test_suspicious_harvest_requires_turnstile_then_unlocks(client, monkeypatch):
    from app.db import get_conn, now_iso

    token = _login("user")
    conn = get_conn()
    uid = conn.execute("SELECT user_id FROM sessions WHERE token=?", (token,)).fetchone()["user_id"]
    now = datetime.now(timezone.utc)
    for batch in range(12):
        acted = now - timedelta(hours=33 - batch * 3)
        record_automation_event(conn, uid, "garden", "mrkev",
                                (acted - timedelta(seconds=1)).isoformat(), acted.isoformat())
    for index in range(3):
        name = f"checkpoint_link_{secrets.token_hex(4)}_{index}"
        linked = conn.execute(
            "INSERT INTO users (kick_username,username,role,created_at) VALUES (?,?,?,?)",
            (name, name, "user", now_iso()),
        ).lastrowid
        conn.execute(
            "INSERT INTO login_events (user_id,ip,method,created_at) VALUES (?,?,?,?)",
            (linked, "1.2.3.4", "test", now_iso()),
        )
    conn.execute(
        "INSERT INTO login_events (user_id,ip,method,created_at) VALUES (?,?,?,?)",
        (uid, "1.2.3.4", "test", now_iso()),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(anticheat, "TURNSTILE_SITE_KEY", "site-key")
    monkeypatch.setattr(anticheat, "TURNSTILE_SECRET_KEY", "secret-key")
    monkeypatch.setattr(anticheat, "TURNSTILE_HOSTNAME", "zurys.live")
    monkeypatch.setattr(anticheat, "_turnstile_siteverify", lambda *_: {
        "success": True, "action": anticheat.TURNSTILE_ACTION, "hostname": "zurys.live"
    })
    headers = {"Cookie": f"{SESSION_COOKIE}={token}"}

    blocked = client.post("/api/garden/harvest-all", headers=headers)
    assert blocked.status_code == 428
    assert blocked.json()["detail"]["code"] == "automation_checkpoint"
    assert blocked.json()["detail"]["site_key"] == "site-key"

    verified = client.post("/api/automation/checkpoint", headers=headers,
                           json={"token": "valid-token"})
    assert verified.status_code == 200
    assert verified.json()["ok"] is True
    assert client.post("/api/garden/harvest-all", headers=headers).status_code == 200

    frontend = (Path(__file__).parents[1] / "web" / "app.js").read_text(encoding="utf-8")
    assert "openAutomationCheckpoint(data.detail)" in frontend
    assert "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit" in frontend
    from app.main import _CSP_STRICT
    csp = _CSP_STRICT
    assert "script-src 'self' https://challenges.cloudflare.com" in csp
    assert "frame-src https://challenges.cloudflare.com" in csp
