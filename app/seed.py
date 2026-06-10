"""Naplnění databáze ukázkovými daty (jen když je DB prázdná). Účty přes Kick nick."""
from datetime import datetime, timezone, timedelta

from .db import get_conn, now_iso, get_setting, set_setting
from .config import ROLE_ADMIN, ROLE_SUB, ROLE_VIP, ROLE_USER

# V DEMO režimu se uživatel „připojí“ jen zadáním Kick nicku.
# Nick „admin“ = správce (lze změnit v config.ADMIN_KICK_USERNAMES / kick.json).
ADMIN_KICK = "admin"

# (kick_username, zobrazované jméno, role, body)
USERS = [
    ("admin", "Admin", ROLE_ADMIN, 50000),
    ("subko", "SubDivák", ROLE_SUB, 1500),
    ("vipko", "VipDivák", ROLE_VIP, 3200),
    ("divak", "BěžnýDivák", ROLE_USER, 320),
    ("ninja_cz", "Ninja_CZ", ROLE_VIP, 8800),
    ("pixelpaja", "PixelPája", ROLE_SUB, 6400),
    ("krtek99", "Krtek99", ROLE_USER, 4100),
    ("aurora", "AuroraStream", ROLE_SUB, 2750),
    ("toxictomas", "ToxicTomáš", ROLE_USER, 1900),
    ("lucka", "Lucka_Plays", ROLE_VIP, 980),
    ("berta", "BertaBerry", ROLE_USER, 540),
]

# (name, image_url, cost, category, type, subs_only, vip_only, stock, description, rarity, hot, ends_h)
# image_url = relativní cesta na lokální obrázky v web/img/products/ (žádná externí závislost)
PRODUCTS = [
    ("Stiletto Knife | Vanilla", "/img/products/stiletto-vanilla.png", 600000, "Nože", "instant", 0, 0, 1,
     "Zaplatíš 600 000 PTS a insta pošli tradelink, já to pošlu.", "covert", 1, None),
    ("ASUS ROG Harpe II Ace (Lava Red)", "/img/products/asus-rog-harpe-ii-ace-lava-red.jpg", 450000, "Hardware", "instant", 0, 0, 1,
     "Super slevy zde – https://cz.rog.gg/ZurGWG0-chatbot. Jakmile někdo koupí, posílám myš na adresu.", "contraband", 0, None),
    ("Stiletto Knife | Scorched", "/img/products/stiletto-scorched.png", 300000, "Nože", "instant", 0, 0, 1,
     "Insta buy = jakmile koupíš, rovnou posílej tradelink do chatu na streamu, insta pošlu.", "covert", 0, None),
    ("Paracord Knife | Rust Coat", "/img/products/paracord-rust-coat.png", 200000, "Nože", "instant", 0, 0, 1,
     "Zaplatíš 200 000 PTS a insta pošli tradelink, já to pošlu.", "restricted", 0, None),
    ("Shadow Daggers | Black Laminate", "/img/products/shadow-daggers-black-laminate.png", 200000, "Nože", "instant", 0, 0, 1,
     "Zaplatíš 200 000 PTS a insta pošli tradelink, já to pošlu.", "classified", 0, None),
    ("AWP | Printstream (WW)", "/img/products/awp-printstream.png", 1000, "Zbraně", "instant", 1, 0, 1000,
     "Skin AWP | Printstream (Well-Worn). Jen pro suby.", "covert", 0, 10),
    ("Nůž Navaja", "/img/products/navaja-vanilla.png", 500, "Tombola", "raffle", 0, 0, 1000,
     "Vyhraj nůž Navaja! Zakoupením získáš 1 tiket (1 tiket = 500 PTS).", "covert", 1, 48),
]

