"""Centrální konfigurace aplikace WebOS – věrnostní bodový shop."""
import json
import os
from pathlib import Path

# Kořen projektu (složka webos/)
BASE_DIR = Path(__file__).resolve().parent.parent

# Datová složka + SQLite databáze.
# Na hostingu (Fly.io) se nastaví WEBOS_DATA_DIR=/data (trvalý disk) – lokálně zůstává ./data.
DATA_DIR = Path(os.environ.get("WEBOS_DATA_DIR") or (BASE_DIR / "data"))
DB_PATH = DATA_DIR / "app.db"
UPLOAD_DIR = DATA_DIR / "uploads"   # nahrané obrázky (na Fly = trvalý disk), servíruje se na /uploads

# Statický frontend (SPA)
WEB_DIR = BASE_DIR / "web"

# Autentizace
SESSION_COOKIE = "webos_session"
SESSION_DAYS = 30  # platnost přihlášení

# Role uživatelů
ROLE_USER = "user"
ROLE_SUB = "sub"
ROLE_VIP = "vip"
ROLE_MOD = "mod"                # moderátor (staff)
ROLE_PREDICTOR = "predictor"    # predikční moderátor (staff) – vidí admin, ale JEN sekci Predikce, nic víc
ROLE_BROADCASTER = "broadcaster"  # broadcaster (staff)
ROLE_ADMIN = "admin"
ALL_ROLES = (ROLE_USER, ROLE_SUB, ROLE_VIP, ROLE_MOD, ROLE_PREDICTOR, ROLE_BROADCASTER, ROLE_ADMIN)

# Staff = role s přístupem do administrace (admin má vždy vše)
STAFF_ROLES = (ROLE_MOD, ROLE_PREDICTOR, ROLE_BROADCASTER, ROLE_ADMIN)

# Sekce admin panelu → NE-admin role, které na ni smí. Admin smí vždy všechno.
#   broadcaster = provoz platformy;
#   moderátor (mod) = JEN: vidět uživatele + úprava bodů, objednávky, predikce, hry. NIC víc.
#   ban/role/flags/import/economy/security/products/raffles/codes/drops/bot/news/stats = NE mod.
#   predikční moderátor (predictor) = vidí admin panel, ale JEN sekci Predikce. Nic jiného (ani uživatele/body).
MOD_POINTS_MAX = 50000          # strop ±bodů, které smí MOD přidat/odebrat na jeden zásah (admin bez limitu)
ADMIN_SECTIONS = {
    "stats":    (ROLE_BROADCASTER,),
    "products": (ROLE_MOD, ROLE_BROADCASTER),   # mod smí spravovat odměny v shopu (vytvořit/upravit/smazat)
    "users":    (ROLE_MOD, ROLE_BROADCASTER),   # mod má JEN: vidět uživatele + úprava bodů; ban/import/role/flags/IP = admin
    "orders":   (ROLE_MOD, ROLE_BROADCASTER),
    "raffles":  (ROLE_BROADCASTER,),
    "auctions": (ROLE_BROADCASTER,),   # aukce o skiny (vystavit/zrušit) – jako tomboly
    "crews":    (ROLE_BROADCASTER,),   # přehled part (kdo s kým, XP, členové) – read-only
    "codes":    (ROLE_BROADCASTER,),
    "drops":    (ROLE_BROADCASTER,),
    "games":    (ROLE_MOD, ROLE_BROADCASTER),   # moderace probíhajících her (ukončit/refund)
    "bot":      (ROLE_BROADCASTER,),
    "predictions": (ROLE_MOD, ROLE_PREDICTOR, ROLE_BROADCASTER),   # mod, predikční moderátor i broadcaster smí spravovat predikce
    "economy":  (ROLE_BROADCASTER,),   # vč. zapnout/vypnout stream (live toggle)
    "news":     (ROLE_BROADCASTER,),   # patch notes / novinky (changelog)
    "gifts":    (ROLE_BROADCASTER,),   # schvalování žádostí o dar bodů (gift requests)
    "security": (),
}

