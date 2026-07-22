"""Dev-only: vloží admin session do LOKÁLNÍ dev DB (data/app.db) a vypíše cookie.

Lokálně běží reálný Kick OAuth (kick.json), takže demo login nejde — tohle je jediná
cesta, jak si lokálně otevřít admin featury (Statek je za require_farm_access = admin).

Použití:
    .venv/Scripts/python.exe dev_login.py
    → spusť server (uvicorn app.main:app --port 8000)
    → v prohlížeči na http://127.0.0.1:8000 vlož do konzole vypsaný document.cookie řádek
    → reload → jsi přihlášený admin, Statek je v navigaci (#/statek)

NIKDY nepouštět na produkci (tam je DB /data/app.db na Contabo, ne tady).
"""
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

DB = Path(__file__).parent / "data" / "app.db"
assert DB.exists(), f"Dev DB nenalezena: {DB} — spusť nejdřív server, ať se vytvoří."

db = sqlite3.connect(DB)
db.row_factory = sqlite3.Row
admin = db.execute("SELECT id, username FROM users WHERE role = 'admin' ORDER BY id").fetchone()
assert admin, "V dev DB není žádný admin účet."

token = secrets.token_hex(24)
now = datetime.now(timezone.utc)
db.execute("INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?,?,?,?)",
           (token, admin["id"], now.isoformat(), (now + timedelta(days=7)).isoformat()))
db.commit()

print(f"Admin session pro '{admin['username']}' (id {admin['id']}), platí 7 dní.")
print("V konzoli prohlížeče (F12) na http://127.0.0.1:8000 spusť:\n")
print(f'document.cookie = "webos_session={token}; path=/"; location.hash = "#/statek"; location.reload();')
