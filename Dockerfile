# WebOS – ZURYS Drop Arena. Image pro nasazení na Fly.io.
# Fly si tohle sestaví ve svém cloudu (Docker nepotřebuješ mít u sebe).
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    WEBOS_DATA_DIR=/data

WORKDIR /app

# Závislosti zvlášť (rychlejší re-build při změně kódu)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Kód aplikace + frontend
COPY app/ ./app/
COPY web/ ./web/

# Trvalý disk pro SQLite + zálohy se připojí na /data (viz fly.toml)
EXPOSE 8080

# JEDEN worker – kvůli SQLite, in-memory rate-limiteru i budoucímu chat readeru.
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