# Typy odměn (filtry: Vše, Instantní, Krátké, Delší, Roční + tombola)
PRODUCT_TYPES = ("instant", "short", "long", "yearly", "raffle")
# Perioda giveaway/tomboly (volitelná, prázdná = žádná) – jen organizační štítek
PRODUCT_PERIODS = ("", "daily", "weekly", "monthly", "yearly", "random")

# Stav objednávek
ORDER_PENDING = "pending"      # čeká na vyřízení
ORDER_FULFILLED = "fulfilled"  # vyřízeno

# stock = -1 znamená neomezené množství
UNLIMITED_STOCK = -1

# Odměny, u kterých musí mít divák PŘED nákupem vyplněný Steam trade link (CS skiny –
# nože, rukavice, zbraně). Porovnává se podřetězcem (case-insensitive) vůči kategorii
# produktu. Brání nevyřiditelným objednávkám a přidává tření pro odhazovací/bot účty
# (ty si reálný Steam trade link nenastaví). Hardware (myš = poštovní adresa) sem nepatří.
SKIN_TRADE_KEYWORDS = ("nůž", "nuz", "nože", "noze", "knife", "zbraň", "zbran",
                       "skin", "rukavic", "glove", "dagger", "dýk", "dyk")

# ============================================================
#  Přihlášení přes KICK
#  - Když KICK_CLIENT_ID zůstane prázdné → DEMO režim
#    (uživatel jen zadá svůj Kick nick, žádné OAuth).
#  - Pro REÁLNÉ Kick OAuth vytvoř soubor kick.json v kořeni projektu:
#      { "client_id": "...", "client_secret": "...",
#        "redirect_uri": "http://127.0.0.1:8000/api/auth/kick/callback",
#        "admin_usernames": ["tvuj_kick_nick"] }
# ============================================================
KICK_CLIENT_ID = ""
KICK_CLIENT_SECRET = ""
KICK_REDIRECT_URI = "http://127.0.0.1:8000/api/auth/kick/callback"
KICK_AUTH_URL = "https://id.kick.com/oauth/authorize"
KICK_TOKEN_URL = "https://id.kick.com/oauth/token"
KICK_USER_URL = "https://api.kick.com/public/v1/users"
KICK_CHANNELS_URL = "https://api.kick.com/public/v1/channels"  # GET ?slug= → broadcaster_user_id
KICK_CHAT_URL = "https://api.kick.com/public/v1/chat"   # POST – odeslání zprávy do chatu (chat:write)
KICK_EVENTS_SUB_URL = "https://api.kick.com/public/v1/events/subscriptions"  # POST – odběr eventů (events:subscribe)
KICK_MODERATION_URL = "https://api.kick.com/public/v1/moderation/bans"  # POST ban/timeout, DELETE unban (scope moderation:ban)
KICK_SCOPE = "user:read"                                # scope pro přihlášení diváka
KICK_BOT_SCOPE = "user:read chat:write events:subscribe moderation:ban"  # bot: chat + eventy + ban v chatu (sync banů z webu)

# Bot (SedlakBOT) píše do tohoto kanálu. Lze přepsat v kick.json: "broadcaster_channel".
KICK_BROADCASTER_CHANNEL = "zurys1337"
KICK_BOT_USERNAME = "SedlakBOT"                          # očekávané jméno bot účtu (jen pro UI)
# Kdo je po připojení admin (Kick nicky, malými písmeny). V demu stačí „admin“.
ADMIN_KICK_USERNAMES = ["admin"]

