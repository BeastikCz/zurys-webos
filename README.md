# 🎮 WebOS – věrnostní bodový shop pro streamera

Plnohodnotná full-stack webová aplikace: diváci sbírají **body** a utrácí je za odměny.
Inspirace loyalty shopy streamerů (styl StreamElements). Vše v češtině, moderní tmavý
„gaming" design, plně responzivní (mobil i desktop).

> **Body jsou věrnostní měna – dostáváš je jen od admina, NEDAJÍ se koupit za peníze.**
> Žádné reálné platby, ceny jsou vždy v bodech (např. „50 b").

---

## 🚀 Jak appku spustit

**Nejjednodušeji:** dvojklik na **`start.bat`** (otevře prohlížeč a spustí server).

Nebo ručně z PowerShellu ve složce projektu:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Poté otevři **http://127.0.0.1:8000**

> Závislosti (FastAPI + uvicorn) jsou už nainstalované v lokálním `.venv`.
> Databáze (SQLite) se vytvoří a naplní ukázkovými daty automaticky při prvním startu.

---

## 🔑 Přihlášení přes KICK

Žádná registrace ani heslo – diváci se přihlašují **propojením svého Kick účtu**
(tlačítko „🟢 Připojit přes Kick").

### Teď: DEMO režim (běží hned, bez klíčů)
Klikni na „Připojit přes Kick" a zadej svůj **Kick nick** – tím se propojíš a začneš sbírat body.

| Role | Připoj se Kick nickem |
|------|------------------------|
| **Admin** (správce) | `admin` |
| Sub | `subko` · VIP: `vipko` · Divák: `divak` |
| Leaderboard demo | `ninja_cz`, `pixelpaja`, `krtek99`, `aurora`, `lucka`, … |

Jakýkoliv jiný nick = nový divák se 0 body. Admina určuje nick v `ADMIN_KICK_USERNAMES`
(výchozí `admin`).

### Pro ostrý provoz: reálné Kick OAuth
1. Na Kicku si vytvoř OAuth aplikaci (Developer/Settings) → získáš `client_id` a `client_secret`.
2. Jako redirect URI nastav: `http://127.0.0.1:8000/api/auth/kick/callback`
   (nebo tvoji veřejnou doménu).
3. V kořeni projektu vytvoř **`kick.json`**:
   ```json
   {
     "client_id": "TVUJ_CLIENT_ID",
     "client_secret": "TVUJ_CLIENT_SECRET",
     "redirect_uri": "http://127.0.0.1:8000/api/auth/kick/callback",
     "admin_usernames": ["tvuj_kick_nick"]
   }
   ```
4. Restartuj appku – tlačítko „Připojit přes Kick" teď spustí skutečné Kick přihlášení
   (OAuth 2.0 + PKCE). Endpointy: `/api/auth/kick/login` a `/api/auth/kick/callback`.

> ⚠️ Kód reálného OAuth je připravený, ale otestovat ho jde až s tvými klíči – endpointy
> Kick API si pak případně doladíme dle aktuální dokumentace.

### 🎫 Ukázkové redeem kódy (Redeem)

| Kód | Co dělá |
|-----|---------|
| `VITEJ100` | +100 bodů |
| `STREAM50` | +50 bodů |
| `EMOTE-ZDARMA` | odemkne odměnu „SUB: Exkluzivní emote" |

---

## 🧩 Funkce a stránky

- **Shop** – mřížka odměn, filtry (Vše / Instantní / Krátké / Delší / Roční + „Jen subové" /
  „Jen VIP"), stránkování („Načíst další"), detail s nákupem, panel „Poslední nákupy",
  u tombol sekce „Kdo nakoupil tikety".
- **Leaderboard** – žebříček podle bodů, top 3 na pódiu.
- **Exchange** – směnárna: vyber speciální položku → potvrď → odečte body.
- **Redeem** – uplatnění kódu (body nebo odemčení odměny).
- **FAQ** – rozklikávací otázky (accordion).
- **Košík** – přidej víc odměn a kup je najednou.
- **Profil** – zůstatek, moje objednávky, historie pohybu bodů.
- **Admin panel** (jen admin):
  - **Odměny** – přidat / upravit / smazat (název, obrázek/URL, cena, kategorie, typ,
    „jen sub" / „jen VIP", sklad, aktivní).
  - **Uživatelé** – hledání, přidání/odebrání bodů, změna role.
  - **Objednávky** – seznam + označení „vyřízeno".
  - **Tomboly** – vylosování výherce z koupených tiketů.
  - **Kódy** – generování redeem kódů (body nebo odměna, platnost, počet použití).

### 👥 Role
`host` (nepřihlášený – jen prohlížení) · `user` (běžný divák) · `sub` (předplatitel) ·
`vip` (VIP divák) · `admin` (správce).
Odměny „jen sub" / „jen VIP" smí koupit jen uživatel s danou rolí (admin smí vše).

---

## 🛠️ Technologie & architektura

- **Backend:** Python + **FastAPI** (JSON API pod `/api`), server **uvicorn**.
- **Databáze:** **SQLite** (`data/app.db`) – reálná databáze, žádný cloud.
- **Autentizace:** přihlášení přes **Kick účet** (demo: zadání nicku / ostré: OAuth 2.0 + PKCE),
  relace přes **session cookie** (httpOnly). Žádná hesla.
- **Frontend:** čisté **HTML + CSS + vanilla JS** (SPA, žádný build krok, žádné CDN) –
  funguje i offline.

```
webos/
├─ app/                  # backend (FastAPI)
│  ├─ main.py            # vstupní bod, registrace routerů, servírování frontendu
│  ├─ config.py          # konstanty
│  ├─ db.py              # SQLite + schéma
│  ├─ security.py        # hashování hesel, tokeny
│  ├─ deps.py            # závislosti: aktuální uživatel, role, body
│  ├─ models.py          # validační schémata (pydantic)
│  ├─ services.py        # logika nákupu (sdílí shop i košík)
│  ├─ seed.py            # ukázková data + admin účet
│  └─ routers/           # auth, shop, cart, misc (leaderboard/redeem/profil), admin
├─ web/                  # frontend
│  ├─ index.html
│  ├─ styles.css
│  └─ app.js
├─ data/app.db           # SQLite databáze (vznikne automaticky)
├─ .venv/                # virtuální prostředí s FastAPI + uvicorn
├─ start.bat / start.ps1 # spouštěče
└─ requirements.txt
```

### 🗄️ Databázové tabulky
`users`, `products`, `orders`, `redeem_codes`, `points_log`, `raffle_entries`
(+ pomocné `sessions`, `redeem_uses`, `raffle_winners`).

### 🔄 Reset databáze do ukázkového stavu
Zastav server, smaž `data\app.db` (a případně `app.db-wal`, `app.db-shm`) a spusť znovu –
databáze se vytvoří a naplní ukázkovými daty od začátku.

```powershell
Remove-Item .\data\app.db* -Force
```

---

## 🔒 Bezpečnostní poznámky
- Aplikace běží lokálně na `127.0.0.1` (jen tvůj počítač). Pro veřejné nasazení na internet
  je potřeba doplnit HTTPS, `secure` cookie a silnější tajný klíč/limity – řekni si o to.
- Žádná hesla se neukládají – identita je Kick účet. `kick.json` s OAuth tajemstvím nedávej do gitu.
- Body nelze získat za peníze – jen od admina, redeem kódem, nebo (volitelně) si doplň
  vlastní logiku odměňování.

---

## 📚 API dokumentace
Po spuštění je k dispozici interaktivní dokumentace na **http://127.0.0.1:8000/api/docs**.
