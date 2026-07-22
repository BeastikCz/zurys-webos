#!/bin/bash
# Prvotní setup Contabo VPS (Ubuntu 24.04) pro zurys.live.
# Spouští se JEDNOU jako root: bash contabo_setup.sh
# Idempotentní — bezpečné pustit znovu.
set -euo pipefail

echo "== 1/6 Systém + balíčky =="
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y \
  python3.12-venv python3-pip git ufw fail2ban unattended-upgrades sqlite3 caddy || {
  # caddy není v základních repo — přidej oficiální repo a zkus znovu
  apt-get install -y debian-keyring debian-archive-keyring apt-transport-https curl
  curl -1sLf https://dl.cloudflare.com/dummy 2>/dev/null || true
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update && apt-get install -y caddy
}

echo "== 2/6 Firewall (ufw) =="
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp   # SSH
ufw allow 80/tcp   # HTTP (Cloudflare)
ufw allow 443/tcp  # HTTPS (Cloudflare)
ufw --force enable

echo "== 3/6 Fail2ban + auto security updaty =="
systemctl enable --now fail2ban
dpkg-reconfigure -f noninteractive unattended-upgrades

echo "== 4/6 Uživatel webos + adresáře =="
id webos &>/dev/null || useradd -m -s /bin/bash webos
mkdir -p /data /opt/webos
chown webos:webos /data /opt/webos

echo "== 5/6 Aplikace =="
if [ ! -d /opt/webos/app/.git ]; then
  sudo -u webos git clone https://github.com/BeastikCz/zurys-webos.git /opt/webos/app
fi
cd /opt/webos/app
sudo -u webos python3 -m venv /opt/webos/venv
sudo -u webos /opt/webos/venv/bin/pip install -r requirements.txt

# .env se secrets NENÍ v gitu — vytvoří se ručně při migraci (viz DEPLOY.md)
touch /opt/webos/env
chown webos:webos /opt/webos/env
chmod 600 /opt/webos/env

echo "== 6/6 systemd service =="
cat > /etc/systemd/system/webos.service <<'EOF'
[Unit]
Description=zurys.live WebOS (FastAPI)
After=network.target

[Service]
User=webos
WorkingDirectory=/opt/webos/app
EnvironmentFile=/opt/webos/env
Environment=WEBOS_DATA_DIR=/data
ExecStart=/opt/webos/venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8080 --workers 1 --limit-concurrency 512
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

# Caddy: reverse proxy 80/443 -> uvicorn. TLS řeší Cloudflare origin cert (doplní se při migraci).
cat > /etc/caddy/Caddyfile <<'EOF'
# Fáze 1 (test na holé IP): jen HTTP
:80 {
    reverse_proxy 127.0.0.1:8080
}
# Fáze 2 (po přepnutí DNS): odkomentuj a doplň CF origin cert
# zurys.live {
#     tls /etc/caddy/cf-origin.pem /etc/caddy/cf-origin.key
#     reverse_proxy 127.0.0.1:8080
# }
EOF

systemctl daemon-reload
systemctl enable webos caddy
systemctl restart caddy
# webos service se startuje až po vytvoření /opt/webos/env s secrets

echo "HOTOVO. Další kroky: naplnit /opt/webos/env, zkopírovat DB do /data, systemctl start webos"