# ============================================================
#  Anticheat pravidla (konfigurovatelná v admin panelu)
#  enforced=True → server pravidlo reálně vyhodnocuje a flaguje.
#  enforced=False → zatím jen toggle (chce IP databázi / hlubší fingerprint).
# ============================================================
ANTICHEAT_RULES = [
    {"key": "multi_account", "label": "Multi-account detection", "severity": "HIGH",
     "desc": "Stejná IP / fingerprint používá víc účtů", "threshold": 3,
     "prah": "3+ účty / IP", "enforced": True},
    {"key": "headless", "label": "Headless browser", "severity": "CRITICAL",
     "desc": "Puppeteer, Playwright, Selenium – webdriver signatura", "threshold": None,
     "prah": "auto-flag", "enforced": True},
    {"key": "vpn_proxy", "label": "VPN / Proxy / Datacenter IP", "severity": "MEDIUM",
     "desc": "IP patří známým VPN providerům nebo cloud datacentrům", "threshold": None,
     "prah": "auto-flag", "enforced": True},
    {"key": "rapid_fire", "label": "Rapid-fire purchasing", "severity": "HIGH",
     "desc": "Příliš mnoho nákupů v krátkém čase = bot pattern", "threshold": 10,
     "prah": "10+ akcí / 5 min", "enforced": True},
    {"key": "stream_sleep", "label": "Stream sleep detection", "severity": "LOW",
     "desc": "Tab inactive / okno minimalizované při sledování", "threshold": 30,
     "prah": ">30 min neaktivní", "enforced": False, "default_off": True},
    {"key": "click_anomaly", "label": "Click pattern anomaly", "severity": "MEDIUM",
     "desc": "Příliš uniformní intervaly mezi kliky (bot signatura)", "threshold": 50,
     "prah": "σ < 50 ms", "enforced": False},
    {"key": "new_account_spend", "label": "New account high spend", "severity": "HIGH",
     "desc": "Účet mladší 24 h utratí hodně sedláků = farm bot", "threshold": 1000,
     "prah": ">1000 sedláků / 24 h", "enforced": True},
    {"key": "geo_anomaly", "label": "Geo anomaly", "severity": "MEDIUM",
     "desc": "Login ze dvou zemí za <30 min (impossible travel)", "threshold": 30,
     "prah": "30 min", "enforced": False},
    {"key": "canvas_spoof", "label": "Canvas fingerprint spoofing", "severity": "HIGH",
     "desc": "Canvas randomization / fingerprint masking", "threshold": None,
     "prah": "auto-flag", "enforced": False},
]

# Ukázkový seznam datacenter/VPN rozsahů (cloud providery). NENÍ vyčerpávající –
# pro plné pokrytí použij GeoLite2-ASN nebo IP reputation API. Slouží k základní detekci.
DATACENTER_CIDRS = [
    "13.32.0.0/15", "13.64.0.0/11", "18.32.0.0/11",      # AWS
    "34.64.0.0/10", "35.184.0.0/13", "35.190.0.0/17",    # Google Cloud
    "20.33.0.0/16", "40.64.0.0/10", "52.224.0.0/11",     # Azure
    "104.16.0.0/13", "172.64.0.0/13",                    # Cloudflare
    "159.69.0.0/16", "168.119.0.0/16", "78.46.0.0/15",   # Hetzner
    "51.15.0.0/16", "51.158.0.0/15",                     # Scaleway / OVH
    "146.70.0.0/15", "45.83.64.0/22", "185.220.100.0/22"  # známé VPN/Tor
]

# IP, které anticheat ÚPLNĚ ignoruje: skóre 0, žádné bloky, žádné alerty.
# Použij pro známé „dobré" sdílené IP (NAT mobilního operátora, síť streamera…),
# které jinak falešně spouští pravidlo „sdílená IP" a zaplavují Discord alerty.
# Rozšiřitelné přes ENV `TRUSTED_IPS="1.2.3.4,5.6.7.8"` bez zásahu do kódu.
TRUSTED_IPS = {
    "194.228.7.45",   # spamovala anticheat (sdílená/NAT IP) – přidáno ručně
}
_env_trusted = os.environ.get("TRUSTED_IPS", "")
if _env_trusted:
    TRUSTED_IPS |= {x.strip() for x in _env_trusted.split(",") if x.strip()}