# Changelog (novinky) – KANONICKÝ seznam v kódu. Při startu se nasyncuje do patch_notes
# (jen NOVÉ záznamy podle `key`; smazané v adminu se NEvrací). Přidání novinky = přidat
# řádek sem (ideálně nahoru) + deploy → objeví se sama, žádné ruční psaní.
# (key, titulek, popis, tag: new|improve|fix, created_at ISO UTC)
CHANGELOG = [
    ("fair-games-0608", "⚖️ Férovější hry – konec nabalování", "Zatočili jsme s farmením: duely (coinflip/kostky) mají teď malý poplatek a krátkou pauzu mezi sebou a přibyl denní strop zisku z her. Pro běžné hraní se skoro nic nemění – jen už nejde nekonečně grindovat a žebříček je férovější. 🎲", "improve", "2026-06-08T22:00:00+00:00"),
    ("faster-web-0608", "⚡ Web je teď rychlejší a vydrží nápor", "Posílili jsme web, takže běží svižněji a líp to zvládá, když jste tu všichni naráz (hlavně při streamu). Méně sekání, víc pohody. 💨", "improve", "2026-06-08T21:00:00+00:00"),
    ("daily-wheel", "🎡 Kolo štěstí – zatoč si každý den!", "Na hlavní stránce přibylo Kolo štěstí! Jednou denně si zatočíš a vyhraješ sedláky – od pár drobných až po 🎰 JACKPOT 3000! Kolo se nabíjí každých 20 hodin, tak ať ti žádný den neuteče. Hodně štěstí! 🍀", "new", "2026-06-03T18:00:00+00:00"),
    ("shop-ending", "Shop řadí podle konce dostupnosti", "Odměny, kterým brzy končí dostupnost, jsou teď v shopu první (podle toho, kdy končí) – ať ti žádná neuteče. Položky bez limitu jsou pod nimi.", "improve", "2026-06-03T12:10:00+00:00"),
    ("stream-status", "Stav streamu v hlavičce", "Nahoře v menu teď svítí tečka, jestli je stream živě 🟢 nebo offline 🔴. Když je live, klikni na ni a skočíš rovnou na stream.", "new", "2026-06-02T20:00:00+00:00"),
    ("raffle-limit", "Férovější tomboly – limit ticketů na osobu", "U tomboly jde teď nastavit max ticketů na osobu (% z celku), ať férovou tombolu neskoupí jeden bohatej. Limit vidíš přímo na kartě.", "improve", "2026-06-02T14:00:00+00:00"),
    ("rank-tiers", "Tituly podle pozice + nová liga UNREAL", "Ligy jsou teď podle pozice na leaderboardu: TOP 3 = 🌈 UNREAL (×10 na daily!), TOP 10 ELITE, TOP 30 GOLD, TOP 50 SILVER, TOP 100 BRONZE. Tvůj titul = tvůj násobič na denním streaku.", "improve", "2026-06-01T20:30:00+00:00"),
    ("daily-tier", "Denní bonus podle ligy", "Daily Streak teď násobí denní odměny podle tvojí ligy – Bronze a Silver ×2, Gold ×3, Elite ×5.", "new", "2026-06-01T19:00:00+00:00"),
    ("mobile", "Optimalizace pro mobil", "Celý web teď pohodlně funguje i na telefonu – shop, leaderboard, exchange i predikce.", "improve", "2026-06-01T18:00:00+00:00"),
    ("sub-badges", "Automatické SUB odznaky", "Po subnutí, resubu nebo gift subu na Kicku se ti SUB odznak nahodí sám a po vypršení zase sundá.", "new", "2026-06-01T15:30:00+00:00"),
    ("chat-cmds", "Příkazy bota v chatu", "Napiš !sedláci, !leaderboard, !shop, !drop nebo !predikce přímo v Kick chatu a bot ti odpoví.", "new", "2026-06-01T12:00:00+00:00"),
    ("auto-drops", "Automatické dropy", "Dropy kódů se teď můžou spouštět samy v intervalu během streamu.", "new", "2026-06-01T11:00:00+00:00"),
    ("unified-codes", "Sjednocené kódy", "DROP kód i promo kód teď zadáš na jednom místě – v poli Uplatnit kód.", "improve", "2026-06-01T10:00:00+00:00"),
    ("gift", "Darování sedláků", "V sekci Exchange teď můžeš poslat sedláky kamarádovi.", "new", "2026-05-31T20:00:00+00:00"),
    ("live-pred", "Živé predikce", "Sázky u predikcí se aktualizují živě – nemusíš refreshovat stránku.", "new", "2026-05-31T18:00:00+00:00"),
    ("leaderboard", "Nový leaderboard", "Pódium s medailemi, ligami a korunou pro prvního.", "improve", "2026-05-31T16:00:00+00:00"),
    ("raffle-anim", "Losování tomboly naživo", "Losování tomboly teď běží jako efektní animace ve stylu otevírání bedny.", "new", "2026-05-31T14:00:00+00:00"),
]


