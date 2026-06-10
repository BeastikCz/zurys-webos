# 🚀 Nasazení ZURYS Drop Arena na internet (Fly.io + doména z Namecheap)

Návod pro začátečníka. Appku poběží **Fly.io**, doménu vezmeme z **Namecheapu**.
Stačí projít kroky odshora dolů. Příkazy se píšou do **PowerShellu** (modré okno).

> ℹ️ Docker u sebe **nepotřebuješ** – Fly si appku sestaví ve svém cloudu.

---

## 1) Účet na Fly.io
1. Jdi na <https://fly.io> → **Sign up**.
2. Fly chce **platební kartu** (i pro malé appky kvůli ověření). Provoz téhle appky vyjde
   zhruba na **~3 $/měsíc**.

## 2) Nainstalovat `flyctl` (jednorázově, jeden malý program)
> ⚠️ Tohle je jediná instalace na tvém PC. Spustíš ji **ty** (ne já). Je to oficiální nástroj Fly.io.
> Odinstalace později: smazat složku `%USERPROFILE%\.fly`.

V PowerShellu spusť (oficiální příkaz Fly.io):
```powershell
iwr https://fly.io/install.ps1 -useb | iex
```
Pak zavři a znovu otevři PowerShell a ověř:
```powershell
fly version
```

## 3) Přihlásit se
```powershell
fly auth login
```
(otevře prohlížeč → potvrď)

## 4) Vytvořit aplikaci
V kořeni projektu (`C:\Users\Administrator\webos`):
```powershell
fly launch --no-deploy --copy-config
```
- Zeptá se na **název appky** → zvol něco unikátního, např. `zurys-shop` (adresa pak bude
  `https://zurys-shop.fly.dev`). Když je název obsazený, zkus `zurys-drop`, `zurys-arena`…
- Region nech **fra** (Frankfurt).
- Postgres/Redis **odmítni** (nepotřebujeme, máme SQLite).

> Pokud zvolíš jiný název než `zurys-shop`, uprav si pak v `fly.toml` řádek
> `KICK_REDIRECT_URI` (nahraď `zurys-shop` svým názvem).

## 5) Trvalý disk pro databázi
```powershell
fly volumes create webos_data --size 1 --region fra
```
(1 GB bohatě stačí; tady žije SQLite + zálohy a přežijí restart i nový deploy)

## 6) Tajné klíče Kick (bezpečně, ne v souboru)
Vezmi hodnoty z **kick.json** a nastav je jako „secrets":
```powershell
fly secrets set KICK_CLIENT_ID="01KSSZTSNA3P4K0DM18KVME7D0"
fly secrets set KICK_CLIENT_SECRET="<tvuj_client_secret_z_kick.json>"
fly secrets set KICK_BROADCASTER_CHANNEL="zurys1337"
fly secrets set KICK_BOT_USERNAME="SedlakBOT"
fly secrets set ADMIN_KICK_USERNAMES="interaty"
```

## 7) Nasadit!
```powershell
fly deploy
```
Po chvíli appka poběží na **https://<tvuj-nazev>.fly.dev** 🎉
```powershell
fly open
```

## 8) Říct Kicku novou adresu
V **Kick developer dashboardu** u své aplikace změň **Redirect URI** na:
```
https://<tvuj-nazev>.fly.dev/api/auth/kick/callback
```
(a povol scope **chat:write** kvůli botovi). Tím začne fungovat reálné přihlášení i bot.

---

## 9) (Až budeš chtít) Napojit doménu z Namecheap
Řekněme, že na Namecheapu koupíš **`zurys.store`**:

1. Na Fly přidej doménu (vytvoří HTTPS certifikát):
   ```powershell
   fly certs add zurys.store
   fly certs add www.zurys.store
   ```
   Fly vypíše, jaké **DNS záznamy** máš nastavit.
2. V **Namecheap** → *Domain List* → u domény **Manage** → záložka **Advanced DNS** →
   přidej záznamy, které Fly ukázal (typicky):
   - `A` záznam `@` → IPv4 adresa z `fly ips list`
   - `AAAA` záznam `@` → IPv6 adresa z `fly ips list`
   - `CNAME` `www` → `<tvuj-nazev>.fly.dev`
3. Počkej ~15–60 min (DNS se rozjíždí), pak ověř:
   ```powershell
   fly certs show zurys.store
   ```
4. Nakonec přepni `KICK_REDIRECT_URI` na doménu:
   ```powershell
   fly secrets set KICK_REDIRECT_URI="https://zurys.store/api/auth/kick/callback"
   ```
   a v Kick dashboardu změň Redirect URI na stejnou adresu.

---

## Užitečné příkazy
| Co | Příkaz |
|----|--------|
| Logy naživo | `fly logs` |
| Stav | `fly status` |
| Restart | `fly apps restart` |
| Nová verze po úpravě kódu | `fly deploy` |
| Stažení zálohy DB | `fly ssh console -C "cat /data/app.db" > zaloha.db` |

## Časté chyby
- **„name taken"** při launch → zvol jiný název appky.
- **Appka nejede / 500** → `fly logs` ukáže proč (nejčastěji chybí secret nebo volume).
- **Kick login nefunguje** → zkontroluj, že Redirect URI v Kicku == `KICK_REDIRECT_URI`.
