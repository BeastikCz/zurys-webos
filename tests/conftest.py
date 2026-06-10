"""Pytest setup pro WebOS smoke testy.

DŮLEŽITÉ: WEBOS_DATA_DIR se nastaví PŘED importem app, takže testy běží nad
čerstvou throwaway databází v dočasné složce a NIKDY nesáhnou na produkční/
lokální data. Spuštění z kořene projektu:

    .venv/Scripts/python.exe -m pytest tests/ -q
"""
import os
import tempfile

# musí být před importem app.* – config.py čte WEBOS_DATA_DIR při importu
os.environ["WEBOS_DATA_DIR"] = tempfile.mkdtemp(prefix="webos_test_")
os.environ.pop("FLY_APP_NAME", None)            # ať nejedeme v PROD režimu
os.environ.pop("DISCORD_ALERT_WEBHOOK", None)   # žádné Discord alerty z testů

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c