# IP, ze kterých admin běžně pracuje. Citlivá admin akce z JINÉ IP (a zároveň
# mimo historii přihlášení toho admina) spustí alert „admin akce z neznámé IP"
# = detekce únosu session / krádeže Kick účtu. Default prázdné → spoléhá na
# historii přihlášení (self-learning). Rozšiř přes ENV `KNOWN_ADMIN_IPS="1.2.3.4,…"`.
KNOWN_ADMIN_IPS = set()
_env_admin_ips = os.environ.get("KNOWN_ADMIN_IPS", "")
if _env_admin_ips:
    KNOWN_ADMIN_IPS |= {x.strip() for x in _env_admin_ips.split(",") if x.strip()}

# Volitelné načtení z kick.json (nepřepisuje, jen doplní reálné hodnoty)
_kick_cfg = BASE_DIR / "kick.json"
if _kick_cfg.exists():
    try:
        _data = json.loads(_kick_cfg.read_text(encoding="utf-8"))
        KICK_CLIENT_ID = _data.get("client_id", KICK_CLIENT_ID)
        KICK_CLIENT_SECRET = _data.get("client_secret", KICK_CLIENT_SECRET)
        KICK_REDIRECT_URI = _data.get("redirect_uri", KICK_REDIRECT_URI)
        KICK_BROADCASTER_CHANNEL = _data.get("broadcaster_channel", KICK_BROADCASTER_CHANNEL)
        KICK_BOT_USERNAME = _data.get("bot_username", KICK_BOT_USERNAME)
        if _data.get("admin_usernames"):
            ADMIN_KICK_USERNAMES = [str(x).lower() for x in _data["admin_usernames"]]
    except Exception as _e:  # pragma: no cover
        print("[config] kick.json se nepodařilo načíst:", _e)


# Produkční override přes ENV proměnné (Fly.io „secrets"). ENV má přednost před kick.json.
# Tajný client_secret tak na serveru NEleží v souboru, ale v zašifrovaných secrets.
KICK_CLIENT_ID = os.environ.get("KICK_CLIENT_ID", KICK_CLIENT_ID)
KICK_CLIENT_SECRET = os.environ.get("KICK_CLIENT_SECRET", KICK_CLIENT_SECRET)
KICK_REDIRECT_URI = os.environ.get("KICK_REDIRECT_URI", KICK_REDIRECT_URI)
KICK_BROADCASTER_CHANNEL = os.environ.get("KICK_BROADCASTER_CHANNEL", KICK_BROADCASTER_CHANNEL)
KICK_BOT_USERNAME = os.environ.get("KICK_BOT_USERNAME", KICK_BOT_USERNAME)
_env_admins = os.environ.get("ADMIN_KICK_USERNAMES", "")
if _env_admins.strip():
    ADMIN_KICK_USERNAMES = [x.strip().lower() for x in _env_admins.split(",") if x.strip()]


# Známí boti (chat boti) – NEberou body za chat, nepočítají se do komunitního cíle
# ani do Top Chatterů (i historicky). Lowercase. Rozšiř přes ENV `BOT_USERNAMES="a,b"`.
BOT_USERNAMES = {
    "botrix", "sedlakbot", "streamelements", "nightbot", "moobot", "wizebot",
    "fossabot", "streamlabs", "kickbot", "ohbot", "deepbot", "phantombot", "wzbot",
}
if KICK_BOT_USERNAME:
    BOT_USERNAMES.add(KICK_BOT_USERNAME.strip().lower())
_env_bots = os.environ.get("BOT_USERNAMES", "")
if _env_bots.strip():
    BOT_USERNAMES |= {x.strip().lower() for x in _env_bots.split(",") if x.strip()}
# Streamer sám (broadcaster) NEbere divácké chat odměny ani není v Top Chatterech.
if KICK_BROADCASTER_CHANNEL:
    BOT_USERNAMES.add(KICK_BROADCASTER_CHANNEL.strip().lower())
