# Nasazení zurys.live na Contabo

Produkce běží pouze na Contabo VPS `169.58.8.1`. Produkční databáze je
`/data/app.db`. Fly.io bylo zrušeno; `flyctl` se pro tento projekt nepoužívá.

## Kontrola bez nasazení

Z kořene projektu spusť:

```powershell
.\.venv\Scripts\python.exe deploy.py
```

Dry-run ověří veřejný health, vypnutou údržbu, databázi, testy a frontendovou
syntaxi. Produkci ani lokální soubory nezmění.

## Nasazení

Nasazuj pouze na výslovný pokyn:

```powershell
.\.venv\Scripts\python.exe deploy.py --deploy
```

Skript:

1. spustí predeploy bránu;
2. zvýší cache verzi frontendu;
3. nahraje `app/`, `web/` a `requirements.txt` na Contabo;
4. atomicky přepne release a restartuje `webos.service`;
5. ověří health a vyčistí Cloudflare cache.

Po úspěšném deployi commitni cache bump a doplň nejnovější záznam do
`WORKLOG.md`.

## Ruční diagnostika

```powershell
ssh -i "$env:USERPROFILE\.ssh\hetzner_zurys" root@169.58.8.1 "systemctl status webos --no-pager"
ssh -i "$env:USERPROFILE\.ssh\hetzner_zurys" root@169.58.8.1 "journalctl -u webos -n 100 --no-pager"
```

Veřejný health:

```text
https://zurys.live/api/monitor/healthz
```

Při chybě nic nepřepínej ručně. Aktivní release je symlink `/opt/webos/app`
a `deploy.py` při neúspěšném health checku automaticky vrátí předchozí release.
