"""Freshness check Kick webhook podpisů (replay guard po prune dedup tabulky).

RSA podpis se mockuje – testuje se JEN timestampová větev verify():
čerstvý timestamp projde (ISO i Z varianta), starý/neparsovatelný se odmítne.
"""
from datetime import datetime, timedelta, timezone

from app import kickevents


def _mock_rsa_ok(monkeypatch):
    monkeypatch.setattr(kickevents, "_public_key", lambda: object())
    monkeypatch.setattr(kickevents.rsa, "verify", lambda *a: True)


def test_fresh_timestamp_passes(monkeypatch):
    _mock_rsa_ok(monkeypatch)
    now = datetime.now(timezone.utc)
    assert kickevents.verify("id1", now.isoformat(), b"{}", "c2ln")
    # Z-varianta (RFC3339 tak, jak ji posílá Kick)
    z = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    assert kickevents.verify("id2", z, b"{}", "c2ln")


def test_stale_timestamp_rejected(monkeypatch):
    _mock_rsa_ok(monkeypatch)
    old = (datetime.now(timezone.utc) - timedelta(days=4)).isoformat()
    assert not kickevents.verify("id3", old, b"{}", "c2ln")


def test_unparsable_timestamp_rejected(monkeypatch):
    _mock_rsa_ok(monkeypatch)
    assert not kickevents.verify("id4", "nesmysl", b"{}", "c2ln")
    assert not kickevents.verify("id5", "", b"{}", "c2ln")


def test_retry_window_tolerant(monkeypatch):
    """Kick retry nese PŮVODNÍ timestamp – pár hodin starý event musí projít."""
    _mock_rsa_ok(monkeypatch)
    retried = (datetime.now(timezone.utc) - timedelta(hours=6)).isoformat()
    assert kickevents.verify("id6", retried, b"{}", "c2ln")