def seed_if_empty() -> None:
    conn = get_conn()
    try:
        if conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]:
            return

        ts = now_iso()

        # --- Uživatelé (Kick nick) ---
        user_ids = {}
        for kick, display, role, points in USERS:
            cur = conn.execute(
                "INSERT INTO users (kick_username, username, points, role, avatar_url, created_at) "
                "VALUES (?, ?, ?, ?, NULL, ?)",
                (kick, display, points, role, ts),
            )
            user_ids[kick] = cur.lastrowid
            conn.execute(
                "INSERT INTO points_log (user_id, change, reason, created_at) VALUES (?, ?, ?, ?)",
                (cur.lastrowid, points, "Počáteční body od admina", ts),
            )

        # --- Odměny ---
        product_ids = {}
        now_dt = datetime.now(timezone.utc)
        for name, img, cost, cat, ptype, subs, vip, stock, desc, rarity, hot, ends_h in PRODUCTS:
            ends = (now_dt + timedelta(hours=ends_h)).isoformat() if ends_h is not None else None
            cur = conn.execute(
                "INSERT INTO products (name, image_url, cost_points, category, type, "
                "subs_only, vip_only, stock, description, rarity, ends_at, hot, active, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)",
                (name, img, cost, cat, ptype, subs, vip, stock, desc, rarity, ends, hot, ts),
            )
            product_ids[name] = cur.lastrowid

        # --- Redeem kódy ---
        codes = [
            ("VITEJ100", 100, None, 1000),
            ("STREAM50", 50, None, 500),
            ("BONUS200", 200, None, 200),
        ]
        code_ids = {}
        for code, pts, pid, max_uses in codes:
            cur = conn.execute(
                "INSERT INTO redeem_codes (code, points_value, product_id, max_uses, "
                "uses_count, expires_at, created_at) VALUES (?, ?, ?, ?, 0, NULL, ?)",
                (code, pts, pid, max_uses, ts),
            )
            code_ids[code] = cur.lastrowid

        # --- Tikety do tomboly (ať je co losovat) ---
        navaja = product_ids["Nůž Navaja"]
        raffle_seed = [
            (navaja, "ninja_cz", 3), (navaja, "pixelpaja", 2), (navaja, "aurora", 1),
            (navaja, "lucka", 2), (navaja, "krtek99", 2),
        ]
        for pid, kick, n in raffle_seed:
            for _ in range(n):
                conn.execute(
                    "INSERT INTO raffle_entries (product_id, user_id, created_at) VALUES (?, ?, ?)",
                    (pid, user_ids[kick], ts),
                )

        # --- Login eventy s IP (demo pro anticheat) ---
        # 85.71.10.20 sdílí 3 účty (alt farma); divak se hlásí ze 3 IP.
        login_seed = [
            ("admin", "127.0.0.1"),
            ("ninja_cz", "85.71.10.20"),
            ("pixelpaja", "85.71.10.20"),
            ("toxictomas", "85.71.10.20"),
            ("divak", "31.30.1.5"),
            ("divak", "147.32.80.9"),
            ("divak", "194.50.12.3"),
            ("aurora", "212.96.45.7"),
            ("krtek99", "90.176.20.11"),
            ("vipko", "78.45.200.14"),
            ("subko", "78.45.200.14"),
        ]
        for kick, ip in login_seed:
            conn.execute(
                "INSERT INTO login_events (user_id, ip, user_agent, method, created_at) "
                "VALUES (?, ?, 'Mozilla/5.0 (ukázka)', 'kick-demo', ?)",
                (user_ids[kick], ip, ts),
            )

        # --- Demo pro anticheat: stejná IP u redeem kódu (ninja_cz + pixelpaja sdílí 85.71.10.20) ---
        for kick in ("ninja_cz", "pixelpaja"):
            conn.execute(
                "INSERT INTO redeem_uses (code_id, user_id, created_at) VALUES (?, ?, ?)",
                (code_ids["VITEJ100"], user_ids[kick], ts),
            )
            conn.execute("UPDATE redeem_codes SET uses_count = uses_count + 1 WHERE id = ?",
                         (code_ids["VITEJ100"],))
            conn.execute(
                "INSERT INTO points_log (user_id, change, reason, created_at) VALUES (?, ?, ?, ?)",
                (user_ids[kick], 100, "Redeem kód VITEJ100", ts),
            )

        # --- Demo pro anticheat: rychlé farmení (krtek99 nasbíral hodně přírůstků za chvíli) ---
        for _ in range(6):
            conn.execute(
                "INSERT INTO points_log (user_id, change, reason, created_at) VALUES (?, ?, ?, ?)",
                (user_ids["krtek99"], 50, "Drop (ukázka)", ts),
            )

        conn.commit()
        print(f"[seed] Naplněno: {len(USERS)} uživatelů, {len(PRODUCTS)} odměn, {len(codes)} kódů.")
        print(f"[seed] Admin se připojí Kick nickem: {ADMIN_KICK}")
    finally:
        conn.close()


def sync_changelog() -> int:
    """Nasype do patch_notes jen NOVÉ changelog entry (klíč ještě nebyl synced). Běží při
    KAŽDÉM startu (i na produkci). Smazané v adminu se NEvrací (klíč zůstává v synced setu).
    → Přidat novinku = nový řádek do CHANGELOG + deploy; objeví se sama."""
    conn = get_conn()
    try:
        synced = {s for s in get_setting(conn, "changelog_synced", "").split(",") if s}
        added = 0
        for key, title, body, tag, created in CHANGELOG:
            if key in synced:
                continue
            # když novinka se stejným titulkem už v DB je (původní seed) → jen ji označ jako synced
            if not conn.execute("SELECT 1 FROM patch_notes WHERE title = ?", (title,)).fetchone():
                conn.execute(
                    "INSERT INTO patch_notes (title, body, tag, published, created_at) VALUES (?, ?, ?, 1, ?)",
                    (title, body, tag, created),
                )
                added += 1
            synced.add(key)
        set_setting(conn, "changelog_synced", ",".join(sorted(synced)))
        conn.commit()
        if added:
            print(f"[changelog] +{added} novinek nasyncováno z kódu")
        return added
    finally:
        conn.close()
